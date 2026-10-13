from langgraph.types import interrupt
from boundary_schemas import ApprovalResumeInput

from db_service import (
    create_approval_request,
    get_approval_request,
    log_decision_once,
    review_approval_request,
    update_decision_memory_status,
)
from state import SupplyChainState


def human_approval_node(state: SupplyChainState) -> dict:
    """Persist an approval task, interrupt the graph, then validate its resume payload."""
    decision = state.get("final_decision") or {}
    assessment = state.get("decision_assessment") or {}
    run_id = state.get("workflow_run_id")
    if not run_id:
        raise ValueError("workflow_run_id is required for approval idempotency")
    log_creation = log_decision_once(  # 记录审批日志
        decision_key=f"{run_id}:approval",
        agent_name="system",
        decision="HUMAN_APPROVAL_REQUESTED",
        reasoning=(
            f"风险评分 {assessment.get('score', '未知')}；"
            f"原因：{'；'.join(assessment.get('reasons', []))}；"
            f"决策：{decision}"
        ),
    )
    approval_id = create_approval_request(  # 创建审批请求  写入到表中
        decision_log_id=log_creation.log_id,
        decision_data={
            "workflow_run_id": state.get("workflow_run_id"),
            "product_id": state.get("product_id"),
            "decision": decision,
            "assessment": assessment,
            "db_snapshot": state.get("db_snapshot", {}),
            "state_snapshot": {
                "product_id": state.get("product_id"),
                "workflow_run_id": state.get("workflow_run_id"),
                "db_snapshot": state.get("db_snapshot", {}),
                "retrieved_knowledge": state.get("retrieved_knowledge", {}),
                "agent_outputs": state.get("agent_outputs", {}),
                "final_decision": decision,
                "decision_risk": state.get("decision_risk"),
                "decision_assessment": assessment,
                "reflection_round": state.get("reflection_round", 0),
                "reflection_history": state.get("reflection_history", []),
                "reflection_feedback": state.get("reflection_feedback"),
                "long_term_memory": state.get("long_term_memory", {}),
                "decision_memory_id": state.get("decision_memory_id"),
            },
            "requested_by": "system-agent",
        },
    )
    update_decision_memory_status(  # 更新记忆的状态：正在等待人工审核
        state.get("decision_memory_id"),
        "PENDING_APPROVAL",
        approval_request_id=approval_id,
    )
    approval = get_approval_request(approval_id)  # 获取审批信息
    review_payload = interrupt({  # 暂停工作流 等待人工输入
        "type": "procurement_approval",
        "approval_id": approval_id,
        "workflow_run_id": run_id,
        "product_id": state.get("product_id"),
        "decision": decision,
        "assessment": assessment,
        "decision_hash": approval["decision_hash"],
        "required_role": approval["required_role"],
        "expires_at": approval["expires_at"],
        "instruction": "请由有权限且与申请方分离的人员批准或拒绝该采购决策。",
    })
    review_payload = ApprovalResumeInput.model_validate(review_payload).model_dump()  # 校验人工输入：Pydantic
    status = review_approval_request(approval_id, review_payload)  # 更新审批状态
    update_decision_memory_status(  # 更新长期记忆状态
        state.get("decision_memory_id"),
        status,
        approval_request_id=approval_id,
    )
    return {
        "human_feedback": status,
        "approval_request_id": approval_id,
        "human_review": {
            "status": status,
            "reviewer_email": review_payload.get("reviewer_email"),
            "reviewer_role": review_payload.get("reviewer_role"),
            "feedback": review_payload.get("feedback"),
        },
    }


def post_approval_routing(state: SupplyChainState) -> str:
    """Only a validated APPROVED resume may reach the side-effecting node."""
    return "execute" if state.get("human_feedback") == "APPROVED" else "end"
