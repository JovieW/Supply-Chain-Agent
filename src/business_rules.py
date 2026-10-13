from __future__ import annotations

import math
from datetime import datetime
from statistics import NormalDist, fmean, pstdev


def calculate_demand_forecast(history: list[dict], horizon_days: int = 28) -> dict:
    """Forecast daily demand with a transparent recency-weighted moving average."""
    quantities = [max(0.0, float(row["quantity"])) for row in history]
    if not quantities:
        return {"method": "fallback_zero", "history_days": 0, "avg_daily_demand": 0.0,
                "forecast_daily_demand": 0.0, "forecast_horizon_days": horizon_days,
                "forecast_horizon_quantity": 0, "demand_std_dev": 0.0,
                "coefficient_of_variation": 0.0, "trend_pct": 0.0, "volatility": "LOW"}
    recent = quantities[-7:]
    previous = quantities[-14:-7] or recent
    older = quantities[:-14] or previous
    recent_avg, previous_avg = fmean(recent), fmean(previous)
    weighted_average = 0.60 * recent_avg + 0.30 * previous_avg + 0.10 * fmean(older)
    trend_pct = (recent_avg - previous_avg) / previous_avg if previous_avg else 0.0
    forecast_daily = max(0.0, weighted_average * (1 + max(-0.20, min(0.20, trend_pct)) * 0.35))
    deviation = pstdev(quantities[-28:]) if len(quantities) > 1 else 0.0
    cv = deviation / forecast_daily if forecast_daily else 0.0
    volatility = "HIGH" if cv >= 0.35 else "MEDIUM" if cv >= 0.20 else "LOW"
    return {"method": "weighted_moving_average_7_14_28", "history_days": len(quantities),
            "avg_daily_demand": round(fmean(quantities), 2),
            "forecast_daily_demand": round(forecast_daily, 2),
            "forecast_horizon_days": horizon_days,
            "forecast_horizon_quantity": math.ceil(forecast_daily * horizon_days),
            "demand_std_dev": round(deviation, 2), "coefficient_of_variation": round(cv, 3),
            "trend_pct": round(trend_pct, 3), "volatility": volatility}


