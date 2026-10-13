from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from threading import Lock

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

from agents import inventory_agent
from models import (
    DecisionLog,
    DecisionMemory,
    ApprovalQueue,
    DemandHistory,
    ExecutionRecord,
    Base,
    Inventory,
    InventoryPolicy,
    OutcomeFeedback,
    Product,
    ProductSupplier,
    ProcurementEvent,
    ProcurementPlan,
    PurchaseOrder,
    Shipment,
    Supplier,
    SupplierOffer,
)
from approval_policy import (
    StaleApprovalError,
    approval_expiry,
    decision_hash,
    required_approval_role,
    snapshot_hash,
    validate_review_payload,
)
from memory_rules import evaluate_outcome, rank_memories, validate_outcome_feedback
from boundary_schemas import ProcurementTransitionInput


load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///data/supply_chain.db')
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)
_schema_lock = Lock()
_execution_lock = Lock()
_approval_lock = Lock()
_memory_lock = Lock()
DEFAULT_PRODUCT_SUPPLIERS = {1: {1, 3}, 2: {1, 2}, 3: {1, 2, 3}}
DEFAULT_POLICIES = {
    1: dict(service_level=0.95, review_period_days=7, reserved_quantity=30,
            backorder_quantity=0, holding_cost_per_unit_year=2.4, stockout_cost_per_unit=18.0),
    2: dict(service_level=0.98, review_period_days=7, reserved_quantity=12,
            backorder_quantity=8, holding_cost_per_unit_year=4.8, stockout_cost_per_unit=45.0),
    3: dict(service_level=0.99, review_period_days=5, reserved_quantity=5,
            backorder_quantity=15, holding_cost_per_unit_year=7.5, stockout_cost_per_unit=80.0),
}
DEFAULT_OFFERS = {
    (1, 1): dict(unit_cost=18.5, min_order_quantity=80, order_multiple=20, max_order_quantity=600,
                 freight_cost=120, expedited_freight_cost=360, expedite_lead_time_days=2,
                 on_time_delivery_rate=0.96, defect_rate=0.008),
    (1, 3): dict(unit_cost=16.2, min_order_quantity=150, order_multiple=50, max_order_quantity=900,
                 freight_cost=180, expedited_freight_cost=520, expedite_lead_time_days=5,
                 on_time_delivery_rate=0.78, defect_rate=0.035),
    (2, 1): dict(unit_cost=42.0, min_order_quantity=50, order_multiple=10, max_order_quantity=400,
                 freight_cost=100, expedited_freight_cost=280, expedite_lead_time_days=2,
                 on_time_delivery_rate=0.96, defect_rate=0.008),
    (2, 2): dict(unit_cost=44.5, min_order_quantity=30, order_multiple=10, max_order_quantity=300,
                 freight_cost=90, expedited_freight_cost=190, expedite_lead_time_days=1,
                 on_time_delivery_rate=0.88, defect_rate=0.015),
    (3, 1): dict(unit_cost=85.0, min_order_quantity=20, order_multiple=5, max_order_quantity=150,
                 freight_cost=160, expedited_freight_cost=420, expedite_lead_time_days=2,
                 on_time_delivery_rate=0.96, defect_rate=0.008),
    (3, 2): dict(unit_cost=91.0, min_order_quantity=10, order_multiple=5, max_order_quantity=100,
                 freight_cost=140, expedited_freight_cost=260, expedite_lead_time_days=1,
                 on_time_delivery_rate=0.88, defect_rate=0.015),
    (3, 3): dict(unit_cost=78.0, min_order_quantity=50, order_multiple=10, max_order_quantity=200,
                 freight_cost=220, expedited_freight_cost=650, expedite_lead_time_days=5,
                 on_time_delivery_rate=0.78, defect_rate=0.035),
}

PROCUREMENT_TRANSITIONS = {
    "CREATED": {"SUPPLIER_CONFIRMED": "SUPPLIER_CONFIRMED", "CANCELLED": "CANCELLED"},
    "SUPPLIER_CONFIRMED": {"SHIPPED": "SHIPPED", "CANCELLED": "CANCELLED"},
    "SHIPPED": {"RECEIVED": "RECEIVED"},
    "RECEIVED": {"QC_ACCEPTED": "QC_ACCEPTED", "QC_REJECTED": "QC_REJECTED"},
    "QC_ACCEPTED": {"CLOSED": "CLOSED"},
    "QC_REJECTED": {"CLOSED": "CLOSED"},
}
STATUS_ALIASES = {
    "PENDING": "CREATED", "CONFIRMED": "SUPPLIER_CONFIRMED",
    "IN_TRANSIT": "SHIPPED",
}


@dataclass(frozen=True)
class PurchaseOrderCreation:
    po_id: int
    created: bool
    reason: str


@dataclass(frozen=True)
class DecisionLogCreation:
    log_id: int
    created: bool


