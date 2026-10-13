from state import SupplyChainState
from llm_config import get_llm
from rag.retriever import format_retrieved_context, get_retrieved_source_ids
from agents.schemas import ExplanationOutput, invoke_structured
from memory_rules import format_memory_context


async def logistics_agent_node(state: SupplyChainState) -> dict:
    """Explain a cost-versus-shortage optimized transport mode."""
    inventory = state["agent_outputs"]["inventory"]
    option = inventory["recommended_option"]
    retrieved = state.get("retrieved_knowledge", {})
    context = format_retrieved_context(retrieved, "logistics", supplier_id=option["supplier_id"])
    sources = get_retrieved_source_ids(retrieved, "logistics", supplier_id=option["supplier_id"])
    memory_context = format_memory_context(state.get("long_term_memory", {}))
    prompt = f"""你是物流计划专员。运输模式已由缺货损失与运输成本比较确定。
是否加急：{option['expedite']}；交期：{option['lead_time_days']} 天；
预计到货时库存：{option['projected_inventory_at_arrival']}；预计缺口：{option['projected_shortage']}；
运费：{option['freight_cost']:.2f}；风险调整总成本：{option['total_landed_risk_cost']:.2f}；
标准运输总成本：{option['standard_total_cost']:.2f}。
企业知识：
{context}
经过结果验证的历史经验：
{memory_context}
请用 2-3 句中文解释成本和服务风险权衡，不得改变运输模式。"""
    response = await invoke_structured(
        get_llm(), ExplanationOutput, prompt,
        config={"run_name": "logistics_cost_explanation", "tags": ["logistics", "cost", "p1"],
                "metadata": {"expedite": option["expedite"], "lead_time_days": option["lead_time_days"]}},
    )
    return {"agent_outputs": {"logistics": {
        "expedite": inventory["expedite"], "lead_time_days": option["lead_time_days"],
        "projected_inventory_at_arrival": option["projected_inventory_at_arrival"],
        "projected_shortage": option["projected_shortage"],
        "freight_cost": option["freight_cost"],
        "total_landed_risk_cost": option["total_landed_risk_cost"],
        "standard_total_cost": option["standard_total_cost"],
        "reasoning": response.reasoning, "knowledge_sources": sources,
    }}}
