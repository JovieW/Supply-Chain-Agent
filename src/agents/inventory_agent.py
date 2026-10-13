from state import SupplyChainState
from llm_config import get_llm
from rag.retriever import format_retrieved_context, get_retrieved_source_ids
from agents.schemas import ExplanationOutput, invoke_structured
from memory_rules import format_memory_context


async def inventory_agent_node(state: SupplyChainState) -> dict:
    """Publish the deterministic constrained plan and explain it with enterprise context."""
    plan = state["agent_outputs"]["demand"]["planning"]
    retrieved = state.get("retrieved_knowledge", {})
    memory_context = format_memory_context(state.get("long_term_memory", {}))
    option = plan["recommended_option"]
    supplier_id = plan["supplier_id"]
    context = format_retrieved_context(
        retrieved, "inventory", supplier_id=supplier_id
    )
    sources = get_retrieved_source_ids(retrieved, "inventory", supplier_id=supplier_id)
    prompt = f"""你是供应链库存与补货计划专员。系统已通过确定性约束模型完成方案计算，你只负责解释，不得修改结果。
动作：{plan['action']}；数量：{plan['quantity']}；供应商：{plan['supplier_id']}；是否加急：{plan['expedite']}。
库存位置：{plan['inventory_position']}；动态补货点：{plan['planning_reorder_point']}；
安全库存：{option['safety_stock']}；目标库存：{option['target_stock']}；
预计到货前缺口：{option['projected_shortage']}；风险调整总成本：{option['total_landed_risk_cost']:.2f}。
企业知识：
{context}
经过结果验证的相似历史决策：
{memory_context}
请用 2-4 句中文说明库存触发、约束、供应商与成本取舍。"""
    response = await invoke_structured(
        get_llm(), ExplanationOutput, prompt,
        config={"run_name": "inventory_constrained_plan_explanation",
                "tags": ["inventory", "replenishment", "p1"],
                "metadata": {"action": plan["action"], "supplier_id": supplier_id}},
    )
    return {"agent_outputs": {"inventory": {
        "action": plan["action"], "quantity": plan["quantity"],
        "supplier_id": supplier_id, "expedite": plan["expedite"],
        "inventory_position": plan["inventory_position"],
        "days_of_supply": plan["days_of_supply"],
        "planning_reorder_point": plan["planning_reorder_point"],
        "safety_stock": option["safety_stock"], "target_stock": option["target_stock"],
        "recommended_option": option, "supplier_options": plan["supplier_options"],
        "constraints": plan["constraints"], "reasoning": response.reasoning,
        "knowledge_sources": sources,
    }}}
