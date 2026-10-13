from __future__ import annotations

import copy
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from business_rules import calculate_demand_forecast, calculate_supply_chain_plan


class StrictToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArguments(StrictToolArguments):
    pass


class ForecastArguments(StrictToolArguments):
    horizon_days: int = Field(default=28, ge=7, le=90)


class KnowledgeSearchArguments(StrictToolArguments):
    query: str = Field(min_length=2, max_length=300)
    document_type: Literal["all", "sop", "contract", "historical_event"] = "all"
    top_k: int = Field(default=3, ge=1, le=5)


class StressTestArguments(StrictToolArguments):
    demand_multiplier: float = Field(default=1.2, ge=0.5, le=2.0)
    lead_time_delay_days: int = Field(default=3, ge=0, le=30)


def inspect_inventory_position(snapshot: dict) -> dict:
    plan = calculate_supply_chain_plan(snapshot)  # 计算供应链
    return {
        "on_hand_quantity": plan["on_hand_quantity"],
        "effective_inbound_quantity": plan["inbound_quantity"],
        "excluded_late_inbound": plan["excluded_late_inbound"],
        "reserved_quantity": plan["reserved_quantity"],
        "backorder_quantity": plan["backorder_quantity"],
        "inventory_position": plan["inventory_position"],
        "days_of_supply": plan["days_of_supply"],
        "dynamic_reorder_point": plan["planning_reorder_point"],
        "action": plan["action"],
    }


def forecast_demand(snapshot: dict, horizon_days: int = 28) -> dict:
    return calculate_demand_forecast(snapshot.get("demand_history", []), horizon_days)


def compare_supplier_options(snapshot: dict) -> dict:
    plan = calculate_supply_chain_plan(snapshot)
    return {
        "recommended_supplier_id": plan["supplier_id"],
        "recommended_quantity": plan["quantity"],
        "expedite": plan["expedite"],
        "constraints": plan["constraints"],
        "options": [
            {
                "supplier_id": option["supplier_id"],
                "supplier_name": option["supplier_name"],
                "lead_time_days": option["lead_time_days"],
                "order_quantity": option["order_quantity"],
                "on_time_delivery_rate": option["on_time_delivery_rate"],
                "defect_rate": option["defect_rate"],
                "projected_shortage": option["projected_shortage"],
                "total_landed_risk_cost": option["total_landed_risk_cost"],
                "capacity_constrained": option["capacity_constrained"],
            }
            for option in plan["supplier_options"]
        ],
    }


def inspect_inbound_shipments(snapshot: dict) -> dict:
    now = datetime.utcnow()
    rows = []
    for order in snapshot.get("purchase_orders", []):
        eta_value = order.get("expected_arrival")
        try:
            eta = datetime.fromisoformat(eta_value)
            days_until_arrival = round((eta - now).total_seconds() / 86400, 1)
            status = "overdue" if days_until_arrival < 0 else "scheduled"
        except (TypeError, ValueError):
            days_until_arrival, status = None, "missing_eta"
        rows.append({
            "purchase_order_id": order.get("id"),
            "supplier_id": order.get("supplier_id"),
            "quantity": order.get("quantity", 0),
            "expected_arrival": eta_value,
            "days_until_arrival": days_until_arrival,
            "status": status,
        })
    return {
        "active_orders": len(rows),
        "total_inbound_quantity": sum(int(row["quantity"]) for row in rows),
        "exceptions": sum(row["status"] != "scheduled" for row in rows),
        "orders": rows,
    }