def create_approval_request(decision_log_id: int, decision_data: dict) -> int:
    """Persist one durable approval task per workflow, including stale-data tokens."""
    ensure_runtime_schema()
    workflow_run_id = str(decision_data.get('workflow_run_id') or '').strip()
    if not workflow_run_id:
        raise ValueError('workflow_run_id is required for durable approval')
    decision = decision_data.get('decision') or {}
    assessment = decision_data.get('assessment') or {}
    captured_snapshot = decision_data.get('db_snapshot') or {}
    with _approval_lock:
        try:
            with get_session() as session:
                existing = session.query(ApprovalQueue).filter_by(
                    workflow_run_id=workflow_run_id
                ).first()
                if existing:
                    return existing.id
                approval = ApprovalQueue(  # 往这个表中插入数据
                    decision_log_id=decision_log_id,
                    workflow_run_id=workflow_run_id,
                    product_id=decision_data.get('product_id'),
                    status='pending',
                    decision_data=json.dumps(
                        decision_data, ensure_ascii=False, sort_keys=True, default=str
                    ),
                    decision_hash=decision_hash(decision, assessment),
                    snapshot_hash=snapshot_hash(captured_snapshot),
                    risk_level=assessment.get('level'),
                    risk_score=assessment.get('score'),
                    requested_by=str(decision_data.get('requested_by') or 'system-agent'),
                    required_role=required_approval_role(decision),
                    requested_at=datetime.utcnow(),
                    expires_at=approval_expiry(),
                )
                session.add(approval)
                session.flush()
                return approval.id
        except IntegrityError:
            with get_session() as session:
                existing = session.query(ApprovalQueue).filter_by(
                    workflow_run_id=workflow_run_id
                ).first()
                if existing:
                    return existing.id
            raise


def review_approval_request(approval_id: int, review_payload: dict) -> str:
    """Atomically authorize a review and reject expired/stale decisions."""
    ensure_runtime_schema()
    with _approval_lock:
        with get_session() as session:
            current = session.get(ApprovalQueue, approval_id)
            if not current:
                raise ValueError(f'Approval request not found: {approval_id}')
            current_status = current.status
            current_email = current.reviewer_email
            current_role = current.reviewer_role
            current_hash = current.decision_hash or ''
            current_required_role = current.required_role or 'supply_chain_manager'
            current_requested_by = current.requested_by
            current_product_id = current.product_id
            current_snapshot_hash = current.snapshot_hash
            current_expires_at = current.expires_at

        if current_status != 'pending':
            desired = str(review_payload.get('action', '')).strip().lower()
            same_completed_review = (
                desired in {'approve', 'reject'}
                and current_status == ('approved' if desired == 'approve' else 'rejected')
                and (current_email or '').lower()
                == str(review_payload.get('reviewer_email', '')).strip().lower()
                and (current_role or '').lower()
                == str(review_payload.get('reviewer_role', '')).strip().lower()
                and current_hash == str(review_payload.get('decision_hash', '')).strip()
            )
            if same_completed_review or current_status in {'stale', 'expired'}:
                return current_status.upper()
            raise ValueError(f'Approval request already closed: {current_status}')

        now = datetime.utcnow()
        if current_expires_at and current_expires_at <= now:
            with get_session() as session:
                updated = session.query(ApprovalQueue).filter_by(
                    id=approval_id, status='pending'
                ).update({'status': 'expired', 'reviewed_at': now})
                if updated != 1:
                    raise ValueError('Approval request was concurrently reviewed')
            return 'EXPIRED'

        normalized = validate_review_payload(
            review_payload,
            required_role=current_required_role,
            expected_decision_hash=current_hash,
            requested_by=current_requested_by,
        )
        latest_snapshot = (
            read_supply_chain_snapshot(int(current_product_id))
            if normalized['action'] == 'approve' else None
        )

        with get_session() as session:
            if (latest_snapshot is not None
                    and snapshot_hash(latest_snapshot) != current_snapshot_hash):
                status = 'stale'
                feedback = (
                    normalized['feedback'] + '；审批期间实时库存或在途发生变化，必须重新规划'
                )
            else:
                status = 'approved' if normalized['action'] == 'approve' else 'rejected'
                feedback = normalized['feedback']
            updated = session.query(ApprovalQueue).filter_by(
                id=approval_id, status='pending'
            ).update({
                'status': status,
                'reviewed_at': now,
                'reviewer_email': normalized['reviewer_email'],
                'reviewer_role': normalized['reviewer_role'],
                'feedback': feedback,
            })
            if updated != 1:
                raise ValueError('Approval request was concurrently reviewed')
            return status.upper()


def _approval_to_dict(approval: ApprovalQueue) -> dict:
    try:
        data = json.loads(approval.decision_data or '{}')
    except json.JSONDecodeError:
        data = {}
    stored_workflow_run_id = approval.workflow_run_id
    product_id = approval.product_id or data.get('product_id')
    return {
        'id': approval.id,
        'decision_log_id': approval.decision_log_id,
        'workflow_run_id': stored_workflow_run_id or data.get('workflow_run_id'),
        'checkpoint_thread_id': stored_workflow_run_id,
        'product_id': product_id,
        'resumable': bool(stored_workflow_run_id and data.get('state_snapshot')),
        'status': str(approval.status or 'pending').upper(),
        'decision_data': data,
        'decision_hash': approval.decision_hash,
        'snapshot_hash': approval.snapshot_hash,
        'risk_level': approval.risk_level,
        'risk_score': approval.risk_score,
        'requested_by': approval.requested_by,
        'required_role': approval.required_role,
        'requested_at': approval.requested_at.isoformat() if approval.requested_at else None,
        'expires_at': approval.expires_at.isoformat() if approval.expires_at else None,
        'reviewed_at': approval.reviewed_at.isoformat() if approval.reviewed_at else None,
        'reviewer_email': approval.reviewer_email,
        'reviewer_role': approval.reviewer_role,
        'feedback': approval.feedback,
    }


