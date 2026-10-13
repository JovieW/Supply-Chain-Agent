"""Pure presentation models for the business-facing supply-chain cockpit.

These helpers deliberately consume the public workflow result only.  They do
not change decisions, approvals, inventory, or any Agent state.
"""
from __future__ import annotations

from typing import Any


STATUS_META = {
    "completed": ("已完成", "✅"),
    "active": ("处理中", "🔄"),
    "waiting": ("等待人工", "⏸️"),
    "warning": ("需关注", "⚠️"),
    "blocked": ("已阻断", "⛔"),
    "not_started": ("未执行", "○"),
}


def _stage(
    stage_id: str, title: str, status: str, detail: str, *, evidence_count: int = 0
) -> dict[str, Any]:
    label, icon = STATUS_META[status]
    return {
        "id": stage_id,
        "title": title,
        "status": status,
        "status_label": label,
        "icon": icon,
        "detail": detail,
        "evidence_count": evidence_count,
    }


def build_workflow_trace(result: dict) -> list[dict[str, Any]]:
    """Create an auditable post-run timeline; it never claims live streaming."""
    outputs = result.get("agent_outputs") or {}
    knowledge = result.get("retrieved_knowledge") or {}
    memory = result.get("long_term_memory") or {}
    decision = result.get("final_decision") or {}
    assessment = result.get("decision_assessment") or {}
    snapshot = result.get("db_snapshot") or {}
    reflection = outputs.get("reflection") or {}
    investigation = outputs.get("tool_investigator") or {}
    execution = outputs.get("execution") or {}
    product = (snapshot.get("product") or {}).get("name", "当前产品")
    retrieved_count = len(knowledge.get("all") or [])
    tool_calls = int(investigation.get("tool_call_count") or 0)
    recalled_count = len(memory.get("recalled") or [])
    agent_keys = {"demand", "inventory", "risk", "logistics", "coordinator"}
    completed_agents = len(agent_keys & set(outputs))

    trace = [
        _stage(
            "snapshot", "实时业务快照", "completed" if snapshot else "not_started",
            f"已读取 {product} 的库存、在途订单和合格供应商数据。",
        ),
        _stage(
            "knowledge", "企业知识检索",
            "completed" if knowledge.get("index_stats") else "warning",
            f"本轮检索到 {retrieved_count} 条 SOP、合同或历史事件证据。",
            evidence_count=retrieved_count,
        ),
        _stage(
            "tools", "自主事实核查",
            "warning" if investigation.get("status") in {"degraded", "completed_with_errors", "limit_reached"}
            else "completed" if investigation else "not_started",
            f"已执行 {tool_calls} 次只读工具调用；状态：{investigation.get('status', '未运行')}。",
            evidence_count=tool_calls,
        ),
        _stage(
            "memory", "长期记忆召回",
            "warning" if memory.get("status") == "DEGRADED" else "completed" if memory else "not_started",
            f"召回 {recalled_count} 条已验证的相似历史决策。",
            evidence_count=recalled_count,
        ),
        _stage(
            "agents", "多 Agent 协同分析",
            "completed" if completed_agents == len(agent_keys) else "warning",
            f"需求、库存、风险、物流与协调 Agent 已完成 {completed_agents}/{len(agent_keys)} 个输出。",
        ),
        _stage(
            "reflection", "自我反思与修订",
            "warning" if reflection.get("outcome") == "ESCALATE"
            else "completed" if reflection.get("outcome") == "PASS" else "not_started",
            f"反思结果：{reflection.get('outcome', '未运行')}；共 {reflection.get('round', 0)} 轮。",
        ),
    ]
    approval_required = bool(assessment.get("approval_required"))
    feedback = result.get("human_feedback")
    if approval_required and feedback == "PENDING":
        trace.append(_stage(
            "approval", "人工审批", "waiting",
            f"风险门要求 {result.get('approval_required_role', '指定审批人')} 审批；采购尚未执行。",
        ))
        trace.append(_stage("execution", "采购执行", "blocked", "等待审批完成后才可执行。"))
    elif approval_required and feedback in {"REJECTED", "STALE", "EXPIRED"}:
        trace.append(_stage("approval", "人工审批", "blocked", f"审批状态：{feedback}。"))
        trace.append(_stage("execution", "采购执行", "blocked", "方案未获得有效审批，执行被阻断。"))
    else:
        trace.append(_stage(
            "approval", "风险门与审批",
            "completed",
            "低风险自动放行。" if not approval_required else "审批已完成并允许恢复流程。",
        ))
        trace.append(_stage(
            "execution", "采购执行",
            "completed" if execution.get("executed") else "warning" if execution else "not_started",
            execution.get("message", "尚未返回执行结果。"),
        ))
    if decision:
        return trace
    return [item for item in trace if item["id"] in {"snapshot", "knowledge", "tools", "memory", "agents"}]


