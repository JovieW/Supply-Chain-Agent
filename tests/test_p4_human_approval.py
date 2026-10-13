from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from approval_policy import (
    StaleApprovalError,
    decision_hash,
    required_approval_role,
    snapshot_hash,
    validate_review_payload,
)


class ApprovalPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "HITL_APPROVER_BINDINGS": (
                "planner@example.com:supply_chain_manager,"
                "director@example.com:procurement_director"
            ),
            "HITL_DIRECTOR_COST_THRESHOLD": "10000",
        })
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_decision_hash_is_stable_and_detects_changes(self) -> None:
        decision = {"decision_type": "REORDER", "details": {"quantity": 10}}
        assessment = {"score": 5}
        first = decision_hash(decision, assessment)
        self.assertEqual(first, decision_hash(decision, assessment))
        decision["details"]["quantity"] = 11
        self.assertNotEqual(first, decision_hash(decision, assessment))

    def test_snapshot_hash_detects_material_inventory_change(self) -> None:
        snapshot = {
            "product": {"id": 1},
            "inventory": {"quantity": 20, "reorder_point": 100},
            "suppliers": [{"id": 1}],
            "purchase_orders": [],
            "shipments": [],
        }
        first = snapshot_hash(snapshot)
        snapshot["inventory"]["quantity"] = 30
        self.assertNotEqual(first, snapshot_hash(snapshot))

    def test_server_binding_prevents_role_spoofing(self) -> None:
        with self.assertRaises(PermissionError):
            validate_review_payload(
                {
                    "action": "approve",
                    "reviewer_email": "planner@example.com",
                    "reviewer_role": "procurement_director",
                    "feedback": "已核对缺货风险和采购成本",
                    "decision_hash": "abc",
                },
                required_role="supply_chain_manager",
                expected_decision_hash="abc",
            )

    def test_director_threshold_and_role_hierarchy(self) -> None:
        decision = {"business_case": {"total_landed_risk_cost": 12000}}
        self.assertEqual(required_approval_role(decision), "procurement_director")
        normalized = validate_review_payload(
            {
                "action": "approve",
                "reviewer_email": "director@example.com",
                "reviewer_role": "procurement_director",
                "feedback": "已核对预算、合同和供应风险",
                "decision_hash": "abc",
            },
            required_role="procurement_director",
            expected_decision_hash="abc",
        )
        self.assertEqual(normalized["action"], "approve")

    def test_stale_form_and_short_justification_are_rejected(self) -> None:
        base = {
            "action": "reject",
            "reviewer_email": "planner@example.com",
            "reviewer_role": "supply_chain_manager",
            "feedback": "拒绝",
            "decision_hash": "old",
        }
        with self.assertRaisesRegex(ValueError, "已过期"):
            validate_review_payload(
                base,
                required_role="supply_chain_manager",
                expected_decision_hash="new",
            )
        base["decision_hash"] = "new"
        with self.assertRaisesRegex(ValueError, "至少"):
            validate_review_payload(
                base,
                required_role="supply_chain_manager",
                expected_decision_hash="new",
            )


HAS_DATABASE_RUNTIME = importlib.util.find_spec("sqlalchemy") is not None
if HAS_DATABASE_RUNTIME:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import db_service
    from models import Base, Inventory, Product, ProductSupplier, Supplier


@unittest.skipUnless(HAS_DATABASE_RUNTIME, "SQLAlchemy not installed")
class DurableApprovalDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "HITL_APPROVER_BINDINGS": "planner@example.com:supply_chain_manager",
            "HITL_DIRECTOR_COST_THRESHOLD": "10000",
        })
        self.environment.start()
        self.original_engine = db_service.engine
        self.original_session_local = db_service.SessionLocal
        engine = create_engine("sqlite:///:memory:")
        db_service.engine = engine
        db_service.SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(engine)
        with db_service.get_session() as session:
            session.add(Product(id=1, name="关键芯片", sku="CHIP-P4"))
            session.add(Inventory(product_id=1, quantity=20, reorder_point=100))
            session.add(Supplier(
                id=1, name="合格供应商", lead_time_days=5, reliability_score=0.96
            ))
            session.add(ProductSupplier(product_id=1, supplier_id=1))

    def tearDown(self) -> None:
        db_service.engine.dispose()
        db_service.engine = self.original_engine
        db_service.SessionLocal = self.original_session_local
        self.environment.stop()

    def _create(self, workflow_run_id: str) -> tuple[int, dict]:
        snapshot = db_service.read_supply_chain_snapshot(1)
        data = {
            "workflow_run_id": workflow_run_id,
            "product_id": 1,
            "decision": {
                "decision_type": "REORDER",
                "details": {"supplier_id": 1, "quantity": 100, "expedite": False},
                "business_case": {"total_landed_risk_cost": 1000},
            },
            "assessment": {"level": "HIGH", "score": 5},
            "db_snapshot": snapshot,
        }
        approval_id = db_service.create_approval_request(1, data)
        return approval_id, db_service.get_approval_request(approval_id)

    @staticmethod
    def _payload(record: dict, action: str = "approve") -> dict:
        return {
            "action": action,
            "reviewer_email": "planner@example.com",
            "reviewer_role": "supply_chain_manager",
            "feedback": "已完成库存、成本和风险核对",
            "decision_hash": record["decision_hash"],
        }

    def test_request_is_idempotent_and_rejection_is_persisted(self) -> None:
        approval_id, record = self._create("p4-reject")
        duplicate_id, _ = self._create("p4-reject")
        self.assertEqual(approval_id, duplicate_id)
        status = db_service.review_approval_request(
            approval_id, self._payload(record, "reject")
        )
        self.assertEqual(status, "REJECTED")
        self.assertEqual(db_service.get_approval_request(approval_id)["status"], "REJECTED")

    def test_live_data_change_invalidates_approval(self) -> None:
        approval_id, record = self._create("p4-stale")
        with db_service.get_session() as session:
            session.query(Inventory).filter_by(product_id=1).one().quantity = 80
        status = db_service.review_approval_request(approval_id, self._payload(record))
        self.assertEqual(status, "STALE")

    def test_data_change_after_approval_is_blocked_before_execution(self) -> None:
        approval_id, record = self._create("p4-post-approval-stale")
        status = db_service.review_approval_request(
            approval_id, self._payload(record)
        )
        self.assertEqual(status, "APPROVED")
        with db_service.get_session() as session:
            session.query(Inventory).filter_by(product_id=1).one().quantity = 70
        latest = db_service.read_supply_chain_snapshot(1)
        with self.assertRaises(StaleApprovalError):
            db_service.assert_approval_authorizes_execution(
                approval_id,
                "p4-post-approval-stale",
                record["decision_hash"],
                latest_snapshot=latest,
            )
        self.assertEqual(
            db_service.get_approval_request(approval_id)["status"], "STALE"
        )


if __name__ == "__main__":
    unittest.main()