def get_approval_request(approval_id: int) -> dict:
    ensure_runtime_schema()
    with get_session() as session:
        approval = session.get(ApprovalQueue, approval_id)
        if not approval:
            raise ValueError(f'Approval request not found: {approval_id}')
        if (approval.status == 'pending' and approval.expires_at
                and approval.expires_at <= datetime.utcnow()):
            approval.status = 'expired'
            approval.reviewed_at = datetime.utcnow()
            memory = session.query(DecisionMemory).filter_by(
                approval_request_id=approval.id
            ).first()
            if memory and memory.status == 'PENDING_APPROVAL':
                memory.status = 'EXPIRED'
                memory.updated_at = datetime.utcnow()
        return _approval_to_dict(approval)


def list_approval_requests(status: str | None = None, limit: int = 100) -> list[dict]:
    """Return durable approval work items for UI/API queues."""
    ensure_runtime_schema()  # 确保数据库结构存在
    normalized_status = status.strip().lower() if status else None  # 标准化状态
    with get_session() as session:
        now = datetime.utcnow()  # 获取当前时间
        expired = session.query(ApprovalQueue).filter(  # 查找过期审批任务
            ApprovalQueue.status == 'pending',  # 找当前状态是pending
            ApprovalQueue.expires_at.is_not(None),  # 有截止日期
            ApprovalQueue.expires_at <= now,  # 且已经超过戒指日期
        ).all()
        for approval in expired:  # 标记过期任务
            approval.status = 'expired'  # 修改状态
            approval.reviewed_at = now  # 记录审核时间
            memory = session.query(DecisionMemory).filter_by(  # 同步decisionMemory状态
                approval_request_id=approval.id
            ).first()
            if memory and memory.status == 'PENDING_APPROVAL':
                memory.status = 'EXPIRED'
                memory.updated_at = now
        query = session.query(ApprovalQueue)  # 创建查询
        if normalized_status:  # 根据状态过滤
            query = query.filter(ApprovalQueue.status == normalized_status)
        if normalized_status == 'pending':  # 过滤：如果查询待审批，就必须有workflow_run_id
            query = query.filter(ApprovalQueue.workflow_run_id.is_not(None))
        rows = query.order_by(ApprovalQueue.requested_at.desc()).limit(
            max(1, min(500, int(limit)))
        ).all()
        return [_approval_to_dict(row) for row in rows]


def assert_approval_authorizes_execution(
    approval_id: int,
    workflow_run_id: str,
    expected_decision_hash: str,
    latest_snapshot: dict | None = None,
) -> None:
    """Validate approval again immediately before the governed side effect."""
    approval = get_approval_request(approval_id)
    if approval['status'] != 'APPROVED':
        raise PermissionError('A completed approval is required before execution')
    if approval['workflow_run_id'] != workflow_run_id:
        raise PermissionError('Approval belongs to a different workflow')
    if approval['decision_hash'] != expected_decision_hash:
        raise PermissionError('Approved decision content does not match execution content')
    if latest_snapshot is not None and snapshot_hash(latest_snapshot) != approval['snapshot_hash']:
        now = datetime.utcnow()
        with _approval_lock:
            with get_session() as session:
                updated = session.query(ApprovalQueue).filter_by(
                    id=approval_id, status='approved'
                ).update({
                    'status': 'stale',
                    'reviewed_at': now,
                    'feedback': (
                        f"{approval.get('feedback') or ''}；"
                        "审批完成后业务数据再次变化，执行前复核已阻断旧方案"
                    ).strip('；'),
                })
                if updated != 1:
                    raise PermissionError('Approval changed concurrently before execution')
        raise StaleApprovalError(
            'Approval became stale before execution; regenerate and approve a fresh plan'
        )


