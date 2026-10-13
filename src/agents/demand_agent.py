from state import SupplyChainState
from llm_config import get_llm
from rag.retriever import format_retrieved_context, get_retrieved_source_ids
from agents.schemas import ExplanationOutput, invoke_structured
from business_rules import calculate_supply_chain_plan


async def demand_agent_node(state: SupplyChainState) -> dict:
    """Forecast demand and classify exposure with deterministic planning logic."""
    snapshot = state["db_snapshot"]
    retrieved = state.get("retrieved_knowledge", {})
    plan = calculate_supply_chain_plan(snapshot)
    forecast = plan["forecast"]
    demand_risk = (
        "HIGH"
        if plan["backorder_quantity"] > 0
        or plan["days_of_supply"] <= plan["recommended_option"]["lead_time_days"]
        or forecast["volatility"] == "HIGH"
        else "MEDIUM"
        if forecast["volatility"] == "MEDIUM" or plan["days_of_supply"] <= 14
        else "LOW"
    )
    context = format_retrieved_context(retrieved, "demand")  # 把检索到的结构化结果转化为prompt文本
    sources = get_retrieved_source_ids(retrieved, "demand")
    prompt = f"""你是供应链需求计划专员。系统已用可复算的加权移动平均完成预测，
请只用 2-3 句中文解释结果，不得修改数值或风险等级。
历史天数：{forecast['history_days']}；预测日均需求：{forecast['forecast_daily_demand']}；
28 天需求：{forecast['forecast_horizon_quantity']}；变异系数：{forecast['coefficient_of_variation']}；
趋势：{forecast['trend_pct']:.1%}；波动等级：{forecast['volatility']}；
可用天数：{plan['days_of_supply']}；需求风险：{demand_risk}。
企业知识：
{context}
如有相关依据可引用知识来源 ID。"""
    response = await invoke_structured(
        get_llm(), ExplanationOutput, prompt,
        config={"run_name": "demand_forecast_explanation",
                "tags": ["demand", "forecast", "p1"],
                "metadata": {"forecast_method": forecast["method"], "demand_risk": demand_risk}},
    )
    return {"agent_outputs": {"demand": {
        "demand_risk": demand_risk,
        "forecast": forecast,
        "planning": plan,
        "reasoning": response.reasoning,
        "knowledge_sources": sources,
    }}}
