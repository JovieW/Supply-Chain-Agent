"""Versioned schemas at untrusted or persistent workflow boundaries."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


SCHEMA_VERSION = "supply-chain-agent/v2"


class StrictBoundaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowStartInput(StrictBoundaryModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    product_id: int = Field(ge=1)
    workflow_run_id: str = Field(min_length=8, max_length=64)


class ApprovalResumeInput(StrictBoundaryModel):
    action: Literal["approve", "reject"]
    reviewer_email: str = Field(min_length=5, max_length=254)
    reviewer_role: Literal["supply_chain_manager", "procurement_director", "admin"]
    feedback: str = Field(min_length=5, max_length=2000)
    decision_hash: str = Field(min_length=32, max_length=128)


class DecisionDetails(StrictBoundaryModel):
    supplier_id: Optional[int] = Field(default=None, ge=1)
    quantity: int = Field(ge=0)
    expedite: bool = False


class GovernedDecision(StrictBoundaryModel):
    decision_type: Literal["REORDER", "HOLD"]
    details: DecisionDetails
    business_case: Dict[str, Any] = Field(default_factory=dict)
    explanation: str = Field(min_length=2)
    evidence: List[str] = Field(default_factory=list)
    memory_evidence: List[int] = Field(default_factory=list)
    revision_number: int = Field(default=0, ge=0, le=3)

    @model_validator(mode="after")
    def validate_shape(self):
        if self.decision_type == "HOLD":
            if self.details.supplier_id is not None or self.details.quantity != 0 or self.details.expedite:
                raise ValueError("HOLD cannot contain supplier, quantity, or expedite")
        elif self.details.supplier_id is None or self.details.quantity <= 0:
            raise ValueError("REORDER requires a supplier and positive quantity")
        return self


class ExecutionResult(StrictBoundaryModel):
    executed: bool
    po_id: Optional[int] = None
    log_id: Optional[int] = None
    message: str
    idempotent_replay: bool = False
    idempotency_reason: Optional[str] = None
    memory_status: Optional[str] = None
    memory_error: Optional[str] = None


class ProcurementTransitionInput(StrictBoundaryModel):
    purchase_order_id: int = Field(ge=1)
    event_type: Literal[
        "SUPPLIER_CONFIRMED", "SHIPPED", "RECEIVED",
        "QC_ACCEPTED", "QC_REJECTED", "CLOSED", "CANCELLED",
    ]
    actor_email: str = Field(min_length=5, max_length=254)
    idempotency_key: str = Field(min_length=16, max_length=128)
    payload: Dict[str, Any] = Field(default_factory=dict)


class InventoryPositionToolResult(StrictBoundaryModel):
    on_hand_quantity: int
    effective_inbound_quantity: int
    excluded_late_inbound: int
    reserved_quantity: int
    backorder_quantity: int
    inventory_position: int
    days_of_supply: float
    dynamic_reorder_point: int
    action: Literal["REORDER", "HOLD"]


class ForecastToolResult(StrictBoundaryModel):
    method: str
    history_days: int = Field(ge=0)
    avg_daily_demand: float = Field(ge=0)
    forecast_daily_demand: float = Field(ge=0)
    forecast_horizon_days: int = Field(ge=1)
    forecast_horizon_quantity: int = Field(ge=0)
    demand_std_dev: float = Field(ge=0)
    coefficient_of_variation: float = Field(ge=0)
    trend_pct: float
    volatility: Literal["LOW", "MEDIUM", "HIGH"]


class InboundToolResult(StrictBoundaryModel):
    active_orders: int = Field(ge=0)
    total_inbound_quantity: int = Field(ge=0)
    exceptions: int = Field(ge=0)
    orders: List[Dict[str, Any]]


class SupplierToolResult(StrictBoundaryModel):
    recommended_supplier_id: Optional[int]
    recommended_quantity: int = Field(ge=0)
    expedite: bool
    constraints: List[str]
    options: List[Dict[str, Any]]


class KnowledgeToolResult(StrictBoundaryModel):
    mode: str
    results: List[Dict[str, Any]]


class StressToolResult(StrictBoundaryModel):
    scenario: Dict[str, Any]
    action: Literal["REORDER", "HOLD"]
    quantity: int = Field(ge=0)
    supplier_id: Optional[int]
    expedite: bool
    inventory_position: int
    days_of_supply: float
    projected_shortage: int = Field(ge=0)
    total_landed_risk_cost: float = Field(ge=0)
    constraints: List[str]


TOOL_RESPONSE_SCHEMAS = {
    "inspect_inventory_position": InventoryPositionToolResult,
    "forecast_demand": ForecastToolResult,
    "inspect_inbound_shipments": InboundToolResult,
    "compare_supplier_options": SupplierToolResult,
    "search_enterprise_knowledge": KnowledgeToolResult,
    "stress_test_plan": StressToolResult,
}


def validate_tool_response(tool_name: str, result: Any) -> dict:
    """Fail a tool call when its response violates the registered contract."""
    schema = TOOL_RESPONSE_SCHEMAS.get(tool_name)  # 该工具的规范返回
    if schema is None:
        raise ValueError(f"No response schema registered for tool: {tool_name}")
    return schema.model_validate(result).model_dump()