def create_decision_memory(state: dict) -> int:
    """Persist the proposed decision once before execution or human interruption."""
    ensure_runtime_schema()
    workflow_run_id = str(state.get('workflow_run_id') or '').strip()
    if not workflow_run_id:
        raise ValueError('workflow_run_id is required for long-term memory')
    decision = state.get('final_decision') or {}
    details = decision.get('details') or {}
    assessment = state.get('decision_assessment') or {}
    outputs = state.get('agent_outputs') or {}
    demand = outputs.get('demand') or {}
    inventory = outputs.get('inventory') or {}
    logistics = outputs.get('logistics') or {}
    product_id = int(state.get('product_id'))
    context = {
        'product': (state.get('db_snapshot') or {}).get('product', {}),
        'demand_volatility': (demand.get('forecast') or {}).get('volatility'),
        'forecast_daily_demand': (demand.get('forecast') or {}).get('forecast_daily_demand'),
        'target_service_level': (demand.get('planning') or {}).get('service_level'),
        'inventory_position': inventory.get('inventory_position'),
        'days_of_supply': inventory.get('days_of_supply'),
        'projected_shortage': logistics.get('projected_shortage'),
        'constraints': inventory.get('constraints', []),
        'retrieved_memory_ids': [
            item.get('id') for item in (state.get('long_term_memory') or {}).get('recalled', [])
        ],
    }
    status = 'PENDING_APPROVAL' if assessment.get('approval_required') else 'PROPOSED'
    with _memory_lock:  # 锁 防止并发写入
        try:
            with get_session() as session:
                existing = session.query(DecisionMemory).filter_by(
                    workflow_run_id=workflow_run_id
                ).first()
                if existing:
                    return existing.id
                memory = DecisionMemory(
                    workflow_run_id=workflow_run_id,
                    product_id=product_id,
                    decision_type=str(decision.get('decision_type') or 'UNKNOWN'),
                    supplier_id=details.get('supplier_id'),
                    planned_quantity=int(details.get('quantity') or 0),
                    expedite=bool(details.get('expedite', False)),
                    planned_lead_time_days=int(
                        (decision.get('business_case') or {}).get('lead_time_days') or 0
                    ),
                    planned_total_cost=float(
                        (decision.get('business_case') or {}).get('total_landed_risk_cost') or 0
                    ),
                    risk_level=assessment.get('level'),
                    risk_score=assessment.get('score'),
                    status=status,
                    context_data=json.dumps(context, ensure_ascii=False, sort_keys=True, default=str),
                    decision_data=json.dumps(decision, ensure_ascii=False, sort_keys=True, default=str),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                )
                session.add(memory)
                session.flush()
                return memory.id
        except IntegrityError:
            with get_session() as session:
                existing = session.query(DecisionMemory).filter_by(
                    workflow_run_id=workflow_run_id
                ).first()
                if existing:
                    return existing.id
            raise


def update_decision_memory_status(
    memory_id: int | None,
    status: str,
    *,
    approval_request_id: int | None = None,
    purchase_order_id: int | None = None,
) -> None:
    if not memory_id:
        return
    allowed = {
        'PROPOSED', 'PENDING_APPROVAL', 'APPROVED', 'REJECTED', 'STALE',
        'EXPIRED', 'EXECUTED', 'EXECUTION_FAILED', 'OUTCOME_RECORDED',
    }
    normalized = str(status).strip().upper()
    if normalized not in allowed:
        raise ValueError(f'Unsupported memory status: {status}')
    ensure_runtime_schema()
    with get_session() as session:
        memory = session.get(DecisionMemory, int(memory_id))
        if not memory:
            raise ValueError(f'Decision memory not found: {memory_id}')
        memory.status = normalized
        if approval_request_id is not None:
            memory.approval_request_id = approval_request_id
        if purchase_order_id is not None:
            memory.purchase_order_id = purchase_order_id
        memory.updated_at = datetime.utcnow()


def _outcome_to_dict(outcome: OutcomeFeedback | None) -> dict | None:
    if outcome is None:
        return None
    try:
        penalties = json.loads(outcome.penalties_data or '[]')
    except json.JSONDecodeError:
        penalties = []
    return {
        'id': outcome.id,
        'actual_lead_time_days': outcome.actual_lead_time_days,
        'received_quantity': outcome.received_quantity,
        'stockout_units': outcome.stockout_units,
        'quality_reject_units': outcome.quality_reject_units,
        'actual_total_cost': outcome.actual_total_cost,
        'service_level': outcome.service_level,
        'outcome_score': outcome.outcome_score,
        'outcome_label': outcome.outcome_label,
        'penalties': penalties,
        'lesson': outcome.lesson,
        'notes': outcome.notes,
        'submitted_by': outcome.submitted_by,
        'submitted_at': outcome.submitted_at.isoformat() if outcome.submitted_at else None,
    }


def _memory_to_dict(
    memory: DecisionMemory,
    outcome: OutcomeFeedback | None = None,
    purchase_order_status: str | None = None,
) -> dict:
    try:
        context = json.loads(memory.context_data or '{}')
    except json.JSONDecodeError:
        context = {}
    try:
        decision = json.loads(memory.decision_data or '{}')
    except json.JSONDecodeError:
        decision = {}
    return {
        'id': memory.id,
        'workflow_run_id': memory.workflow_run_id,
        'product_id': memory.product_id,
        'decision_type': memory.decision_type,
        'supplier_id': memory.supplier_id,
        'planned_quantity': memory.planned_quantity,
        'expedite': memory.expedite,
        'planned_lead_time_days': memory.planned_lead_time_days,
        'planned_total_cost': memory.planned_total_cost,
        'risk_level': memory.risk_level,
        'risk_score': memory.risk_score,
        'status': memory.status,
        'approval_request_id': memory.approval_request_id,
        'purchase_order_id': memory.purchase_order_id,
        'purchase_order_status': purchase_order_status,
        'context': context,
        'decision': decision,
        'created_at': memory.created_at.isoformat() if memory.created_at else None,
        'updated_at': memory.updated_at.isoformat() if memory.updated_at else None,
        'outcome': _outcome_to_dict(outcome),
    }