def _round_order_quantity(raw_quantity: float, minimum: int, multiple: int, maximum: int) -> tuple[int, bool]:
    if raw_quantity <= 0:
        return 0, False
    multiple = max(1, multiple)
    quantity = max(minimum, math.ceil(raw_quantity / multiple) * multiple)
    constrained = quantity > maximum
    if constrained:
        quantity = (maximum // multiple) * multiple
    return max(0, quantity), constrained


def _candidate_plan(supplier: dict, forecast: dict, policy: dict,
                    inventory_position: int, should_reorder: bool) -> dict:
    daily = forecast["forecast_daily_demand"]
    deviation = forecast["demand_std_dev"]
    service_level = min(0.999, max(0.501, float(policy["service_level"])))
    z_score = NormalDist().inv_cdf(service_level)
    standard_lead = max(1, int(supplier["lead_time_days"]))

    def evaluate_mode(expedite: bool) -> dict:
        lead = max(1, int(supplier["expedite_lead_time_days"])) if expedite else standard_lead
        safety_stock = math.ceil(z_score * deviation * math.sqrt(lead))
        reorder_point = math.ceil(daily * lead + safety_stock)
        target_stock = math.ceil(daily * (lead + int(policy["review_period_days"])) + safety_stock)
        raw_quantity = max(0, target_stock - inventory_position) if should_reorder else 0
        quantity, capacity_constrained = _round_order_quantity(
            raw_quantity, int(supplier["min_order_quantity"]),
            int(supplier["order_multiple"]), int(supplier["max_order_quantity"]))
        projected_at_arrival = inventory_position - math.ceil(daily * lead)
        projected_shortage = max(0, -projected_at_arrival)
        freight = float(supplier["expedited_freight_cost"] if expedite else supplier["freight_cost"])
        purchase_cost = quantity * float(supplier["unit_cost"])
        holding_cost = quantity * 0.5 * float(policy["holding_cost_per_unit_year"]) * (
            lead + int(policy["review_period_days"])) / 365
        quality_loss = quantity * float(supplier["defect_rate"]) * float(policy["stockout_cost_per_unit"])
        delivery_loss = projected_shortage * (2 - float(supplier["on_time_delivery_rate"])) * float(policy["stockout_cost_per_unit"])
        total_cost = purchase_cost + freight + holding_cost + quality_loss + delivery_loss
        return {"expedite": expedite, "lead_time_days": lead, "safety_stock": safety_stock,
                "reorder_point": reorder_point, "target_stock": target_stock,
                "raw_order_quantity": math.ceil(raw_quantity), "order_quantity": quantity,
                "projected_inventory_at_arrival": projected_at_arrival,
                "projected_shortage": projected_shortage, "purchase_cost": round(purchase_cost, 2),
                "freight_cost": round(freight, 2), "holding_cost": round(holding_cost, 2),
                "risk_cost": round(quality_loss + delivery_loss, 2),
                "total_landed_risk_cost": round(total_cost, 2),
                "capacity_constrained": capacity_constrained}

    standard = evaluate_mode(False)
    modes = [standard]
    if supplier["expedite_lead_time_days"] < standard_lead:
        modes.append(evaluate_mode(True))
    best = min(modes, key=lambda row: row["total_landed_risk_cost"])
    if best["expedite"] and standard["projected_shortage"] <= 0:
        best = standard
    return {"supplier_id": supplier["id"], "supplier_name": supplier["name"],
            "unit_cost": supplier["unit_cost"],
            "on_time_delivery_rate": supplier["on_time_delivery_rate"],
            "defect_rate": supplier["defect_rate"],
            "min_order_quantity": supplier["min_order_quantity"],
            "order_multiple": supplier["order_multiple"],
            "max_order_quantity": supplier["max_order_quantity"], **best,
            "standard_total_cost": standard["total_landed_risk_cost"]}


def calculate_supply_chain_plan(snapshot: dict) -> dict:
    """Create an auditable constrained replenishment and sourcing plan."""
    # 1. 检查需求预测
    forecast = calculate_demand_forecast(snapshot.get("demand_history", []))
    policy, inventory = snapshot["inventory_policy"], snapshot["inventory"]
    daily = forecast["forecast_daily_demand"]
    days_of_supply = (max(0.0, int(inventory["quantity"]) - int(policy["reserved_quantity"])) / daily
                      if daily else 999.0)
    suppliers = snapshot.get("suppliers", [])
    if not suppliers:
        raise ValueError("No approved supplier offers available")
    base_supplier = min(suppliers, key=lambda s: (
        float(s["unit_cost"])
        + (1 - float(s["on_time_delivery_rate"])) * float(policy["stockout_cost_per_unit"])
        + float(s["defect_rate"]) * float(policy["stockout_cost_per_unit"])))
    base_lead = max(1, int(base_supplier["lead_time_days"]))
    now = datetime.utcnow()
    inbound_quantity = 0
    excluded_late_inbound = 0
    for po in snapshot.get("purchase_orders", []):
        quantity = int(po["quantity"])
        try:
            eta = datetime.fromisoformat(po.get("expected_arrival"))
        except (TypeError, ValueError):
            eta = None
        if eta is not None and (eta - now).total_seconds() <= base_lead * 86400:
            inbound_quantity += quantity
        else:
            excluded_late_inbound += quantity

    # 3. 计算库存位置
    inventory_position = (int(inventory["quantity"]) - int(policy["reserved_quantity"])
                          - int(policy["backorder_quantity"]) + inbound_quantity)
    # 4. 计算可支撑天数
    z_score = NormalDist().inv_cdf(min(0.999, max(0.501, float(policy["service_level"]))))
    # 5. 计算动态补货点
    planning_reorder_point = math.ceil(daily * base_lead + z_score * forecast["demand_std_dev"] * math.sqrt(base_lead))
    # 6. 判断是否补货
    should_reorder = inventory_position <= planning_reorder_point or int(policy["backorder_quantity"]) > 0
    candidates = [_candidate_plan(s, forecast, policy, inventory_position, should_reorder) for s in suppliers]
    feasible = [c for c in candidates if not c["capacity_constrained"] and (not should_reorder or c["order_quantity"] > 0)]
    ranked = sorted(feasible or candidates, key=lambda row: row["total_landed_risk_cost"])
    recommended = ranked[0]
    action = "REORDER" if should_reorder and recommended["order_quantity"] > 0 else "HOLD"
    return {"action": action, "quantity": recommended["order_quantity"] if action == "REORDER" else 0,
            "supplier_id": recommended["supplier_id"] if action == "REORDER" else None,
            "expedite": recommended["expedite"] if action == "REORDER" else False,
            "forecast": forecast, "inventory_position": inventory_position,
            "on_hand_quantity": int(inventory["quantity"]), "inbound_quantity": inbound_quantity,
            "reserved_quantity": int(policy["reserved_quantity"]),
            "backorder_quantity": int(policy["backorder_quantity"]),
            "excluded_late_inbound": excluded_late_inbound,
            "days_of_supply": round(days_of_supply, 1), "service_level": policy["service_level"],
            "planning_reorder_point": planning_reorder_point, "recommended_option": recommended,
            "supplier_options": sorted(candidates, key=lambda row: row["total_landed_risk_cost"]),
            "constraints": [f"供应商 {c['supplier_id']} 受最大供货量约束" for c in candidates if c["capacity_constrained"]]}


def calculate_demand_risk(inventory: dict, purchase_orders: list[dict], forecast: dict | None = None) -> str:
    if forecast and forecast.get("volatility") == "HIGH":
        return "HIGH"
    return "HIGH" if inventory["quantity"] < inventory["reorder_point"] or not purchase_orders else "LOW"


def calculate_inventory_decision(inventory: dict) -> tuple[str, int]:
    current, reorder_point = inventory["quantity"], inventory["reorder_point"]
    return ("REORDER", reorder_point * 2 - current) if current < reorder_point else ("HOLD", 0)


def calculate_supplier_risk(suppliers: list[dict]) -> str:
    if not suppliers:
        return "HIGH"
    return "HIGH" if any(s.get("on_time_delivery_rate", s.get("reliability_score", 0)) < 0.90
                         or s.get("defect_rate", 0) > 0.03 for s in suppliers) else "LOW"


def calculate_logistics_risk(shipments: list[dict], now: datetime | None = None) -> str:
    if not shipments:
        return "HIGH"
    now = now or datetime.utcnow()
    for shipment in shipments:
        try:
            eta = datetime.fromisoformat(shipment.get("expected_arrival"))
        except (TypeError, ValueError):
            return "HIGH"
        days_until_arrival = (eta - now).total_seconds() / 86400
        if days_until_arrival < 0 or days_until_arrival > 7:
            return "HIGH"
    return "LOW"
