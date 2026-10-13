"""Generate a deterministic, enterprise-shaped local demonstration dataset.

The records are synthetic and must not be represented as production customer
data.  They use realistic supply-chain distributions and stable identifiers so
the dataset, knowledge corpus, and retrieval golden set are reproducible.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import math
import random
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_SEED = 20260913
PRODUCT_START = 1001
SUPPLIER_START = 101
PO_START = 10001
SHIPMENT_START = 20001
MEMORY_START = 30001
OUTCOME_START = 40001

CATEGORIES = [
    ("半导体控制器", "SEM", 42.0, 0.99),
    ("汽车制动组件", "AUT", 28.0, 0.99),
    ("医药冷链包装", "PHA", 18.0, 0.995),
    ("工业传感器", "SEN", 34.0, 0.98),
    ("消费电子模组", "ELE", 55.0, 0.97),
    ("储能电芯辅料", "ENE", 31.0, 0.985),
    ("精密机械件", "MEC", 22.0, 0.97),
    ("食品级包装", "FOD", 65.0, 0.98),
    ("医疗器械耗材", "MED", 38.0, 0.995),
    ("化工工艺材料", "CHM", 26.0, 0.985),
    ("网络通信部件", "NET", 48.0, 0.98),
    ("航空维修件", "AER", 12.0, 0.995),
]

SUPPLIER_PREFIXES = [
    "华东精工", "北方智造", "南岭物流科技", "海岳新材料", "启航电子",
    "恒信工业", "远川供应链", "泰和医疗", "星海半导体", "江城汽车部件",
    "云峰包装", "中科传感", "东港化材", "联盛通信", "安澜能源",
]

POLICIES = [
    ("INVENTORY-ABCXYZ", "ABC-XYZ库存分层与服务水平策略", "ABC-XYZ", "A类高价值物料与X类稳定需求采用连续复核；AX物料目标服务水平不低于99%，CZ物料采用周期复核。安全库存必须基于交期需求波动计算，禁止统一使用固定天数。", "库存计划每月复核分类结果；发生需求结构变化时触发临时重算。"),
    ("DEMAND-SPIKE", "促销与异常需求尖峰识别规范", "需求尖峰", "连续三日需求超过过去二十八日均值两倍时标记异常尖峰。确认促销、客户拉货或重复订单后，才允许把异常量纳入长期基线。", "需求计划员应记录异常原因并提供去极值预测与包含尖峰预测两套结果。"),
    ("COLD-CHAIN", "冷链物料温控与放行策略", "冷链温控", "2至8摄氏度物料在运输中累计温度偏离超过30分钟必须隔离，不得直接入可用库存。质量部门完成稳定性评估前，计划系统按零可用量处理。", "冷链承运商必须提供可追溯温度曲线和校准有效的记录仪编号。"),
    ("QUALITY-QUARANTINE", "来料质量隔离与替代供方规则", "质量隔离", "供应商批次连续两次出现关键缺陷，或单批关键缺陷率超过2%，应暂停该批次入库并启动替代供方评估。隔离库存不得计入可承诺库存。", "质量、采购与生产计划共同确认恢复放行条件。"),
    ("DUAL-SOURCING", "关键物料双供与份额分配政策", "双供份额", "关键物料不得长期由单一供应商承担超过80%的年度份额。主供发生重大延迟、停产或财务风险时，备供份额应在两个工作日内提升至至少40%。", "切换前必须检查模具、认证、最小订单量与质量批准状态。"),
    ("MOQ-ROUNDING", "最小订单量与包装倍数处理规范", "MOQ包装倍数", "建议采购量低于最小订单量时提升至MOQ；高于MOQ时按包装倍数向上取整，但不得突破供应商最大供货量。由取整产生的额外持有成本必须进入总着陆风险成本。", "超过最大供货量时不得拆成同一供应商的伪多订单规避限制。"),
    ("OBSOLESCENCE", "呆滞与生命周期末期物料控制政策", "EOL呆滞", "九十日无需求且未来预测为零的物料进入呆滞评审。收到停产通知的物料必须结合剩余生命周期、替代料认证周期和报废风险计算最后采购量。", "未经生命周期委员会批准不得执行大额最后采购。"),
    ("SEMICONDUCTOR-ALLOCATION", "半导体短缺分配与客户优先级策略", "芯片分配", "晶圆产能受限时按安全法规、停线损失、合同承诺和替代可行性分配芯片。不得仅按客户订单先后顺序分配。", "分配决定须保留受影响订单、缺口和审批人记录。"),
    ("PHARMA-TRACE", "医药物料批次追溯与效期管理规范", "批次追溯", "医药包装和耗材必须执行批号、序列号和有效期追溯。预计到货时剩余效期不足总效期三分之二的批次原则上拒收。", "发生召回时应在四小时内定位关联采购单、运输单和库存批次。"),
    ("AUTO-LINE-STOP", "汽车产线停线风险升级规则", "停线风险", "预测在供应商最短加急交期前发生汽车产线断料时，风险等级直接提升为HIGH，并通知生产、采购和供应商质量负责人。", "停线损失应计入缺货成本，不得以最低采购单价作为唯一选供依据。"),
    ("ESD-HANDLING", "电子器件静电防护仓储规范", "ESD静电防护", "静电敏感器件必须使用屏蔽包装并在防静电区域拆包。包装湿度指示异常、密封破损或接地监测失败时转入质量隔离。", "仓库按月抽检接地电阻并保留记录。"),
    ("REVERSE-LOGISTICS", "退货与逆向物流处置政策", "逆向物流", "客户退回物料应按可再销售、返工、供应商退货和报废四类处置。未完成质量判定前不得冲减新的采购建议。", "高价值物料退货须关联原销售批次和原采购来源。"),
]

CONTRACT_TOPICS = [
    ("晶圆交付", "晶圆投片后标准交期为45天；确认延期超过7天时供应商必须提供产能恢复计划，并承担约定的停线升级责任。"),
    ("汽车件PPAP", "量产供货前必须完成PPAP批准；未经书面偏差许可不得更换材料、模具或生产地点。"),
    ("冷链承运", "承运全程保持2至8摄氏度并提供温度曲线；温度偏离事件须在2小时内通知。"),
    ("质量索赔", "关键缺陷批次可拒收；供应商须在24小时内提交围堵措施，并在十个工作日内完成8D报告。"),
    ("网络芯片安全库存", "供应商为指定网络芯片维护四周成品安全库存，库存低于两周时必须预警。"),
    ("危化品运输", "承运商必须具备危险品道路运输资质，路线变更和异常停车应在一小时内上报。"),
    ("航空件证书", "每批航空维修件必须随附适航证书和材料可追溯证明，缺少证书的到货不得接收入库。"),
    ("食品包装迁移", "食品接触包装应提供迁移测试报告，配方或油墨变更需提前九十日书面通知。"),
    ("电芯辅料产能", "月度锁定产能不得低于滚动预测的110%，紧急订单响应时间不超过48小时。"),
    ("传感器校准", "工业传感器出货校准证书有效期不得少于十二个月，批次测量偏差超过规格时整批隔离。"),
    ("精密件模具", "专用模具归采购方所有，供应商应维持预防性维护并提供模具寿命月报。"),
    ("医械无菌包装", "无菌屏障包装须通过密封完整性验证，灭菌批次记录保存期不少于产品寿命加两年。"),
]

INCIDENTS = [
    ("PORT-CONGESTION", "华东港口拥堵应急复盘", "港口拥堵导致海运晚到九天。团队根据库存覆盖拆分30%空运与70%铁路，并每日核对预计到达时间，最终避免核心客户停线。"),
    ("CHIP-SHORTAGE", "晶圆产能短缺分配复盘", "晶圆厂减产后，团队按停线损失和替代认证周期分配库存，同时启动双供认证。单纯按订单先后分配的初版方案被风险委员会否决。"),
    ("TEMP-EXCURSION", "冷链温度偏离事件复盘", "运输记录显示温度达到11摄氏度持续47分钟。到货批次被隔离，质量评估后仅放行稳定性合格部分，并向承运商追偿。"),
    ("QUALITY-RECALL", "关键部件质量召回复盘", "现场失效追溯至特定材料批次。系统通过采购单与批次关系在三小时内定位受影响库存，暂停供应商并启用备供。"),
    ("CUSTOMS-DELAY", "进口清关资料缺失复盘", "原产地证书字段错误造成清关延误五天。后续增加发运前单证四眼审核，并为高风险航线预留报关缓冲。"),
    ("CYBER-OUTAGE", "供应商网络攻击中断复盘", "供应商遭勒索软件攻击，EDI和排产系统中断。采购团队切换电话确认与离线发运清单，同时冻结未经验证的交期承诺。"),
    ("EARTHQUAKE", "地震导致单一来源停产复盘", "震区供应商停产十四天。由于关键物料缺少已认证备供，只能高成本空运库存；事后将双供覆盖纳入季度风险指标。"),
    ("FLOOD", "洪水造成内陆运输中断复盘", "仓库周边洪水中断公路运输。团队改走铁路中转并优先配送服务水平最高的订单，运输状态每四小时更新。"),
    ("AIR-CAPACITY", "旺季航空舱位紧张复盘", "旺季临时加急无法获得航空舱位。提前锁定的保量协议保障了关键物料，其余订单改用海空联运。"),
    ("BANKRUPTCY", "二级供应商破产风险复盘", "供应商财务预警显示现金流恶化，调查发现关键二级原料商申请破产。团队提前提高备供份额并回购专用模具。"),
    ("LABOR-STRIKE", "工厂罢工交付中断复盘", "供应商工厂罢工造成生产暂停。合同升级条款触发后启用备用产线，并将未确认在途量从库存位置中剔除。"),
    ("PACKAGING-SHORTAGE", "包装材料短缺复盘", "标签材料缺货使成品无法出库。团队通过批准临时替代材料和调整包装顺序恢复发运，同时保留法规标签复核。"),
]

PLAYBOOKS = [
    ("ALTERNATE-SOURCE", "替代供应商启用作业手册", "替代供方", "依次完成资质核验、样件验证、产能确认、商务条款、首批加严检验和份额爬坡。未经质量批准不得跳过样件验证。"),
    ("EXPEDITE", "加急运输决策手册", "加急运输", "比较标准运输下的预计缺口损失与加急运费。只有避免的缺货损失高于额外运费且加急交期可覆盖缺口窗口时才建议加急。"),
    ("ALLOCATION", "短缺物料分配作业手册", "短缺分配", "汇总可用量、在途可信量和客户优先级，生成缺口清单；安全法规和停线风险订单优先，分配结果需审批留痕。"),
    ("RECALL", "供应链召回响应手册", "产品召回", "确认批次范围后冻结库存、停止发运、定位客户和供应商来源，并在法规时限内完成通知、退回和处置记录。"),
    ("CUSTOMS", "进口清关异常处理手册", "清关异常", "核对发票、装箱单、原产地证、HS编码和许可证；资料差异由报关、采购和供应商共同确认，不得擅自修改受控单证。"),
    ("SUPPLIER-RECOVERY", "供应商交付恢复作业手册", "交付恢复", "要求供应商提供日级产能、缺料、在制和发运恢复计划。连续三日未达到承诺产出时升级管理层并启动替代供方。"),
    ("DATA-QUALITY", "主数据质量异常处置手册", "主数据异常", "发现交期、MOQ、价格或供应商资格缺失时暂停自动执行；由数据责任人修正并通过双人复核后重新计算方案。"),
    ("BCP", "供应链业务连续性演练手册", "业务连续性", "针对单一来源、区域灾害、系统中断和物流封锁开展桌面演练，记录恢复时间目标、手工替代流程和改进责任人。"),
    ("CAPACITY", "供应商产能爬坡验证手册", "产能爬坡", "核验设备节拍、班次、人力、良率和二级物料供给。产能声明必须由现场证据或连续四周交付记录支持。"),
    ("QUALITY-ESCALATION", "供应商质量升级作业手册", "质量升级", "关键缺陷触发一级升级，重复缺陷触发二级升级和驻厂遏制。解除升级须验证纠正措施有效性。"),
    ("LATE-INBOUND", "在途延期与库存位置修正规则", "在途延期", "预计到达日晚于计划覆盖窗口的采购单不得计入有效库存位置；日期缺失或承诺未确认时按高风险在途处理。"),
    ("COST-CHANGE", "采购价格异常波动处置手册", "价格异常", "单价较合同基准上涨超过8%时暂停自动下单，核对指数联动、汇率、附加费和审批记录后再发布报价。"),
]


def _front_matter(document_id: str, owner: str, *, supplier_scope: str = "all",
                  product_scope: str = "all", status: str = "active",
                  effective_to: str = "2099-12-31") -> str:
    return (
        "---\n"
        f"document_id: {document_id}\n"
        "version: 1.0\n"
        f"status: {status}\n"
        "effective_from: 2026-01-01\n"
        f"effective_to: {effective_to}\n"
        f"owner: {owner}\n"
        f"product_ids: {product_scope}\n"
        f"supplier_ids: {supplier_scope}\n"
        "regions: cn\n"
        "---\n\n"
    )


def generate_knowledge_corpus() -> dict:
    base = ROOT / "data" / "knowledge" / "enterprise_generated"
    folders = {kind: base / kind for kind in ("policies", "contracts", "incidents", "playbooks", "negative_controls")}
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    cases = []
    written = []

    for index, (code, title, keyword, body, control) in enumerate(POLICIES, start=1):
        document_id = f"ENT-SOP-{code}-001"
        path = folders["policies"] / f"{index:02d}_{code.lower()}.md"
        text = _front_matter(document_id, "Enterprise Supply Chain Governance")
        text += f"# {title}\n\n## 适用原则\n\n{body}\n\n## 控制要求\n\n{control}\n"
        path.write_text(text, encoding="utf-8")
        written.append(path)
        cases.append(_golden_case(
            f"policy_{code.lower()}", f"企业制度中关于{keyword}的控制要求是什么", document_id,
            "sop", tags=["policy", "governance"],
        ))

    for index, (topic, clause) in enumerate(CONTRACT_TOPICS):
        supplier_id = SUPPLIER_START + index
        document_id = f"ENT-CONTRACT-SUPPLIER-{supplier_id}"
        path = folders["contracts"] / f"supplier_{supplier_id}_{topic.lower()}.md"
        text = _front_matter(
            document_id, "Enterprise Procurement Legal", supplier_scope=str(supplier_id)
        )
        text += (
            f"# 供应商{supplier_id}{topic}框架条款\n\n"
            f"## 核心履约条款\n\n{clause}\n\n"
            "## 违约与升级\n\n供应商预见不能履约时必须主动预警；采购方可要求恢复计划、替代供货和管理层升级。所有偏差必须形成书面记录。\n"
        )
        path.write_text(text, encoding="utf-8")
        written.append(path)
        other_supplier = SUPPLIER_START + ((index + 1) % len(CONTRACT_TOPICS))
        cases.append(_golden_case(
            f"contract_supplier_{supplier_id}",
            f"供应商{supplier_id}的{topic}合同履约、交付和升级条款",
            document_id, "contract", supplier_ids=[supplier_id],
            forbidden=[f"ENT-CONTRACT-SUPPLIER-{other_supplier}"],
            tags=["contract", "supplier-scope"],
        ))

    for index, (code, title, lesson) in enumerate(INCIDENTS, start=1):
        document_id = f"ENT-INCIDENT-{code}-2026"
        path = folders["incidents"] / f"{index:02d}_{code.lower()}.md"
        text = _front_matter(document_id, "Enterprise Supply Chain Risk")
        text += (
            f"# {title}\n\n## 事件经过\n\n{lesson}\n\n"
            "## 复盘控制\n\n后续同类场景必须同时核对库存覆盖、在途可信度、合同约束和替代方案，并记录决策证据。\n"
        )
        path.write_text(text, encoding="utf-8")
        written.append(path)
        cases.append(_golden_case(
            f"incident_{code.lower()}", f"{title}中采取了什么措施，有什么经验",
            document_id, "historical_event", tags=["incident", "lesson"],
        ))

    for index, (code, title, keyword, steps) in enumerate(PLAYBOOKS, start=1):
        document_id = f"ENT-PLAYBOOK-{code}-001"
        path = folders["playbooks"] / f"{index:02d}_{code.lower()}.md"
        text = _front_matter(document_id, "Enterprise Operations Excellence")
        text += f"# {title}\n\n## 触发条件\n\n发生{keyword}相关风险时启动本手册。\n\n## 处置步骤\n\n{steps}\n"
        path.write_text(text, encoding="utf-8")
        written.append(path)
        cases.append(_golden_case(
            f"playbook_{code.lower()}", f"发生{keyword}问题时应该按照什么步骤处置",
            document_id, "sop", tags=["playbook", "operations"],
        ))

    negative_docs = [
        ("RET-EXPEDITE-2019", "retired_expedite.md", "retired", "2099-12-31", "旧版规则要求所有缺货场景无条件空运。"),
        ("DRAFT-AUTO-ORDER", "draft_auto_order.md", "draft", "2099-12-31", "草案允许模型绕过审批直接创建采购单。"),
        ("EXP-CONTRACT-2024", "expired_contract.md", "active", "2024-12-31", "旧合同约定所有交付延期均不承担责任。"),
        ("RET-QUALITY-WAIVER", "retired_quality_waiver.md", "retired", "2099-12-31", "旧版规则允许关键缺陷物料免检入库。"),
    ]
    for document_id, filename, status, effective_to, body in negative_docs:
        path = folders["negative_controls"] / filename
        text = _front_matter(
            document_id, "Synthetic Negative Control", status=status,
            effective_to=effective_to,
        )
        text += f"# 负向控制文档 {document_id}\n\n## 不应使用的内容\n\n{body}\n"
        path.write_text(text, encoding="utf-8")
        written.append(path)

    # Governance-focused cases prove that unsafe near-matches never leak.
    cases.extend([
        _golden_case(
            "governance_no_retired_expedite", "发生缺货时是否应该无条件采用空运加急",
            "ENT-PLAYBOOK-EXPEDITE-001", "sop",
            forbidden=["RET-EXPEDITE-2019"], tags=["negative-control", "retired"],
        ),
        _golden_case(
            "governance_no_draft_auto_order", "模型是否可以绕过审批直接创建采购单",
            "SOP-APPROVAL-001", "sop",
            forbidden=["DRAFT-AUTO-ORDER"], tags=["negative-control", "draft"],
        ),
        _golden_case(
            "governance_no_expired_contract", "供应商交付延期后的合同责任和升级要求",
            "CONTRACT-SUPPLIER-FRAMEWORK-001", "contract", supplier_ids=[1],
            forbidden=["EXP-CONTRACT-2024"], tags=["negative-control", "expired"],
        ),
        _golden_case(
            "governance_no_quality_waiver", "关键缺陷物料应该如何隔离和放行",
            "ENT-SOP-QUALITY-QUARANTINE-001", "sop",
            forbidden=["RET-QUALITY-WAIVER"], tags=["negative-control", "retired"],
        ),
    ])

    golden_path = ROOT / "evals" / "retrieval_cases_enterprise.json"
    golden_path.write_text(json.dumps({
        "schema_version": "rag-retrieval-golden/v2",
        "description": "Synthetic enterprise retrieval judgements with policy, contract, incident, playbook, scope, and negative-control coverage.",
        "dataset_notice": "All entities and operational narratives are synthetic; they are not customer or production data.",
        "cases": cases,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"documents": len(written), "golden_cases": len(cases), "golden_path": str(golden_path)}


def _golden_case(case_id: str, query: str, expected: str, document_type: str,
                 *, supplier_ids=None, forbidden=None, tags=None) -> dict:
    return {
        "id": case_id,
        "name": query,
        "query": query,
        "top_k": 5,
        "metric_ks": [1, 3, 5],
        "expected_document_ids": [expected],
        "expected_document_types": [document_type],
        "forbidden_document_ids": forbidden or [],
        "minimum_recall": 1.0,
        "minimum_reciprocal_rank": 0.2,
        "product_id": 1001,
        "supplier_ids": supplier_ids or list(range(SUPPLIER_START, SUPPLIER_START + 12)),
        "region": "cn",
        "as_of": "2026-09-13",
        "tags": tags or [],
    }


def seed_database(product_count: int, supplier_count: int, history_days: int, seed: int) -> dict:
    """Upsert the synthetic operational dataset using the project's ORM models."""
    from sqlalchemy.orm import sessionmaker
    from db_init import init_database
    from db_service import ensure_runtime_schema
    from models import (
        DemandHistory, DecisionMemory, Inventory, InventoryPolicy, OutcomeFeedback,
        Product, ProductSupplier, PurchaseOrder, Shipment, Supplier, SupplierOffer,
    )

    engine = init_database()
    # Backfill the original three showcase products before measuring whole-database
    # coverage; enterprise quality gates must include legacy records as well.
    ensure_runtime_schema()
    Session = sessionmaker(bind=engine)
    session = Session()
    today = date.today()
    rng = random.Random(seed)
    try:
        for offset in range(supplier_count):
            supplier_id = SUPPLIER_START + offset
            prefix = SUPPLIER_PREFIXES[offset % len(SUPPLIER_PREFIXES)]
            session.merge(Supplier(
                id=supplier_id,
                name=f"{prefix}{offset // len(SUPPLIER_PREFIXES) + 1:02d}号（合成）",
                lead_time_days=2 + (offset * 7) % 19,
                reliability_score=round(0.74 + ((offset * 13) % 25) / 100, 3),
            ))
        session.flush()

        demand_rows = 0
        mapping_rows = 0
        purchase_orders = 0
        shipments = 0
        memories = 0
        for offset in range(product_count):
            product_id = PRODUCT_START + offset
            category, prefix, base_daily, service_level = CATEGORIES[offset % len(CATEGORIES)]
            product_rng = random.Random(seed + product_id)
            daily = base_daily * (0.65 + (offset % 11) * 0.07)
            scenario = offset % 4
            session.merge(Product(
                id=product_id,
                name=f"{category}-{offset + 1:04d}（合成）",
                sku=f"{prefix}-{product_id:06d}",
            ))
            quantity_days = [38, 12, 3, 19][scenario]
            reorder_days = [15, 18, 20, 14][scenario]
            session.merge(Inventory(
                id=product_id,
                product_id=product_id,
                quantity=max(0, round(daily * quantity_days)),
                reorder_point=max(1, round(daily * reorder_days)),
            ))
            session.merge(InventoryPolicy(
                product_id=product_id,
                service_level=service_level,
                review_period_days=5 + offset % 10,
                reserved_quantity=round(daily * (1 + offset % 3)),
                backorder_quantity=round(daily * 2) if scenario == 2 else 0,
                holding_cost_per_unit_year=round(1.5 + (offset % 17) * 0.7, 2),
                stockout_cost_per_unit=round(20 + (offset % 13) * 12.5, 2),
            ))

            supplier_ids = [
                SUPPLIER_START + ((offset * 3 + step * 7) % supplier_count)
                for step in range(4)
            ]
            supplier_ids = list(dict.fromkeys(supplier_ids))
            for rank, supplier_id in enumerate(supplier_ids):
                mapping_rows += 1
                session.merge(ProductSupplier(
                    product_id=product_id, supplier_id=supplier_id,
                    is_active=not (rank == 3 and offset % 10 == 0),
                ))
                minimum = 20 * (1 + (offset + rank) % 8)
                multiple = [5, 10, 20, 25, 50][(offset + rank) % 5]
                session.merge(SupplierOffer(
                    product_id=product_id,
                    supplier_id=supplier_id,
                    unit_cost=round(8 + daily * 0.35 + rank * 2.4 + (offset % 9), 2),
                    min_order_quantity=minimum,
                    order_multiple=multiple,
                    max_order_quantity=minimum * (8 + (offset + rank) % 15),
                    freight_cost=round(80 + rank * 65 + (offset % 7) * 20, 2),
                    expedited_freight_cost=round(260 + rank * 110 + (offset % 7) * 35, 2),
                    expedite_lead_time_days=1 + rank % 3,
                    on_time_delivery_rate=round(0.78 + ((offset + rank * 5) % 21) / 100, 3),
                    defect_rate=round(0.003 + ((offset * 3 + rank) % 35) / 1000, 4),
                ))

            existing_days = {
                row[0] for row in session.query(DemandHistory.demand_date).filter(
                    DemandHistory.product_id == product_id
                ).all()
            }
            for days_ago in range(history_days, 0, -1):
                demand_date = today - timedelta(days=days_ago)
                if demand_date in existing_days:
                    continue
                seasonality = 1 + 0.16 * math.sin(2 * math.pi * days_ago / 30)
                weekday = 0.78 if demand_date.weekday() >= 5 else 1.0
                trend = 1 + ((history_days - days_ago) / max(1, history_days)) * ((offset % 5) - 2) * 0.04
                promotion = 1.9 if (days_ago + offset * 11) % 97 in {0, 1, 2} else 1.0
                noise = max(0.2, product_rng.gauss(1.0, 0.08 + (offset % 4) * 0.04))
                session.add(DemandHistory(
                    product_id=product_id,
                    demand_date=demand_date,
                    quantity=max(0, round(daily * seasonality * weekday * trend * promotion * noise)),
                ))
                demand_rows += 1

            for po_index in range(2):
                po_id = PO_START + offset * 2 + po_index
                supplier_id = supplier_ids[po_index % len(supplier_ids)]
                active = po_index == 0 and scenario in {0, 2, 3}
                status = "confirmed" if active else "CLOSED"
                order_quantity = max(20, round(daily * (12 + po_index * 5)))
                session.merge(PurchaseOrder(
                    id=po_id, supplier_id=supplier_id, product_id=product_id,
                    quantity=order_quantity, status=status,
                    created_at=datetime.utcnow() - timedelta(days=8 + (offset + po_index) % 120),
                ))
                purchase_orders += 1
                if active:
                    shipment_id = SHIPMENT_START + offset
                    eta_days = [-2, 4, 15, 7][scenario]
                    session.merge(Shipment(
                        id=shipment_id, po_id=po_id, status="in_transit",
                        expected_arrival=datetime.utcnow() + timedelta(days=eta_days),
                    ))
                    shipments += 1

            for memory_index in range(2):
                memory_id = MEMORY_START + offset * 2 + memory_index
                supplier_id = supplier_ids[memory_index % len(supplier_ids)]
                planned_quantity = max(20, round(daily * (10 + memory_index * 3)))
                planned_lead = 3 + (offset + memory_index) % 12
                planned_cost = round(planned_quantity * (12 + daily * 0.25), 2)
                volatility = ["LOW", "MEDIUM", "HIGH"][offset % 3]
                created = datetime.utcnow() - timedelta(days=20 + (offset * 5 + memory_index * 31) % 330)
                session.merge(DecisionMemory(
                    id=memory_id,
                    workflow_run_id=f"enterprise-synthetic-{product_id}-{memory_index}",
                    product_id=product_id,
                    decision_type="REORDER",
                    supplier_id=supplier_id,
                    planned_quantity=planned_quantity,
                    expedite=bool((offset + memory_index) % 3 == 0),
                    planned_lead_time_days=planned_lead,
                    planned_total_cost=planned_cost,
                    risk_level=["LOW", "MEDIUM", "HIGH"][scenario % 3],
                    risk_score=[1, 4, 8, 5][scenario],
                    status="OUTCOME_RECORDED",
                    context_data=json.dumps({
                        "product": {"id": product_id, "sku": f"{prefix}-{product_id:06d}"},
                        "demand_volatility": volatility,
                        "forecast_daily_demand": round(daily, 2),
                        "target_service_level": service_level,
                        "inventory_position": round(daily * quantity_days),
                        "days_of_supply": quantity_days,
                        "projected_shortage": round(daily * 3) if scenario == 2 else 0,
                        "constraints": [],
                    }, ensure_ascii=False),
                    decision_data=json.dumps({
                        "decision_type": "REORDER",
                        "details": {"supplier_id": supplier_id, "quantity": planned_quantity},
                    }, ensure_ascii=False),
                    created_at=created,
                    updated_at=created,
                ))
                success_band = (offset + memory_index) % 5
                score = [94.0, 86.0, 76.0, 63.0, 48.0][success_band]
                label = "SUCCESS" if score >= 80 else "MIXED" if score >= 60 else "FAILURE"
                delay = [0, 1, 3, 6, 11][success_band]
                rejects = round(planned_quantity * [0.0, 0.005, 0.015, 0.035, 0.08][success_band])
                session.merge(OutcomeFeedback(
                    id=OUTCOME_START + offset * 2 + memory_index,
                    memory_id=memory_id,
                    actual_lead_time_days=planned_lead + delay,
                    received_quantity=planned_quantity - round(planned_quantity * (0.1 if success_band == 4 else 0)),
                    stockout_units=round(daily * max(0, delay - 2)),
                    quality_reject_units=rejects,
                    actual_total_cost=round(planned_cost * (1 + success_band * 0.035), 2),
                    service_level=[0.995, 0.98, 0.94, 0.88, 0.72][success_band],
                    outcome_score=score,
                    outcome_label=label,
                    penalties_data=json.dumps([]),
                    lesson=f"合成履约样本：实际比计划晚{delay}天，质量拒收{rejects}件，结果等级{label}。",
                    notes="用于长期记忆召回和评测的确定性合成结果。",
                    submitted_by="enterprise-data@example.com",
                    submitted_at=created + timedelta(days=planned_lead + delay),
                ))
                memories += 1
        session.commit()
        return {
            "products": product_count,
            "suppliers": supplier_count,
            "supplier_mappings": mapping_rows,
            "new_demand_history_rows": demand_rows,
            "purchase_orders": purchase_orders,
            "active_shipments": shipments,
            "verified_decision_memories": memories,
            "history_days_per_product": history_days,
            "seed": seed,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="生成可复现的企业规模合成供应链数据")
    parser.add_argument("--products", type=int, default=120)
    parser.add_argument("--suppliers", type=int, default=30)
    parser.add_argument("--history-days", type=int, default=365)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--knowledge-only", action="store_true")
    args = parser.parse_args()
    if args.products < 12 or args.suppliers < 12 or args.history_days < 56:
        raise ValueError("企业数据至少需要12个产品、12个供应商和56天需求历史")

    knowledge = generate_knowledge_corpus()
    output = {
        "dataset_notice": "全部新增实体和业务事件均为确定性合成数据，不是真实客户数据。",
        "knowledge": knowledge,
    }
    if not args.knowledge_only:
        output["database"] = seed_database(
            args.products, args.suppliers, args.history_days, args.seed
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