def list_decision_memories(
    product_id: int | None = None, *, limit: int = 100
) -> list[dict]:
    ensure_runtime_schema()
    with get_session() as session:
        query = session.query(DecisionMemory)  # 查询
        if product_id is not None:
            query = query.filter(DecisionMemory.product_id == int(product_id))
        memories = query.order_by(DecisionMemory.created_at.desc()).limit(  # 按时间排序
            max(1, min(500, int(limit)))
        ).all()
        memory_ids = [memory.id for memory in memories]
        outcomes = {  # 结果怎么样
            outcome.memory_id: outcome
            for outcome in session.query(OutcomeFeedback).filter(
                OutcomeFeedback.memory_id.in_(memory_ids)
            ).all()
        } if memory_ids else {}
        po_ids = [memory.purchase_order_id for memory in memories if memory.purchase_order_id]  # 关联采购单的状态
        po_statuses = {
            order.id: _normalized_po_status(order.status)
            for order in session.query(PurchaseOrder).filter(PurchaseOrder.id.in_(po_ids)).all()
        } if po_ids else {}
        return [
            _memory_to_dict(
                memory, outcomes.get(memory.id), po_statuses.get(memory.purchase_order_id)
            )
            for memory in memories
        ]


def recall_decision_memories(current_context: dict, limit: int | None = None) -> list[dict]:
    """Retrieve evaluated episodes only; unresolved proposals cannot influence agents."""
    memories = list_decision_memories(limit=300)  # 读取最近的300条历史决策
    return rank_memories(current_context, memories, limit=limit)


def record_outcome_feedback(memory_id: int, payload: dict) -> dict:
    """Close the plan-versus-actual loop exactly once and derive a reusable lesson."""
    ensure_runtime_schema()
    with get_session() as session:
        memory = session.get(DecisionMemory, int(memory_id))
        if not memory:
            raise ValueError(f'Decision memory not found: {memory_id}')
        if session.query(OutcomeFeedback).filter_by(memory_id=memory.id).first():
            raise ValueError('该决策已经提交过最终结果反馈，不能重复覆盖')
        if memory.status not in {'EXECUTED', 'EXECUTION_FAILED'}:
            raise ValueError('只有已经执行的决策可以录入结果反馈')
        if memory.decision_type == 'REORDER':
            order = session.get(PurchaseOrder, memory.purchase_order_id)
            if not order or _normalized_po_status(order.status) != 'CLOSED':
                raise ValueError('补货决策必须完成到货、质检并关闭采购单后才能录入最终结果')
        normalized = validate_outcome_feedback(payload, memory.planned_quantity)
        evaluation = evaluate_outcome(_memory_to_dict(memory), normalized)
        outcome = OutcomeFeedback(
            memory_id=memory.id,
            actual_lead_time_days=normalized['actual_lead_time_days'],
            received_quantity=normalized['received_quantity'],
            stockout_units=normalized['stockout_units'],
            quality_reject_units=normalized['quality_reject_units'],
            actual_total_cost=normalized['actual_total_cost'],
            service_level=normalized['service_level'],
            outcome_score=evaluation['outcome_score'],
            outcome_label=evaluation['outcome_label'],
            penalties_data=json.dumps(evaluation['penalties'], ensure_ascii=False),
            lesson=evaluation['lesson'],
            notes=normalized['notes'],
            submitted_by=normalized['submitted_by'],
            submitted_at=datetime.utcnow(),
        )
        session.add(outcome)
        memory.status = 'OUTCOME_RECORDED'
        memory.updated_at = datetime.utcnow()
        session.flush()
        return _memory_to_dict(memory, outcome)


@contextmanager
def get_session():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_runtime_schema() -> None:
    """Create additive runtime tables/columns and safely backfill master data."""
    with _schema_lock:
        Base.metadata.create_all(engine)
        _ensure_approval_columns()
        with get_session() as session:
            product_ids = {row.id for row in session.query(Product).all()}
            supplier_ids = {row.id for row in session.query(Supplier).all()}
            existing_mappings = {
                (row.product_id, row.supplier_id) for row in session.query(ProductSupplier).all()
            }
            for product_id, eligible_ids in DEFAULT_PRODUCT_SUPPLIERS.items():
                for supplier_id in eligible_ids:
                    if (product_id in product_ids and supplier_id in supplier_ids
                            and (product_id, supplier_id) not in existing_mappings):
                        session.add(ProductSupplier(product_id=product_id, supplier_id=supplier_id))
            for product_id in product_ids:
                if not session.get(InventoryPolicy, product_id):
                    session.add(InventoryPolicy(product_id=product_id, **DEFAULT_POLICIES.get(
                        product_id, DEFAULT_POLICIES[1])))
            session.flush()
            mappings = session.query(ProductSupplier).filter(ProductSupplier.is_active.is_(True)).all()
            for mapping in mappings:
                key = (mapping.product_id, mapping.supplier_id)
                if not session.get(SupplierOffer, key):
                    values = DEFAULT_OFFERS.get(key)
                    if values:
                        session.add(SupplierOffer(product_id=key[0], supplier_id=key[1], **values))
            history_products = {row[0] for row in session.query(DemandHistory.product_id).distinct().all()}
            today = date.today()
            bases = {1: 12, 2: 19, 3: 9}
            for product_id in product_ids - history_products:
                base = bases.get(product_id, 10)
                for days_ago in range(56, 0, -1):
                    weekly = (days_ago % 7) - 3
                    trend = (56 - days_ago) // 14
                    volatility = ((days_ago * (product_id + 3)) % (3 + product_id)) - 1
                    session.add(DemandHistory(
                        product_id=product_id,
                        demand_date=today - timedelta(days=days_ago),
                        quantity=max(0, base + weekly // 2 + trend + volatility),
                    ))


def _ensure_approval_columns() -> None:
    """Add P4 columns to existing SQLite databases without deleting user data."""
    if 'approval_queue' not in inspect(engine).get_table_names():
        return
    existing = {column['name'] for column in inspect(engine).get_columns('approval_queue')}
    additions = {
        'workflow_run_id': 'VARCHAR(64)',
        'product_id': 'INTEGER',
        'decision_hash': 'VARCHAR(64)',
        'snapshot_hash': 'VARCHAR(64)',
        'risk_level': 'VARCHAR(16)',
        'risk_score': 'INTEGER',
        'requested_by': 'VARCHAR',
        'required_role': 'VARCHAR',
        'expires_at': 'DATETIME',
        'reviewer_role': 'VARCHAR',
    }
    missing = [(name, sql_type) for name, sql_type in additions.items() if name not in existing]
    with engine.begin() as connection:
        for name, sql_type in missing:
            connection.execute(text(
                f'ALTER TABLE approval_queue ADD COLUMN {name} {sql_type}'
            ))
        if engine.dialect.name == 'sqlite':
            connection.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS uq_approval_workflow_run_id '
                'ON approval_queue (workflow_run_id) WHERE workflow_run_id IS NOT NULL'
            ))

