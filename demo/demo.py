"""
供应链控制塔静态演示页。

该页面展示预先记录的决策场景，不调用大语言模型、不访问数据库，适合面试或作品集演示。
"""

import streamlit as st


DEMO_SCENARIOS = {
    1: {
        "name": "场景 1 - 标准件 A",
        "description": "库存充足，并且可靠供应商已有在途订单。",
        "db_snapshot": {
            "product": {"name": "标准件 A", "sku": "WGT-A-001"},
            "inventory": {"quantity": 150, "reorder_point": 100},
            "suppliers": [
                {"id": 1, "name": "可靠供应商", "reliability_score": 0.95, "lead_time_days": 5},
                {"id": 2, "name": "快速交付供应商", "reliability_score": 0.88, "lead_time_days": 3},
                {"id": 3, "name": "低价物料供应商", "reliability_score": 0.72, "lead_time_days": 10},
            ],
            "purchase_orders": [
                {"id": 1, "quantity": 200, "status": "待处理", "supplier_name": "可靠供应商"}
            ],
            "shipments": [
                {"id": 1, "status": "运输中", "eta": "2 天后"}
            ],
        },
        "rag_evidence": [
            "库存高于补货点且有近期到达的在途订单时，可以暂不补货。",
            "低风险暂不补货决策可以自动执行，因为不会产生新的采购成本。",
        ],
        "agent_outputs": {
            "demand": {
                "demand_risk": "低风险",
                "reasoning": "当前库存为 150 件，高于 100 件补货点，并且存在 200 件活跃采购单，需求风险较低。",
            },
            "inventory": {
                "action": "暂不补货",
                "quantity": 0,
                "reasoning": "库存已超过补货点，且已有在途采购单补充库存，因此无需立即创建新采购单。",
            },
            "risk": {
                "supplier_risk": "低风险",
                "logistics_risk": "低风险",
                "reasoning": "主供应商可靠性为 95%，运输预计 2 天后到达，处于可接受窗口内。",
            },
            "logistics": {
                "expedite": False,
                "reasoning": "当前缺货风险较低，普通运输已能满足补给节奏，加急运输会带来不必要成本。",
            },
        },
        "final_decision": {
            "decision_type": "暂不补货",
            "details": {"supplier_id": None, "quantity": 0, "expedite": False},
            "explanation": "系统建议暂不补货。当前库存高于补货点，且已有可靠供应商的在途采购单，需求、供应商和物流风险均较低，因此不需要额外采购动作。",
        },
        "decision_risk": "低风险",
        "human_feedback": None,
        "execution": {
            "executed": False,
            "message": "无需执行采购动作，当前库存水平充足。",
        },
    },
    2: {
        "name": "场景 2 - 高端件 B",
        "description": "库存低于补货点，且没有活跃采购单。",
        "db_snapshot": {
            "product": {"name": "高端件 B", "sku": "WGT-B-002"},
            "inventory": {"quantity": 45, "reorder_point": 100},
            "suppliers": [
                {"id": 1, "name": "可靠供应商", "reliability_score": 0.95, "lead_time_days": 5},
                {"id": 2, "name": "快速交付供应商", "reliability_score": 0.88, "lead_time_days": 3},
                {"id": 3, "name": "低价物料供应商", "reliability_score": 0.72, "lead_time_days": 10},
            ],
            "purchase_orders": [],
            "shipments": [],
        },
        "rag_evidence": [
            "库存低于补货点且没有活跃采购单时，应视为补货候选。",
            "补货订单需要加急运输时，必须进入人工审批。",
        ],
        "agent_outputs": {
            "demand": {
                "demand_risk": "高风险",
                "reasoning": "当前库存只有 45 件，明显低于 100 件补货点，并且没有活跃采购单，存在较高缺货风险。",
            },
            "inventory": {
                "action": "补货",
                "quantity": 155,
                "reasoning": "根据补货策略，需要将库存恢复到约两倍补货点，因此建议采购 155 件。",
            },
            "risk": {
                "supplier_risk": "高风险",
                "logistics_risk": "高风险",
                "reasoning": "系统中存在可靠性低于 90% 的供应商，同时没有活跃运输单，供应商和物流风险都偏高。",
            },
            "logistics": {
                "expedite": True,
                "reasoning": "当前库存缺口明显，标准运输可能无法及时覆盖缺货风险，因此建议加急运输。",
            },
        },
        "final_decision": {
            "decision_type": "补货",
            "details": {"supplier_id": 1, "quantity": 155, "expedite": True},
            "explanation": "系统建议向可靠供应商采购 155 件并采用加急运输。虽然加急运输成本更高，但当前没有在途订单，库存低于补货点，缺货风险优先级高于物流成本。",
        },
        "decision_risk": "高风险",
        "human_feedback": "已批准",
        "execution": {
            "executed": True,
            "message": "采购单已创建：向可靠供应商采购 155 件，并使用加急运输。",
        },
    },
    3: {
        "name": "场景 3 - 专用件 C",
        "description": "库存偏低，现有在途订单来自低可靠性供应商且预计到达较晚。",
        "db_snapshot": {
            "product": {"name": "专用件 C", "sku": "WGT-C-003"},
            "inventory": {"quantity": 85, "reorder_point": 100},
            "suppliers": [
                {"id": 1, "name": "可靠供应商", "reliability_score": 0.95, "lead_time_days": 5},
                {"id": 2, "name": "快速交付供应商", "reliability_score": 0.88, "lead_time_days": 3},
                {"id": 3, "name": "低价物料供应商", "reliability_score": 0.72, "lead_time_days": 10},
            ],
            "purchase_orders": [
                {"id": 3, "quantity": 100, "status": "待处理", "supplier_name": "低价物料供应商"}
            ],
            "shipments": [
                {"id": 3, "status": "已延误", "eta": "12 天后"}
            ],
        },
        "rag_evidence": [
            "预计到达时间超过 7 天的运输应视为物流风险。",
            "低库存且唯一在途运输来自低可靠性供应商时，可以向更可靠供应商创建备份订单。",
        ],
        "agent_outputs": {
            "demand": {
                "demand_risk": "高风险",
                "reasoning": "库存低于补货点，虽然存在采购单，但运输预计 12 天后到达，无法充分缓解短期风险。",
            },
            "inventory": {
                "action": "补货",
                "quantity": 115,
                "reasoning": "当前库存低于补货点，且现有采购单到达较晚，因此需要补充备份订单。",
            },
            "risk": {
                "supplier_risk": "高风险",
                "logistics_risk": "高风险",
                "reasoning": "现有采购单来自可靠性 72% 的供应商，且运输 ETA 超过 7 天，供应商和物流风险同时偏高。",
            },
            "logistics": {
                "expedite": True,
                "reasoning": "为了降低延误运输造成的缺货风险，新订单应采用加急运输。",
            },
        },
        "final_decision": {
            "decision_type": "补货",
            "details": {"supplier_id": 1, "quantity": 115, "expedite": True},
            "explanation": "系统建议向可靠供应商采购 115 件，并使用加急运输作为风险缓解措施。该决策用更可靠供应商覆盖原有延误订单的不确定性。",
        },
        "decision_risk": "高风险",
        "human_feedback": "已批准",
        "execution": {
            "executed": True,
            "message": "采购单已创建：向可靠供应商采购 115 件，并使用加急运输。",
        },
    },
}


