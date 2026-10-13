from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from business_rules import calculate_demand_forecast, calculate_supply_chain_plan
from nodes.decision_gate import decision_gate_node, should_request_human_approval


def planning_snapshot(*, on_hand: int = 20, eta_days: int = 12, maximum: int = 500) -> dict:
    return {
        "inventory": {"quantity": on_hand, "reorder_point": 50},
        "inventory_policy": {
            "service_level": 0.95, "review_period_days": 7,
            "reserved_quantity": 5, "backorder_quantity": 10,
            "holding_cost_per_unit_year": 2.0, "stockout_cost_per_unit": 50.0,
        },
        "demand_history": [{"quantity": 10 + i % 3} for i in range(56)],
        "suppliers": [{
            "id": 1, "name": "合格供应商", "lead_time_days": 5,
            "unit_cost": 10.0, "min_order_quantity": 50, "order_multiple": 20,
            "max_order_quantity": maximum, "freight_cost": 100.0,
            "expedited_freight_cost": 180.0, "expedite_lead_time_days": 2,
            "on_time_delivery_rate": 0.96, "defect_rate": 0.01,
        }],
        "purchase_orders": [{
            "quantity": 100,
            "expected_arrival": (datetime.utcnow() + timedelta(days=eta_days)).isoformat(),
        }],
    }


class DemandForecastTests(unittest.TestCase):
    def test_forecast_is_deterministic_and_auditable(self) -> None:
        history = [{"quantity": 10 + i % 2} for i in range(56)]
        first = calculate_demand_forecast(history)
        second = calculate_demand_forecast(history)
        self.assertEqual(first, second)
        self.assertEqual(first["method"], "weighted_moving_average_7_14_28")
        self.assertEqual(first["history_days"], 56)
        self.assertGreater(first["forecast_horizon_quantity"], 0)


class ConstrainedPlanningTests(unittest.TestCase):
    def test_late_inbound_is_excluded_from_inventory_position(self) -> None:
        plan = calculate_supply_chain_plan(planning_snapshot(eta_days=12))
        self.assertEqual(plan["inbound_quantity"], 0)
        self.assertEqual(plan["excluded_late_inbound"], 100)
        self.assertEqual(plan["inventory_position"], 5)
        self.assertEqual(plan["action"], "REORDER")

    def test_order_respects_moq_and_pack_multiple(self) -> None:
        plan = calculate_supply_chain_plan(planning_snapshot())
        quantity = plan["quantity"]
        self.assertGreaterEqual(quantity, 50)
        self.assertEqual(quantity % 20, 0)

    def test_capacity_constraint_is_exposed(self) -> None:
        plan = calculate_supply_chain_plan(planning_snapshot(maximum=60))
        self.assertTrue(plan["recommended_option"]["capacity_constrained"])
        self.assertTrue(plan["constraints"])

    def test_on_time_inbound_can_prevent_replenishment(self) -> None:
        snapshot = planning_snapshot(on_hand=150, eta_days=2)
        snapshot["inventory_policy"]["backorder_quantity"] = 0
        plan = calculate_supply_chain_plan(snapshot)
        self.assertEqual(plan["inbound_quantity"], 100)
        self.assertEqual(plan["action"], "HOLD")


class DecisionAuthorityTests(unittest.TestCase):
    def test_projected_shortage_and_expedite_require_approval(self) -> None:
        state = {
            "final_decision": {"decision_type": "REORDER", "business_case": {"total_landed_risk_cost": 3000}},
            "agent_outputs": {
                "demand": {"demand_risk": "HIGH"},
                "inventory": {"constraints": []},
                "risk": {"supplier_risk": "LOW", "logistics_risk": "HIGH", "capacity_constrained": False},
                "logistics": {"expedite": True, "projected_shortage": 5},
            },
        }
        update = decision_gate_node(state)
        self.assertEqual(update["decision_risk"], "HIGH")
        state.update(update)
        self.assertEqual(should_request_human_approval(state), "human_approval")

    def test_hold_is_low_risk_without_procurement_approval(self) -> None:
        state = {"final_decision": {"decision_type": "HOLD"}, "agent_outputs": {}}
        update = decision_gate_node(state)
        self.assertEqual(update["decision_assessment"]["score"], 0)
        state.update(update)
        self.assertEqual(should_request_human_approval(state), "auto_execute")


if __name__ == "__main__":
    unittest.main()
