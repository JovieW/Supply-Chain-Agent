from state import SupplyChainState
from db_service import (
    assert_approval_authorizes_execution,
    log_decision_once,
    read_supply_chain_snapshot,
    update_decision_memory_status,
)
from approval_policy import StaleApprovalError, decision_hash
from business_rules import calculate_supply_chain_plan
from boundary_schemas import ExecutionResult, GovernedDecision
from observability import record_event
from procurement_adapter import get_procurement_adapter
import hashlib
import json


def execution_node(state: SupplyChainState) -> dict:
    """
    Execute the final decision by writing to the database.
    
    This is a deterministic execution node - NO LLM.
    This is the ONLY node (besides data ingestion) that writes to the database.
    
    Responsibilities:
    1. Create purchase order if decision is REORDER
    2. Log the decision and reasoning to decision_log table
    
    Args:
        state: Current graph state with final_decision
        
    Returns:
        Partial state update with execution status
    """
    final_decision = state.get('final_decision', {})
    snapshot = state['db_snapshot']
    
    # Extract decision details
    decision_type = final_decision.get('decision_type', 'HOLD')
    details = final_decision.get('details', {})
    explanation = final_decision.get('explanation', '暂无决策说明')
    
    supplier_id = details.get('supplier_id')
    quantity = details.get('quantity', 0)
    expedite = details.get('expedite', False)
    product_id = snapshot['product']['id']
    
    execution_result = {
        'executed': False,
        'po_id': None,
        'log_id': None,
        'message': ''
    }
    
    execution_error = None
    try:
        latest_snapshot = read_supply_chain_snapshot(int(product_id))
        validate_execution_authority(state, latest_snapshot=latest_snapshot)
        validate_final_decision(state, snapshot=latest_snapshot)
        record_event(
            str(state.get('workflow_run_id') or ''),
            'pre_execution_revalidation_passed',
            product_id=product_id,
            approval_request_id=state.get('approval_request_id'),
        )
        decision_key = build_decision_key(state)
        # Execute REORDER decision
        if decision_type == 'REORDER' and supplier_id and quantity > 0:
            # Create purchase order in database
            creation = get_procurement_adapter().create_order(
                supplier_id=supplier_id,
                product_id=product_id,
                quantity=quantity,
                decision_key=decision_key,
                plan_data={
                    **final_decision.get('business_case', {}),
                    'expedite': expedite,
                    'supplier_id': supplier_id,
                    'quantity': quantity,
                    'forecast': state.get('agent_outputs', {}).get('demand', {}).get('forecast', {}),
                },
            )
            po_id = creation.po_id
            
            # Log the decision
            expedite_label = "是" if expedite else "否"
            decision_text = (
                f"补货：向供应商 {supplier_id} 采购 {quantity} 件，"
                f"是否加急={expedite_label}"
            )
            log_creation = log_decision_once(
                decision_key=decision_key,
                agent_name='coordinator',
                decision=decision_text,
                reasoning=explanation,
            )
            log_id = log_creation.log_id
            
            execution_result = {
                'executed': True,
                'po_id': po_id,
                'log_id': log_id,
                'message': (
                    f'采购单 #{po_id} 已创建'
                    if creation.created
                    else f'幂等保护：复用采购单 #{po_id}，未重复创建'
                ),
                'idempotent_replay': not creation.created,
                'idempotency_reason': creation.reason,
            }
        
        # Execute HOLD decision (just log, no PO)
        elif decision_type == 'HOLD':
            # Log the decision to hold
            log_creation = log_decision_once(
                decision_key=decision_key,
                agent_name='coordinator',
                decision='暂不补货：当前无需创建采购单',
                reasoning=explanation
            )
            
            execution_result = {
                'executed': True,
                'po_id': None,
                'log_id': log_creation.log_id,
                'message': (
                    '已记录暂不补货决策，未创建采购单'
                    if log_creation.created
                    else '幂等保护：暂不补货决策已记录，未重复写入'
                ),
                'idempotent_replay': not log_creation.created,
            }
        
        else:
            # Invalid decision state
            execution_result = {
                'executed': False,
                'po_id': None,
                'log_id': None,
                'message': f'无效决策：类型={decision_type}，供应商={supplier_id}，数量={quantity}'
            }
    
    except Exception as e:
        execution_error = e
        record_event(
            str(state.get('workflow_run_id') or ''),
            'execution_blocked',
            product_id=product_id,
            approval_request_id=state.get('approval_request_id'),
            error=f'{type(e).__name__}: {e}',
        )
        # Handle execution errors
        execution_result = {
            'executed': False,
            'po_id': None,
            'log_id': None,
            'message': f'执行失败：{str(e)}'
        }
    
    try:
        memory_status = (
            "EXECUTED" if execution_result.get("executed")
            else "STALE" if isinstance(execution_error, StaleApprovalError)
            else "EXECUTION_FAILED"
        )
        update_decision_memory_status(
            state.get("decision_memory_id"),
            memory_status,
            approval_request_id=state.get("approval_request_id"),
            purchase_order_id=execution_result.get("po_id"),
        )
        execution_result["memory_status"] = memory_status
    except Exception as exc:
        # Procurement idempotency remains authoritative even if memory synchronization fails.
        execution_result["memory_status"] = "SYNC_FAILED"
        execution_result["memory_error"] = f"{type(exc).__name__}: {exc}"

    execution_result = ExecutionResult.model_validate(execution_result).model_dump()

    # Return execution status (could be logged or displayed)
    return {
        'agent_outputs': {
            **state['agent_outputs'],
            'execution': execution_result
        }
    }