# 读取数据库快照
def read_supply_chain_snapshot(product_id: int | None = None) -> dict:
    """Read a product-scoped snapshot with only approved supplier candidates."""
    # 1. 初始化schema 并开启数据库session
    ensure_runtime_schema()
    with get_session() as session:
        query = session.query(Product)

         # 2. 定位产品并检查库存是否合法
        product = query.filter_by(id=product_id).first() if product_id else query.first()
        if not product:
            raise ValueError(f"Product not found: {product_id}")

        inventory = session.query(Inventory).filter_by(product_id=product.id).first()
        if not inventory:
            raise ValueError(f"Inventory not found for product: {product.id}")

        # 3. 只读取已批准且激活的供应商
        suppliers = (
            session.query(Supplier)
            .join(ProductSupplier, ProductSupplier.supplier_id == Supplier.id)
            .filter(
                ProductSupplier.product_id == product.id,
                ProductSupplier.is_active.is_(True),
            )
            .order_by(Supplier.id)
            .all()
        )
        if not suppliers:
            raise ValueError(f"No approved suppliers configured for product: {product.id}")

        # 4. 查询有效采购单与在途运输
        purchase_orders = session.query(PurchaseOrder).filter(
            PurchaseOrder.product_id == product.id,
            PurchaseOrder.status.in_([
                'pending', 'confirmed',
                'CREATED', 'SUPPLIER_CONFIRMED', 'SHIPPED',
            ]),
        ).all()
        po_ids = [po.id for po in purchase_orders]
        plans = {
            plan.po_id: plan
            for plan in session.query(ProcurementPlan).filter(
                ProcurementPlan.po_id.in_(po_ids)
            ).all()
        } if po_ids else {}
        shipments = (
            session.query(Shipment).filter(
                Shipment.po_id.in_(po_ids), Shipment.status == 'in_transit'
            ).all()
            if po_ids
            else []
        )
        policy = session.get(InventoryPolicy, product.id)
        history = session.query(DemandHistory).filter_by(product_id=product.id).order_by(
            DemandHistory.demand_date
        ).all()
        offers = {
            row.supplier_id: row
            for row in session.query(SupplierOffer).filter_by(product_id=product.id).all()
        }

        return {
            'product': {'id': product.id, 'name': product.name, 'sku': product.sku},
            'inventory': {
                'quantity': inventory.quantity,
                'reorder_point': inventory.reorder_point,
            },
            'inventory_policy': {
                'service_level': policy.service_level,
                'review_period_days': policy.review_period_days,
                'reserved_quantity': policy.reserved_quantity,
                'backorder_quantity': policy.backorder_quantity,
                'holding_cost_per_unit_year': policy.holding_cost_per_unit_year,
                'stockout_cost_per_unit': policy.stockout_cost_per_unit,
            },
            'demand_history': [
                {'date': row.demand_date.isoformat(), 'quantity': row.quantity}
                for row in history
            ],
            'suppliers': [
                {
                    'id': supplier.id,
                    'name': supplier.name,
                    'lead_time_days': supplier.lead_time_days,
                    'reliability_score': supplier.reliability_score,
                    'unit_cost': offers[supplier.id].unit_cost,
                    'min_order_quantity': offers[supplier.id].min_order_quantity,
                    'order_multiple': offers[supplier.id].order_multiple,
                    'max_order_quantity': offers[supplier.id].max_order_quantity,
                    'freight_cost': offers[supplier.id].freight_cost,
                    'expedited_freight_cost': offers[supplier.id].expedited_freight_cost,
                    'expedite_lead_time_days': offers[supplier.id].expedite_lead_time_days,
                    'on_time_delivery_rate': offers[supplier.id].on_time_delivery_rate,
                    'defect_rate': offers[supplier.id].defect_rate,
                }
                for supplier in suppliers if supplier.id in offers
            ],
            'purchase_orders': [
                {
                    'id': po.id,
                    'supplier_id': po.supplier_id,
                    'quantity': po.quantity,
                    'status': po.status,
                    'created_at': po.created_at.isoformat(),
                    'expected_arrival': next((
                        sh.expected_arrival.isoformat() for sh in shipments
                        if sh.po_id == po.id and sh.expected_arrival
                    ), (
                        plans[po.id].expected_arrival.isoformat()
                        if po.id in plans and plans[po.id].expected_arrival else None
                    )),
                }
                for po in purchase_orders
            ],
            'shipments': [
                {
                    'id': shipment.id,
                    'po_id': shipment.po_id,
                    'status': shipment.status,
                    'expected_arrival': (
                        shipment.expected_arrival.isoformat()
                        if shipment.expected_arrival
                        else None
                    ),
                }
                for shipment in shipments
            ],
        }


