from sqlalchemy import Boolean, Column, Date, Integer, String, Float, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase
from datetime import datetime


class Base(DeclarativeBase):
    """Base class for all models"""
    pass


class Product(Base):
    __tablename__ = 'products'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    sku = Column(String, unique=True, nullable=False)


class Inventory(Base):
    __tablename__ = 'inventory'
    
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, default=0)
    reorder_point = Column(Integer, default=100)


class Supplier(Base):
    __tablename__ = 'suppliers'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    lead_time_days = Column(Integer, nullable=False)
    reliability_score = Column(Float, default=1.0)


class ProductSupplier(Base):
    """Approved supplier list for each product."""

    __tablename__ = 'product_suppliers'
    product_id = Column(Integer, ForeignKey('products.id'), primary_key=True)
    supplier_id = Column(Integer, ForeignKey('suppliers.id'), primary_key=True)
    is_active = Column(Boolean, default=True, nullable=False)


class DemandHistory(Base):
    """Observed daily demand used by the deterministic forecasting model."""

    __tablename__ = 'demand_history'
    __table_args__ = (
        UniqueConstraint('product_id', 'demand_date', name='uq_demand_product_date'),
    )
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    demand_date = Column(Date, nullable=False)
    quantity = Column(Integer, nullable=False)


class InventoryPolicy(Base):
    """Product-specific planning policy owned by supply-chain operations."""

    __tablename__ = 'inventory_policies'
    product_id = Column(Integer, ForeignKey('products.id'), primary_key=True)
    service_level = Column(Float, nullable=False, default=0.95)
    review_period_days = Column(Integer, nullable=False, default=7)
    reserved_quantity = Column(Integer, nullable=False, default=0)
    backorder_quantity = Column(Integer, nullable=False, default=0)
    holding_cost_per_unit_year = Column(Float, nullable=False, default=2.0)
    stockout_cost_per_unit = Column(Float, nullable=False, default=15.0)


class SupplierOffer(Base):
    """Commercial and operational constraints for a product/supplier lane."""

    __tablename__ = 'supplier_offers'
    product_id = Column(Integer, ForeignKey('products.id'), primary_key=True)
    supplier_id = Column(Integer, ForeignKey('suppliers.id'), primary_key=True)
    unit_cost = Column(Float, nullable=False)
    min_order_quantity = Column(Integer, nullable=False, default=1)
    order_multiple = Column(Integer, nullable=False, default=1)
    max_order_quantity = Column(Integer, nullable=False, default=100000)
    freight_cost = Column(Float, nullable=False, default=0.0)
    expedited_freight_cost = Column(Float, nullable=False, default=0.0)
    expedite_lead_time_days = Column(Integer, nullable=False, default=1)
    on_time_delivery_rate = Column(Float, nullable=False, default=0.95)
    defect_rate = Column(Float, nullable=False, default=0.01)


class PurchaseOrder(Base):
    __tablename__ = 'purchase_orders'
    
    id = Column(Integer, primary_key=True)
    supplier_id = Column(Integer, ForeignKey('suppliers.id'), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False)
    quantity = Column(Integer, nullable=False)
    status = Column(String, default='pending')
    created_at = Column(DateTime, default=datetime.utcnow)


class ProcurementPlan(Base):
    """Immutable planning snapshot attached to an executed purchase order."""

    __tablename__ = 'procurement_plans'
    po_id = Column(Integer, ForeignKey('purchase_orders.id'), primary_key=True)
    decision_key = Column(String(64), unique=True, nullable=False)
    transport_mode = Column(String, nullable=False, default='standard')
    planned_lead_time_days = Column(Integer, nullable=False)
    expected_arrival = Column(DateTime, nullable=True)
    total_landed_risk_cost = Column(Float, nullable=False, default=0.0)
    decision_data = Column(String, nullable=False)


class ProcurementEvent(Base):
    """Immutable lifecycle event emitted by a procurement-system adapter."""

    __tablename__ = 'procurement_events'
    __table_args__ = (
        UniqueConstraint('idempotency_key', name='uq_procurement_event_idempotency'),
    )
    id = Column(Integer, primary_key=True)
    po_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    from_status = Column(String(32), nullable=False)
    to_status = Column(String(32), nullable=False)
    payload_data = Column(String, nullable=False, default='{}')
    actor_email = Column(String, nullable=False)
    idempotency_key = Column(String(128), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Shipment(Base):
    __tablename__ = 'shipments'
    
    id = Column(Integer, primary_key=True)
    po_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=False)
    status = Column(String, default='in_transit')
    expected_arrival = Column(DateTime)
    actual_arrival = Column(DateTime, nullable=True)