def yes_no(value: bool) -> str:
    return "是" if value else "否"


st.set_page_config(
    page_title="供应链控制塔 - 静态演示",
    layout="wide",
)

st.title("多智能体供应链控制塔")
st.caption("静态演示：预先记录的中文决策场景")

st.markdown(
    """
**说明：** 该页面展示预先记录的输出，不调用大语言模型、不访问数据库。
真实运行请启动主界面应用。
"""
)

st.markdown("---")

scenario_choice = st.selectbox(
    "选择演示场景",
    options=[1, 2, 3],
    format_func=lambda value: DEMO_SCENARIOS[value]["name"],
)

scenario = DEMO_SCENARIOS[scenario_choice]
st.info(f"**场景说明：** {scenario['description']}")

if st.button("查看决策分析", type="primary", width="stretch"):
    st.success("演示场景已加载")

st.markdown("---")

st.header("供应链状态（数据库快照）")

snapshot = scenario["db_snapshot"]

col1, col2 = st.columns(2)

with col1:
    st.subheader("产品与库存")
    product = snapshot["product"]
    inventory = snapshot["inventory"]

    st.write(f"**产品：** {product['name']}")
    st.write(f"**库存单位编码：** {product['sku']}")
    st.write(f"**当前库存：** {inventory['quantity']} 件")
    st.write(f"**补货点：** {inventory['reorder_point']} 件")

    if inventory["quantity"] < inventory["reorder_point"]:
        st.warning("库存低于补货点")
    else:
        st.success("库存充足")

