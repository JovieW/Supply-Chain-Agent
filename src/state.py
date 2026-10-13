from __future__ import annotations

from typing_extensions import TypedDict, Annotated


def merge_agent_outputs(left: dict, right: dict | None) -> dict:
    """
    Reducer function to safely merge agent outputs.
    Prevents overwriting - appends new agent outputs to existing ones.
    """
    if right is None:
        return left
    return {**left, **right}


def replace_snapshot(left: dict, right: dict | None) -> dict:
    """
    Reducer function for db_snapshot.
    Always replaces with the latest snapshot (no merging needed).
    """
    if right is None:
        return left
    return right


def replace_retrieved_knowledge(left: dict, right: dict | None) -> dict:
    """
    Reducer function for retrieved_knowledge.
    Always replaces with the latest RAG retrieval result.
    """
    if right is None:
        return left
    return right


def replace_long_term_memory(left: dict, right: dict | None) -> dict:
    if right is None:
        return left
    return right


class SupplyChainState(TypedDict):
    """
    Shared state passed between all nodes in the LangGraph workflow.
    
    This state represents the in-memory context for the supply chain decision loop.
    Each node receives this state, processes it, and returns updates.
    
    Reducers control how updates are merged into the state:
    - agent_outputs: Merges new agent outputs with existing ones
    - db_snapshot: Replaces with latest snapshot
    - Other fields: Default overwrite behavior
    """
    schema_version: str
    product_id: int
    # Stable across retries/resume; used to make external writes idempotent.
    workflow_run_id: str
    # Current snapshot of database state (read by agents)
    # Reducer: Always replace with latest
    db_snapshot: Annotated[dict, replace_snapshot]   # 当前库存、供应商、订单、运输等数据库快照

    # Retrieved policies, playbooks, and historical cases for RAG-enhanced agents
    # Reducer: Always replace with latest retrieval result
    retrieved_knowledge: Annotated[dict, replace_retrieved_knowledge]  # RAG 检索到的制度、案例、规则

    # Outcome-verified episodic memories recalled for this decision.
    long_term_memory: Annotated[dict, replace_long_term_memory]  # 历史决策及实际结果
    decision_memory_id: int | None  #
    
    # Outputs from individual agents (keyed by agent name)
    # Reducer: Merge dictionaries to preserve all agent outputs
    agent_outputs: Annotated[dict, merge_agent_outputs]  # 各专业agent的输出集合
    
    # Final procurement decision (supplier_id, quantity, reasoning)
    # Default behavior: overwrite
    final_decision: dict | None  # Coordinator汇总出的最终方案
    
    # Risk assessment from Risk Agent
    # Default behavior: overwrite
    decision_risk: str | None  # 决策门计算的风险和审批要求

    # Auditable score and trigger reasons produced by the policy gate
    decision_assessment: dict | None
    
    # Optional human override or approval
    # Default behavior: overwrite
    human_feedback: str | None  # 人工审批结果

    approval_request_id: int | None
    human_review: dict | None

    # Bounded self-reflection loop state
    reflection_round: int  # 反思轮次
    reflection_history: list[dict]  # 反思历史
    reflection_feedback: str | None  # 修改意见