def build_action_center(result: dict) -> dict[str, Any]:
    """Summarize the one business action a planner should take next."""
    decision = result.get("final_decision") or {}
    details = decision.get("details") or {}
    outputs = result.get("agent_outputs") or {}
    logistics = outputs.get("logistics") or {}
    assessment = result.get("decision_assessment") or {}
    feedback = result.get("human_feedback")
    action = decision.get("decision_type", "HOLD")
    shortage = float(logistics.get("projected_shortage", 0) or 0)
    risk = result.get("decision_risk", assessment.get("level", "UNKNOWN"))
    if feedback == "PENDING":
        next_step = "等待审批：高风险方案已暂停，不能创建采购单。"
        state = "waiting"
    elif feedback in {"REJECTED", "STALE", "EXPIRED"}:
        next_step = "重新规划：当前方案未获得有效批准。"
        state = "blocked"
    elif outputs.get("execution", {}).get("executed"):
        next_step = "跟踪执行：在到货后录入实际交期、数量、成本和服务水平。"
        state = "completed"
    elif action == "REORDER":
        next_step = "等待流程执行结果；请核对供应商、数量和运输方式。"
        state = "active"
    else:
        next_step = "持续监控：当前无需新建采购单，关注需求或在途变化。"
        state = "completed"
    return {
        "action": action,
        "supplier_id": details.get("supplier_id"),
        "quantity": details.get("quantity", 0),
        "expedite": bool(details.get("expedite")),
        "risk": risk,
        "projected_shortage": shortage,
        "state": state,
        "next_step": next_step,
    }


def build_feedback_diagnosis(memories: list[dict], orders: list[dict]) -> dict[str, Any]:
    """Explain why a plan can or cannot enter the final-outcome feedback queue.

    A closed purchase order is necessary but deliberately insufficient: only a
    purchase order created by a successfully executed governed decision has a
    DecisionMemory link and can become an Agent memory.
    """
    closed_orders = [row for row in orders if row.get("status") == "CLOSED"]
    linked_po_ids = {
        row.get("purchase_order_id") for row in memories
        if row.get("purchase_order_id") is not None
    }
    eligible = [
        row for row in memories
        if row.get("status") in {"EXECUTED", "EXECUTION_FAILED"}
        and not row.get("outcome")
        and (
            row.get("decision_type") == "HOLD"
            or row.get("purchase_order_status") == "CLOSED"
        )
    ]
    if eligible:
        return {
            "state": "ready",
            "message": "已有已执行计划满足结果反馈条件，请选择计划并录入实际业务结果。",
            "eligible_count": len(eligible),
            "closed_order_count": len(closed_orders),
        }
    if not memories:
        return {
            "state": "missing_decision_memory",
            "message": (
                "当前产品没有由本系统成功执行并持久化的决策记录。"
                "仅录入采购履约不会创建 Agent 决策记忆；请先成功生成、执行（必要时审批）一份新方案。"
            ),
            "eligible_count": 0,
            "closed_order_count": len(closed_orders),
        }
    unlinked_closed = [row for row in closed_orders if row.get("id") not in linked_po_ids]
    if unlinked_closed:
        return {
            "state": "unlinked_closed_order",
            "message": (
                f"发现 {len(unlinked_closed)} 个已关闭采购单，但它们未关联到系统决策记录，"
                "可能是历史/手工采购单或工作流在执行前失败。为避免污染长期记忆，不能直接录入为 Agent 决策结果。"
            ),
            "eligible_count": 0,
            "closed_order_count": len(closed_orders),
        }
    if closed_orders:
        return {
            "state": "closed_not_executable",
            "message": (
                "关联采购单已关闭，但对应决策尚未处于已执行状态。"
                "请在决策档案或运行记录检查审批恢复和采购执行是否成功。"
            ),
            "eligible_count": 0,
            "closed_order_count": len(closed_orders),
        }
    return {
        "state": "lifecycle_incomplete",
        "message": "当前没有已关闭的关联采购单；请先完成供应商确认、发运、到货、质检和关闭采购单。",
        "eligible_count": 0,
        "closed_order_count": 0,
    }


def decision_evidence(result: dict) -> list[dict[str, Any]]:
    """Resolve final-decision citations to the concrete retrieved chunks."""
    cited = list((result.get("final_decision") or {}).get("evidence") or [])
    chunks = (result.get("retrieved_knowledge") or {}).get("all") or []
    by_id = {item.get("display_id", item.get("id")): item for item in chunks}
    return [
        {
            "source_id": source_id,
            "found": source_id in by_id,
            "title": by_id.get(source_id, {}).get("title", "未找到对应检索证据"),
            "document_type": by_id.get(source_id, {}).get("document_type", "unknown"),
            "score": by_id.get(source_id, {}).get("score"),
            "content": by_id.get(source_id, {}).get("content", ""),
            "document_id": by_id.get(source_id, {}).get("document_id"),
            "version": by_id.get(source_id, {}).get("version"),
            "effective_from": by_id.get(source_id, {}).get("effective_from"),
            "effective_to": by_id.get(source_id, {}).get("effective_to"),
        }
        for source_id in cited
    ]
