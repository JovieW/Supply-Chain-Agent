from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from pydantic import ValidationError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from boundary_schemas import ApprovalResumeInput, validate_tool_response
from evaluation.retrieval_evaluator import aggregate_retrieval_reports, evaluate_retrieval_case
from rag.retriever import _is_applicable


class BoundarySchemaTests(unittest.TestCase):
    def test_approval_rejects_short_feedback_and_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ApprovalResumeInput(
                action="approve", reviewer_email="manager@example.com",
                reviewer_role="supply_chain_manager", feedback="同意",
                decision_hash="a" * 64, browser_role="admin",
            )

    def test_tool_response_rejects_missing_fields(self) -> None:
        with self.assertRaises(ValidationError):
            validate_tool_response("inspect_inventory_position", {"inventory_position": 10})


class KnowledgeApplicabilityTests(unittest.TestCase):
    def test_expired_or_wrong_supplier_contract_is_filtered(self) -> None:
        contract = {
            "status": "active", "effective_from": "2025-01-01",
            "effective_to": "2027-12-31", "product_scope": "1,2",
            "supplier_scope": "1", "region_scope": "cn",
        }
        self.assertTrue(_is_applicable(contract, {
            "as_of": "2026-08-30", "product_id": 1,
            "supplier_ids": [1], "region": "cn",
        }))
        self.assertFalse(_is_applicable(contract, {
            "as_of": "2028-01-01", "product_id": 1,
            "supplier_ids": [1], "region": "cn",
        }))
        self.assertFalse(_is_applicable(contract, {
            "as_of": "2026-08-30", "product_id": 1,
            "supplier_ids": [3], "region": "cn",
        }))


class RetrievalEvaluationTests(unittest.TestCase):
    def test_enterprise_golden_set_keeps_hard_case_coverage(self) -> None:
        path = PROJECT_ROOT / "evals" / "retrieval_cases_enterprise.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        cases = payload["cases"]
        ids = [case["id"] for case in cases]
        hard = [case for case in cases if case.get("difficulty") == "hard"]
        semantic = [
            case for case in cases
            if "semantic-hard-positive" in case.get("capabilities", [])
        ]
        cross_domain = [
            case for case in cases
            if "cross-domain" in case.get("capabilities", [])
        ]
        multi_evidence = [
            case for case in cases if len(case.get("expected_document_ids", [])) > 1
        ]
        self.assertEqual(payload["schema_version"], "rag-retrieval-golden/v3")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(hard), 12)
        self.assertGreaterEqual(len(semantic), 6)
        self.assertGreaterEqual(len(cross_domain), 5)
        self.assertGreaterEqual(len(multi_evidence), 5)

    def test_recall_and_applicability_are_measured(self) -> None:
        case = {"id": "contract", "query": "交付", "top_k": 2,
                "expected_document_ids": ["CONTRACT-1"], "minimum_recall": 1.0,
                "as_of": "2026-08-30"}
        report = evaluate_retrieval_case(case, [{
            "document_id": "CONTRACT-1", "status": "active",
            "effective_from": "2026-01-01", "effective_to": "2027-01-01",
        }])
        aggregate = aggregate_retrieval_reports([report])
        self.assertTrue(report["passed"])
        self.assertEqual(aggregate["mean_recall_at_k"], 1.0)

    def test_hard_and_cross_domain_slices_have_separate_quality_gates(self) -> None:
        case = {
            "id": "cross-domain-hard",
            "query": "库存撑不到船期时是否需要加钱改走更快渠道并审批",
            "top_k": 3,
            "expected_document_ids": ["INVENTORY", "LOGISTICS", "APPROVAL"],
            "minimum_recall": 1.0,
            "difficulty": "hard",
            "capabilities": ["semantic-hard-positive", "cross-domain"],
        }
        results = [
            {"document_id": value, "status": "active"}
            for value in ("INVENTORY", "LOGISTICS", "APPROVAL")
        ]
        report = evaluate_retrieval_case(case, results)
        aggregate = aggregate_retrieval_reports([report], {
            "minimum_hard_case_pass_rate": 1.0,
            "minimum_semantic_hard_positive_pass_rate": 1.0,
            "minimum_cross_domain_pass_rate": 1.0,
        })
        self.assertEqual(report["difficulty"], "hard")
        self.assertEqual(
            aggregate["difficulty_breakdown"]["hard"]["pass_rate"], 1.0
        )
        self.assertEqual(
            aggregate["capability_breakdown"]["cross-domain"]["case_count"], 1
        )
        self.assertTrue(all(aggregate["quality_gates"].values()))


HAS_DATABASE_RUNTIME = importlib.util.find_spec("sqlalchemy") is not None
if HAS_DATABASE_RUNTIME:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import db_service
    from models import Base, Inventory, Product, ProductSupplier, Supplier


@unittest.skipUnless(HAS_DATABASE_RUNTIME, "SQLAlchemy not installed")
class ProcurementLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_engine, self.old_session = db_service.engine, db_service.SessionLocal
        engine = create_engine("sqlite:///:memory:")
        db_service.engine = engine
        db_service.SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(engine)
        with db_service.get_session() as session:
            session.add(Product(id=1, name="芯片", sku="CHIP-P1"))
            session.add(Inventory(product_id=1, quantity=10, reorder_point=50))
            session.add(Supplier(id=1, name="供应商", lead_time_days=5, reliability_score=.95))
            session.add(ProductSupplier(product_id=1, supplier_id=1))

    def tearDown(self) -> None:
        db_service.engine.dispose()
        db_service.engine, db_service.SessionLocal = self.old_engine, self.old_session

    def test_lifecycle_is_idempotent_and_qc_updates_inventory_once(self) -> None:
        po = db_service.create_purchase_order(1, 1, 20, "p1-lifecycle")
        steps = [
            ("SUPPLIER_CONFIRMED", {}, "lifecycle-event-confirm"),
            ("SHIPPED", {"expected_arrival": "2026-09-05"}, "lifecycle-event-ship"),
            ("RECEIVED", {"received_quantity": 20}, "lifecycle-event-receive"),
            ("QC_ACCEPTED", {"accepted_quantity": 18}, "lifecycle-event-qc"),
            ("CLOSED", {}, "lifecycle-event-close"),
        ]
        for event, payload, key in steps:
            result = db_service.transition_purchase_order(
                po.po_id, event, "operator@example.com", key, payload
            )
            self.assertTrue(result["created"])
        replay = db_service.transition_purchase_order(
            po.po_id, "QC_ACCEPTED", "operator@example.com", "lifecycle-event-qc",
            {"accepted_quantity": 18},
        )
        self.assertFalse(replay["created"])
        with db_service.get_session() as session:
            self.assertEqual(session.query(Inventory).filter_by(product_id=1).one().quantity, 28)
        self.assertEqual(db_service.list_purchase_orders(1)[0]["status"], "CLOSED")


if __name__ == "__main__":
    unittest.main()