def create_purchase_order(
    supplier_id: int,
    product_id: int,
    quantity: int,
    decision_key: str,
    plan_data: dict | None = None,
) -> PurchaseOrderCreation:
    """Create one approved PO exactly once for a workflow decision."""
    if quantity <= 0:
        raise ValueError("Purchase order quantity must be positive")
    if not decision_key:
        raise ValueError("decision_key is required for idempotent execution")

    ensure_runtime_schema()
    with _execution_lock:
        return _create_purchase_order_locked(
            supplier_id=supplier_id,
            product_id=product_id,
            quantity=quantity,
            decision_key=decision_key,
            plan_data=plan_data,
        )


def _create_purchase_order_locked(
    supplier_id: int,
    product_id: int,
    quantity: int,
    decision_key: str,
    plan_data: dict | None = None,
) -> PurchaseOrderCreation:
    with get_session() as session:
        eligible = session.query(ProductSupplier).filter_by(
            product_id=product_id,
            supplier_id=supplier_id,
            is_active=True,
        ).first()
        if not eligible:
            raise ValueError(
                f"Supplier {supplier_id} is not approved for product {product_id}"
            )

        previous = session.query(ExecutionRecord).filter_by(
            decision_key=decision_key
        ).first()
        if previous and previous.purchase_order_id:
            return PurchaseOrderCreation(
                po_id=previous.purchase_order_id,
                created=False,
                reason='decision_already_executed',
            )

        record = previous or ExecutionRecord(decision_key=decision_key, status='started')
        session.add(record)
        session.flush()
        po = PurchaseOrder(
            supplier_id=supplier_id,
            product_id=product_id,
            quantity=quantity,
            status='CREATED',
            created_at=datetime.utcnow(),
        )
        session.add(po)
        session.flush()
        session.add(ProcurementEvent(
            po_id=po.id,
            event_type='CREATED',
            from_status='NONE',
            to_status='CREATED',
            payload_data=json.dumps(
                {'decision_key': decision_key}, ensure_ascii=False, sort_keys=True
            ),
            actor_email='system-agent',
            idempotency_key=f'{decision_key}:created',
        ))
        if plan_data:
            lead_time = max(1, int(plan_data.get('lead_time_days', 1)))
            session.add(ProcurementPlan(
                po_id=po.id,
                decision_key=decision_key,
                transport_mode='expedite' if plan_data.get('expedite') else 'standard',
                planned_lead_time_days=lead_time,
                expected_arrival=datetime.utcnow() + timedelta(days=lead_time),
                total_landed_risk_cost=float(plan_data.get('total_landed_risk_cost', 0)),
                decision_data=json.dumps(plan_data, ensure_ascii=False, sort_keys=True),
            ))
        record.purchase_order_id = po.id
        record.status = 'completed'
        return PurchaseOrderCreation(po_id=po.id, created=True, reason='created')


def _normalized_po_status(value: str | None) -> str:
    normalized = str(value or "CREATED").upper()
    return STATUS_ALIASES.get(normalized, normalized)


