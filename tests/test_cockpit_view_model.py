from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cockpit_view_model import (
    build_action_center,
    build_feedback_diagnosis,
    build_workflow_trace,
    decision_evidence,
)


def result_fixture() -> dict:
    return {
        "db_snapshot": {"product": {"name": "关键芯片"}},
        "retrieved_knowledge": {
            "index_stats": {"documents": 2},
            "all": [{"display_id": "SOP#1", "title": "缺货应急 SOP", "score": 0.9, "content": "优先核查在途。"}],
        },
        "long_term_memory": {"status": "READY", "recalled": []},
        "final_decision": {
            "decision_type": "REORDER",
            "details": {"supplier_id": 1, "quantity": 100, "expedite": True},
            "evidence": ["SOP#1", "合同#9"],
        },
        "decision_risk": "HIGH",
        "decision_assessment": {"approval_required": True},
        "human_feedback": "PENDING",
        "approval_required_role": "supply_chain_manager",
        "agent_outputs": {
            "demand": {}, "inventory": {}, "risk": {}, "logistics": {"projected_shortage": 25},
            "coordinator": {},
            "tool_investigator": {"status": "completed", "tool_call_count": 2},
            "reflection": {"outcome": "PASS", "round": 1},
        },
    }


class CockpitViewModelTests(unittest.TestCase):
    def test_pending_approval_blocks_execution_in_timeline(self) -> None:
        trace = build_workflow_trace(result_fixture())
        by_id = {item["id"]: item for item in trace}
        self.assertEqual(by_id["approval"]["status"], "waiting")
        self.assertEqual(by_id["execution"]["status"], "blocked")

    def test_action_center_exposes_next_business_step(self) -> None:
        card = build_action_center(result_fixture())
        self.assertEqual(card["action"], "REORDER")
        self.assertEqual(card["state"], "waiting")
        self.assertIn("不能创建采购单", card["next_step"])

    def test_evidence_resolution_preserves_missing_source_for_audit(self) -> None:
        evidence = decision_evidence(result_fixture())
        self.assertTrue(evidence[0]["found"])
        self.assertFalse(evidence[1]["found"])

    def test_feedback_diagnosis_explains_missing_decision_memory(self) -> None:
        diagnosis = build_feedback_diagnosis(
            memories=[], orders=[{"id": 7, "status": "CLOSED"}]
        )
        self.assertEqual(diagnosis["state"], "missing_decision_memory")
        self.assertIn("不会创建", diagnosis["message"])

    def test_feedback_diagnosis_accepts_closed_executed_plan(self) -> None:
        diagnosis = build_feedback_diagnosis(
            memories=[{
                "purchase_order_id": 7,
                "purchase_order_status": "CLOSED",
                "decision_type": "REORDER",
                "status": "EXECUTED",
                "outcome": None,
            }],
            orders=[{"id": 7, "status": "CLOSED"}],
        )
        self.assertEqual(diagnosis["state"], "ready")
        self.assertEqual(diagnosis["eligible_count"], 1)


if __name__ == "__main__":
    unittest.main()
