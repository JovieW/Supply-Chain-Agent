"""HTTP request contracts for the FastAPI service boundary."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import Field

from boundary_schemas import StrictBoundaryModel


class DecisionCreateRequest(StrictBoundaryModel):
    product_id: int = Field(ge=1)


class ApprovalReviewRequest(StrictBoundaryModel):
    approved: bool
    feedback: str = Field(min_length=5, max_length=2000)
    decision_hash: Optional[str] = Field(default=None, min_length=32, max_length=128)


class PurchaseOrderEventRequest(StrictBoundaryModel):
    event_type: Literal[
        "SUPPLIER_CONFIRMED", "SHIPPED", "RECEIVED",
        "QC_ACCEPTED", "QC_REJECTED", "CLOSED", "CANCELLED",
    ]
    payload: Dict[str, Any] = Field(default_factory=dict)


class OutcomeFeedbackRequest(StrictBoundaryModel):
    actual_lead_time_days: int = Field(ge=0, le=3650)
    received_quantity: int = Field(ge=0)
    stockout_units: int = Field(ge=0)
    quality_reject_units: int = Field(ge=0)
    actual_total_cost: float = Field(ge=0)
    service_level: float = Field(ge=0.0, le=1.0)
    submitted_by: str = Field(min_length=5, max_length=254)
    notes: str = Field(min_length=5, max_length=2000)


class StressTestRequest(StrictBoundaryModel):
    product_id: int = Field(ge=1)
    demand_multiplier: float = Field(ge=0.5, le=2.0)
    extra_delay_days: int = Field(ge=0, le=30)
