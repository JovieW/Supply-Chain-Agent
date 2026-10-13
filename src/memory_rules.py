from __future__ import annotations

import math
import os
import re
from datetime import datetime


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def max_recalled_memories() -> int:
    try:
        configured = int(os.getenv("AGENT_MAX_LONG_TERM_MEMORIES", "5"))
    except ValueError:
        configured = 5
    return max(1, min(10, configured))


def feedback_users() -> set[str]:
    configured = {
        email.strip().lower()
        for email in os.getenv("MEMORY_FEEDBACK_USERS", "").split(",")
        if email.strip()
    }
    if configured:
        return configured
    # Local demo fallback: authorized HITL identities may also close the outcome loop.
    users = set()
    for binding in os.getenv("HITL_APPROVER_BINDINGS", "").split(","):
        email = binding.split(":", 1)[0].strip().lower()
        if EMAIL_PATTERN.match(email):
            users.add(email)
    return users


def validate_outcome_feedback(payload: dict, planned_quantity: int) -> dict:
    submitter = str(payload.get("submitted_by", "")).strip().lower()
    notes = str(payload.get("notes", "")).strip()
    if not EMAIL_PATTERN.match(submitter):
        raise ValueError("必须提供有效的反馈人邮箱")
    if submitter not in feedback_users():
        raise PermissionError("该邮箱不在 MEMORY_FEEDBACK_USERS 结果反馈白名单中")
    if len(notes) < 5:
        raise ValueError("结果说明至少需要 5 个字符")

    def integer(name: str, minimum: int = 0, maximum: int = 10_000_000) -> int:
        value = payload.get(name, 0)
        if isinstance(value, bool):
            raise ValueError(f"{name} 必须是整数")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须是整数") from None
        if parsed < minimum or parsed > maximum:
            raise ValueError(f"{name} 必须位于 {minimum} 到 {maximum} 之间")
        return parsed

    def number(name: str, minimum: float = 0.0, maximum: float = 1e12) -> float:
        try:
            parsed = float(payload.get(name, 0))
        except (TypeError, ValueError):
            raise ValueError(f"{name} 必须是数字") from None
        if not math.isfinite(parsed) or parsed < minimum or parsed > maximum:
            raise ValueError(f"{name} 必须位于 {minimum} 到 {maximum} 之间")
        return parsed

    normalized = {
        "actual_lead_time_days": integer("actual_lead_time_days", 0, 3650),
        "received_quantity": integer("received_quantity"),
        "stockout_units": integer("stockout_units"),
        "quality_reject_units": integer("quality_reject_units"),
        "actual_total_cost": number("actual_total_cost"),
        "service_level": number("service_level", 0.0, 1.0),
        "submitted_by": submitter,
        "notes": notes,
    }
    if normalized["quality_reject_units"] > normalized["received_quantity"]:
        raise ValueError("质量拒收数量不能超过实际收货数量")
    if planned_quantity == 0 and normalized["received_quantity"] > 0:
        raise ValueError("HOLD 决策不能填写本决策产生的收货数量")
    return normalized