class DecisionLog(Base):
    __tablename__ = 'decision_log'
    
    id = Column(Integer, primary_key=True)
    agent_name = Column(String, nullable=False)
    decision = Column(String, nullable=False)
    reasoning = Column(String)
    timestamp = Column(DateTime, default=datetime.utcnow)

class ApprovalQueue(Base):
    """Durable human-review task linked to a resumable workflow thread."""

    __tablename__ = 'approval_queue'
    __table_args__ = (
        UniqueConstraint('workflow_run_id', name='uq_approval_workflow_run_id'),
    )
    id = Column(Integer, primary_key=True)
    decision_log_id = Column(Integer)
    workflow_run_id = Column(String(64), index=True, nullable=True)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=True)
    status = Column(String, default='pending')
    decision_data = Column(String)
    decision_hash = Column(String(64), nullable=True)
    snapshot_hash = Column(String(64), nullable=True)
    risk_level = Column(String(16), nullable=True)
    risk_score = Column(Integer, nullable=True)
    requested_by = Column(String, nullable=True)
    required_role = Column(String, nullable=True)
    requested_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    reviewer_email = Column(String, nullable=True)
    reviewer_role = Column(String, nullable=True)
    feedback = Column(String, nullable=True)


class ExecutionRecord(Base):
    """Idempotency ledger for side-effecting workflow executions."""

    __tablename__ = 'execution_records'
    __table_args__ = (UniqueConstraint('decision_key', name='uq_execution_decision_key'),)
    id = Column(Integer, primary_key=True)
    decision_key = Column(String(64), nullable=False)
    purchase_order_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=True)
    decision_log_id = Column(Integer, ForeignKey('decision_log.id'), nullable=True)
    status = Column(String, nullable=False, default='started')
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class DecisionMemory(Base):
    """Long-lived episodic memory for one governed supply-chain decision."""

    __tablename__ = 'decision_memories'
    __table_args__ = (
        UniqueConstraint('workflow_run_id', name='uq_memory_workflow_run_id'),
    )
    id = Column(Integer, primary_key=True)
    workflow_run_id = Column(String(64), nullable=False)
    product_id = Column(Integer, ForeignKey('products.id'), nullable=False, index=True)
    decision_type = Column(String(16), nullable=False)
    supplier_id = Column(Integer, ForeignKey('suppliers.id'), nullable=True)
    planned_quantity = Column(Integer, nullable=False, default=0)
    expedite = Column(Boolean, nullable=False, default=False)
    planned_lead_time_days = Column(Integer, nullable=False, default=0)
    planned_total_cost = Column(Float, nullable=False, default=0.0)
    risk_level = Column(String(16), nullable=True)
    risk_score = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default='PROPOSED')
    approval_request_id = Column(Integer, ForeignKey('approval_queue.id'), nullable=True)
    purchase_order_id = Column(Integer, ForeignKey('purchase_orders.id'), nullable=True)
    context_data = Column(String, nullable=False)
    decision_data = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OutcomeFeedback(Base):
    """Immutable operational outcome used to evaluate and retrieve a decision memory."""

    __tablename__ = 'outcome_feedback'
    __table_args__ = (
        UniqueConstraint('memory_id', name='uq_outcome_memory_id'),
    )
    id = Column(Integer, primary_key=True)
    memory_id = Column(Integer, ForeignKey('decision_memories.id'), nullable=False)
    actual_lead_time_days = Column(Integer, nullable=False, default=0)
    received_quantity = Column(Integer, nullable=False, default=0)
    stockout_units = Column(Integer, nullable=False, default=0)
    quality_reject_units = Column(Integer, nullable=False, default=0)
    actual_total_cost = Column(Float, nullable=False, default=0.0)
    service_level = Column(Float, nullable=False, default=0.0)
    outcome_score = Column(Float, nullable=False)
    outcome_label = Column(String(16), nullable=False)
    penalties_data = Column(String, nullable=False)
    lesson = Column(String, nullable=False)
    notes = Column(String, nullable=False)
    submitted_by = Column(String, nullable=False)
    submitted_at = Column(DateTime, default=datetime.utcnow, nullable=False)