def search_enterprise_knowledge(
    retrieved_knowledge: dict,
    query: str,
    document_type: str = "all",
    top_k: int = 3,
    *,
    vector_search_fn=None,
) -> dict:
    """Search the already-synchronized vector index, with cached-result fallback."""
    fallback_reason = "empty_live_result"
    try:
        if vector_search_fn is None:
            from rag.retriever import search_enterprise_knowledge as vector_search
        else:
            vector_search = vector_search_fn

        results = vector_search(
            query,
            top_k=top_k,
            document_type=document_type,
            applicability_context=retrieved_knowledge.get("applicability_context", {}),
        )
        mode = "live_vector_search"
    except Exception as exc:
        results = []
        fallback_reason = type(exc).__name__
    if not results:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        candidates = retrieved_knowledge.get("all", [])
        if document_type != "all":
            candidates = [c for c in candidates if c.get("document_type") == document_type]
        results = sorted(
            candidates,
            key=lambda c: (
                sum(term in (c.get("content", "") + c.get("title", "")).lower() for term in query_terms),
                c.get("score", 0),
            ),
            reverse=True,
        )[:top_k]
        mode = f"cached_retrieval_fallback:{fallback_reason}"
    return {
        "mode": mode,
        "results": [
            {
                "source_id": item.get("display_id", item.get("id")),
                "document_type": item.get("document_type"),
                "title": item.get("title"),
                "content": item.get("content", "")[:1200],
                "score": item.get("score", 0),
            }
            for item in results
        ],
    }


def stress_test_plan(
    snapshot: dict, demand_multiplier: float = 1.2, lead_time_delay_days: int = 3
) -> dict:
    stressed = copy.deepcopy(snapshot)
    for row in stressed.get("demand_history", []):
        row["quantity"] = round(float(row["quantity"]) * demand_multiplier)
    for supplier in stressed.get("suppliers", []):
        supplier["lead_time_days"] += lead_time_delay_days
        supplier["expedite_lead_time_days"] += lead_time_delay_days
    plan = calculate_supply_chain_plan(stressed)
    option = plan["recommended_option"]
    return {
        "scenario": {
            "demand_multiplier": demand_multiplier,
            "lead_time_delay_days": lead_time_delay_days,
        },
        "action": plan["action"],
        "quantity": plan["quantity"],
        "supplier_id": plan["supplier_id"],
        "expedite": plan["expedite"],
        "inventory_position": plan["inventory_position"],
        "days_of_supply": plan["days_of_supply"],
        "projected_shortage": option["projected_shortage"],
        "total_landed_risk_cost": option["total_landed_risk_cost"],
        "constraints": plan["constraints"],
    }


def build_read_only_tools(snapshot: dict, retrieved_knowledge: dict) -> list:
    """Build state-bound LangChain tools. None of these functions can write data."""
    from langchain_core.tools import StructuredTool

    return [
        StructuredTool.from_function(
            name="inspect_inventory_position",
            description="核查在手、有效在途、占用、欠单、库存位置和动态补货点。",
            func=lambda: inspect_inventory_position(snapshot),
            args_schema=NoArguments,
        ),
        StructuredTool.from_function(
            name="forecast_demand",
            description="根据历史日需求预测指定未来周期的需求、趋势和波动性。",
            func=lambda horizon_days=28: forecast_demand(snapshot, horizon_days),
            args_schema=ForecastArguments,
        ),
        StructuredTool.from_function(
            name="inspect_inbound_shipments",
            description="核查采购在途的 ETA、逾期和缺失到货时间异常。",
            func=lambda: inspect_inbound_shipments(snapshot),
            args_schema=NoArguments,
        ),
        StructuredTool.from_function(
            name="compare_supplier_options",
            description="比较合格供应商的交期、质量、产能、预计缺口和风险调整总成本。",
            func=lambda: compare_supplier_options(snapshot),
            args_schema=NoArguments,
        ),
        StructuredTool.from_function(
            name="search_enterprise_knowledge",
            description="按需检索企业知识库中的 SOP、合同条款或历史事件。",
            func=lambda query, document_type="all", top_k=3: search_enterprise_knowledge(
                retrieved_knowledge, query, document_type, top_k
            ),
            args_schema=KnowledgeSearchArguments,
        ),
        StructuredTool.from_function(
            name="stress_test_plan",
            description="模拟需求变化和供应商交期延迟，评估补货方案的韧性。",
            func=lambda demand_multiplier=1.2, lead_time_delay_days=3: stress_test_plan(
                snapshot, demand_multiplier, lead_time_delay_days
            ),
            args_schema=StressTestArguments,
        ),
    ]
