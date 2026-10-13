from __future__ import annotations

from agents.schemas import ReflectionOutput, invoke_structured
from llm_config import get_llm
from reflection_rules import (
    determine_reflection_outcome,
    deterministic_reflection_checks,
    max_reflection_rounds,
)
from state import SupplyChainState
from memory_rules import format_memory_context


async def reflection_agent_node(state: SupplyChainState) -> dict:
    """Critique a decision, request bounded revision, or fail closed to approval."""
    round_number = int(state.get("reflection_round", 0)) + 1
    max_rounds = max_reflection_rounds()
    deterministic_issues = deterministic_reflection_checks(state)
    decision = state.get("final_decision") or {}
    outputs = state.get("agent_outputs", {})
    investigation = outputs.get("tool_investigator", {})
    previous_history = list(state.get("reflection_history", []))
    memory_context = format_memory_context(state.get("long_term_memory", {}))
    llm_error = None

    prompt = f"""你是独立于决策生成者的供应链审计与反思 Agent，这是第 {round_number}/{max_rounds} 轮审查。
请批判性检查以下最终决策是否完整、一致、有证据且充分回应风险。你不能修改业务动作、供应商、数量或运输方式。

最终决策：{decision}
需求结论：{outputs.get('demand', {})}
库存结论：{outputs.get('inventory', {})}
风险结论：{outputs.get('risk', {})}
物流结论：{outputs.get('logistics', {})}
自主工具摘要：{investigation.get('summary', '')}
自主工具告警：{investigation.get('alerts', [])}
经过结果验证的相似历史决策：{memory_context}
确定性校验发现：{deterministic_issues}

审查标准：
1. 解释是否覆盖库存触发、供应商选择、成本权衡、物流方式和主要风险。
2. 是否正面回应自主工具告警与压力测试结果。
3. 是否区分实时事实、预测/压力情景和企业知识证据。
4. 引用是否真正支持结论，是否存在无依据断言。
5. 只有存在会影响可解释性、证据完整性或风险认知的问题时才要求修订。

通过结构化字段返回 PASS 或 REVISE、问题、优点、证据覆盖率、置信度和中文批评意见。"""
    try:
        review = await invoke_structured(
            get_llm(),
            ReflectionOutput,
            prompt,
            config={
                "run_name": "supply_chain_self_reflection",
                "tags": ["reflection", "critic", "p3"],
                "metadata": {"round": round_number, "max_rounds": max_rounds},
            },
        )
        llm_review = review.model_dump()
    except Exception as exc:
        llm_error = f"{type(exc).__name__}: {exc}"
        llm_review = {
            "verdict": "REVISE",
            "critique": "反思模型不可用，无法完成独立语义审查。",
            "issues": [llm_error],
            "strengths": [],
            "evidence_coverage": 0.0,
            "confidence": 0.0,
        }

    issue_messages = [item["message"] for item in deterministic_issues]
    issue_messages.extend(llm_review.get("issues", []))
    outcome = determine_reflection_outcome(
        round_number=round_number,
        max_rounds=max_rounds,
        deterministic_issues=deterministic_issues,
        llm_verdict=llm_review.get("verdict", "REVISE"),
        llm_error=llm_error,
    )
    feedback = "；".join(dict.fromkeys(issue_messages))
    entry = {
        "round": round_number,
        "outcome": outcome,
        "deterministic_issues": deterministic_issues,
        "llm_review": llm_review,
        "feedback": feedback,
        "llm_error": llm_error,
    }
    history = [*previous_history, entry]
    return {
        "reflection_round": round_number,
        "reflection_history": history,
        "reflection_feedback": feedback if outcome == "REVISE" else None,
        "agent_outputs": {
            "reflection": {
                **entry,
                "max_rounds": max_rounds,
                "history": history,
            }
        },
    }
