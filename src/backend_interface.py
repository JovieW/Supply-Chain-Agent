"""
Backend interface for UI.
Single entry point that triggers LangGraph workflow.
UI calls this, never touches agents directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from graph import create_supply_chain_graph
from auth_context import get_current_actor
from boundary_schemas import ApprovalResumeInput, SCHEMA_VERSION, WorkflowStartInput
from observability import WorkflowTraceHandler, read_trace, record_event
from langsmith_config import LangSmithSettings
from procurement_adapter import get_procurement_adapter
from db_service import (
    get_approval_request,
    list_approval_requests,
    list_decision_memories,
    record_outcome_feedback,
)


# #region agent log
DEBUG_LOG_PATH = Path(__file__).resolve().parents[1] / ".cursor" / "debug.log"


def _agent_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict | None = None,
    run_id: str = "initial",
) -> None:
    """Lightweight NDJSON logger for debug-mode instrumentation (backend)."""
    payload = {
        "sessionId": "debug-session",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    try:
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        # Logging failures must never affect business logic
        pass


# #endregion


def _checkpoint_path() -> str:
    configured = os.getenv("HITL_CHECKPOINT_DB", "data/workflow_checkpoints.sqlite")
    path = Path(configured)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[1] / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)

# 负责启动或者恢复一个可持久化的agent工作流线程，并执行整个供应链决策图
async def _invoke_durable_graph(graph_input, thread_id: str) -> dict:
    """Invoke or resume one graph thread using a process-independent checkpoint."""
    langsmith = LangSmithSettings.from_env()
    langsmith.apply_canonical_environment()
    is_resume = isinstance(graph_input, Command)  # 判断输入类型，如果graph_input是Command，说明不是新决策，而是回复暂停流程
    product_id = graph_input.get("product_id") if isinstance(graph_input, dict) else None
    async with AsyncSqliteSaver.from_conn_string(_checkpoint_path()) as checkpointer:
        await checkpointer.setup()
        app = create_supply_chain_graph(checkpointer=checkpointer)  # 创建langgraph
        return await app.ainvoke(  # 执行graph
            graph_input,
            config={
                "configurable": {"thread_id": thread_id},
                "callbacks": [WorkflowTraceHandler(thread_id)],
                "run_name": "supply_chain_approval_resume" if is_resume else "supply_chain_decision",
                "tags": [
                    "supply_chain_workflow",
                    "governed_agent",
                    "approval_resume" if is_resume else "new_decision",
                    f"env:{langsmith.environment}",
                    f"version:{langsmith.app_version}",
                ],
                "metadata": langsmith.trace_metadata(
                    workflow_run_id=thread_id,
                    product_id=int(product_id) if product_id is not None else None,
                ),
            },
        )


async def _read_durable_graph_state(thread_id: str) -> dict:
    """Read a completed or interrupted graph state without invoking any node.

    Approval records intentionally retain the pre-approval snapshot for audit.
    The checkpoint is the authoritative source for the latest state after a
    successful `Command(resume=...)`, including the execution result.
    """
    async with AsyncSqliteSaver.from_conn_string(_checkpoint_path()) as checkpointer:
        await checkpointer.setup()
        app = create_supply_chain_graph(checkpointer=checkpointer)
        snapshot = await app.aget_state(
            {"configurable": {"thread_id": thread_id}}
        )
        values = getattr(snapshot, "values", None)
        return dict(values or {})


async def load_workflow_result_async(workflow_run_id: str) -> dict:
    """Load the latest durable public state for an API status request."""
    state = await _read_durable_graph_state(str(workflow_run_id))
    if not state:
        raise ValueError(f"Workflow not found: {workflow_run_id}")
    product_id = state.get("product_id")
    if product_id is None:
        raise ValueError(f"Workflow has no product_id: {workflow_run_id}")
    return _public_result(
        state,
        product_id=int(product_id),
        workflow_run_id=str(workflow_run_id),
    )


def _interrupt_value(state: dict) -> dict | None:
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return None
    first = interrupts[0]
    value = getattr(first, "value", None)
    return value if isinstance(value, dict) else None


def _public_result(state: dict, *, product_id: int, workflow_run_id: str) -> dict:
    interrupt_value = _interrupt_value(state)
    pending = interrupt_value is not None
    return {
        "schema_version": state.get("schema_version", SCHEMA_VERSION),
        "product_id": product_id,
        "workflow_run_id": workflow_run_id,
        "db_snapshot": state.get("db_snapshot", {}),
        "retrieved_knowledge": state.get("retrieved_knowledge", {}),
        "long_term_memory": state.get("long_term_memory", {}),
        "decision_memory_id": state.get("decision_memory_id"),
        "agent_outputs": state.get("agent_outputs", {}),
        "final_decision": state.get("final_decision"),
        "decision_risk": state.get("decision_risk"),
        "decision_assessment": state.get("decision_assessment", {}),
        "human_feedback": "PENDING" if pending else state.get("human_feedback"),
        "human_review": state.get("human_review"),
        "approval_request_id": (
            interrupt_value.get("approval_id") if pending
            else state.get("approval_request_id")
        ),
        "approval_decision_hash": (
            interrupt_value.get("decision_hash") if pending else None
        ),
        "approval_required_role": (
            interrupt_value.get("required_role") if pending else None
        ),
        "approval_expires_at": (
            interrupt_value.get("expires_at") if pending else None
        ),
        "workflow_interrupted": pending,
        "reflection_round": state.get("reflection_round", 0),
        "reflection_history": state.get("reflection_history", []),
        "reflection_feedback": state.get("reflection_feedback"),
    }


async def run_one_cycle_async(product_id: int = 1) -> dict:
    """
    Execute one complete decision cycle for a product.

    Args:
        product_id: Product to analyze (1, 2, or 3)

    Returns:
        Structured output with all agent decisions and reasoning
    """
    # #region agent log开始调试日志
    _agent_debug_log(
        hypothesis_id="H3",
        location="src/backend_interface.py:run_one_cycle:start",
        message="Entered run_one_cycle",
        data={"product_id": int(product_id)},
    )
    # #endregion
    # Initial state 构造初始State
    workflow_run_id = str(uuid.uuid4())  # 生成工作流id
    start = WorkflowStartInput(  # 创建工作流输入对象
        product_id=product_id,
        workflow_run_id=workflow_run_id,
    )
    record_event(  # 记录工作流开始事件
        workflow_run_id,
        "workflow_started",
        product_id=int(product_id),
        mode="new_decision",
    )
    initial_state = {
        "schema_version": start.schema_version,
        "product_id": start.product_id,
        "workflow_run_id": workflow_run_id,
        "db_snapshot": {},  # 用于保存数据库快照
        "retrieved_knowledge": {},  # 用于保存RAG检索到的政策、案例和规则
        "long_term_memory": {},
        "decision_memory_id": None,
        "agent_outputs": {},  # 用于存放各agent的分析结果
        "final_decision": None,  # 用于存放最终的综合决策
        "decision_risk": None,  # 保存最终决策的风险等级
        "decision_assessment": None,
        "human_feedback": None,  # 保存人工审批结果
        "human_review": None,
        "approval_request_id": None,
        "reflection_round": 0,
        "reflection_history": [],
        "reflection_feedback": None,
    }

    try:
        result = await _invoke_durable_graph(initial_state, workflow_run_id)  # 执行
    except Exception as exc:  #捕获异常并记录失败事件
        record_event(
            workflow_run_id,
            "workflow_failed",
            product_id=int(product_id),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    record_event(  # 记录事件日志
        workflow_run_id,
        "workflow_paused" if _interrupt_value(result) else "workflow_completed",
        product_id=int(product_id),
        approval_request_id=(
            (_interrupt_value(result) or {}).get("approval_id")
            if _interrupt_value(result) else result.get("approval_request_id")
        ),
    )

    # #region agent log  结束调试日志
    _agent_debug_log(
        hypothesis_id="H4",
        location="src/backend_interface.py:run_one_cycle:end",
        message="Completed run_one_cycle",
        data={
            "has_final_decision": result.get("final_decision") is not None,
            "keys": list(result.keys()),
        },
    )
    # #endregion

    return _public_result(
        result, product_id=product_id, workflow_run_id=workflow_run_id
    )


def run_one_cycle(product_id: int = 1) -> dict:
    """Synchronous adapter retained for Streamlit direct mode and local scripts."""
    return asyncio.run(run_one_cycle_async(product_id))


async def review_decision_async(
    result: dict,
    approved: bool,
    feedback: str = "",
) -> dict:
    """Resume with a server-owned actor; browser fields never establish identity."""
    approval_id = result.get("approval_request_id")
    if not approval_id:
        raise ValueError("This decision has no pending approval request")
    approval = get_approval_request(int(approval_id))
    workflow_run_id = approval.get("checkpoint_thread_id")
    if not approval.get("resumable") or not workflow_run_id:
        raise ValueError(
            "该审批单创建于 P4 持久化升级之前，没有可恢复的 LangGraph checkpoint；"
            "请重新运行该产品的决策流程生成新审批单"
        )
    submitted_hash = result.get("approval_decision_hash") or approval.get("decision_hash")
    actor = get_current_actor()
    resume_payload = ApprovalResumeInput.model_validate({
        "action": "approve" if approved else "reject",
        "reviewer_email": actor.email,
        "reviewer_role": actor.role,
        "feedback": feedback,
        "decision_hash": submitted_hash,
    }).model_dump()
    record_event(
        workflow_run_id,
        "approval_resume_requested",
        approval_request_id=int(approval_id),
        action=resume_payload["action"],
        actor_email=actor.email,
        actor_role=actor.role,
        actor_source=actor.source,
    )
    try:
        state = await _invoke_durable_graph(Command(resume=resume_payload), workflow_run_id)
    except Exception as exc:
        record_event(
            workflow_run_id,
            "approval_resume_failed",
            approval_request_id=int(approval_id),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    execution = (state.get("agent_outputs") or {}).get("execution") or {}
    record_event(
        workflow_run_id,
        "approval_resume_completed",
        approval_request_id=int(approval_id),
        approval_status=state.get("human_feedback"),
        executed=bool(execution.get("executed")),
        purchase_order_id=execution.get("po_id"),
    )
    return _public_result(
        state,
        product_id=int(approval["product_id"]),
        workflow_run_id=workflow_run_id,
    )


def review_decision(
    result: dict,
    approved: bool,
    feedback: str = "",
) -> dict:
    """Synchronous adapter retained for Streamlit direct mode and local scripts."""
    return asyncio.run(review_decision_async(result, approved, feedback))


def approval_workbench(status: str | None = None) -> list[dict]:
    """List persistent work items even after browser or process restarts."""
    return list_approval_requests(status=status)


def current_approval_actor() -> dict:
    """Expose the server-resolved demo principal for read-only UI display."""
    return get_current_actor().to_dict()


def workflow_trace(workflow_run_id: str, limit: int = 500) -> list[dict]:
    """Read the persistent operational trace for one decision run."""
    return read_trace(str(workflow_run_id), limit=limit)


def procurement_workbench(product_id: int | None = None) -> list[dict]:
    return get_procurement_adapter().list_orders(product_id=product_id)


def advance_purchase_order(
    purchase_order_id: int, event_type: str, payload: dict | None = None
) -> dict:
    actor = get_current_actor()
    canonical = json.dumps(payload or {}, sort_keys=True, ensure_ascii=False, default=str)
    key = hashlib.sha256(
        f"{purchase_order_id}:{event_type}:{canonical}".encode("utf-8")
    ).hexdigest()
    result = get_procurement_adapter().transition_order(
        purchase_order_id=int(purchase_order_id),
        event_type=event_type,
        actor_email=actor.email,
        idempotency_key=key,
        payload=payload or {},
    )
    record_event(
        f"procurement-po-{purchase_order_id}",
        "procurement_lifecycle_transition",
        purchase_order_id=purchase_order_id,
        event_type=event_type,
        status=result.get("status"),
        actor_email=actor.email,
    )
    return result


def load_approval_result(approval_id: int) -> dict:
    """Rehydrate an approval card, preferring its latest durable graph state."""
    approval = get_approval_request(int(approval_id))  # 获取审批记录
    approval_data = approval.get("decision_data") or {}
    state = dict(approval_data.get("state_snapshot") or {})
    product_id = approval.get("product_id") or state.get("product_id")
    if product_id is None:
        raise ValueError(
            "审批单缺少 product_id，属于升级前的不完整历史数据；"
            "请重新运行决策流程生成新审批单"
        )
    workflow_run_id = (
        approval.get("checkpoint_thread_id")
        or approval.get("workflow_run_id")
        or state.get("workflow_run_id")
        or f"legacy-approval-{approval['id']}"
    )
    state_source = "approval_snapshot"
    if approval.get("resumable") and approval.get("checkpoint_thread_id"):
        try:
            checkpoint_state = asyncio.run(
                _read_durable_graph_state(str(approval["checkpoint_thread_id"]))
            )
            if checkpoint_state:
                state = checkpoint_state
                state_source = "durable_checkpoint"
        except Exception:
            # A completed approval remains visible even if the optional
            # checkpoint read is temporarily unavailable; do not hide it.
            state_source = "approval_snapshot_fallback"
    if not state:
        state = {
            "product_id": product_id,
            "workflow_run_id": workflow_run_id,
            "db_snapshot": approval_data.get("db_snapshot", {}),
            "retrieved_knowledge": {},
            "long_term_memory": {},
            "decision_memory_id": None,
            "agent_outputs": {},
            "final_decision": approval_data.get("decision"),
            "decision_risk": approval.get("risk_level"),
            "decision_assessment": approval_data.get("assessment", {}),
            "reflection_round": 0,
            "reflection_history": [],
            "reflection_feedback": None,
        }
    state["human_feedback"] = approval["status"]
    state["approval_request_id"] = approval["id"]
    state["human_review"] = {
        "status": approval["status"],
        "reviewer_email": approval.get("reviewer_email"),
        "reviewer_role": approval.get("reviewer_role"),
        "feedback": approval.get("feedback"),
    }
    result = _public_result(
        state,
        product_id=int(product_id),
        workflow_run_id=workflow_run_id,
    )
    result.update({
        "human_feedback": approval["status"],
        "approval_request_id": approval["id"],
        "approval_decision_hash": approval["decision_hash"],
        "approval_required_role": approval["required_role"],
        "approval_expires_at": approval["expires_at"],
        "workflow_interrupted": (
            approval["status"] == "PENDING" and approval.get("resumable", False)
        ),
        "approval_resumable": approval.get("resumable", False),
        "workflow_state_source": state_source,
    })
    return result


def memory_workbench(product_id: int | None = None) -> list[dict]:
    """Return durable decision episodes and their operational outcomes."""
    return list_decision_memories(product_id=product_id)


def submit_outcome_feedback(memory_id: int, feedback: dict) -> dict:
    """Record immutable plan-versus-actual feedback for future Agent recall."""
    return record_outcome_feedback(int(memory_id), feedback)
