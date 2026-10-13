from state import SupplyChainState


def decision_gate_node(state: SupplyChainState) -> dict:
    """Apply an auditable authority matrix after self-reflection."""
    outputs = state["agent_outputs"]
    decision = state.get("final_decision") or {}
    reflection = outputs.get("reflection", {})
    reflection_outcome = reflection.get("outcome")

    if decision.get("decision_type") == "HOLD" and reflection_outcome != "ESCALATE":
        assessment = {
            "score": 0,
            "level": "LOW",
            "approval_required": False,
            "reasons": ["暂不补货不产生采购资金承诺，且反思未要求升级"],
            "authority_rule": "风险分≥4 转人工审批；2-3 为中风险自动执行；0-1 为低风险自动执行",
        }
        return {"decision_risk": "LOW", "decision_assessment": assessment}

    demand = outputs.get("demand", {})
    risk = outputs.get("risk", {})
    logistics = outputs.get("logistics", {})
    inventory = outputs.get("inventory", {})
    case = decision.get("business_case", {})
    score, reasons = 0, []

    weights = {"HIGH": 3, "MEDIUM": 1, "LOW": 0}
    for label, value in (
        ("需求", demand.get("demand_risk", "HIGH")),
        ("供应商", risk.get("supplier_risk", "HIGH")),
        ("物流", risk.get("logistics_risk", "HIGH")),
    ):
        points = weights.get(value, 3)
        score += points
        if points:
            reasons.append(f"{label}风险为 {value}（+{points}）")
    if logistics.get("expedite"):
        score += 2
        reasons.append("使用加急运输，产生额外成本（+2）")
    if risk.get("capacity_constrained"):
        score += 4
        reasons.append("供应商产能不足以覆盖计划量（+4）")

    total_cost = float(case.get("total_landed_risk_cost", 0))
    if total_cost >= 10000:
        score += 2
        reasons.append("风险调整总成本达到 10,000（+2）")
    elif total_cost >= 5000:
        score += 1
        reasons.append("风险调整总成本达到 5,000（+1）")
    if logistics.get("projected_shortage", 0) > 0:
        score += 3
        reasons.append("预计到货前仍会发生缺货（+3）")
    if inventory.get("constraints"):
        reasons.extend(inventory["constraints"])

    tool_alerts = outputs.get("tool_investigator", {}).get("alerts", [])
    if tool_alerts:
        alert_points = min(2, len(tool_alerts))
        score += alert_points
        reasons.append(f"自主工具调查发现 {len(tool_alerts)} 项异常（+{alert_points}）")
        reasons.extend(tool_alerts)

    if reflection_outcome == "ESCALATE":
        score += 4
        reasons.append("自我反思达到轮次上限仍未通过，或反思服务不可用（+4）")
        reflection_feedback = reflection.get("feedback")
        if reflection_feedback:
            reasons.append(reflection_feedback)

    level = "HIGH" if score >= 4 else "MEDIUM" if score >= 2 else "LOW"
    assessment = {
        "score": score,
        "level": level,
        "approval_required": level == "HIGH",
        "reasons": reasons or ["未触发升级审批条件"],
        "authority_rule": "风险分≥4 转人工审批；2-3 为中风险自动执行；0-1 为低风险自动执行",
        "reflection_outcome": reflection_outcome,
        "reflection_rounds": reflection.get("round", 0),
    }
    return {"decision_risk": level, "decision_assessment": assessment}


def should_request_human_approval(state: SupplyChainState) -> str:
    assessment = state.get("decision_assessment") or {}
    return "human_approval" if assessment.get("approval_required", True) else "auto_execute"
