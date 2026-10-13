from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import api_client
from api_schemas import ApprovalReviewRequest, DecisionCreateRequest
from pydantic import ValidationError


class FakeResponse:
    def __init__(self, payload) -> None:
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.payload


class ApiSchemaTests(unittest.TestCase):
    def test_decision_request_rejects_unknown_and_invalid_fields(self) -> None:
        with self.assertRaises(ValidationError):
            DecisionCreateRequest(product_id=0)
        with self.assertRaises(ValidationError):
            DecisionCreateRequest(product_id=1, browser_role="admin")

    def test_approval_request_does_not_accept_browser_identity(self) -> None:
        with self.assertRaises(ValidationError):
            ApprovalReviewRequest(
                approved=True,
                feedback="approved after review",
                reviewer_role="admin",
            )


class ApiClientTests(unittest.TestCase):
    def test_decision_uses_json_http_boundary(self) -> None:
        captured = {}

        def fake_open(request, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.method
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse({"workflow_run_id": "run-1"})

        with patch("api_client.urlopen", side_effect=fake_open):
            result = api_client.run_one_cycle(2)
        self.assertEqual(captured["url"], "http://127.0.0.1:8000/api/v1/decisions")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"], {"product_id": 2})
        self.assertEqual(result["workflow_run_id"], "run-1")


class FastApiTopologyTests(unittest.TestCase):
    def test_async_routes_await_async_business_service(self) -> None:
        source = (SRC_ROOT / "api.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
        }
        create_calls = {node.func.id for node in ast.walk(functions["create_decision"])
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        review_calls = {node.func.id for node in ast.walk(functions["review_approval"])
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertIn("run_one_cycle_async", create_calls)
        self.assertIn("review_decision_async", review_calls)
        self.assertNotIn("asyncio", source)


if __name__ == "__main__":
    unittest.main()
