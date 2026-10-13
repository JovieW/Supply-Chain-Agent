from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from autonomous_tools import build_read_only_tools
from boundary_schemas import validate_tool_response
from llm_config import get_llm
from state import SupplyChainState


DEFAULT_MAX_TOOL_CALLS = 6
HARD_MAX_TOOL_CALLS = 12
DEFAULT_TOOL_TIMEOUT_SECONDS = 8.0


def _setting_int(name: str, default: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(1, min(maximum, value))


def _setting_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(1.0, min(30.0, value))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            item.get("text", "") if isinstance(item, dict) else str(item)
            for item in content
        ).strip()
    return str(content or "").strip()


def _derive_alerts(trace: list[dict]) -> list[str]:
    alerts: list[str] = []
    for call in trace:
        if call.get("status") != "success":
            continue
        result = call.get("result", {})
        if call["tool"] == "inspect_inbound_shipments" and result.get("exceptions", 0):
            alerts.append(f"检测到 {result['exceptions']} 个在途异常")
        elif call["tool"] == "stress_test_plan":
            if result.get("projected_shortage", 0) > 0:
                alerts.append(
                    f"压力情景预计缺口 {result['projected_shortage']} 件"
                )
            if result.get("constraints"):
                alerts.extend(result["constraints"])
        elif call["tool"] == "compare_supplier_options" and result.get("constraints"):
            alerts.extend(result["constraints"])
    return list(dict.fromkeys(alerts))


async def autonomous_tool_investigation_node(state: SupplyChainState) -> dict:
    """Let the LLM choose bounded read-only tools and preserve a complete audit trace."""
    snapshot = state["db_snapshot"]
    retrieved = state.get("retrieved_knowledge", {})
    tools = build_read_only_tools(snapshot, retrieved)  # 只读工具
    tool_map = {tool.name: tool for tool in tools}
    max_calls = _setting_int("AGENT_MAX_TOOL_CALLS", DEFAULT_MAX_TOOL_CALLS, HARD_MAX_TOOL_CALLS)
    timeout = _setting_float("AGENT_TOOL_TIMEOUT_SECONDS", DEFAULT_TOOL_TIMEOUT_SECONDS)
    trace: list[dict] = []
    executed_calls = 0
    cache: dict[str, dict] = {}
    status = "completed"

    product = snapshot.get("product", {})
    inventory = snapshot.get("inventory", {})
    system_prompt = """你是供应链控制塔的自主调查 Agent。
你可以自行选择工具核查事实和补充证据，但只能使用提供的只读工具。
通常调用 1-4 个有助于当前判断的工具，不需要用完全部工具。
发现缺货、延迟、波动或供应风险时可考虑压力测试和企业知识检索。
禁止创建采购单、修改数据库、批准决策，禁止编造工具结果。
调查完成后，用中文给出简洁摘要，区分事实、压力情景和知识库依据。"""
    user_prompt = (
        f"调查产品 {product.get('name')}（SKU={product.get('sku')}）的供应链状态。"
        f"当前在手库存={inventory.get('quantity')}，静态补货点={inventory.get('reorder_point')}，"
        f"活跃采购单={len(snapshot.get('purchase_orders', []))}，"
        f"合格供应商={len(snapshot.get('suppliers', []))}。"
    )
    messages = [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]

    try:
        tool_enabled_llm = get_llm().bind_tools(tools, tool_choice="auto")  # 绑定工具
        last_response = None
        for _ in range(5):
            last_response = await tool_enabled_llm.ainvoke(  # 调用 获取结果
                messages,
                config={
                    "run_name": "autonomous_supply_chain_investigation",
                    "tags": ["autonomous_tools", "read_only", "p2"],
                    "metadata": {"product_id": state.get("product_id"), "max_calls": max_calls},
                },
            )
            messages.append(last_response)  # 将结果放进去
            requested = list(getattr(last_response, "tool_calls", []) or [])
            if not requested:
                break
            for call in requested:
                name = call.get("name", "")
                args = call.get("args") or {}
                call_id = call.get("id") or f"tool-call-{len(trace) + 1}"
                if executed_calls >= max_calls:  # 达到该工具的最大调用次数
                    status = "limit_reached"
                    trace.append({
                        "call_id": call_id,
                        "tool": name,
                        "arguments": args,
                        "status": "blocked",
                        "error": "tool_call_limit_reached",
                        "duration_ms": 0.0,
                    })
                    messages.append(ToolMessage(
                        content=json.dumps({"error": "tool_call_limit_reached"}),
                        tool_call_id=call_id,
                    ))
                    continue
                executed_calls += 1
                started = time.perf_counter()
                cache_key = json.dumps({"name": name, "args": args}, sort_keys=True, ensure_ascii=False)
                audit = {"call_id": call_id, "tool": name, "arguments": args}
                try:
                    if name not in tool_map:
                        raise ValueError(f"Tool is not allowlisted: {name}")
                    if cache_key in cache:
                        result = cache[cache_key]
                        audit["cached"] = True
                    else:  # 执行工具
                        result = await asyncio.wait_for(
                            asyncio.to_thread(tool_map[name].invoke, args), timeout=timeout
                        )
                        result = validate_tool_response(name, result)  # 校验返回结果
                        cache[cache_key] = result  # 缓存
                        audit["cached"] = False
                    audit.update({"status": "success", "result": result})
                    tool_content = json.dumps(result, ensure_ascii=False, default=str)    # 转换成 ToolMessage 内容
                except Exception as exc:
                    audit.update({
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    tool_content = json.dumps({"error": audit["error"]}, ensure_ascii=False)  # 返回给llm
                audit["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)  # 计算耗时
                trace.append(audit)  # 保存审计轨迹
                messages.append(ToolMessage(content=tool_content[:12000], tool_call_id=call_id))  # 保存到message

        summary = _content_text(getattr(last_response, "content", "")) if last_response else ""  # 获取最后一次的llm输出
        if not summary or getattr(last_response, "tool_calls", None):
            summary_response = await get_llm().ainvoke(
                messages + [HumanMessage(content="工具调用已结束。请基于工具结果给出最终中文调查摘要，不再调用工具。")],
                config={"run_name": "autonomous_investigation_summary", "tags": ["p2", "summary"]},
            )
            summary = _content_text(summary_response.content)  # 获取新的摘要
        if not trace:
            status = "no_tool_selected"
        elif status == "completed" and any(
            call.get("status") == "error" for call in trace
        ):
            status = "completed_with_errors"
    except Exception as exc:
        status = "degraded"
        summary = f"自主工具调查降级：{type(exc).__name__}: {exc}。后续确定性决策流程继续执行。"

    return {"agent_outputs": {"tool_investigator": {
        "status": status,
        "summary": summary,
        "tool_calls": trace,
        "tool_call_count": executed_calls,
        "tool_request_count": len(trace),
        "max_tool_calls": max_calls,
        "allowlist": sorted(tool_map),
        "read_only": True,
        "alerts": _derive_alerts(trace),
    }}}
