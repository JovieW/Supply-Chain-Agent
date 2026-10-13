from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from memory_rules import (
    evaluate_outcome,
    format_memory_context,
    rank_memories,
    validate_outcome_feedback,
)


class OutcomeEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "MEMORY_FEEDBACK_USERS": "analyst@example.com",
        })
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_successful_outcome_scores_high(self) -> None:
        memory = {
            "planned_quantity": 100,
            "planned_lead_time_days": 5,
            "planned_total_cost": 1000,
        }
        feedback = validate_outcome_feedback({
            "actual_lead_time_days": 5,
            "received_quantity": 100,
            "stockout_units": 0,
            "quality_reject_units": 0,
            "actual_total_cost": 1000,
            "service_level": 0.99,
            "submitted_by": "analyst@example.com",
            "notes": "按期足量交付，业务运行正常",
        }, planned_quantity=100)
        result = evaluate_outcome(memory, feedback)
        self.assertEqual(result["outcome_label"], "SUCCESS")
        self.assertEqual(result["outcome_score"], 100.0)

    def test_delay_stockout_and_quality_loss_reduce_score(self) -> None:
        memory = {
            "planned_quantity": 100,
            "planned_lead_time_days": 5,
            "planned_total_cost": 1000,
        }
        feedback = validate_outcome_feedback({
            "actual_lead_time_days": 12,
            "received_quantity": 70,
            "stockout_units": 30,
            "quality_reject_units": 10,
            "actual_total_cost": 1400,
            "service_level": 0.70,
            "submitted_by": "analyst@example.com",
            "notes": "供应商延期且出现缺货和质量拒收",
        }, planned_quantity=100)
        result = evaluate_outcome(memory, feedback)
        self.assertEqual(result["outcome_label"], "FAILURE")
        self.assertLess(result["outcome_score"], 60)
        self.assertIn("实际交期", result["lesson"])

    def test_unauthorized_feedback_and_invalid_quality_are_rejected(self) -> None:
        payload = {
            "actual_lead_time_days": 5,
            "received_quantity": 10,
            "stockout_units": 0,
            "quality_reject_units": 11,
            "actual_total_cost": 100,
            "service_level": 0.9,
            "submitted_by": "outsider@example.com",
            "notes": "包含完整的实际结果说明",
        }
        with self.assertRaises(PermissionError):
            validate_outcome_feedback(payload, planned_quantity=10)
        payload["submitted_by"] = "analyst@example.com"
        with self.assertRaisesRegex(ValueError, "拒收"):
            validate_outcome_feedback(payload, planned_quantity=10)


class MemoryRetrievalTests(unittest.TestCase):
    def test_only_evaluated_memories_are_ranked(self) -> None:
        now = datetime.utcnow()
        memories = [
            {
                "id": 1, "product_id": 1, "decision_type": "REORDER",
                "supplier_id": 2, "planned_quantity": 100,
                "context": {"demand_volatility": "HIGH"},
                "created_at": (now - timedelta(days=5)).isoformat(),
                "outcome": {"outcome_label": "FAILURE", "outcome_score": 30,
                            "lesson": "历史延期导致缺货"},
            },
            {
                "id": 2, "product_id": 2, "decision_type": "HOLD",
                "supplier_id": None, "planned_quantity": 0,
                "context": {"demand_volatility": "LOW"},
                "created_at": (now - timedelta(days=1)).isoformat(),
                "outcome": {"outcome_label": "SUCCESS", "outcome_score": 95,
                            "lesson": "库存充足"},
            },
            {
                "id": 3, "product_id": 1, "decision_type": "REORDER",
                "supplier_id": 2, "context": {"demand_volatility": "HIGH"},
                "created_at": now.isoformat(), "outcome": None,
            },
        ]
        ranked = rank_memories({
            "product_id": 1, "decision_type": "REORDER",
            "supplier_id": 2, "demand_volatility": "HIGH",
        }, memories)
        self.assertEqual([item["id"] for item in ranked], [1, 2])
        self.assertGreater(ranked[0]["similarity_score"], ranked[1]["similarity_score"])
        self.assertIn("记忆#1", format_memory_context({"recalled": ranked}))


