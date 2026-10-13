from __future__ import annotations

import os


DEFAULT_MAX_REFLECTION_ROUNDS = 2
HARD_MAX_REFLECTION_ROUNDS = 3
REQUIRED_BUSINESS_CASE_FIELDS = {
    "inventory_position", "days_of_supply", "reorder_point", "safety_stock",
    "target_stock", "lead_time_days", "projected_shortage", "purchase_cost",
    "freight_cost", "risk_cost", "total_landed_risk_cost", "constraints",
}


def max_reflection_rounds() -> int:
    try:
        configured = int(os.getenv(
            "AGENT_MAX_REFLECTION_ROUNDS", str(DEFAULT_MAX_REFLECTION_ROUNDS)
        ))
    except ValueError:
        configured = DEFAULT_MAX_REFLECTION_ROUNDS
    return max(1, min(HARD_MAX_REFLECTION_ROUNDS, configured))


def deterministic_reflection_checks(state: dict) -> list[dict]:
    """Verify that the proposed decision still matches authoritative calculations."""
    issues: list[dict] = []

    def add(code: str, severity: str, message: str) -> None:
        issues.append({"code": code, "severity": severity, "message": message})

    decision = state.get("final_decision") or {}
    outputs = state.get("agent_outputs", {})
    inventory = outputs.get("inventory", {})
    logistics = outputs.get("logistics", {})
    snapshot = state.get("db_snapshot", {})
    details = decision.get("details") or {}
    decision_type = decision.get("decision_type")
    if decision_type not in {"REORDER", "HOLD"}:
        add("INVALID_DECISION_TYPE", "CRITICAL", "最终决策类型缺失或不受支持")
        return issues
    if decision_type != inventory.get("action"):
        add("ACTION_MISMATCH", "CRITICAL", "最终动作与库存模型不一致")

    if decision_type == "HOLD":
        if details.get("supplier_id") is not None or details.get("quantity", 0) != 0 or details.get("expedite"):
            add("UNSAFE_HOLD_FIELDS", "CRITICAL", "暂不补货决策包含采购副作用字段")
    else:
        approved_ids = {supplier.get("id") for supplier in snapshot.get("suppliers", [])}
        if details.get("supplier_id") not in approved_ids:
            add("UNAPPROVED_SUPPLIER", "CRITICAL", "最终供应商不在合格供应商清单中")
        if details.get("supplier_id") != inventory.get("supplier_id"):
            add("SUPPLIER_MISMATCH", "CRITICAL", "最终供应商与确定性寻源结果不一致")
        if details.get("quantity") != inventory.get("quantity"):
            add("QUANTITY_MISMATCH", "CRITICAL", "最终采购数量与约束补货模型不一致")
        if bool(details.get("expedite")) != bool(logistics.get("expedite")):
            add("EXPEDITE_MISMATCH", "CRITICAL", "最终运输模式与物流成本模型不一致")

    business_case = decision.get("business_case") or {}
    missing_fields = sorted(REQUIRED_BUSINESS_CASE_FIELDS - set(business_case))
    if missing_fields:
        add("INCOMPLETE_BUSINESS_CASE", "CRITICAL", f"业务案例缺少字段：{', '.join(missing_fields)}")
    for field in REQUIRED_BUSINESS_CASE_FIELDS - {"constraints"}:
        value = business_case.get(field)
        if value is not None and (not isinstance(value, (int, float)) or value < 0):
            add("INVALID_BUSINESS_METRIC", "CRITICAL", f"业务指标 {field} 不是非负数")

    valid_sources = {
        item.get("display_id", item.get("id"))
        for item in state.get("retrieved_knowledge", {}).get("all", [])
    }
    cited_sources = set(decision.get("evidence") or [])
    unknown_sources = sorted(cited_sources - valid_sources)
    if unknown_sources:
        add("UNKNOWN_EVIDENCE", "CRITICAL", f"决策引用了不存在的知识来源：{unknown_sources}")
    if valid_sources and not cited_sources:
        add("MISSING_EVIDENCE", "WARNING", "知识库存在相关证据，但最终决策没有引用")
    valid_memory_ids = {
        item.get("id")
        for item in (state.get("long_term_memory") or {}).get("recalled", [])
        if item.get("id") is not None
    }
    cited_memory_ids = set(decision.get("memory_evidence") or [])
    unknown_memory_ids = sorted(cited_memory_ids - valid_memory_ids)
    if unknown_memory_ids:
        add(
            "UNKNOWN_MEMORY_EVIDENCE",
            "CRITICAL",
            f"决策引用了本轮未召回的长期记忆：{unknown_memory_ids}",
        )
    if valid_memory_ids and not cited_memory_ids:
        add("MISSING_MEMORY_EVIDENCE", "WARNING", "存在相似历史结果，但决策没有标记记忆来源")
    if len(str(decision.get("explanation", "")).strip()) < 40:
        add("THIN_EXPLANATION", "WARNING", "管理层解释过短，未充分说明约束、成本和风险")
    return issues


def reflection_route(state: dict) -> str:
    latest = state.get("agent_outputs", {}).get("reflection", {})
    return "revise" if latest.get("outcome") == "REVISE" else "continue"


def determine_reflection_outcome(
    round_number: int,
    max_rounds: int,
    deterministic_issues: list[dict],
    llm_verdict: str,
    llm_error: str | None = None,
) -> str:
    if llm_error:
        return "ESCALATE"
    has_critical = any(item.get("severity") == "CRITICAL" for item in deterministic_issues)
    needs_revision = has_critical or llm_verdict == "REVISE"
    if not needs_revision:
        return "PASS"
    return "REVISE" if round_number < max_rounds else "ESCALATE"
