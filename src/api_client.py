"""Small synchronous HTTP client exposing the UI's existing backend contract."""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def _base_url() -> str:
    return os.getenv("BACKEND_API_URL", "http://127.0.0.1:8000").rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    query: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    url = f"{_base_url()}{path}"
    clean_query = {
        key: value for key, value in (query or {}).items() if value is not None
    }
    if clean_query:
        url = f"{url}?{urlencode(clean_query)}"
    data = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    effective_timeout = timeout or float(
        os.getenv("BACKEND_API_TIMEOUT_SECONDS", "180")
    )
    try:
        with urlopen(request, timeout=effective_timeout) as response:
            content = response.read().decode("utf-8")
            return json.loads(content) if content else None
    except HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(content).get("detail", content)
        except json.JSONDecodeError:
            detail = content
        raise RuntimeError(f"API {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(
            f"无法连接后端 API {_base_url()}；请先启动 python src/api_server.py。"
            f"原因：{exc.reason}"
        ) from exc


def run_one_cycle(product_id: int = 1) -> dict:
    return _request("POST", "/api/v1/decisions", payload={"product_id": product_id})


def review_decision(result: dict, approved: bool, feedback: str = "") -> dict:
    approval_id = result.get("approval_request_id")
    if not approval_id:
        raise ValueError("This decision has no pending approval request")
    return _request(
        "POST",
        f"/api/v1/approvals/{int(approval_id)}/review",
        payload={
            "approved": bool(approved),
            "feedback": feedback,
            "decision_hash": result.get("approval_decision_hash"),
        },
    )


def approval_workbench(status: str | None = None) -> list[dict]:
    return _request("GET", "/api/v1/approvals", query={"status": status})


def current_approval_actor() -> dict:
    return _request("GET", "/api/v1/approvals/actor")


def load_approval_result(approval_id: int) -> dict:
    return _request("GET", f"/api/v1/approvals/{int(approval_id)}")


def workflow_trace(workflow_run_id: str, limit: int = 500) -> list[dict]:
    return _request(
        "GET", f"/api/v1/workflows/{workflow_run_id}/trace", query={"limit": limit}
    )


def procurement_workbench(product_id: int | None = None) -> list[dict]:
    return _request(
        "GET", "/api/v1/purchase-orders", query={"product_id": product_id}
    )


def advance_purchase_order(
    purchase_order_id: int, event_type: str, payload: dict | None = None
) -> dict:
    return _request(
        "POST",
        f"/api/v1/purchase-orders/{int(purchase_order_id)}/events",
        payload={"event_type": event_type, "payload": payload or {}},
    )


def memory_workbench(product_id: int | None = None) -> list[dict]:
    return _request("GET", "/api/v1/memories", query={"product_id": product_id})


def submit_outcome_feedback(memory_id: int, feedback: dict) -> dict:
    return _request(
        "POST", f"/api/v1/memories/{int(memory_id)}/outcome", payload=feedback
    )


def stress_test_plan(
    snapshot: dict, demand_multiplier: float, extra_delay_days: int
) -> dict:
    product_id = (snapshot.get("product") or {}).get("id")
    if product_id is None:
        raise ValueError("Snapshot product id is required for API stress testing")
    return _request(
        "POST",
        "/api/v1/simulations/stress-test",
        payload={
            "product_id": int(product_id),
            "demand_multiplier": demand_multiplier,
            "extra_delay_days": extra_delay_days,
        },
    )
