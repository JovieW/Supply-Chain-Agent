"""FastAPI service boundary for the governed supply-chain Agent workflow."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from api_schemas import (
    ApprovalReviewRequest,
    DecisionCreateRequest,
    OutcomeFeedbackRequest,
    PurchaseOrderEventRequest,
    StressTestRequest,
)
from autonomous_tools import stress_test_plan
from backend_interface import (
    advance_purchase_order,
    approval_workbench,
    current_approval_actor,
    load_approval_result,
    load_workflow_result_async,
    memory_workbench,
    procurement_workbench,
    review_decision_async,
    run_one_cycle_async,
    submit_outcome_feedback,
    workflow_trace,
)
from langsmith_config import langsmith_status
from db_service import read_supply_chain_snapshot


app = FastAPI(
    title="Governed Supply Chain Agent API",
    version="1.0.0",
    description=(
        "HTTP boundary for decisions, durable approvals, procurement lifecycle, "
        "outcome memory, and workflow traces."
    ),
)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Make a browser visit land on the interactive API documentation."""
    return RedirectResponse(url="/docs")


@app.exception_handler(ValueError)
async def value_error_handler(request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(PermissionError)
async def permission_error_handler(request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "supply-chain-agent-api",
        "langsmith": langsmith_status(),
    }


@app.post("/api/v1/decisions", tags=["decisions"])
async def create_decision(body: DecisionCreateRequest) -> dict:
    return await run_one_cycle_async(body.product_id)


@app.get("/api/v1/decisions/{workflow_run_id}", tags=["decisions"])
async def get_decision(workflow_run_id: str) -> dict:
    return await load_workflow_result_async(workflow_run_id)


@app.get("/api/v1/approvals", tags=["approvals"])
def list_approvals(status: str | None = Query(default=None)) -> list[dict]:
    return approval_workbench(status=status)


@app.get("/api/v1/approvals/actor", tags=["approvals"])
def approval_actor() -> dict:
    return current_approval_actor()


@app.get("/api/v1/approvals/{approval_id}", tags=["approvals"])
def get_approval(approval_id: int) -> dict:
    return load_approval_result(approval_id)


@app.post("/api/v1/approvals/{approval_id}/review", tags=["approvals"])
async def review_approval(approval_id: int, body: ApprovalReviewRequest) -> dict:
    # load_approval_result has a synchronous compatibility wrapper that may
    # inspect an async checkpoint, so execute it outside FastAPI's event loop.
    result = await run_in_threadpool(load_approval_result, approval_id)
    if body.decision_hash:
        result["approval_decision_hash"] = body.decision_hash
    return await review_decision_async(result, body.approved, body.feedback)


@app.get("/api/v1/purchase-orders", tags=["procurement"])
def list_purchase_orders(product_id: int | None = Query(default=None, ge=1)) -> list[dict]:
    return procurement_workbench(product_id=product_id)


@app.post("/api/v1/purchase-orders/{purchase_order_id}/events", tags=["procurement"])
def transition_purchase_order(
    purchase_order_id: int, body: PurchaseOrderEventRequest
) -> dict:
    return advance_purchase_order(purchase_order_id, body.event_type, body.payload)


@app.get("/api/v1/memories", tags=["memory"])
def list_memories(product_id: int | None = Query(default=None, ge=1)) -> list[dict]:
    return memory_workbench(product_id=product_id)


@app.post("/api/v1/memories/{memory_id}/outcome", tags=["memory"])
def record_outcome(memory_id: int, body: OutcomeFeedbackRequest) -> dict:
    return submit_outcome_feedback(memory_id, body.model_dump())


@app.post("/api/v1/simulations/stress-test", tags=["simulation"])
def run_stress_test(body: StressTestRequest) -> dict:
    snapshot = read_supply_chain_snapshot(body.product_id)
    return stress_test_plan(
        snapshot, body.demand_multiplier, body.extra_delay_days
    )


@app.get("/api/v1/workflows/{workflow_run_id}/trace", tags=["observability"])
def get_workflow_trace(
    workflow_run_id: str, limit: int = Query(default=500, ge=1, le=2000)
) -> list[dict]:
    return workflow_trace(workflow_run_id, limit=limit)
