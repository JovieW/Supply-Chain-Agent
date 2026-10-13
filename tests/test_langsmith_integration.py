from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from evaluation.langsmith_runner import (
    critical_safety,
    dimension_evaluator,
    overall_quality,
    scenario_example,
    sync_dataset,
)
from langsmith_config import LangSmithSettings, langsmith_status


class FakeLangSmithClient:
    def __init__(self, existing: list | None = None) -> None:
        self.dataset = SimpleNamespace(id="dataset-1", name="agent-evals")
        self.examples = existing or []
        self.created: list[dict] = []
        self.updated: list[dict] = []
        self.dataset_created = False

    def has_dataset(self, *, dataset_name: str) -> bool:
        return not self.dataset_created

    def read_dataset(self, *, dataset_name: str):
        return self.dataset

    def create_dataset(self, **kwargs):
        self.dataset_created = True
        self.dataset.name = kwargs["dataset_name"]
        return self.dataset

    def list_examples(self, *, dataset_id: str):
        return list(self.examples)

    def create_example(self, **kwargs):
        self.created.append(kwargs)

    def update_example(self, **kwargs):
        self.updated.append(kwargs)


class LangSmithConfigurationTests(unittest.TestCase):
    def test_current_environment_names_are_preferred_and_secrets_are_not_reported(self) -> None:
        env = {
            "LANGSMITH_TRACING": "true",
            "LANGSMITH_API_KEY": "secret-key",
            "LANGSMITH_PROJECT": "interview-project",
            "AGENT_VERSION": "commit-123",
            "AGENT_PROMPT_VERSION": "reflection-v2",
        }
        with patch.dict(os.environ, env, clear=True):
            settings = LangSmithSettings.from_env()
            settings.validate(require_enabled=True)
            status = langsmith_status()
            metadata = settings.trace_metadata(workflow_run_id="run-1", product_id=2)
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.project, "interview-project")
        self.assertNotIn("api_key", status)
        self.assertNotIn("secret-key", str(status))
        self.assertEqual(metadata["agent_version"], "commit-123")
        self.assertEqual(metadata["prompt_version"], "reflection-v2")

    def test_legacy_environment_names_are_supported(self) -> None:
        with patch.dict(os.environ, {
            "LANGCHAIN_TRACING_V2": "true",
            "LANGCHAIN_API_KEY": "legacy-key",
            "LANGCHAIN_PROJECT": "legacy-project",
        }, clear=True):
            settings = LangSmithSettings.from_env()
            settings.apply_canonical_environment()
            self.assertTrue(settings.enabled)
            self.assertEqual(os.environ["LANGSMITH_API_KEY"], "legacy-key")
            self.assertEqual(os.environ["LANGSMITH_PROJECT"], "legacy-project")


class LangSmithDatasetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = {
            "id": "volatile-demand",
            "name": "Volatile demand",
            "product_id": 2,
            "expected_decision_type": "REORDER",
            "tags": ["volatile", "hitl"],
        }

    def test_scenario_conversion_keeps_reference_expectations(self) -> None:
        example = scenario_example(self.case)
        self.assertEqual(example["inputs"], {"case_id": "volatile-demand", "product_id": 2})
        self.assertEqual(example["outputs"]["case"]["expected_decision_type"], "REORDER")
        self.assertEqual(example["metadata"]["tags"], ["volatile", "hitl"])

    def test_dataset_sync_updates_existing_and_creates_missing_examples(self) -> None:
        existing = SimpleNamespace(
            id="example-1", inputs={"case_id": "volatile-demand"}
        )
        client = FakeLangSmithClient([existing])
        second = {**self.case, "id": "stable-hold", "product_id": 1}
        _, stats = sync_dataset(client, "agent-evals", [self.case, second])
        self.assertEqual(stats, {"created": 1, "updated": 1, "total": 2})
        self.assertEqual(client.updated[0]["example_id"], "example-1")
        self.assertEqual(client.created[0]["inputs"]["case_id"], "stable-hold")


class LangSmithEvaluatorTests(unittest.TestCase):
    def test_feedback_reuses_local_report_and_normalizes_scores(self) -> None:
        report = {
            "overall_score": 86.0,
            "passed": False,
            "critical_failures": [{"id": "GOVERNANCE.FAIL", "message": "unsafe"}],
            "dimensions": {
                "governance_safety": {"score": 75.0, "minimum": 90.0, "passed": False}
            },
            "checks": [{
                "id": "GOVERNANCE.FAIL", "dimension": "governance_safety", "passed": False
            }],
        }
        with patch("evaluation.langsmith_runner.evaluate_result", return_value=report):
            args = ({"case_id": "x"}, {"result": {}}, {"case": {"id": "x"}})
            overall = overall_quality(*args)
            safety = critical_safety(*args)
            governance = dimension_evaluator("governance_safety")(*args)
        self.assertEqual(overall["score"], 0.86)
        self.assertEqual(safety["score"], 0)
        self.assertEqual(governance["score"], 0.75)
        self.assertIn("GOVERNANCE.FAIL", governance["comment"])


if __name__ == "__main__":
    unittest.main()
