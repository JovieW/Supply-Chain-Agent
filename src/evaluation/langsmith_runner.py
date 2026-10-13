"""Publish versioned scenarios and run governed Agent experiments in LangSmith.

The deterministic evaluator in ``evaluation.evaluator`` remains authoritative.
This adapter publishes the same checks as LangSmith feedback so local CI reports
and the hosted experiment view cannot silently use different quality criteria.

Examples:
    python src/evaluation/langsmith_runner.py --dry-run
    python src/evaluation/langsmith_runner.py --sync-only
    python src/evaluation/langsmith_runner.py
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any, Callable, Iterator


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evaluation.evaluator import evaluate_result
from evaluation.runner import load_scenarios
from langsmith_config import LangSmithSettings


DIMENSIONS = (
    "business_correctness",
    "governance_safety",
    "grounding",
    "autonomous_tools",
    "reflection",
    "human_in_the_loop",
    "long_term_memory",
)


def scenario_example(case: dict) -> dict:
    """Convert one repository scenario into a stable LangSmith example."""
    return {
        "inputs": {
            "case_id": str(case["id"]),
            "product_id": int(case["product_id"]),
        },
        "outputs": {"case": case},
        "metadata": {
            "schema_version": "agent-eval-scenarios/v1",
            "case_name": case.get("name", case["id"]),
            "tags": list(case.get("tags") or []),
        },
    }


def sync_dataset(client: Any, dataset_name: str, cases: list[dict]) -> tuple[Any, dict]:
    """Create/update scenario examples without deleting unrelated remote data."""
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description=(
                "Versioned supply-chain Agent regression scenarios. Expected values "
                "come from deterministic business and governance rules."
            ),
        )

    existing = {
        str((getattr(example, "inputs", None) or {}).get("case_id")): example
        for example in client.list_examples(dataset_id=dataset.id)
        if (getattr(example, "inputs", None) or {}).get("case_id") is not None
    }
    created = updated = 0
    for case in cases:
        payload = scenario_example(case)
        previous = existing.get(str(case["id"]))
        if previous is None:
            client.create_example(dataset_id=dataset.id, **payload)
            created += 1
        else:
            client.update_example(example_id=previous.id, **payload)
            updated += 1
    return dataset, {"created": created, "updated": updated, "total": len(cases)}


def _case(reference_outputs: dict | None) -> dict:
    case = (reference_outputs or {}).get("case")
    if not isinstance(case, dict):
        raise ValueError("LangSmith example is missing reference_outputs.case")
    return case


def _result(outputs: dict | None) -> dict:
    result = (outputs or {}).get("result")
    if not isinstance(result, dict):
        raise ValueError("Agent target did not return outputs.result")
    return result


def overall_quality(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict:
    """Publish the local gate's overall score as normalized LangSmith feedback."""
    report = evaluate_result(_result(outputs), _case(reference_outputs))
    failures = [item["id"] for item in report.get("critical_failures", [])]
    return {
        "key": "overall_quality",
        "score": float(report["overall_score"]) / 100.0,
        "comment": (
            f"local_score={report['overall_score']}; passed={report['passed']}; "
            f"critical_failures={failures}"
        ),
    }


def critical_safety(
    inputs: dict, outputs: dict, reference_outputs: dict
) -> dict:
    """Make unsafe regressions prominent instead of hiding them in an average."""
    report = evaluate_result(_result(outputs), _case(reference_outputs))
    failures = report.get("critical_failures", [])
    return {
        "key": "critical_safety",
        "score": 0 if failures else 1,
        "comment": json.dumps(
            [{"id": item.get("id"), "message": item.get("message")} for item in failures],
            ensure_ascii=False,
        ),
    }


def dimension_evaluator(dimension: str) -> Callable[[dict, dict, dict], dict]:
    if dimension not in DIMENSIONS:
        raise ValueError(f"Unsupported evaluation dimension: {dimension}")

    def evaluate_dimension(
        inputs: dict, outputs: dict, reference_outputs: dict
    ) -> dict:
        report = evaluate_result(_result(outputs), _case(reference_outputs))
        value = report.get("dimensions", {}).get(dimension, {})
        failed_checks = [
            item["id"] for item in report.get("checks", [])
            if item.get("dimension") == dimension and not item.get("passed")
        ]
        return {
            "key": dimension,
            "score": float(value.get("score", 0.0)) / 100.0,
            "comment": (
                f"minimum={value.get('minimum')}; passed={value.get('passed')}; "
                f"failed_checks={failed_checks}"
            ),
        }

    evaluate_dimension.__name__ = f"evaluate_{dimension}"
    return evaluate_dimension


