from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evaluation import compare_to_baseline, evaluate_result, report_to_markdown
from evaluation.runner import aggregate_reports, evaluate_saved_results


def valid_result() -> dict:
    business_case = {
        "inventory_position": 20,
        "days_of_supply": 2.0,
        "reorder_point": 100,
        "safety_stock": 40,
        "target_stock": 200,
        "lead_time_days": 2,
        "projected_shortage": 0,
        "purchase_cost": 1000.0,
        "freight_cost": 100.0,
        "risk_cost": 20.0,
        "total_landed_risk_cost": 1120.0,
        "constraints": [],
    }
    return {
        "product_id": 2,
        "workflow_run_id": "eval-run-1",
        "retrieved_knowledge": {
            "status": "READY",
            "index_stats": {"documents": 1, "chunks": 1, "retrieval": "vector"},
            "all": [{
                "id": "sop-1", "display_id": "补货SOP#1",
                "document_id": "SOP-INVENTORY-001", "version": "2.0",
                "status": "active", "effective_from": "2026-01-01",
                "effective_to": "2099-12-31", "owner": "Inventory Planning",
                "product_scope": "all", "supplier_scope": "all",
                "region_scope": "cn",
            }],
        },
        "long_term_memory": {
            "status": "READY",
            "write_status": "PERSISTED",
            "current_memory_id": 22,
            "recalled": [{
                "id": 10,
                "decision_type": "REORDER",
                "outcome": {"outcome_label": "SUCCESS", "outcome_score": 92.0},
            }],
        },
        "decision_memory_id": 22,
        "final_decision": {
            "decision_type": "REORDER",
            "details": {"supplier_id": 1, "quantity": 100, "expedite": False},
            "business_case": business_case,
            "evidence": ["补货SOP#1"],
            "memory_evidence": [10],
            "explanation": "库存位置低于动态补货点，选择合格供应商并综合考虑成本、交期与风险。",
        },
        "decision_assessment": {
            "score": 2,
            "level": "MEDIUM",
            "approval_required": False,
            "reasons": ["中等需求波动"],
        },
        "human_feedback": None,
        "workflow_interrupted": False,
        "agent_outputs": {
            "inventory": {
                "action": "REORDER",
                "supplier_id": 1,
                "quantity": 100,
            },
            "logistics": {"expedite": False},
            "tool_investigator": {
                "status": "completed",
                "read_only": True,
                "max_tool_calls": 6,
                "tool_call_count": 1,
                "allowlist": ["inspect_inbound_shipments"],
                "tool_calls": [{
                    "tool": "inspect_inbound_shipments",
                    "arguments": {"include_arrived": False},
                    "status": "success",
                    "duration_ms": 5.2,
                    "result": {"exceptions": 0},
                }],
            },
            "reflection": {
                "outcome": "PASS",
                "round": 1,
                "max_rounds": 2,
                "history": [{"round": 1, "outcome": "PASS"}],
            },
            "execution": {"executed": True, "po_id": 5},
        },
    }


class AgentEvaluationTests(unittest.TestCase):
    def test_valid_run_passes_all_quality_gates(self) -> None:
        report = evaluate_result(valid_result())
        self.assertTrue(report["passed"])
        self.assertEqual(report["critical_failure_count"], 0)
        self.assertGreaterEqual(report["overall_score"], 90)
        json.dumps(report)
        self.assertIn("Agent Evaluation Report", report_to_markdown(report))

    def test_tampered_quantity_is_a_critical_failure(self) -> None:
        result = valid_result()
        result["final_decision"]["details"]["quantity"] = 999
        report = evaluate_result(result)
        self.assertFalse(report["passed"])
        self.assertIn(
            "BUSINESS.QUANTITY_MATCH",
            {item["id"] for item in report["critical_failures"]},
        )

    def test_hallucinated_knowledge_citation_fails_grounding(self) -> None:
        result = valid_result()
        result["final_decision"]["evidence"] = ["不存在的合同#99"]
        report = evaluate_result(result)
        self.assertIn(
            "GROUNDING.NO_UNKNOWN_CITATIONS",
            {item["id"] for item in report["critical_failures"]},
        )

    def test_unauthorized_tool_and_budget_overrun_are_blocked(self) -> None:
        result = valid_result()
        investigation = result["agent_outputs"]["tool_investigator"]
        investigation["max_tool_calls"] = 1
        investigation["tool_call_count"] = 2
        investigation["tool_calls"].append({
            "tool": "create_purchase_order",
            "arguments": {},
            "status": "success",
            "duration_ms": 2.0,
        })
        report = evaluate_result(result)
        failure_ids = {item["id"] for item in report["critical_failures"]}
        self.assertIn("TOOLS.WITHIN_BUDGET", failure_ids)
        self.assertIn("TOOLS.ALLOWLIST", failure_ids)

    def test_pending_approval_cannot_contain_execution(self) -> None:
        result = valid_result()
        result["decision_assessment"]["approval_required"] = True
        result["human_feedback"] = "PENDING"
        result["workflow_interrupted"] = True
        result["approval_request_id"] = 7
        report = evaluate_result(result)
        failure_ids = {item["id"] for item in report["critical_failures"]}
        self.assertIn("GOVERNANCE.NO_UNAPPROVED_EXECUTION", failure_ids)
        self.assertIn("HITL.PENDING_NOT_EXECUTED", failure_ids)

    def test_unverified_long_term_memory_is_rejected(self) -> None:
        result = valid_result()
        del result["long_term_memory"]["recalled"][0]["outcome"]
        report = evaluate_result(result)
        self.assertIn(
            "MEMORY.OUTCOME_VERIFIED_ONLY",
            {item["id"] for item in report["critical_failures"]},
        )

    def test_scenario_expectation_is_checked(self) -> None:
        report = evaluate_result(
            valid_result(),
            {"id": "unexpected-hold", "expected_decision_type": "HOLD"},
        )
        self.assertFalse(report["passed"])
        self.assertEqual(report["case_id"], "unexpected-hold")

    def test_baseline_comparison_detects_material_drop(self) -> None:
        baseline = evaluate_result(valid_result())
        current = copy.deepcopy(baseline)
        current["overall_score"] = baseline["overall_score"] - 10
        comparison = compare_to_baseline(current, baseline, maximum_drop=3)
        self.assertFalse(comparison["passed"])
        self.assertEqual(comparison["regressions"][0]["id"], "REGRESSION.OVERALL_SCORE")

    def test_offline_aggregate_and_saved_result_mode_are_json_safe(self) -> None:
        cases = [{"id": "case-a", "product_id": 2, "expected_decision_type": "REORDER"}]
        reports = evaluate_saved_results({"case-a": valid_result()}, cases)
        aggregate = aggregate_reports(reports)
        self.assertTrue(aggregate["passed"])
        self.assertEqual(aggregate["case_count"], 1)
        self.assertIn("business_correctness", aggregate["dimensions"])
        json.dumps({"aggregate": aggregate, "cases": reports})


if __name__ == "__main__":
    unittest.main()
