from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from reflection_rules import (
    determine_reflection_outcome,
    deterministic_reflection_checks,
    reflection_route,
)
from nodes.decision_gate import decision_gate_node, should_request_human_approval


def valid_state() -> dict:
    return {
        "db_snapshot": {"suppliers": [{"id": 1}]},
        "retrieved_knowledge": {
            "all": [{"id": "source-1", "display_id": "合同#片段1"}]
        },
        "agent_outputs": {
            "inventory": {
                "action": "REORDER", "supplier_id": 1, "quantity": 100,
                "constraints": [],
            },
            "logistics": {"expedite": False, "projected_shortage": 0},
            "demand": {"demand_risk": "LOW"},
            "risk": {
                "supplier_risk": "LOW", "logistics_risk": "LOW",
                "capacity_constrained": False,
            },
        },
        "final_decision": {
            "decision_type": "REORDER",
            "details": {"supplier_id": 1, "quantity": 100, "expedite": False},
            "business_case": {
                "inventory_position": 10, "days_of_supply": 2,
                "reorder_point": 50, "safety_stock": 15, "target_stock": 110,
                "lead_time_days": 5, "projected_shortage": 0,
                "purchase_cost": 1000, "freight_cost": 100,
                "risk_cost": 20, "total_landed_risk_cost": 1120,
                "constraints": [],
            },
            "explanation": "库存位置低于动态补货点，因此按服务水平补货，并选择风险调整成本最低的合格供应商，标准运输不会产生预计缺口。",
            "evidence": ["合同#片段1"],
        },
    }


class DeterministicReflectionTests(unittest.TestCase):
    def test_consistent_decision_passes_hard_checks(self) -> None:
        self.assertEqual(deterministic_reflection_checks(valid_state()), [])

    def test_tampered_quantity_is_critical(self) -> None:
        state = valid_state()
        state["final_decision"]["details"]["quantity"] = 999
        issues = deterministic_reflection_checks(state)
        self.assertIn("QUANTITY_MISMATCH", {issue["code"] for issue in issues})
        self.assertIn("CRITICAL", {issue["severity"] for issue in issues})

    def test_unknown_evidence_is_rejected(self) -> None:
        state = valid_state()
        state["final_decision"]["evidence"] = ["不存在的合同#1"]
        issues = deterministic_reflection_checks(state)
        self.assertIn("UNKNOWN_EVIDENCE", {issue["code"] for issue in issues})


class BoundedReflectionPolicyTests(unittest.TestCase):
    def test_first_failed_review_requests_revision(self) -> None:
        outcome = determine_reflection_outcome(1, 2, [], "REVISE")
        self.assertEqual(outcome, "REVISE")
        state = {"agent_outputs": {"reflection": {"outcome": outcome}}}
        self.assertEqual(reflection_route(state), "revise")

    def test_last_failed_review_escalates(self) -> None:
        self.assertEqual(
            determine_reflection_outcome(2, 2, [], "REVISE"), "ESCALATE"
        )

    def test_reflection_service_error_fails_closed(self) -> None:
        self.assertEqual(
            determine_reflection_outcome(1, 2, [], "PASS", "timeout"), "ESCALATE"
        )

    def test_clean_review_passes(self) -> None:
        self.assertEqual(determine_reflection_outcome(1, 2, [], "PASS"), "PASS")

    def test_escalated_reflection_forces_human_approval(self) -> None:
        state = valid_state()
        state["agent_outputs"]["reflection"] = {
            "outcome": "ESCALATE", "round": 2, "feedback": "风险解释仍不完整"
        }
        update = decision_gate_node(state)
        state.update(update)
        self.assertEqual(update["decision_risk"], "HIGH")
        self.assertEqual(should_request_human_approval(state), "human_approval")


if __name__ == "__main__":
    unittest.main()
