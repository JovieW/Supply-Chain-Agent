from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from autonomous_tools import (
    NoArguments,
    StressTestArguments,
    build_read_only_tools,
    compare_supplier_options,
    inspect_inbound_shipments,
    inspect_inventory_position,
    search_enterprise_knowledge,
    stress_test_plan,
)
from nodes.decision_gate import decision_gate_node


HAS_LANGCHAIN_CORE = importlib.util.find_spec("langchain_core") is not None


def snapshot() -> dict:
    return {
        "inventory": {"quantity": 25, "reorder_point": 60},
        "inventory_policy": {
            "service_level": 0.98, "review_period_days": 7,
            "reserved_quantity": 5, "backorder_quantity": 8,
            "holding_cost_per_unit_year": 3.0, "stockout_cost_per_unit": 40.0,
        },
        "demand_history": [{"quantity": 9 + index % 4} for index in range(56)],
        "suppliers": [{
            "id": 1, "name": "合格供应商", "lead_time_days": 5,
            "unit_cost": 15.0, "min_order_quantity": 30, "order_multiple": 10,
            "max_order_quantity": 500, "freight_cost": 100.0,
            "expedited_freight_cost": 220.0, "expedite_lead_time_days": 2,
            "on_time_delivery_rate": 0.96, "defect_rate": 0.01,
        }],
        "purchase_orders": [{
            "id": 7, "supplier_id": 1, "quantity": 80,
            "expected_arrival": (datetime.utcnow() + timedelta(days=12)).isoformat(),
        }],
        "shipments": [],
    }


class ReadOnlyToolTests(unittest.TestCase):
    def test_inventory_and_supplier_tools_return_structured_facts(self) -> None:
        current = snapshot()
        inventory = inspect_inventory_position(current)
        sourcing = compare_supplier_options(current)
        self.assertEqual(inventory["action"], "REORDER")
        self.assertGreater(sourcing["recommended_quantity"], 0)
        self.assertTrue(sourcing["options"])

    def test_inbound_tool_surfaces_missing_eta(self) -> None:
        current = snapshot()
        current["purchase_orders"][0]["expected_arrival"] = None
        result = inspect_inbound_shipments(current)
        self.assertEqual(result["exceptions"], 1)
        self.assertEqual(result["orders"][0]["status"], "missing_eta")

    def test_stress_test_does_not_mutate_live_snapshot(self) -> None:
        current = snapshot()
        before = copy.deepcopy(current)
        result = stress_test_plan(current, demand_multiplier=1.5, lead_time_delay_days=7)
        self.assertEqual(current, before)
        self.assertEqual(result["scenario"]["demand_multiplier"], 1.5)

    def test_tool_parameters_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            StressTestArguments(demand_multiplier=5.0, lead_time_delay_days=90)
        with self.assertRaises(ValidationError):
            NoArguments(unexpected="write-attempt")

    def test_knowledge_tool_falls_back_to_cached_retrieval(self) -> None:
        retrieved = {"all": [{
            "id": "case-1", "display_id": "历史案例#1",
            "document_type": "historical_event", "title": "港口延期",
            "content": "发生运输延期时启动备选路线。", "score": 0.91,
        }]}
        def unavailable_vector_search(*args, **kwargs):
            raise RuntimeError("simulated vector store outage")

        result = search_enterprise_knowledge(
            retrieved,
            "延期",
            document_type="historical_event",
            top_k=1,
            vector_search_fn=unavailable_vector_search,
        )
        self.assertEqual(
            result["mode"], "cached_retrieval_fallback:RuntimeError"
        )
        self.assertEqual(result["results"][0]["source_id"], "历史案例#1")

    @unittest.skipUnless(HAS_LANGCHAIN_CORE, "langchain-core not installed")
    def test_registered_tool_allowlist_contains_no_write_operation(self) -> None:
        names = {tool.name for tool in build_read_only_tools(snapshot(), {})}
        self.assertEqual(names, {
            "inspect_inventory_position", "forecast_demand",
            "inspect_inbound_shipments", "compare_supplier_options",
            "search_enterprise_knowledge", "stress_test_plan",
        })
        self.assertFalse(any("create" in name or "approve" in name for name in names))


class ToolRiskIntegrationTests(unittest.TestCase):
    def test_autonomous_alerts_are_included_in_risk_score(self) -> None:
        state = {
            "final_decision": {
                "decision_type": "REORDER",
                "business_case": {"total_landed_risk_cost": 1000},
            },
            "agent_outputs": {
                "demand": {"demand_risk": "LOW"},
                "inventory": {"constraints": []},
                "risk": {
                    "supplier_risk": "LOW", "logistics_risk": "LOW",
                    "capacity_constrained": False,
                },
                "logistics": {"expedite": False, "projected_shortage": 0},
                "tool_investigator": {
                    "alerts": ["压力情景预计缺口 10 件", "检测到 1 个在途异常"]
                },
            },
        }
        update = decision_gate_node(state)
        self.assertEqual(update["decision_risk"], "MEDIUM")
        self.assertEqual(update["decision_assessment"]["score"], 2)
        self.assertIn("自主工具调查", "；".join(update["decision_assessment"]["reasons"]))


if __name__ == "__main__":
    unittest.main()
