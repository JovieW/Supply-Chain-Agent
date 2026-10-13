from state import SupplyChainState
from llm_config import get_llm
from rag.retriever import format_retrieved_context, get_retrieved_source_ids
from agents.schemas import CoordinatorDecisionOutput, ExplanationOutput, invoke_structured
from memory_rules import format_memory_context


async def coordinator_agent_node(state: SupplyChainState) -> dict:
    """Publish the computed plan; the LLM may explain it but cannot alter it."""
    outputs = state["agent_outputs"]
    inventory = outputs["inventory"]
    logistics = outputs["logistics"]
    risk = outputs["risk"]
    investigation = outputs.get("tool_investigator", {})
    reflection_feedback = state.get("reflection_feedback")
    revision_number = int(state.get("reflection_round", 0))
    retrieved = state.get("retrieved_knowledge", {})
    context = format_retrieved_context(
        retrieved, "coordinator", supplier_id=inventory["supplier_id"]
    )
    sources = get_retrieved_source_ids(
        retrieved, "coordinator", supplier_id=inventory["supplier_id"]
    )
    option = inventory["recommended_option"]
    memory_context = format_memory_context(state.get("long_term_memory", {}))
    memory_ids = [
        item.get("id")
        for item in state.get("long_term_memory", {}).get("recalled", [])
        if item.get("id") is not None
    ]
    prompt = f"""你是供应链决策协调专员。系统已完成需求、库存、采购、物流和风险计算。
最终动作：{inventory['action']}；供应商：{inventory['supplier_id']}；数量：{inventory['quantity']}；
是否加急：{inventory['expedite']}；风险调整总成本：{option['total_landed_risk_cost']:.2f}；
供应风险：{risk['supplier_risk']}；物流风险：{risk['logistics_risk']}。
企业知识：
{context}
经过结果验证的相似历史决策：
{memory_context}
自主工具调查摘要：{investigation.get('summary', '未执行')}
自主工具告警：{investigation.get('alerts', [])}
上一轮反思反馈：{reflection_feedback or '无，这是首次生成'}
当前修订次数：{revision_number}
请用 3-5 句中文形成管理层摘要，说明业务触发、约束、成本和风险；历史经验只能用于提示风险和解释，不能修改确定性结果。"""
    explanation = await invoke_structured(
        get_llm(), ExplanationOutput, prompt,
        config={"run_name": "coordinator_management_summary", "tags": ["coordinator", "p1", "p2"],
                "metadata": {"action": inventory["action"], "supplier_id": inventory["supplier_id"]}},
    )
    final_decision = {
        "decision_type": inventory["action"],
        "details": {"supplier_id": inventory["supplier_id"], "quantity": inventory["quantity"],
                    "expedite": inventory["expedite"]},
        "business_case": {
            "inventory_position": inventory["inventory_position"],
            "days_of_supply": inventory["days_of_supply"],
            "reorder_point": inventory["planning_reorder_point"],
            "safety_stock": inventory["safety_stock"],
            "target_stock": inventory["target_stock"],
            "lead_time_days": logistics["lead_time_days"],
            "projected_shortage": logistics["projected_shortage"],
            "purchase_cost": option["purchase_cost"],
            "freight_cost": option["freight_cost"],
            "risk_cost": option["risk_cost"],
            "total_landed_risk_cost": option["total_landed_risk_cost"],
            "constraints": inventory["constraints"],
            "autonomous_tool_alerts": investigation.get("alerts", []),
        },
        "explanation": explanation.reasoning, "evidence": sources,
        "memory_evidence": memory_ids,
        "revision_number": revision_number,
    }
    return {"final_decision": final_decision,
            "agent_outputs": {"coordinator": {"knowledge_sources": sources,
                                              "business_case": final_decision["business_case"],
                                              "memory_evidence": memory_ids,
                                              "revision_number": revision_number}}}


def validate_coordinator_proposal(
    proposal: CoordinatorDecisionOutput, inventory_output: dict,
    logistics_output: dict, suppliers: list[dict],
) -> None:
    """Backward-compatible fail-closed validator retained for tests and integrations."""
    expected_type = inventory_output.get("action")
    if proposal.decision_type != expected_type:
        raise ValueError(f"Unsafe coordinator decision: expected {expected_type}, got {proposal.decision_type}")
    if proposal.decision_type == "HOLD":
        return
    if proposal.supplier_id not in {supplier["id"] for supplier in suppliers}:
        raise ValueError(f"Supplier {proposal.supplier_id} is not an approved candidate")
    if proposal.quantity != inventory_output.get("quantity"):
        raise ValueError(f"Unsafe quantity: expected {inventory_output.get('quantity')}, got {proposal.quantity}")
    if proposal.expedite != bool(logistics_output.get("expedite", False)):
        raise ValueError("Unsafe expedite flag")