HAS_DATABASE_RUNTIME = importlib.util.find_spec("sqlalchemy") is not None
if HAS_DATABASE_RUNTIME:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import db_service
    from models import Base, Inventory, Product, ProductSupplier, Supplier


@unittest.skipUnless(HAS_DATABASE_RUNTIME, "SQLAlchemy not installed")
class DecisionMemoryDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(os.environ, {
            "MEMORY_FEEDBACK_USERS": "analyst@example.com",
        })
        self.environment.start()
        self.original_engine = db_service.engine
        self.original_session_local = db_service.SessionLocal
        engine = create_engine("sqlite:///:memory:")
        db_service.engine = engine
        db_service.SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(engine)
        with db_service.get_session() as session:
            session.add(Product(id=1, name="关键芯片", sku="MEM-1"))
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

    @staticmethod
    def _state() -> dict:
        return {
            "workflow_run_id": "memory-run-1",
            "product_id": 1,
            "db_snapshot": {"product": {"id": 1, "name": "关键芯片"}},
            "agent_outputs": {
                "demand": {"forecast": {"volatility": "HIGH", "forecast_daily_demand": 20}},
                "inventory": {"inventory_position": 20, "days_of_supply": 1, "constraints": []},
                "logistics": {"projected_shortage": 30},
            },
            "long_term_memory": {"recalled": []},
            "final_decision": {
                "decision_type": "REORDER",
                "details": {"supplier_id": 1, "quantity": 100, "expedite": False},
                "business_case": {"lead_time_days": 5, "total_landed_risk_cost": 1000},
            },
            "decision_assessment": {"level": "LOW", "score": 0, "approval_required": False},
        }

    def test_persist_feedback_and_recall_closed_loop(self) -> None:
        memory_id = db_service.create_decision_memory(self._state())
        self.assertEqual(memory_id, db_service.create_decision_memory(self._state()))
        po = db_service.create_purchase_order(1, 1, 100, "memory-po-1")
        transitions = [
            ("SUPPLIER_CONFIRMED", {}, "memory-event-confirm"),
            ("SHIPPED", {"expected_arrival": "2026-09-05"}, "memory-event-shipped"),
            ("RECEIVED", {"received_quantity": 100}, "memory-event-received"),
            ("QC_ACCEPTED", {"accepted_quantity": 100}, "memory-event-accepted"),
            ("CLOSED", {}, "memory-event-closed"),
        ]
        for event_type, payload, key in transitions:
            db_service.transition_purchase_order(
                po.po_id, event_type, "analyst@example.com", key, payload
            )
        db_service.update_decision_memory_status(
            memory_id, "EXECUTED", purchase_order_id=po.po_id
        )
        saved = db_service.record_outcome_feedback(memory_id, {
            "actual_lead_time_days": 5,
            "received_quantity": 100,
            "stockout_units": 0,
            "quality_reject_units": 0,
            "actual_total_cost": 1000,
            "service_level": 0.99,
            "submitted_by": "analyst@example.com",
            "notes": "实际交付结果符合原计划",
        })
        self.assertEqual(saved["status"], "OUTCOME_RECORDED")
        recalled = db_service.recall_decision_memories({
            "product_id": 1, "decision_type": "REORDER",
            "supplier_id": 1, "demand_volatility": "HIGH",
        })
        self.assertEqual(recalled[0]["id"], memory_id)
        with self.assertRaisesRegex(ValueError, "已经提交"):
            db_service.record_outcome_feedback(memory_id, {
                "submitted_by": "analyst@example.com", "notes": "重复提交结果"
            })


if __name__ == "__main__":
    unittest.main()