def langsmith_evaluators() -> list[Callable[[dict, dict, dict], dict]]:
    return [overall_quality, critical_safety, *(dimension_evaluator(name) for name in DIMENSIONS)]


@contextmanager
def isolated_agent_runtime() -> Iterator[Callable[[dict], dict]]:
    """Run experiments against temporary databases, never the interactive DB."""
    with tempfile.TemporaryDirectory(prefix="supply-chain-langsmith-eval-") as temp_dir:
        temp_path = Path(temp_dir)
        previous_database = os.environ.get("DATABASE_URL")
        previous_checkpoint = os.environ.get("HITL_CHECKPOINT_DB")
        os.environ["DATABASE_URL"] = f"sqlite:///{(temp_path / 'business.db').as_posix()}"
        os.environ["HITL_CHECKPOINT_DB"] = str(temp_path / "checkpoints.sqlite")
        try:
            # Database modules read DATABASE_URL on import. This runner imports
            # them only after applying the isolated paths.
            from db_init import init_database, seed_data
            from db_service import ensure_runtime_schema
            from backend_interface import run_one_cycle

            engine = init_database()
            seed_data(engine)
            ensure_runtime_schema()

            def target(inputs: dict) -> dict:
                return {"result": run_one_cycle(int(inputs["product_id"]))}

            yield target
        finally:
            if previous_database is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous_database
            if previous_checkpoint is None:
                os.environ.pop("HITL_CHECKPOINT_DB", None)
            else:
                os.environ["HITL_CHECKPOINT_DB"] = previous_checkpoint


def _langsmith_client() -> Any:
    try:
        from langsmith import Client
    except ImportError as exc:
        raise RuntimeError(
            "LangSmith SDK is not installed; run: pip install -r requirements.txt"
        ) from exc
    return Client()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sync Agent scenarios and run a LangSmith evaluation experiment"
    )
    parser.add_argument("--scenarios", help="Path to versioned scenarios.json")
    parser.add_argument("--dataset", help="Override LANGSMITH_EVAL_DATASET")
    parser.add_argument("--experiment-prefix", help="Override LANGSMITH_EXPERIMENT_PREFIX")
    parser.add_argument("--sync-only", action="store_true", help="Only create/update examples")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate local scenario conversion without network calls or LLM execution",
    )
    args = parser.parse_args(argv)

    cases = load_scenarios(args.scenarios)
    settings = LangSmithSettings.from_env()
    dataset_name = args.dataset or settings.dataset
    experiment_prefix = args.experiment_prefix or settings.experiment_prefix
    examples = [scenario_example(case) for case in cases]
    if args.dry_run:
        print(json.dumps({
            "dataset": dataset_name,
            "experiment_prefix": experiment_prefix,
            "case_count": len(examples),
            "case_ids": [item["inputs"]["case_id"] for item in examples],
            "evaluators": [item.__name__ for item in langsmith_evaluators()],
        }, ensure_ascii=False, indent=2))
        return 0

    settings.validate(require_enabled=True)
    settings.apply_canonical_environment()
    client = _langsmith_client()
    dataset, stats = sync_dataset(client, dataset_name, cases)
    print(f"Dataset: {dataset.name} ({stats['created']} created, {stats['updated']} updated)")
    if args.sync_only:
        return 0

    with isolated_agent_runtime() as target:
        results = client.evaluate(
            target,
            data=dataset.id,
            evaluators=langsmith_evaluators(),
            experiment_prefix=experiment_prefix,
            description=(
                f"agent_version={settings.app_version}; "
                f"prompt_version={settings.prompt_version}; deterministic governance gates"
            ),
            max_concurrency=1,
        )
    experiment_name = getattr(results, "experiment_name", None)
    print(f"Experiment: {experiment_name or 'created in LangSmith'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
