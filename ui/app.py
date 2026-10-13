"""Business-facing supply-chain decision cockpit.

The UI is organised around real operator work: generate a plan, handle an
exceptional approval, record its outcome, and inspect the decision archive.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import streamlit as st

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()

UI_BACKEND_MODE = os.getenv("UI_BACKEND_MODE", "direct").strip().lower()
if UI_BACKEND_MODE not in {"direct", "api"}:
    raise RuntimeError("UI_BACKEND_MODE must be 'direct' or 'api'")

if UI_BACKEND_MODE == "api":
    from api_client import (
        advance_purchase_order,
        approval_workbench,
        current_approval_actor,
        load_approval_result,
        memory_workbench,
        procurement_workbench,
        review_decision,
        run_one_cycle,
        stress_test_plan,
        submit_outcome_feedback,
        workflow_trace,
    )
else:
    from autonomous_tools import stress_test_plan
    from backend_interface import (
    advance_purchase_order,
    approval_workbench,
    current_approval_actor,
    load_approval_result,
    memory_workbench,
    procurement_workbench,
    review_decision,
    run_one_cycle,
    submit_outcome_feedback,
    workflow_trace,
    )
from cockpit_view_model import (
    build_action_center,
    build_feedback_diagnosis,
    build_workflow_trace,
    decision_evidence,
)


PRODUCT_OPTIONS = {
    1: "标准件 A（稳定需求）",
    2: "高端件 B（波动需求）",
    3: "专用件 C（高缺货风险）",
}
RISK_LABELS = {"HIGH": "高风险", "MEDIUM": "中风险", "LOW": "低风险", "UNKNOWN": "待评估"}
ACTION_LABELS = {"REORDER": "创建补货计划", "HOLD": "暂不补货"}
STATUS_LABELS = {
    "PENDING": "等待审批", "APPROVED": "已批准", "REJECTED": "已拒绝",
    "STALE": "已失效", "EXPIRED": "已过期", "EXECUTED": "已执行",
    "EXECUTION_FAILED": "执行异常", "OUTCOME_RECORDED": "已完成结果反馈",
}


def money(value: object) -> str:
    return f"{float(value or 0):,.2f}"


def supplier_name(suppliers: list[dict], supplier_id: int | None) -> str:
    return next((row["name"] for row in suppliers if row["id"] == supplier_id), "未指定供应商")


def display_status(status: object) -> str:
    raw = str(status or "").upper()
    return STATUS_LABELS.get(raw, raw or "未开始")


def current_result() -> dict | None:
    return st.session_state.get("result")


def navigate_to(page: str) -> None:
    """Update the sidebar selection before its widget is instantiated."""
    st.session_state["page"] = page


def run_decision(product_id: int) -> None:
    with st.spinner("正在读取业务快照并生成补货建议…"):  # 显示加载状态
        try:
            st.session_state["result"] = run_one_cycle(product_id)  # 执行
            st.session_state["product_id"] = product_id
            st.success("本轮决策已生成。")
        except Exception as exc:
            st.error(f"决策流程执行失败：{exc}")


def render_empty_workbench(product_id: int) -> None:
    st.header("决策工作台")
    st.write("选择一个产品后生成本轮补货建议。系统会读取当前库存、在途订单、合格供应商和企业知识库。")
    st.info("先生成方案；如方案触发高风险门槛，它会自动进入审批队列，未获批准前不会创建采购订单。")
    if st.button("生成补货建议", type="primary", width="content"):
        run_decision(product_id)


def render_decision_evidence(result: dict) -> None:
    """Keep implementation evidence available without making it main navigation."""
    outputs = result.get("agent_outputs") or {}
    reflection = outputs.get("reflection") or {}
    investigation = outputs.get("tool_investigator") or {}
    evidence = decision_evidence(result)

    st.subheader("企业知识依据")
    if evidence:
        for item in evidence:
            with st.expander(f"{item['source_id']} · {item['title']}"):
                if item["found"]:
                    st.caption(
                        f"类型：{item['document_type']} · 检索相关度：{float(item['score'] or 0):.3f} · "
                        f"文档版本：{item.get('document_id', '—')} v{item.get('version', '—')} · "
                        f"有效期：{item.get('effective_from', '—')} 至 {item.get('effective_to', '—')}"
                    )
                    st.write(item["content"])
                else:
                    st.warning("该引用未在本轮检索结果中找到，需要复核。")
    else:
        st.caption("本轮最终方案没有引用知识片段。")

    st.subheader("系统核查")
    st.write(f"反思结论：**{reflection.get('outcome', '未运行')}**；反思轮次：{reflection.get('round', 0)}")
    if reflection.get("feedback"):
        st.info(reflection["feedback"])
    if investigation:
        st.write(f"已执行 {investigation.get('tool_call_count', 0)} 次只读业务核查。")
        for alert in investigation.get("alerts", []):
            st.warning(alert)
    for stage in build_workflow_trace(result):
        st.write(f"- {stage['title']}：{stage['status_label']} — {stage['detail']}")

    st.subheader("运行观测")
    run_id = result.get("workflow_run_id")
    events = workflow_trace(str(run_id)) if run_id else []
    if not events:
        st.caption("当前运行暂无持久化观测事件。")
    else:
        event_labels = {
            "workflow_started": "工作流启动",
            "workflow_paused": "工作流等待审批",
            "workflow_completed": "工作流完成",
            "workflow_failed": "工作流失败",
            "llm_started": "模型调用开始",
            "llm_completed": "模型调用完成",
            "llm_failed": "模型调用失败",
            "tool_started": "工具调用开始",
            "tool_completed": "工具调用完成",
            "tool_failed": "工具调用失败",
            "pre_execution_revalidation_passed": "执行前实时复核通过",
            "execution_blocked": "采购执行被阻断",
            "approval_resume_requested": "提交审批恢复",
            "approval_resume_completed": "审批恢复完成",
            "approval_resume_failed": "审批恢复失败",
        }
        visible = [item for item in events if item.get("event") in event_labels]
        st.dataframe([
            {
                "时间": str(item.get("timestamp", "")),
                "事件": event_labels.get(item.get("event"), str(item.get("event", ""))),
                "组件/工具": str(item.get("component") or item.get("tool") or "—"),
                "耗时(ms)": str(item.get("duration_ms") or "—"),
                "错误": str(item.get("error") or ""),
            }
            for item in visible[-100:]
        ], hide_index=True, width="stretch")


def render_decision_summary(result: dict) -> None:
    snapshot = result.get("db_snapshot") or {}
    outputs = result.get("agent_outputs") or {}
    inventory = outputs.get("inventory") or {}
    demand = outputs.get("demand") or {}
    decision = result.get("final_decision") or {}
    assessment = result.get("decision_assessment") or {}
    details = decision.get("details") or {}
    business_case = decision.get("business_case") or {}
    suppliers = snapshot.get("suppliers") or []
    card = build_action_center(result)

    st.header("决策工作台")
    st.caption(f"本轮对象：{(snapshot.get('product') or {}).get('name', '当前产品')} · 运行编号：{result.get('workflow_run_id', '—')}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("在手库存", snapshot.get("inventory", {}).get("quantity", 0))
    c2.metric("库存位置", inventory.get("inventory_position", 0), help="在手库存 + 可按时到货的在途量 − 已分配量 − 欠单量。它用于判断未来可用库存，不等同于仓库实物库存。")
    c3.metric("可用天数", f"{inventory.get('days_of_supply', 0)} 天")
    c4.metric("28 天预测需求", (demand.get("forecast") or {}).get("forecast_horizon_quantity", 0))

    if card["state"] == "waiting":
        st.warning("该方案需要人工审批，采购尚未执行。请前往“审批任务”完成审核。")
    elif card["state"] == "blocked":
        st.error("当前方案已失效或被拒绝，不能执行。请在业务数据更新后重新生成方案。")
    elif card["state"] == "completed" and (outputs.get("execution") or {}).get("executed"):
        st.success("采购计划已执行。下一步是在到货或结案后录入实际业务结果。")
    else:
        st.info(card["next_step"])

    left, right = st.columns([1.35, 1])
    with left:
        st.subheader("建议执行什么")
        if decision.get("decision_type") == "REORDER":
            st.markdown(
                f"向 **{supplier_name(suppliers, details.get('supplier_id'))}** 采购 "
                f"**{details.get('quantity', 0):,} 件**，"
                f"采用 **{'加急运输' if details.get('expedite') else '标准运输'}**。"
            )
            st.caption("数量已按最低起订量、包装倍数、产能和服务水平约束计算。")
        else:
            st.markdown("**暂不创建补货计划。** 当前库存位置能够覆盖规划期需求，继续监控需求和在途变化即可。")
        st.write(decision.get("explanation", "暂无方案说明。"))
        st.subheader("业务影响")
        st.dataframe([
            {"指标": "动态补货点", "本轮值": str(inventory.get("planning_reorder_point", 0))},
            {"指标": "安全库存", "本轮值": str(inventory.get("safety_stock", 0))},
            {"指标": "预计到货前缺口", "本轮值": str(business_case.get("projected_shortage", 0))},
            {"指标": "风险调整总成本", "本轮值": f"¥ {money(business_case.get('total_landed_risk_cost'))}"},
        ], hide_index=True, width="stretch")
    with right:
        st.subheader("风险与下一步")
        st.metric("风险等级", RISK_LABELS.get(result.get("decision_risk"), "待评估"))
        st.metric("风险评分", assessment.get("score", 0))
        for reason in assessment.get("reasons", []):
            st.write(f"- {reason}")
        st.caption(assessment.get("authority_rule", ""))
        if result.get("human_feedback") == "PENDING":
            st.button(
                "前往审批任务",
                width="stretch",
                on_click=navigate_to,
                args=("审批任务",),
            )
        elif (outputs.get("execution") or {}).get("executed"):
            st.button(
                "录入实际结果",
                width="stretch",
                on_click=navigate_to,
                args=("结果反馈",),
            )
    with st.expander("查看方案依据与运行记录"):
        render_decision_evidence(result)


def render_approval_form(result: dict) -> None:
    decision = result.get("final_decision") or {}
    details = decision.get("details") or {}
    snapshot = result.get("db_snapshot") or {}
    st.divider()
    st.subheader(f"审核方案 #{result.get('approval_request_id')}")
    st.write(f"建议：**{ACTION_LABELS.get(decision.get('decision_type'), decision.get('decision_type'))}**；数量 **{details.get('quantity', 0):,} 件**；风险 **{RISK_LABELS.get(result.get('decision_risk'), '待评估')}**。")
    st.caption(f"产品：{(snapshot.get('product') or {}).get('name', '—')} · 失效时间：{result.get('approval_expires_at', '—')}")
    try:
        actor = current_approval_actor()
    except Exception as exc:
        st.error(f"服务器审批身份不可用：{exc}")
        return
    st.info(
        f"当前服务端审批主体：{actor['email']}（{actor['role']}）。"
        "浏览器不能修改审批身份或提升角色。系统会在批准时以及采购写入前各复核一次业务快照。"
    )
    with st.form("approval_review"):
        note = st.text_area("审核意见", placeholder="至少 5 个字符，说明批准依据或拒绝原因")
        approve, reject = st.columns(2)
        approve_clicked = approve.form_submit_button("批准并恢复执行", type="primary", width="stretch")
        reject_clicked = reject.form_submit_button("拒绝方案", width="stretch")
    if approve_clicked or reject_clicked:
        try:
            st.session_state["result"] = review_decision(result, approve_clicked, note)
            st.rerun()
        except Exception as exc:
            st.error(f"审批处理失败：{exc}")


def render_approval_page() -> None:
    st.header("审批任务")
    st.write("仅处理触发风险门槛的补货方案。批准会恢复原决策流程；拒绝或数据失效均不会创建采购订单。")
    try:
        pending = approval_workbench(status="pending")  # 获取当前正在审批的任务
        recent = approval_workbench()  # 获取所有审批记录
    except Exception as exc:
        st.error(f"审批队列加载失败：{exc}")
        return
    if not pending:
        st.success("目前没有待审批任务。")
    else:
        st.subheader(f"待处理任务（{len(pending)}）")
        selected = st.selectbox(
            "选择任务", [item["id"] for item in pending],
            format_func=lambda task_id: next(
                f"#{row['id']} · 产品 {row.get('product_id', '—')} · {RISK_LABELS.get(row.get('risk_level'), row.get('risk_level', '待评估'))} · 截止 {row.get('expires_at', '—')}"
                for row in pending if row["id"] == task_id
            ),
        )
        if st.button("打开任务", type="primary", width="content"):
            try:
                st.session_state["result"] = load_approval_result(selected)  # 加载审批情况
                st.rerun()
            except Exception as exc:
                st.error(f"加载审批任务失败：{exc}")
    with st.expander("查看近期审批记录"):
        rows = [{
            "任务": f"#{item['id']}", "产品": item.get("product_id"), "状态": display_status(item.get("status")),
            "风险": RISK_LABELS.get(item.get("risk_level"), item.get("risk_level")), "发起时间": item.get("requested_at"),
            "审核人": item.get("reviewer_email") or "—",
        } for item in recent[:20]]
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.caption("暂无审批记录。")
    result = current_result()
    if result and result.get("human_feedback") == "PENDING":
        render_approval_form(result)


def render_feedback_form(memory: dict) -> None:
    st.subheader(f"录入计划 #{memory['id']} 的实际结果")
    st.caption(f"计划数量：{memory.get('planned_quantity', 0)} · 计划交期：{memory.get('planned_lead_time_days', 0)} 天 · 计划成本：{money(memory.get('planned_total_cost'))}")
    with st.form("outcome_feedback"):
        row1 = st.columns(3)
        actual_lead = row1[0].number_input("实际交期（天）", min_value=0, max_value=3650, value=int(memory.get("planned_lead_time_days") or 0))
        received = row1[1].number_input("实际收货数量", min_value=0, value=int(memory.get("planned_quantity") or 0))
        stockout = row1[2].number_input("期间缺货数量", min_value=0, value=0)
        row2 = st.columns(3)
        rejected = row2[0].number_input("质量拒收数量", min_value=0, value=0)
        actual_cost = row2[1].number_input("实际总成本", min_value=0.0, value=float(memory.get("planned_total_cost") or 0), step=100.0)
        service_level = row2[2].slider("实际服务水平", min_value=0.0, max_value=1.0, value=0.95, step=0.01)
        submitted_by = st.text_input("反馈人企业邮箱")
        notes = st.text_area("结果说明", placeholder="至少 5 个字符，说明交付、缺货、质量或成本异常")
        submitted = st.form_submit_button("提交结果反馈", type="primary", width="content")
    if submitted:
        try:
            saved = submit_outcome_feedback(memory["id"], {
                "actual_lead_time_days": actual_lead, "received_quantity": received, "stockout_units": stockout,
                "quality_reject_units": rejected, "actual_total_cost": actual_cost, "service_level": service_level,
                "submitted_by": submitted_by, "notes": notes,
            })
            outcome = saved.get("outcome") or {}
            st.success(f"结果已记录：{outcome.get('outcome_label')}（评分 {outcome.get('outcome_score')}）。后续相似场景可以召回该经验。")
            st.rerun()
        except Exception as exc:
            st.error(f"提交结果反馈失败：{exc}")


PROCUREMENT_NEXT_EVENTS = {
    "CREATED": ["SUPPLIER_CONFIRMED", "CANCELLED"],
    "SUPPLIER_CONFIRMED": ["SHIPPED", "CANCELLED"],
    "SHIPPED": ["RECEIVED"],
    "RECEIVED": ["QC_ACCEPTED", "QC_REJECTED"],
    "QC_ACCEPTED": ["CLOSED"],
    "QC_REJECTED": ["CLOSED"],
}
PROCUREMENT_EVENT_LABELS = {
    "SUPPLIER_CONFIRMED": "供应商确认",
    "SHIPPED": "已发运",
    "RECEIVED": "已到货",
    "QC_ACCEPTED": "质检通过并入库",
    "QC_REJECTED": "质检拒收",
    "CLOSED": "关闭采购单",
    "CANCELLED": "取消采购单",
}
PROCUREMENT_STATUS_LABELS = {
    "CREATED": "待供应商确认", "SUPPLIER_CONFIRMED": "供应商已确认",
    "SHIPPED": "运输中", "RECEIVED": "已到货待质检",
    "QC_ACCEPTED": "质检通过待关闭", "QC_REJECTED": "质检异常待关闭",
    "CLOSED": "已关闭", "CANCELLED": "已取消",
}


def render_procurement_lifecycle(product_id: int) -> None:
    st.subheader("采购履约")
    st.caption("本地 adapter 模拟供应商确认、发运、到货、质检和关闭；每一步均为幂等事件。")
    try:
        orders = procurement_workbench(product_id=product_id)
    except Exception as exc:
        st.error(f"采购履约加载失败：{exc}")
        return
    active = [order for order in orders if order.get("status") not in {"CLOSED", "CANCELLED"}]
    finished = [order for order in orders if order.get("status") in {"CLOSED", "CANCELLED"}]
    if not active:
        if finished:
            st.info(f"当前产品没有待履约采购单；已有 {len(finished)} 个已结束采购单。")
            with st.expander("查看已结束采购单"):
                st.dataframe([{
                    "采购单": f"#{item['id']}",
                    "数量": item.get("quantity"),
                    "状态": PROCUREMENT_STATUS_LABELS.get(item.get("status"), item.get("status")),
                    "结束事件": PROCUREMENT_EVENT_LABELS.get(
                        (item.get("events") or [{}])[-1].get("event_type"), "—"
                    ),
                } for item in finished], hide_index=True, width="stretch")
        else:
            st.info("当前产品没有待履约采购单。")
        return
    selected_id = st.selectbox(
        "采购单", [order["id"] for order in active],
        format_func=lambda po_id: next(
            f"#{row['id']} · 数量 {row['quantity']} · "
            f"{PROCUREMENT_STATUS_LABELS.get(row['status'], row['status'])}"
            for row in active if row["id"] == po_id
        ),
        key="lifecycle_po",
    )
    order = next(row for row in active if row["id"] == selected_id)
    received_quantity = next((
        int((item.get("payload") or {}).get("received_quantity", order["quantity"]))
        for item in reversed(order.get("events") or [])
        if item.get("event_type") == "RECEIVED"
    ), int(order["quantity"]))
    events = PROCUREMENT_NEXT_EVENTS.get(order["status"], [])
    if order["status"] == "RECEIVED" and received_quantity == 0:
        events = [event for event in events if event != "QC_REJECTED"]
    if not events:
        st.warning(
            f"状态 {PROCUREMENT_STATUS_LABELS.get(order['status'], order['status'])} "
            "没有可用的本地履约动作。"
        )
        return
    with st.form(f"lifecycle_transition_{selected_id}"):
        event_type = st.selectbox(
            "下一业务事件", events,
            format_func=lambda item: PROCUREMENT_EVENT_LABELS[item],
        )
        expected_arrival = None
        quantity_value = None
        if event_type == "SHIPPED":
            expected_arrival = st.date_input("预计到货日期", value=date.today() + timedelta(days=3))
        elif event_type == "RECEIVED":
            quantity_value = st.number_input(
                "实际到货数量", min_value=0, max_value=int(order["quantity"]),
                value=int(order["quantity"]),
            )
        elif event_type == "QC_ACCEPTED":
            quantity_value = st.number_input(
                "质检合格并入库数量", min_value=0, max_value=received_quantity,
                value=received_quantity,
            )
        elif event_type == "QC_REJECTED":
            quantity_value = st.number_input(
                "质量拒收数量", min_value=1, max_value=max(1, received_quantity),
                value=max(1, received_quantity),
            )
        submitted = st.form_submit_button("记录履约事件", type="primary")
    if submitted:
        payload = {}
        if event_type == "SHIPPED":
            payload["expected_arrival"] = expected_arrival.isoformat()
        elif event_type == "RECEIVED":
            payload["received_quantity"] = int(quantity_value)
        elif event_type == "QC_ACCEPTED":
            payload["accepted_quantity"] = int(quantity_value)
        elif event_type == "QC_REJECTED":
            payload["rejected_quantity"] = int(quantity_value)
        try:
            saved = advance_purchase_order(selected_id, event_type, payload)
            st.success(saved["message"])
            st.rerun()
        except Exception as exc:
            st.error(f"履约事件处理失败：{exc}")
    if order.get("events"):
        with st.expander("查看履约事件"):
            st.dataframe([{
                "时间": item.get("created_at"),
                "事件": PROCUREMENT_EVENT_LABELS.get(item.get("event_type"), item.get("event_type")),
                "状态变化": f"{item.get('from_status')} → {item.get('to_status')}",
                "操作人": item.get("actor_email"),
            } for item in order["events"]], hide_index=True, width="stretch")


def render_feedback_page(product_id: int) -> None:
    st.header("结果反馈")
    st.write("在到货、交付或结案后录入实际结果。系统只会把已执行且有真实结果的决策作为后续相似场景的经验。")
    render_procurement_lifecycle(product_id)
    st.divider()
    try:
        memories = memory_workbench(product_id=product_id)
        orders = procurement_workbench(product_id=product_id)
    except Exception as exc:
        st.error(f"结果记录加载失败：{exc}")
        return
    eligible = [
        item for item in memories
        if item.get("status") in {"EXECUTED", "EXECUTION_FAILED"}
        and not item.get("outcome")
        and (
            item.get("decision_type") == "HOLD"
            or item.get("purchase_order_status") == "CLOSED"
        )
    ]
    completed = [item for item in memories if item.get("outcome")]
    diagnosis = build_feedback_diagnosis(memories, orders)
    c1, c2, c3 = st.columns(3)
    c1.metric("待反馈结果", len(eligible)); c2.metric("已闭环决策", len(completed)); c3.metric("累计决策记录", len(memories))
    if not eligible:
        st.warning(diagnosis["message"])
    else:
        selected_id = st.selectbox(
            "选择已执行计划", [item["id"] for item in eligible],
            format_func=lambda memory_id: next(
                f"#{item['id']} · {item['decision_type']} · 计划数量 {item['planned_quantity']} · {display_status(item['status'])}"
                for item in eligible if item["id"] == memory_id
            ),
        )
        render_feedback_form(next(item for item in eligible if item["id"] == selected_id))
    with st.expander("查看已闭环的历史结果"):
        rows = [{
            "记录": f"#{item['id']}", "计划": item.get("decision_type"), "计划数量": item.get("planned_quantity"),
            "结果": (item["outcome"] or {}).get("outcome_label"), "评分": (item["outcome"] or {}).get("outcome_score"),
            "经验": (item["outcome"] or {}).get("lesson"),
        } for item in completed]
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.caption("暂无已闭环历史。")


def render_archive_page(result: dict | None) -> None:
    st.header("决策档案")
    st.write("用于复核当前方案的预测、供应商比较、知识依据和运行轨迹；日常操作无需停留在此页面。")
    if not result:
        st.info("请先在“决策工作台”生成或打开一份方案。")
        return
    snapshot = result.get("db_snapshot") or {}
    outputs = result.get("agent_outputs") or {}
    demand = outputs.get("demand") or {}
    inventory = outputs.get("inventory") or {}
    logistics = outputs.get("logistics") or {}
    decision = result.get("final_decision") or {}
    with st.expander("需求与库存", expanded=True):
        history = snapshot.get("demand_history") or []
        if history:
            st.line_chart(history, x="date", y="quantity", color="#1F77B4")
        forecast = demand.get("forecast") or {}
        st.dataframe([
            {"指标": "预测日均需求", "值": str(forecast.get("forecast_daily_demand", 0))},
            {"指标": "需求波动等级", "值": str(RISK_LABELS.get(forecast.get("volatility"), forecast.get("volatility", "—")))},
            {"指标": "库存位置", "值": str(inventory.get("inventory_position", 0))},
            {"指标": "动态补货点", "值": str(inventory.get("planning_reorder_point", 0))},
        ], hide_index=True, width="stretch")
    with st.expander("供应商方案比较"):
        selected_supplier = (decision.get("details") or {}).get("supplier_id")
        rows = [{
            "推荐": "是" if row.get("supplier_id") == selected_supplier else "", "供应商": row.get("supplier_name"),
            "数量": row.get("order_quantity"), "交期（天）": row.get("lead_time_days"),
            "预计缺口": row.get("projected_shortage"), "风险调整总成本": money(row.get("total_landed_risk_cost")),
        } for row in inventory.get("supplier_options", [])]
        st.dataframe(rows, hide_index=True, width="stretch")
        if logistics.get("reasoning"):
            st.write(logistics["reasoning"])
    with st.expander("情景沙盘（只读模拟）"):
        st.caption("只基于当前快照重新计算约束计划，不写入采购订单，也不会改变正式方案。")
        if snapshot.get("inventory_policy") and snapshot.get("suppliers"):
            a, b = st.columns(2)
            multiplier = a.slider("需求变化倍数", 0.5, 2.0, 1.2, 0.1)
            delay = b.slider("供应商额外交期延迟（天）", 0, 30, 3, 1)
            if st.button("运行模拟", width="content"):
                simulated = stress_test_plan(snapshot, multiplier, delay)
                st.dataframe([{
                    "建议": ACTION_LABELS.get(simulated["action"], simulated["action"]), "补货数量": simulated["quantity"],
                    "预计缺口": simulated["projected_shortage"], "风险调整总成本": money(simulated["total_landed_risk_cost"]),
                }], hide_index=True, width="stretch")
                for constraint in simulated.get("constraints", []):
                    st.warning(constraint)
        else:
            st.caption("当前运行缺少完整业务快照，不能进行模拟。")
    with st.expander("完整审计记录"):
        render_decision_evidence(result)


st.set_page_config(page_title="供应链决策工作台", page_icon="📦", layout="wide")

# 初始化页面，session_state页面状态，因为每次操作都会重新运行python文件 所以要及时保存页面状态
if "page" not in st.session_state:
    st.session_state["page"] = "决策工作台"

# 创建侧边栏
with st.sidebar:
    st.title("供应链决策助手")
    st.caption("规则引擎负责采购约束，LLM 负责检索、只读调查、解释与审查")
    st.caption(
        f"后端模式：{'FastAPI · ' + os.getenv('BACKEND_API_URL', 'http://127.0.0.1:8000') if UI_BACKEND_MODE == 'api' else '进程内直连'}"
    )
    page = st.radio("工作区", ["决策工作台", "审批任务", "结果反馈", "决策档案"], key="page")
    st.divider()  # 分割线
    product_id = st.selectbox("当前产品", list(PRODUCT_OPTIONS), format_func=lambda item: PRODUCT_OPTIONS[item])
    if st.button("生成新方案", type="primary", width="stretch"):
        run_decision(product_id)
    st.caption("新方案只分析当前产品；不会覆盖已存在的审批任务或历史结果。")

result = current_result()
if page == "决策工作台":
    render_decision_summary(result) if result else render_empty_workbench(product_id)
elif page == "审批任务":
    render_approval_page()
elif page == "结果反馈":
    render_feedback_page(product_id)
else:
    render_archive_page(result)

st.divider()
st.caption("本系统是受治理的决策支持原型，不是自主采购机器人。LLM 没有采购写权限；高风险采购须经服务端身份审批和执行前实时复核。")