def validate_execution_authority(
    state: SupplyChainState, latest_snapshot: dict | None = None
) -> None:
    """Require a matching durable approval whenever the policy gate demanded one."""
    assessment = state.get('decision_assessment') or {}
    if not assessment.get('approval_required', False):
        return
    if state.get('human_feedback') != 'APPROVED':
        raise PermissionError('The risk gate requires explicit human approval')
    approval_id = state.get('approval_request_id')
    if not approval_id:
        raise PermissionError('approval_request_id is required for governed execution')
    assert_approval_authorizes_execution(
        int(approval_id),
        str(state.get('workflow_run_id') or ''),
        decision_hash(state.get('final_decision') or {}, assessment),
        latest_snapshot=latest_snapshot,
    )


def build_decision_key(state: SupplyChainState) -> str:
    run_id = state.get('workflow_run_id')
    if not run_id:
        raise ValueError('workflow_run_id is required for idempotent execution')
    payload = {
        'workflow_run_id': run_id,
        'product_id': state.get('product_id'),
        'final_decision': state.get('final_decision'),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def validate_final_decision(
    state: SupplyChainState, snapshot: dict | None = None
) -> None:
    """Block malformed or LLM-altered decisions before any database write."""
    decision = state.get('final_decision') or {}
    details = decision.get('details') or {}
    decision_type = decision.get('decision_type')
    inventory_output = state.get('agent_outputs', {}).get('inventory', {})
    if snapshot is not None:
        live_plan = calculate_supply_chain_plan(snapshot)
        expected_action = live_plan.get('action')
        expected_quantity = live_plan.get('quantity')
        expected_supplier_id = live_plan.get('supplier_id')
        expected_expedite = bool(live_plan.get('expedite', False))
        eligible_snapshot = snapshot
    else:
        expected_action = inventory_output.get('action')
        expected_quantity = inventory_output.get('quantity')
        expected_supplier_id = (decision.get('details') or {}).get('supplier_id')
        expected_expedite = bool((decision.get('details') or {}).get('expedite', False))
        eligible_snapshot = state.get('db_snapshot', {})
    if decision_type not in {'REORDER', 'HOLD'}:
        raise ValueError(f'Unsupported decision type: {decision_type}')
    if decision_type != expected_action:
        raise ValueError(
            f'Decision conflicts with inventory rule: {decision_type} != {expected_action}'
        )
    if decision_type == 'HOLD':
        if (
            details.get('supplier_id') is not None
            or details.get('quantity', 0) != 0
            or bool(details.get('expedite', False))
        ):
            raise ValueError(
                'HOLD decision cannot contain a supplier, quantity, or expedite flag'
            )
        GovernedDecision.model_validate(decision)
        build_decision_key(state)
        return

    supplier_id = details.get('supplier_id')
    eligible_ids = {
        supplier['id'] for supplier in eligible_snapshot.get('suppliers', [])
    }
    if supplier_id not in eligible_ids:
        raise ValueError(f'Supplier {supplier_id} is not approved for this product')
    quantity = details.get('quantity')
    if not isinstance(quantity, int) or quantity <= 0 or quantity != expected_quantity:
        raise ValueError(
            f'Invalid quantity: expected {expected_quantity}, got {quantity}'
        )
    if supplier_id != expected_supplier_id:
        raise ValueError(
            f'Live supplier selection changed: expected {expected_supplier_id}, got {supplier_id}'
        )
    if bool(details.get('expedite', False)) != expected_expedite:
        raise ValueError(
            f'Live transport mode changed: expected expedite={expected_expedite}'
        )
    GovernedDecision.model_validate(decision)
    build_decision_key(state)
