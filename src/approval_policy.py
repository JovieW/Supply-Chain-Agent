from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta


ROLE_LEVELS = {
    "supply_chain_manager": 10,
    "procurement_director": 20,
    "admin": 100,
}
VALID_ACTIONS = {"approve", "reject"}
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class StaleApprovalError(PermissionError):
    """The approved business snapshot changed before the side effect ran."""


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def decision_hash(decision: dict, assessment: dict) -> str:
    """Fingerprint exactly what the reviewer is being asked to authorize."""
    return _canonical_hash({"decision": decision, "assessment": assessment})


def material_snapshot(snapshot: dict) -> dict:
    """Keep only live fields whose change can invalidate a procurement approval."""
    inventory = snapshot.get("inventory") or {}
    purchase_orders = sorted(
        [
            {
                "id": row.get("id"),
                "supplier_id": row.get("supplier_id"),
                "quantity": row.get("quantity"),
                "status": row.get("status"),
            }
            for row in snapshot.get("purchase_orders", [])
        ],
        key=lambda row: (row.get("id") is None, row.get("id")),
    )
    shipments = sorted(
        [
            {
                "id": row.get("id"),
                "po_id": row.get("po_id"),
                "status": row.get("status"),
                "expected_arrival": row.get("expected_arrival"),
                "actual_arrival": row.get("actual_arrival"),
            }
            for row in snapshot.get("shipments", [])
        ],
        key=lambda row: (row.get("id") is None, row.get("id")),
    )
    return {
        "product_id": (snapshot.get("product") or {}).get("id"),
        "inventory": {
            "quantity": inventory.get("quantity"),
            "reorder_point": inventory.get("reorder_point"),
        },
        "purchase_orders": purchase_orders,
        "shipments": shipments,
        "approved_suppliers": sorted(
            [
                {
                    key: supplier.get(key)
                    for key in (
                        "id", "lead_time_days", "reliability_score", "unit_cost",
                        "min_order_quantity", "order_multiple", "max_order_quantity",
                        "freight_cost", "expedited_freight_cost",
                        "expedite_lead_time_days", "on_time_delivery_rate", "defect_rate",
                    )
                }
                for supplier in snapshot.get("suppliers", [])
                if supplier.get("id") is not None
            ],
            key=lambda supplier: supplier["id"],
        ),
    }


def snapshot_hash(snapshot: dict) -> str:
    return _canonical_hash(material_snapshot(snapshot))


def approval_ttl_hours() -> int:
    try:
        value = int(os.getenv("HITL_APPROVAL_TTL_HOURS", "24"))
    except ValueError:
        value = 24
    return max(1, min(168, value))


def approval_expiry(now: datetime | None = None) -> datetime:
    return (now or datetime.utcnow()) + timedelta(hours=approval_ttl_hours())


def required_approval_role(decision: dict) -> str:
    try:
        threshold = float(os.getenv("HITL_DIRECTOR_COST_THRESHOLD", "10000"))
    except ValueError:
        threshold = 10000.0
    total_cost = float((decision.get("business_case") or {}).get(
        "total_landed_risk_cost", 0
    ) or 0)
    return "procurement_director" if total_cost >= max(0.0, threshold) else "supply_chain_manager"


def approver_bindings() -> dict[str, set[str]]:
    """Parse `email:role|role,email:role` without trusting UI-submitted roles."""
    bindings: dict[str, set[str]] = {}
    raw = os.getenv("HITL_APPROVER_BINDINGS", "")
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        email, roles = entry.split(":", 1)
        normalized_email = email.strip().lower()
        parsed_roles = {role.strip().lower() for role in roles.split("|") if role.strip()}
        if EMAIL_PATTERN.match(normalized_email) and parsed_roles:
            bindings[normalized_email] = parsed_roles
    return bindings


def validate_review_payload(
    payload: dict,
    *,
    required_role: str,
    expected_decision_hash: str,
    requested_by: str | None = None,
) -> dict:
    """Validate identity, role, action, justification and stale-form token."""
    action = str(payload.get("action", "")).strip().lower()
    email = str(payload.get("reviewer_email", "")).strip().lower()
    role = str(payload.get("reviewer_role", "")).strip().lower()
    feedback = str(payload.get("feedback", "")).strip()
    submitted_hash = str(payload.get("decision_hash", "")).strip()

    if action not in VALID_ACTIONS:
        raise ValueError("审批动作必须是 approve 或 reject")
    if not EMAIL_PATTERN.match(email):
        raise ValueError("必须提供有效的审批人邮箱")
    if requested_by and email == requested_by.strip().lower():
        raise ValueError("申请人与审批人必须分离")
    if submitted_hash != expected_decision_hash:
        raise ValueError("审批页面已过期或决策内容已变化，请重新加载审批单")
    if len(feedback) < 5:
        raise ValueError("审批意见至少需要 5 个字符，以满足审计要求")

    bindings = approver_bindings()
    configured_roles = bindings.get(email)
    if not configured_roles:
        raise PermissionError("该邮箱不在 HITL_APPROVER_BINDINGS 审批白名单中")
    if role not in configured_roles:
        raise PermissionError("提交的审批角色与服务器端授权不匹配")
    if role not in ROLE_LEVELS or required_role not in ROLE_LEVELS:
        raise PermissionError("审批角色配置无效")
    if ROLE_LEVELS[role] < ROLE_LEVELS[required_role]:
        raise PermissionError(f"该决策要求 {required_role} 或更高权限")

    return {
        "action": action,
        "reviewer_email": email,
        "reviewer_role": role,
        "feedback": feedback,
        "decision_hash": submitted_hash,
    }