def evaluate_outcome(memory: dict, feedback: dict) -> dict:
    """Create an auditable 0-100 outcome score from operational facts."""
    planned_quantity = max(0, int(memory.get("planned_quantity") or 0))
    planned_lead = max(0, int(memory.get("planned_lead_time_days") or 0))
    planned_cost = max(0.0, float(memory.get("planned_total_cost") or 0))
    received = feedback["received_quantity"]
    stockout = feedback["stockout_units"]
    rejects = feedback["quality_reject_units"]
    actual_lead = feedback["actual_lead_time_days"]
    actual_cost = feedback["actual_total_cost"]
    service_level = feedback["service_level"]
    target_service_level = float(
        (memory.get("context") or {}).get("target_service_level") or 0.95
    )

    score = 100.0
    penalties: list[tuple[str, float]] = []

    if planned_quantity > 0:
        fill_rate = min(1.0, received / planned_quantity)
        penalties.append(("未足量交付", (1.0 - fill_rate) * 20.0))
        reject_rate = rejects / max(1, received)
        penalties.append(("质量拒收", min(20.0, reject_rate * 100.0)))
    elif stockout > 0:
        penalties.append(("暂不补货后发生缺货", min(40.0, 10.0 + stockout * 0.5)))

    delay_days = max(0, actual_lead - planned_lead)
    penalties.append(("交付延期", min(20.0, delay_days * 3.0)))
    penalties.append(("缺货损失", min(35.0, stockout * 0.5)))
    penalties.append((
        "服务水平未达标",
        max(0.0, target_service_level - service_level) * 100.0,
    ))
    if planned_cost > 0 and actual_cost > planned_cost:
        cost_overrun = (actual_cost - planned_cost) / planned_cost
        penalties.append(("成本超支", min(15.0, cost_overrun * 50.0)))

    applied = [(name, round(value, 1)) for name, value in penalties if value > 0]
    score -= sum(value for _, value in applied)
    score = round(max(0.0, min(100.0, score)), 1)
    label = "SUCCESS" if score >= 80 else "MIXED" if score >= 60 else "FAILURE"

    observations = []
    if delay_days:
        observations.append(f"实际交期比计划晚 {delay_days} 天")
    if stockout:
        observations.append(f"发生 {stockout} 件缺货")
    if rejects:
        observations.append(f"质量拒收 {rejects} 件")
    if planned_cost and actual_cost > planned_cost:
        observations.append(f"实际成本高于计划 {actual_cost - planned_cost:.2f}")
    if not observations:
        observations.append("交付、质量、成本和服务结果总体符合计划")
    lesson = "；".join(observations) + f"；结果评分 {score}，后续同类决策应据此校准风险关注点。"
    return {
        "outcome_score": score,
        "outcome_label": label,
        "penalties": applied,
        "lesson": lesson,
    }


def rank_memories(current_context: dict, memories: list[dict], limit: int | None = None) -> list[dict]:
    """Rank verified episodes by scenario similarity, outcome value and recency."""
    desired = limit or max_recalled_memories()
    now = datetime.utcnow()
    ranked = []
    for memory in memories:
        outcome = memory.get("outcome") or {}
        if not outcome.get("outcome_label"):
            continue
        context = memory.get("context") or {}
        score = 0.40 if memory.get("product_id") == current_context.get("product_id") else 0.0
        if memory.get("decision_type") == current_context.get("decision_type"):
            score += 0.15
        if memory.get("supplier_id") == current_context.get("supplier_id"):
            score += 0.10
        if context.get("demand_volatility") == current_context.get("demand_volatility"):
            score += 0.15
        created = memory.get("created_at")
        try:
            age_days = max(0.0, (now - datetime.fromisoformat(created)).total_seconds() / 86400)
        except (TypeError, ValueError):
            age_days = 365.0
        score += 0.10 * math.exp(-age_days / 180.0)
        # Both successes and failures are useful; completed, high-confidence outcomes rank higher.
        score += 0.10 * abs(float(outcome.get("outcome_score", 50)) - 50.0) / 50.0
        ranked.append({**memory, "similarity_score": round(min(1.0, score), 3)})
    return sorted(ranked, key=lambda row: row["similarity_score"], reverse=True)[:desired]


def format_memory_context(memory_state: dict) -> str:
    memories = memory_state.get("recalled", []) if memory_state else []
    if not memories:
        return "暂无经过结果验证的相似历史决策。"
    lines = []
    for item in memories:
        outcome = item.get("outcome") or {}
        lines.append(
            f"[记忆#{item.get('id')}] 动作={item.get('decision_type')}，"
            f"供应商={item.get('supplier_id')}，数量={item.get('planned_quantity')}，"
            f"结果={outcome.get('outcome_label')}({outcome.get('outcome_score')})，"
            f"经验={outcome.get('lesson')}"
        )
    return "\n".join(lines)
