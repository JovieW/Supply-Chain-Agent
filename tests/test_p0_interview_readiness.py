from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from auth_context import get_current_actor


class ServerActorBoundaryTests(unittest.TestCase):
    def test_actor_and_highest_role_come_from_server_configuration(self) -> None:
        with patch.dict(os.environ, {
            "HITL_APPROVER_BINDINGS": "manager@example.com:supply_chain_manager|procurement_director",
            "HITL_CURRENT_ACTOR_EMAIL": "manager@example.com",
        }, clear=False):
            actor = get_current_actor()
        self.assertEqual(actor.email, "manager@example.com")
        self.assertEqual(actor.role, "procurement_director")
        self.assertEqual(actor.source, "server_config")

    def test_unbound_server_actor_is_rejected(self) -> None:
        with patch.dict(os.environ, {
            "HITL_APPROVER_BINDINGS": "manager@example.com:supply_chain_manager",
            "HITL_CURRENT_ACTOR_EMAIL": "attacker@example.com",
        }, clear=False):
            with self.assertRaises(PermissionError):
                get_current_actor()

    def test_local_demo_can_choose_server_default_without_browser_input(self) -> None:
        with patch.dict(os.environ, {
            "HITL_APPROVER_BINDINGS": (
                "planner@example.com:supply_chain_manager,"
                "director@example.com:procurement_director"
            ),
            "HITL_CURRENT_ACTOR_EMAIL": "",
        }, clear=False):
            actor = get_current_actor()
        self.assertEqual(actor.email, "director@example.com")
        self.assertEqual(actor.role, "procurement_director")
        self.assertEqual(actor.source, "server_default")


HAS_CALLBACK_RUNTIME = importlib.util.find_spec("langchain_core") is not None
if HAS_CALLBACK_RUNTIME:
    from observability import read_trace, record_event


@unittest.skipUnless(HAS_CALLBACK_RUNTIME, "langchain-core not installed")
class PersistentTraceTests(unittest.TestCase):
    def test_trace_is_persistent_and_scoped_by_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            with patch.dict(os.environ, {"AGENT_TRACE_PATH": str(path)}, clear=False):
                record_event("run-1", "workflow_started", product_id=1)
                record_event("run-2", "workflow_started", product_id=2)
                record_event("run-1", "workflow_completed", product_id=1)
                events = read_trace("run-1")
        self.assertEqual([item["event"] for item in events], [
            "workflow_started", "workflow_completed"
        ])
        self.assertTrue(all(item["workflow_run_id"] == "run-1" for item in events))