def transition_purchase_order(
    purchase_order_id: int,
    event_type: str,
    actor_email: str,
    idempotency_key: str,
    payload: dict | None = None,
) -> dict:
    """Apply one idempotent lifecycle transition in the local demo adapter."""
    command = ProcurementTransitionInput.model_validate({
        "purchase_order_id": purchase_order_id,
        "event_type": event_type,
        "actor_email": actor_email,
        "idempotency_key": idempotency_key,
        "payload": payload or {},
    })
    ensure_runtime_schema()
    with _execution_lock:
        with get_session() as session:
            existing = session.query(ProcurementEvent).filter_by(
                idempotency_key=command.idempotency_key
            ).first()
            if existing:
                return {
                    "event_id": existing.id, "purchase_order_id": existing.po_id,
                    "status": existing.to_status, "created": False,
                    "message": "幂等保护：该履约事件已处理",
                }
            order = session.get(PurchaseOrder, command.purchase_order_id)
            if not order:
                raise ValueError(f"Purchase order not found: {command.purchase_order_id}")
            current = _normalized_po_status(order.status)
            target = PROCUREMENT_TRANSITIONS.get(current, {}).get(command.event_type)
            if not target:
                raise ValueError(f"Invalid procurement transition: {current} -> {command.event_type}")

            payload_data = command.payload
            shipment = session.query(Shipment).filter_by(po_id=order.id).order_by(
                Shipment.id.desc()
            ).first()
            if command.event_type == "SHIPPED":
                eta_raw = payload_data.get("expected_arrival")
                try:
                    eta = datetime.fromisoformat(str(eta_raw))
                except (TypeError, ValueError) as exc:
                    raise ValueError("SHIPPED requires ISO expected_arrival") from exc
                if shipment is None:
                    shipment = Shipment(po_id=order.id, status='in_transit', expected_arrival=eta)
                    session.add(shipment)
                else:
                    shipment.status, shipment.expected_arrival = 'in_transit', eta
            elif command.event_type == "RECEIVED":
                received = int(payload_data.get("received_quantity", -1))
                if received < 0 or received > int(order.quantity):
                    raise ValueError("received_quantity must be between 0 and ordered quantity")
                if shipment is None:
                    raise ValueError("Cannot receive an order before shipment")
                shipment.status = 'received'
                shipment.actual_arrival = datetime.utcnow()
            elif command.event_type == "QC_ACCEPTED":
                accepted = int(payload_data.get("accepted_quantity", -1))
                received_event = session.query(ProcurementEvent).filter_by(
                    po_id=order.id, event_type='RECEIVED'
                ).order_by(ProcurementEvent.created_at.desc()).first()
                received_quantity = int(json.loads(
                    received_event.payload_data if received_event else '{}'
                ).get('received_quantity', 0))
                if accepted < 0 or accepted > received_quantity:
                    raise ValueError("accepted_quantity must be between 0 and received quantity")
                inventory = session.query(Inventory).filter_by(product_id=order.product_id).one()
                inventory.quantity = int(inventory.quantity) + accepted
            elif command.event_type == "QC_REJECTED":
                rejected = int(payload_data.get("rejected_quantity", -1))
                received_event = session.query(ProcurementEvent).filter_by(
                    po_id=order.id, event_type='RECEIVED'
                ).order_by(ProcurementEvent.created_at.desc()).first()
                received_quantity = int(json.loads(
                    received_event.payload_data if received_event else '{}'
                ).get('received_quantity', 0))
                if rejected <= 0 or rejected > received_quantity:
                    raise ValueError("rejected_quantity must be positive and not exceed received quantity")

            order.status = target
            event = ProcurementEvent(
                po_id=order.id,
                event_type=command.event_type,
                from_status=current,
                to_status=target,
                payload_data=json.dumps(payload_data, ensure_ascii=False, sort_keys=True, default=str),
                actor_email=command.actor_email,
                idempotency_key=command.idempotency_key,
            )
            session.add(event)
            session.flush()
            return {
                "event_id": event.id, "purchase_order_id": order.id,
                "status": target, "created": True,
                "message": f"采购单已从 {current} 更新为 {target}",
            }


def list_purchase_orders(product_id: int | None = None) -> list[dict]:
    """Return purchase orders with immutable lifecycle events for the operator UI."""
    ensure_runtime_schema()
    with get_session() as session:
        query = session.query(PurchaseOrder)
        if product_id is not None:
            query = query.filter(PurchaseOrder.product_id == int(product_id))
        orders = query.order_by(PurchaseOrder.created_at.desc()).limit(200).all()
        result = []
        for order in orders:
            events = session.query(ProcurementEvent).filter_by(po_id=order.id).order_by(
                ProcurementEvent.created_at.asc()
            ).all()
            result.append({
                "id": order.id, "product_id": order.product_id,
                "supplier_id": order.supplier_id, "quantity": order.quantity,
                "status": _normalized_po_status(order.status),
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "events": [{
                    "id": event.id, "event_type": event.event_type,
                    "from_status": event.from_status, "to_status": event.to_status,
                    "actor_email": event.actor_email,
                    "created_at": event.created_at.isoformat() if event.created_at else None,
                    "payload": json.loads(event.payload_data or '{}'),
                } for event in events],
            })
        return result


def log_decision(agent_name: str, decision: str, reasoning: str) -> int:
    with get_session() as session:
        log_entry = DecisionLog(
            agent_name=agent_name,
            decision=decision,
            reasoning=reasoning,
            timestamp=datetime.utcnow(),
        )
        session.add(log_entry)
        session.flush()
        return log_entry.id


def log_decision_once(
    decision_key: str,
    agent_name: str,
    decision: str,
    reasoning: str,
) -> DecisionLogCreation:
    """Write a workflow decision log once while sharing the execution ledger."""
    if not decision_key:
        raise ValueError("decision_key is required for idempotent logging")
    ensure_runtime_schema()
    with _execution_lock:  # 执行锁：防止并发重复写
        with get_session() as session:  # 查询是否执行过
            record = session.query(ExecutionRecord).filter_by(
                decision_key=decision_key
            ).first()
            if record and record.decision_log_id:
                return DecisionLogCreation(log_id=record.decision_log_id, created=False)
            record = record or ExecutionRecord(  # 创建执行
                decision_key=decision_key, status='started'
            )
            session.add(record)
            log_entry = DecisionLog(
                agent_name=agent_name,
                decision=decision,
                reasoning=reasoning,
                timestamp=datetime.utcnow(),
            )
            session.add(log_entry)
            session.flush()
            record.decision_log_id = log_entry.id
            if record.status == 'started':
                record.status = 'completed'
            return DecisionLogCreation(log_id=log_entry.id, created=True)
