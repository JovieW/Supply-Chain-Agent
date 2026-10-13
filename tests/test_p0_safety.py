from __future__ import annotations

import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agents.schemas import (
    CoordinatorDecisionOutput,
    ExplanationOutput,
    invoke_structured,
)


HAS_FULL_RUNTIME = all(
    importlib.util.find_spec(package)
    for package in ("sqlalchemy", "langchain_openai")
)
if HAS_FULL_RUNTIME:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import db_service
    from agents.coordinator_agent import validate_coordinator_proposal
    from models import Base, Inventory, Product, ProductSupplier, PurchaseOrder, Supplier
    from nodes.execution import build_decision_key, validate_final_decision


@unittest.skipUnless(HAS_FULL_RUNTIME, "project runtime dependencies not installed")
class DatabaseSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_engine = db_service.engine
        self.original_session_local = db_service.SessionLocal
        engine = create_engine("sqlite:///:memory:")
        db_service.engine = engine
        db_service.SessionLocal = sessionmaker(bind=engine)
        Base.metadata.create_all(engine)
        with db_service.get_session() as session:
            session.add(Product(id=1, name="关键芯片", sku="CHIP-1"))
            session.add(Inventory(product_id=1, quantity=20, reorder_point=100))
            session.add_all([
                Supplier(id=1, name="已批准供应商", lead_time_days=5, reliability_score=0.96),
                Supplier(id=2, name="未批准供应商", lead_time_days=2, reliability_score=0.80),
            ])
            session.add(ProductSupplier(product_id=1, supplier_id=1))

    def tearDown(self) -> None:
        db_service.engine.dispose()
        db_service.engine = self.original_engine
        db_service.SessionLocal = self.original_session_local

    def test_snapshot_only_exposes_approved_suppliers(self) -> None:
        snapshot = db_service.read_supply_chain_snapshot(1)
        self.assertEqual([supplier["id"] for supplier in snapshot["suppliers"]], [1])

    def test_purchase_order_is_idempotent(self) -> None:
        first = db_service.create_purchase_order(1, 1, 180, "same-decision")
        second = db_service.create_purchase_order(1, 1, 180, "same-decision")
        with db_service.get_session() as session:
            count = session.query(PurchaseOrder).count()
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.po_id, second.po_id)
        self.assertEqual(count, 1)

    def test_decision_log_is_idempotent(self) -> None:
        first = db_service.log_decision_once(
            "hold-decision", "coordinator", "暂不补货", "库存充足"
        )
        second = db_service.log_decision_once(
            "hold-decision", "coordinator", "暂不补货", "库存充足"
        )
        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.log_id, second.log_id)

    def test_distinct_plans_can_create_orders_when_inbound_was_accounted_for(self) -> None:
        first = db_service.create_purchase_order(1, 1, 180, "decision-1")
        second = db_service.create_purchase_order(1, 1, 180, "decision-2")
        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertNotEqual(first.po_id, second.po_id)

    def test_unapproved_supplier_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not approved"):
            db_service.create_purchase_order(2, 1, 180, "unsafe-decision")


@unittest.skipUnless(HAS_FULL_RUNTIME, "project runtime dependencies not installed")
class DecisionValidationTests(unittest.TestCase):
    def _state(self) -> dict:
        return {
            "product_id": 1,
            "workflow_run_id": "run-123",
            "db_snapshot": {
                "product": {"id": 1},
                "suppliers": [{"id": 1}],
            },
            "agent_outputs": {
                "inventory": {"action": "REORDER", "quantity": 180},
            },
            "final_decision": {
                "decision_type": "REORDER",
                "details": {"supplier_id": 1, "quantity": 180, "expedite": False},
            },
        }

    def test_execution_rejects_llm_quantity_change(self) -> None:
        state = self._state()
        state["final_decision"]["details"]["quantity"] = 999
        with self.assertRaisesRegex(ValueError, "Invalid quantity"):
            validate_final_decision(state)

    def test_decision_key_is_stable(self) -> None:
        state = self._state()
        self.assertEqual(build_decision_key(state), build_decision_key(state))

    def test_coordinator_rejects_unapproved_supplier(self) -> None:
        proposal = CoordinatorDecisionOutput(
            decision_type="REORDER",
            supplier_id=2,
            quantity=180,
            expedite=False,
            reasoning="根据库存规则需要补货。",
        )
        with self.assertRaisesRegex(ValueError, "not an approved"):
            validate_coordinator_proposal(
                proposal,
                inventory_output={"action": "REORDER", "quantity": 180},
                logistics_output={"expedite": False},
                suppliers=[{"id": 1}],
            )

class _FakeStructuredRunnable:
    def __init__(self):
        self.async_called = False

    async def ainvoke(self, prompt, config):
        self.async_called = True
        return {"reasoning": "结构化异步响应。"}


class _FakeLLM:
    def __init__(self):
        self.runnable = _FakeStructuredRunnable()

    def with_structured_output(self, schema):
        self.schema = schema
        return self.runnable


class AsyncStructuredOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_helper_uses_async_invocation(self) -> None:
        llm = _FakeLLM()
        result = await invoke_structured(llm, ExplanationOutput, "prompt", {})
        self.assertTrue(llm.runnable.async_called)
        self.assertEqual(result.reasoning, "结构化异步响应。")

    async def test_hold_schema_rejects_side_effect_fields(self) -> None:
        with self.assertRaises(ValueError):
            CoordinatorDecisionOutput(
                decision_type="HOLD",
                supplier_id=1,
                quantity=1,
                expedite=False,
                reasoning="暂不补货。",
            )


if __name__ == "__main__":
    unittest.main()
