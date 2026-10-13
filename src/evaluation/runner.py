"""Offline, isolated regression runner for the supply-chain Agent.

Run it from the repository root:
    python src/evaluation/runner.py

It creates temporary business/checkpoint SQLite files, so scenario execution
cannot create purchase orders, approvals, or memories in the interactive DB.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evaluation.evaluator import compare_to_baseline, evaluate_result, report_to_markdown


def load_scenarios(path: str | Path | None = None) -> list[dict]:
    scenario_path = Path(path) if path else REPO_ROOT / "evals" / "scenarios.json"
    with scenario_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError("Evaluation scenario file must contain a non-empty cases list")
    return cases


def _error_report(case: dict, exc: Exception) -> dict:
    message = f"{type(exc).__name__}: {exc}"
    return {
        "schema_version": "agent-eval-report/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case.get("id"),
        "product_id": case.get("product_id"),
        "passed": False,
        "overall_score": 0.0,
        "minimum_overall_score": 80.0,
        "dimensions": {},
        "critical_failure_count": 1,
        "critical_failures": [{
            "id": "RUNNER.SCENARIO_EXECUTION",
            "dimension": "runner",
            "passed": False,
            "severity": "CRITICAL",
            "weight": 1.0,
            "message": "场景未能完成 Agent 流程执行",
            "evidence": message,
        }],
        "failed_dimensions": ["runner"],
        "checks": [],
        "execution_error": message,
    }


def aggregate_reports(case_reports: list[dict]) -> dict:
    """Build a stable aggregate that can be checked against a committed baseline."""
    if not case_reports:
        raise ValueError("At least one scenario report is required")
    dimension_scores: dict[str, list[float]] = {}
    dimension_minimums: dict[str, list[float]] = {}
    for report in case_reports:
        for name, dimension in (report.get("dimensions") or {}).items():
            dimension_scores.setdefault(name, []).append(float(dimension.get("score", 0)))
            dimension_minimums.setdefault(name, []).append(float(dimension.get("minimum", 0)))
    dimensions = {
        name: {
            "score": round(sum(scores) / len(scores), 1),
            "minimum": round(sum(dimension_minimums[name]) / len(dimension_minimums[name]), 1),
            "passed": all(
                report.get("dimensions", {}).get(name, {}).get("passed", False)
                for report in case_reports
                if name in report.get("dimensions", {})
            ),
        }
        for name, scores in dimension_scores.items()
    }
    failures = [
        failure
        for report in case_reports
        for failure in report.get("critical_failures", [])
    ]
    return {
        "schema_version": "agent-eval-aggregate/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_count": len(case_reports),
        "passed_case_count": sum(bool(report.get("passed")) for report in case_reports),
        "passed": all(bool(report.get("passed")) for report in case_reports),
        "overall_score": round(
            sum(float(report.get("overall_score", 0)) for report in case_reports) / len(case_reports), 1
        ),
        "dimensions": dimensions,
        "critical_failure_count": len(failures),
        "critical_failures": failures,
        "checks": [check for report in case_reports for check in report.get("checks", [])],
    }


def evaluate_saved_results(payload: Any, cases: list[dict]) -> list[dict]:
    """Evaluate results previously exported by an integration, without calling an LLM."""
    if isinstance(payload, dict):
        by_case = payload.get("results", payload)
    else:
        by_case = payload
    reports: list[dict] = []
    for index, case in enumerate(cases):
        if isinstance(by_case, list):
            result = by_case[index] if index < len(by_case) else None
        else:
            result = by_case.get(case["id"]) if isinstance(by_case, dict) else None
        if not isinstance(result, dict):
            reports.append(_error_report(case, ValueError("Missing saved workflow result")))
            continue
        reports.append(evaluate_result(result, case))
    return reports


def run_isolated_scenarios(cases: list[dict]) -> list[dict]:
    """Run all scenarios against a temporary SQLite business and checkpoint DB."""
    with tempfile.TemporaryDirectory(prefix="supply-chain-agent-eval-") as temp_dir:
        temp_path = Path(temp_dir)
        os.environ["DATABASE_URL"] = f"sqlite:///{(temp_path / 'business.db').as_posix()}"
        os.environ["HITL_CHECKPOINT_DB"] = str(temp_path / "checkpoints.sqlite")

        # These modules read DATABASE_URL at import time, so import only after overrides.
        from db_init import init_database, seed_data
        from db_service import ensure_runtime_schema
        from backend_interface import run_one_cycle

        engine = init_database()
        seed_data(engine)
        ensure_runtime_schema()
        reports: list[dict] = []
        for case in cases:
            try:
                reports.append(evaluate_result(run_one_cycle(int(case["product_id"])), case))
            except Exception as exc:  # one failed case must not hide remaining regressions
                reports.append(_error_report(case, exc))
        return reports


def _write_reports(output_dir: Path, aggregate: dict, cases: list[dict]) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = output_dir / f"agent_eval_{timestamp}.json"
    markdown_path = output_dir / f"agent_eval_{timestamp}.md"
    document = {"aggregate": aggregate, "cases": cases}
    json_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(report_to_markdown(aggregate), encoding="utf-8")
    return json_path, markdown_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run versioned Agent evaluation scenarios")
    parser.add_argument("--scenarios", help="Path to scenarios.json")
    parser.add_argument("--input", help="Evaluate exported workflow results instead of calling the Agent")
    parser.add_argument("--baseline", help="Aggregate report JSON (or report wrapper) to compare")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "artifacts" / "evaluations"))
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)

    scenarios = load_scenarios(args.scenarios)
    if args.input:
        with Path(args.input).open("r", encoding="utf-8") as handle:
            reports = evaluate_saved_results(json.load(handle), scenarios)
    else:
        reports = run_isolated_scenarios(scenarios)
    aggregate = aggregate_reports(reports)
    if args.baseline:
        with Path(args.baseline).open("r", encoding="utf-8") as handle:
            baseline_payload = json.load(handle)
        baseline = baseline_payload.get("aggregate", baseline_payload)
        aggregate["baseline_comparison"] = compare_to_baseline(aggregate, baseline)
        if args.fail_on_regression and not aggregate["baseline_comparison"]["passed"]:
            aggregate["passed"] = False
    json_path, markdown_path = _write_reports(Path(args.output_dir), aggregate, reports)
    print(f"Evaluation verdict: {'PASS' if aggregate['passed'] else 'FAIL'}")
    print(f"Overall score: {aggregate['overall_score']:.1f}")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 0 if aggregate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