with col2:
    st.subheader("供应商")
    for supplier in snapshot["suppliers"]:
        st.write(f"**{supplier['name']}**")
        st.write(f"- 可靠性：{supplier['reliability_score']:.0%}")
        st.write(f"- 交付周期：{supplier['lead_time_days']} 天")

st.subheader("活跃采购单")
purchase_orders = snapshot.get("purchase_orders", [])
if purchase_orders:
    for purchase_order in purchase_orders:
        st.write(
            f"采购单 #{purchase_order['id']}：{purchase_order['quantity']} 件，"
            f"供应商：{purchase_order['supplier_name']}，状态：{purchase_order['status']}"
        )
else:
    st.write("暂无活跃采购单")

st.subheader("活跃运输单")
shipments = snapshot.get("shipments", [])
if shipments:
    for shipment in shipments:
        st.write(
            f"运输单 #{shipment['id']}：状态：{shipment['status']}，预计到达：{shipment['eta']}"
        )
else:
    st.write("暂无活跃运输单")

st.markdown("---")

st.header("RAG 知识库证据")
for index, evidence in enumerate(scenario["rag_evidence"], start=1):
    st.write(f"{index}. {evidence}")

st.markdown("---")

st.header("智能体推理结果")

agent_outputs = scenario["agent_outputs"]

with st.expander("1. 需求智能体 - 需求风险分类", expanded=True):
    demand = agent_outputs["demand"]
    st.write(f"**风险等级：** {demand['demand_risk']}")
    st.write(f"**推理说明：** {demand['reasoning']}")

with st.expander("2. 库存智能体 - 补货决策", expanded=True):
    inventory_output = agent_outputs["inventory"]
    st.write(f"**动作：** {inventory_output['action']}")
    st.write(f"**数量：** {inventory_output['quantity']} 件")
    st.write(f"**推理说明：** {inventory_output['reasoning']}")

with st.expander("3. 风险智能体 - 供应商与物流风险", expanded=True):
    risk = agent_outputs["risk"]
    st.write(f"**供应商风险：** {risk['supplier_risk']}")
    st.write(f"**物流风险：** {risk['logistics_risk']}")
    st.write(f"**推理说明：** {risk['reasoning']}")

with st.expander("4. 物流智能体 - 运输方式决策", expanded=True):
    logistics = agent_outputs["logistics"]
    st.write(f"**是否加急运输：** {yes_no(logistics['expedite'])}")
    st.write(f"**推理说明：** {logistics['reasoning']}")

st.markdown("---")

st.header("最终决策（协调智能体综合判断）")

final_decision = scenario["final_decision"]
st.warning(f"**决策：{final_decision['decision_type']}**")

details = final_decision["details"]
if details.get("supplier_id"):
    supplier_name = next(
        supplier["name"]
        for supplier in snapshot["suppliers"]
        if supplier["id"] == details["supplier_id"]
    )
    st.write(f"**选定供应商：** {supplier_name}")
    st.write(f"**采购数量：** {details['quantity']} 件")
    st.write(f"**是否加急：** {yes_no(details['expedite'])}")

st.subheader("决策说明")
st.text_area(
    "协调智能体推理",
    final_decision["explanation"],
    height=220,
    disabled=True,
    label_visibility="collapsed",
)

st.markdown("---")

st.header("治理与安全")

col1, col2, col3 = st.columns(3)

with col1:
    st.metric("决策风险等级", scenario["decision_risk"])

with col2:
    st.metric("是否需要人工审批", yes_no(scenario["decision_risk"] == "高风险"))

with col3:
    st.metric("审批状态", scenario.get("human_feedback") or "自动通过")

execution = scenario["execution"]
if execution["executed"]:
    st.success(execution["message"])
else:
    st.info(execution["message"])

st.markdown("---")
st.caption("该演示页用于展示多智能体供应链决策、RAG 证据引用和治理审批链路。")
