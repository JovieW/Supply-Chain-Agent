"""Persistent, privacy-conscious JSONL traces for the local Agent workflow."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


_WRITE_LOCK = Lock()


def _trace_path() -> Path:
    configured = Path(os.getenv("AGENT_TRACE_PATH", "data/agent_traces.jsonl"))
    if not configured.is_absolute():
        configured = Path(__file__).resolve().parents[1] / configured
    configured.parent.mkdir(parents=True, exist_ok=True)
    return configured


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except TypeError:
        return str(value)


def record_event(workflow_run_id: str, event: str, **data: Any) -> None:
    """Append one durable event without storing prompts or business payloads."""
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "workflow_run_id": str(workflow_run_id),
        "event": event,
        **{key: _json_safe(value) for key, value in data.items()},
    }
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    with _WRITE_LOCK:
        with _trace_path().open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")


def read_trace(workflow_run_id: str, limit: int = 500) -> list[dict]:
    path = _trace_path()
    if not path.exists():
        return []
    matched: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if item.get("workflow_run_id") == str(workflow_run_id):
                matched.append(item)
    return matched[-max(1, min(2000, int(limit))):]


class WorkflowTraceHandler(BaseCallbackHandler):
    """Capture node/model/tool timing and token usage via LangChain callbacks."""

    def __init__(self, workflow_run_id: str) -> None:
        self.workflow_run_id = str(workflow_run_id)
        self._started: dict[str, float] = {}

    def _start(self, run_id: Any) -> None:
        self._started[str(run_id)] = time.perf_counter()

    def _duration(self, run_id: Any) -> float | None:
        started = self._started.pop(str(run_id), None)
        return round((time.perf_counter() - started) * 1000, 1) if started else None

    @staticmethod
    def _name(serialized: dict | None, metadata: dict | None = None) -> str:
        metadata = metadata or {}
        if metadata.get("langgraph_node"):
            return str(metadata["langgraph_node"])
        serialized = serialized or {}
        return str(serialized.get("name") or serialized.get("id", ["chain"])[-1])

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None,
                       tags=None, metadata=None, **kwargs):
        self._start(run_id)
        record_event(self.workflow_run_id, "chain_started", callback_run_id=str(run_id),
                     parent_run_id=str(parent_run_id) if parent_run_id else None,
                     component=self._name(serialized, metadata), tags=tags or [])

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        record_event(self.workflow_run_id, "chain_completed", callback_run_id=str(run_id),
                     duration_ms=self._duration(run_id))

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        record_event(self.workflow_run_id, "chain_failed", callback_run_id=str(run_id),
                     duration_ms=self._duration(run_id),
                     error=f"{type(error).__name__}: {error}")

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None,
                     tags=None, metadata=None, **kwargs):
        self._start(run_id)
        record_event(self.workflow_run_id, "llm_started", callback_run_id=str(run_id),
                     component=self._name(serialized, metadata), prompt_count=len(prompts),
                     tags=tags or [])

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None,
                            tags=None, metadata=None, **kwargs):
        self.on_llm_start(serialized, messages, run_id=run_id,
                          parent_run_id=parent_run_id, tags=tags, metadata=metadata)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        usage = (getattr(response, "llm_output", None) or {}).get("token_usage", {})
        record_event(self.workflow_run_id, "llm_completed", callback_run_id=str(run_id),
                     duration_ms=self._duration(run_id), token_usage=usage)

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        record_event(self.workflow_run_id, "llm_failed", callback_run_id=str(run_id),
                     duration_ms=self._duration(run_id),
                     error=f"{type(error).__name__}: {error}")

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None,
                      tags=None, metadata=None, inputs=None, **kwargs):
        self._start(run_id)
        record_event(self.workflow_run_id, "tool_started", callback_run_id=str(run_id),
                     tool=self._name(serialized, metadata), tags=tags or [])

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        record_event(self.workflow_run_id, "tool_completed", callback_run_id=str(run_id),
                     duration_ms=self._duration(run_id))

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        record_event(self.workflow_run_id, "tool_failed", callback_run_id=str(run_id),
                     duration_ms=self._duration(run_id),
                     error=f"{type(error).__name__}: {error}")
