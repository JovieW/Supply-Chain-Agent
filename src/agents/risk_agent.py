from state import SupplyChainState
from llm_config import get_llm
from rag.retriever import format_retrieved_context, get_retrieved_source_ids
from agents.schemas import ExplanationOutput, invoke_structured
from business_rules import calculate_logistics_risk
from memory_rules import format_memory_context


async def risk_agent_node(state: SupplyChainState) -> dict:
    """Assess the selected supply option rather than penalizing every candidate."""
    inventory = state["agent_outputs"]["inventory"]
    option = inventory["recommended_option"]
    shipments = state["db_snapshot"]["shipments"]
    supplier_risk = (
        "HIGH" if option["on_time_delivery_rate"] < 0.85 or option["defect_rate"] > 0.03
        else "MEDIUM" if option["on_time_delivery_rate"] < 0.92 or option["defect_rate"] > 0.015
        else "LOW"
    )
    shipment_risk = calculate_logistics_risk(shipments)
    logistics_risk = (
        "HIGH" if option["projected_shortage"] > 0
        else "MEDIUM" if shipment_risk == "HIGH" else "LOW"
    )
    retrieved = state.get("retrieved_knowledge", {})
    context = format_retrieved_context(retrieved, "risk", supplier_id=option["supplier_id"])
    sources = get_retrieved_source_ids(retrieved, "risk", supplier_id=option["supplier_id"])
    memory_context = format_memory_context(state.get("long_term_memory", {}))
    prompt = f"""你是供应链风险专员。请解释系统已计算的风险，不得改变分类。
选定供应商：{option['supplier_name']}；准时交付率：{option['on_time_delivery_rate']:.1%}；
缺陷率：{option['defect_rate']:.1%}；容量受限：{option['capacity_constrained']}；
预计到货缺口：{option['projected_shortage']}；供应风险：{supplier_risk}；物流风险：{logistics_risk}。
企业知识：
{context}
经过结果验证的历史经验：
{memory_context}
用 2-3 句中文说明主要暴露和已采用的缓释因素。"""
    response = await invoke_structured(
        get_llm(), ExplanationOutput, prompt,
        config={"run_name": "selected_option_risk_explanation", "tags": ["risk", "supplier", "p1"],
                "metadata": {"supplier_risk": supplier_risk, "logistics_risk": logistics_risk}},
    )
    return {"agent_outputs": {"risk": {
        "supplier_risk": supplier_risk, "logistics_risk": logistics_risk,
        "on_time_delivery_rate": option["on_time_delivery_rate"],
        "defect_rate": option["defect_rate"], "capacity_constrained": option["capacity_constrained"],
        "reasoning": response.reasoning, "knowledge_sources": sources,
    }}}
