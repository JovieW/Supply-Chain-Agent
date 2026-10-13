"""Seed idempotent local showcase data for the Streamlit business workflow.

This utility is intentionally for a local demo SQLite database. It bypasses
LLM reasoning and human approval, so it must not be used in production.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
load_dotenv(ROOT / ".env")

from business_rules import calculate_supply_chain_plan
from db_init import init_database, seed_data
from db_service import (
    create_decision_memory,
    create_purchase_order,
    ensure_runtime_schema,
    list_decision_memories,
    read_supply_chain_snapshot,
    record_outcome_feedback,
    transition_purchase_order,
    update_decision_memory_status,
)


def _feedback_email(value: str | None) -> str:
    if value:
        return value.strip().lower()
    for email in os.getenv("MEMORY_FEEDBACK_USERS", "").split(","):
        if email.strip():
            return email.strip().lower()
    for binding in os.getenv("HITL_APPROVER_BINDINGS", "").split(","):
        if binding.split(":", 1)[0].strip():
            return binding.split(":", 1)[0].strip().lower()
    raise ValueError("请配置 MEMORY_FEEDBACK_USERS 或传入 --feedback-email")


def _memory(run_id: str) -> dict | None:
    return next(
        (item for item in list_decision_memories(limit=300)
         if item.get("workflow_run_id") == run_id),
        None,
    )


def _reorder_state(product_id: int, run_id: str) -> tuple[dict, dict]:
    snapshot = read_supply_chain_snapshot(product_id)
    plan = calculate_supply_chain_plan(snapshot)
    option = plan["recommended_option"]
    quantity = int(option["order_quantity"] or option["min_order_quantity"])
    if quantity <= 0:
        raise ValueError(f"产品 {product_id} 无法生成正数演示采购量")
    decision = {
        "decision_type": "REORDER",
        "details": {
            "supplier_id": option["supplier_id"],
            "quantity": quantity,
            "expedite": bool(option["expedite"]),
        },
        "business_case": {
            "lead_time_days": int(option["lead_time_days"]),
            "total_landed_risk_cost": float(option["total_landed_risk_cost"]),
        },
        "explanation": "本地演示数据：用于展示采购履约、结果反馈与长期记忆闭环。",
    }
    state = {
        "workflow_run_id": run_id,
        "product_id": product_id,
        "db_snapshot": snapshot,
        "agent_outputs": {
            "demand": {"forecast": plan["forecast"], "planning": plan},
            "inventory": {
                "inventory_position": plan["inventory_position"],
                "days_of_supply": plan["days_of_supply"],
                "constraints": plan["constraints"],
            },
            "logistics": {"projected_shortage": option["projected_shortage"]},
        },
        "long_term_memory": {"recalled": []},
        "final_decision": decision,
        "decision_assessment": {"level": "LOW", "score": 0, "approval_required": False},
    }
    return state, decision


def _ensure_reorder(
    product_id: int, run_id: str, email: str, *, close_with_feedback: bool
) -> tuple[int, int, bool]:
    existing = _memory(run_id)
    if existing:
        return int(existing["id"]), int(existing.get("purchase_order_id") or 0), False
    state, decision = _reorder_state(product_id, run_id)
    memory_id = create_decision_memory(state)
    created = create_purchase_order(
        supplier_id=decision["details"]["supplier_id"],
        product_id=product_id,
        quantity=decision["details"]["quantity"],
        decision_key=f"{run_id}:po",
        plan_data={**decision["business_case"], **decision["details"]},
    )
    po_id = created.po_id
    transitions = [
        ("SUPPLIER_CONFIRMED", {}),
        ("SHIPPED", {"expected_arrival": (date.today() + timedelta(days=3)).isoformat()}),
    ]
    if close_with_feedback:
        transitions.extend([
            ("RECEIVED", {"received_quantity": decision["details"]["quantity"]}),
            ("QC_ACCEPTED", {"accepted_quantity": decision["details"]["quantity"]}),
            ("CLOSED", {}),
        ])
    for event_type, payload in transitions:
        transition_purchase_order(
            po_id, event_type, email, f"{run_id}:{event_type}", payload
        )
    update_decision_memory_status(memory_id, "EXECUTED", purchase_order_id=po_id)
    if close_with_feedback:
        record_outcome_feedback(memory_id, {
            "actual_lead_time_days": decision["business_case"]["lead_time_days"],
            "received_quantity": decision["details"]["quantity"],
            "stockout_units": 0,
            "quality_reject_units": 0,
            "actual_total_cost": decision["business_case"]["total_landed_risk_cost"],
            "service_level": 0.99,
            "submitted_by": email,
            "notes": "本地演示数据：按计划到货、质检合格入库并完成结果反馈。",
        })
    return memory_id, po_id, True


def _ensure_hold_history(email: str) -> tuple[int, bool]:
    run_id = "showcase-hold-product-1-v1"
    existing = _memory(run_id)
    if existing:
        return int(existing["id"]), False
    snapshot = read_supply_chain_snapshot(1)
    plan = calculate_supply_chain_plan(snapshot)
    state = {
        "workflow_run_id": run_id,
        "product_id": 1,
        "db_snapshot": snapshot,
        "agent_outputs": {
            "demand": {"forecast": plan["forecast"], "planning": plan},
            "inventory": {
                "inventory_position": plan["inventory_position"],
                "days_of_supply": plan["days_of_supply"], "constraints": [],
            },
            "logistics": {"projected_shortage": 0},
        },
        "long_term_memory": {"recalled": []},
        "final_decision": {
            "decision_type": "HOLD",
            "details": {"supplier_id": None, "quantity": 0, "expedite": False},
            "business_case": {"lead_time_days": 0, "total_landed_risk_cost": 0.0},
            "explanation": "本地演示数据：库存充足且有效在途可覆盖需求，因此暂不补货。",
        },
        "decision_assessment": {"level": "LOW", "score": 0, "approval_required": False},
    }
    memory_id = create_decision_memory(state)
    update_decision_memory_status(memory_id, "EXECUTED")
    record_outcome_feedback(memory_id, {
        "actual_lead_time_days": 0,
        "received_quantity": 0,
        "stockout_units": 0,
        "quality_reject_units": 0,
        "actual_total_cost": 0,
        "service_level": 0.98,
        "submitted_by": email,
        "notes": "本地演示数据：暂不补货期间库存充足，服务水平符合目标。",
    })
    return memory_id, True


def main() -> None:
    parser = argparse.ArgumentParser(description="写入本地供应链控制塔展示数据")
    parser.add_argument("--feedback-email", help="授权结果反馈邮箱")
    args = parser.parse_args()
    email = _feedback_email(args.feedback_email)

    engine = init_database()
    seed_data(engine)
    ensure_runtime_schema()

    hold_id, hold_created = _ensure_hold_history(email)
    pending_id, pending_po, pending_created = _ensure_reorder(
        2, "showcase-pending-feedback-product-2-v1", email,
        close_with_feedback=False,
    )
    closed_id, closed_po, closed_created = _ensure_reorder(
        3, "showcase-closed-loop-product-3-v1", email,
        close_with_feedback=True,
    )
    print("展示数据准备完成：")
    print(f"- 产品 A：暂不补货闭环记忆 #{hold_id}（{'新建' if hold_created else '已存在'}）")
    print(f"- 产品 B：待完成履约/反馈的决策记忆 #{pending_id}，采购单 #{pending_po}（{'新建' if pending_created else '已存在'}）")
    print(f"- 产品 C：已闭环决策记忆 #{closed_id}，采购单 #{closed_po}（{'新建' if closed_created else '已存在'}）")
    print("刷新 Streamlit 后，分别选择产品 A/B/C 查看对应演示状态。")


if __name__ == "__main__":
    main()
