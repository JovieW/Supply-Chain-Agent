from __future__ import annotations

from db_service import create_decision_memory, recall_decision_memories
from memory_rules import max_recalled_memories
from state import SupplyChainState


def recall_long_term_memory_node(state: SupplyChainState) -> dict:
    """Recall outcome-verified episodes after deterministic demand planning."""
    demand = state.get("agent_outputs", {}).get("demand", {}) # 提取结果
    plan = demand.get("planning", {})
    forecast = demand.get("forecast", {})
    context = {
        "product_id": state.get("product_id"),
        "decision_type": plan.get("action"),
        "supplier_id": plan.get("supplier_id"),
        "demand_volatility": forecast.get("volatility"),
    }
    try:
        recalled = recall_decision_memories(  # 调取长期记忆
            context, limit=max_recalled_memories()
        )
        memory_state = {
            "status": "READY",
            "query_context": context,
            "recalled": recalled,
            "count": len(recalled),
            "error": None,
        }
    except Exception as exc:
        memory_state = {
            "status": "DEGRADED",
            "query_context": context,
            "recalled": [],
            "count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"long_term_memory": memory_state}


def persist_decision_memory_node(state: SupplyChainState) -> dict:
    """Create the durable episode before approval or execution side effects."""
    memory_id = create_decision_memory(state)
    current = dict(state.get("long_term_memory") or {})
    current["current_memory_id"] = memory_id
    current["write_status"] = "PERSISTED"
    return {
        "decision_memory_id": memory_id,
        "long_term_memory": current,
    }
