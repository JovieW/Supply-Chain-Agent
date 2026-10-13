# 供应链决策助手：功能启动与使用说明书

> 适用版本：当前仓库代码；推荐 Python 3.12。  
> 系统定位：这是一个受治理的供应链决策支持原型，而不是可自主下单的采购机器人。确定性业务规则决定补货动作、供应商、数量与运输方式；LLM 只负责受限的检索、只读调查、解释与反思。

## 1. 系统能做什么

系统围绕一轮“补货决策—审批—采购履约—结果反馈—经验召回”闭环工作：

```text
实时业务快照 + 企业知识库
        ↓
需求 / 库存 / 风险 / 物流计算与 Agent 解释
        ↓
反思与风险门禁
        ↓
自动执行 或 人工审批后恢复
        ↓
本地采购单履约与质检入库
        ↓
实际结果评分与长期记忆
        ↓
后续相似决策可引用历史经验
```

当前功能边界如下：

- 业务快照来自本地 SQLite，而非真实 ERP、WMS、SRM 或 TMS。
- 采购履约由业务人员在界面中手工录入事件，不会自动接收供应商或仓库消息。
- `QC_ACCEPTED`（质检合格）才会增加在手库存；仅“到货”不会入库。
- 长期记忆只保存已执行且完成最终结果反馈的决策；未验证的 LLM 输出不能影响后续决策。
- 系统通过本地 `ProcurementAdapter` 创建采购单；接入真实 ERP 时应替换 Adapter，不应给予 LLM 直接写权限。

## 2. 首次启动

以下命令都应在仓库根目录执行，即包含 `requirements.txt` 的目录。

### 2.1 创建 Python 3.12 环境并安装依赖

Linux / macOS：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2.2 配置 `.env`

在仓库根目录创建 `.env`。不要提交其中的 API Key。最小演示配置如下（替换示例邮箱和 Key）：

```env
# LLM：二选一。默认使用 DashScope 的 OpenAI 兼容接口。
LLM_PROVIDER=dashscope
DASHSCOPE_API_KEY=your_dashscope_key
DASHSCOPE_MODEL=qwen3.7-flash
# DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# 高风险审批：本地演示时由服务器端配置主体，浏览器不能自行声明角色。
HITL_APPROVER_BINDINGS=manager@example.com:supply_chain_manager,director@example.com:procurement_director
HITL_CURRENT_ACTOR_EMAIL=manager@example.com
HITL_APPROVAL_TTL_HOURS=24
HITL_DIRECTOR_COST_THRESHOLD=10000
HITL_CHECKPOINT_DB=data/workflow_checkpoints.sqlite

# 最终结果反馈白名单；未配置时，回退使用审批人绑定中的邮箱。
MEMORY_FEEDBACK_USERS=manager@example.com,director@example.com

# 可选：Agent 边界配置。
AGENT_MAX_TOOL_CALLS=6
AGENT_TOOL_TIMEOUT_SECONDS=8
AGENT_MAX_REFLECTION_ROUNDS=2
AGENT_MAX_LONG_TERM_MEMORIES=5
```

如使用 OpenRouter，改为：

```env
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=openai/gpt-4o-mini
```

不要同时依赖两套 Key；程序会按 `LLM_PROVIDER` 选择并校验所需变量。模型调用需要网络和有效 Key。

### 2.3 初始化演示数据库

```bash
python3.12 src/db_init.py
```

该命令会创建或增量升级 `data/supply_chain.db`，并在空库中写入 3 个产品、供应商、库存、需求历史及初始采购相关数据。已有数据不会被清空。

如需使用非默认数据库，先配置：

```env
DATABASE_URL=sqlite:///data/supply_chain.db
```

### 2.4 构建企业知识向量库

知识源目录是 `data/knowledge/`，支持 Markdown、TXT、PDF 和 DOCX。首次构建或更换嵌入模型后执行：

```bash
python3.12 -m src.rag.ingest --rebuild
```

日常新增或修改知识文档后执行：

```bash
python3.12 -m src.rag.ingest
```

默认嵌入模型为本地的 `BAAI/bge-small-zh-v1.5`，首次执行会下载模型；向量索引发布到 `data/vector_store/`。如改用 DashScope 嵌入：

```env
RAG_EMBEDDING_PROVIDER=dashscope
RAG_EMBEDDING_MODEL=text-embedding-v4
```

### 2.5 启动 Web 界面

```bash
streamlit run ui/app.py --server.fileWatcherType none
```

浏览器打开终端显示的地址，通常是 `http://localhost:8501`。`--server.fileWatcherType none` 可避免某些 Linux 环境中 Streamlit 扫描 `transformers` 可选视觉模块时出现与项目无关的 `torchvision` 报错。

仓库已提供 `.streamlit/config.toml` 固化该设置，因此更新代码后可以直接使用 `streamlit run ui/app.py`；关闭监视器后，修改源码需要手动重启 Streamlit。

## 3. 业务使用说明

左侧“工作区”有四个页面：**决策工作台、审批任务、结果反馈、决策档案**。左侧产品选择会决定本轮读取的库存、供应商和采购数据。

### 3.1 生成补货建议

1. 在左侧选择产品。
2. 点击“生成新方案”或工作台中的“生成补货建议”。
3. 等待系统完成工作流。
4. 在“决策工作台”查看建议动作、供应商、数量、运输方式、风险原因和下一步。

一轮决策实际会执行：

1. 读取数据库快照：产品、库存、需求历史、有效在途、合格供应商和报价。
2. 检索已发布的 SOP、合同和历史事件向量知识库。
3. LLM 调用限定的只读工具核查库存、需求、在途、供应商、知识或压力情景。
4. 确定性模型计算需求预测、库存位置、动态补货点、供应商候选方案和风险调整成本。
5. 专项 Agent 解释需求、库存、风险、物流；Coordinator 生成管理摘要。
6. Reflection Agent 审查解释、证据、工具告警与硬约束；必要时有限次回到 Coordinator 修订。
7. 风险门决定自动执行，或创建审批任务并中断工作流。

### 3.2 阅读决策工作台

- **建议执行什么**：最终的 `REORDER`（补货）或 `HOLD`（暂不补货）。数量、供应商、加急与否来自确定性业务规则，LLM 不能修改。
- **业务影响**：动态补货点、安全库存、预计到货前缺口、风险调整总成本。
- **风险与下一步**：风险等级、风险评分、触发原因和应该由操作人员完成的动作。
- **查看方案依据与运行记录**：本轮知识库引用、反思结论、工具调用摘要以及持久化运行事件。

### 3.3 处理人工审批

当系统提示“需要人工审批”时：

1. 点击“前往审批任务”，或在侧边栏打开“审批任务”。
2. 打开待处理任务，阅读计划、风险原因、审批时限和当前服务器端审批身份。
3. 填写审批意见，选择批准或拒绝并提交。

批准不会绕过原工作流：系统使用保存的 LangGraph checkpoint 恢复同一线程，在写采购单前重新读取实时数据、重新计算计划并校验审批指纹。若库存、有效在途、供应商报价或合格供应商已变化，审批会标记为 `STALE`（失效），不会创建采购单。

### 3.4 执行采购履约

采购单创建后，进入“结果反馈”页面的“采购履约”区域，选择未结案采购单，逐步录入下一业务事件：

| 当前状态 | 可录入事件 | 操作含义 |
|---|---|---|
| `CREATED` | `SUPPLIER_CONFIRMED` | 供应商确认接单 |
| `SUPPLIER_CONFIRMED` | `SHIPPED` | 填写预计到货日期并记录发运 |
| `SHIPPED` | `RECEIVED` | 填写实际到货数量 |
| `RECEIVED` | `QC_ACCEPTED` | 填写质检合格并入库数量；此时才增加库存 |
| `RECEIVED` | `QC_REJECTED` | 记录质量拒收数量 |
| `QC_ACCEPTED` / `QC_REJECTED` | `CLOSED` | 关闭采购单 |

每次录入会生成不可变的履约事件，包含状态变化、参数、操作人和幂等键。重复提交相同事件不会重复变更状态。

### 3.5 录入最终业务结果并形成长期记忆

采购履约结案不等于已经形成长期记忆。对于 `REORDER` 决策，采购单必须为 `CLOSED`，然后该计划才会进入“待反馈结果”。

选择计划后，录入：实际交期、实际收货量、期间缺货量、质量拒收量、实际总成本、实际服务水平、反馈邮箱及结果说明。

系统将：

- 校验反馈人是否位于 `MEMORY_FEEDBACK_USERS` 白名单中；
- 校验数量和服务水平范围，拒收数不能超过实际收货数；
- 每条决策只允许记录一次最终结果；
- 用交期、足量交付、质量、缺货、成本和服务水平计算 0–100 分；
- 写入 `SUCCESS`、`MIXED` 或 `FAILURE`，并生成可审计经验；
- 将决策状态变为 `OUTCOME_RECORDED`。

下一次生成相似方案时，系统只召回这些结果已验证的历史案例。召回按产品、动作、供应商、需求波动、时间衰减和结果信息量排序；它仅帮助 Agent 解释风险和反思，不会直接篡改业务规则算出的采购数量。

### 3.6 使用决策档案与情景沙盘

“决策档案”用于复核本轮方案，而非日常操作入口：

- **需求与库存**：查看需求历史、预测日均需求、库存位置和补货点。
- **供应商方案比较**：查看候选供应商、数量、交期、预计缺口与风险调整成本。
- **情景沙盘**：设置需求变化倍数和交期延迟，运行只读模拟。
- **完整审计记录**：查看知识证据、反思、工具和持久化运行轨迹。

情景沙盘只在内存副本中重新计算业务规则：不调用 Agent、不写采购单、不覆盖正式方案。

### 3.7 创建一条可演示的闭环记录（本地演示专用）

当模型额度不足、无法完成一轮真实决策时，可使用以下脚本验证“采购履约 → 最终反馈 → 长期记忆”页面。它会在**当前配置的业务 SQLite 数据库**写入一条幂等的演示记录：决策记忆、采购单、完整履约事件、质检入库、关闭单据及 `SUCCESS` 结果反馈。

```bash
python3.12 demo/seed_closed_loop_demo.py --product-id 3
```

脚本会从 `MEMORY_FEEDBACK_USERS` 或 `HITL_APPROVER_BINDINGS` 中选择第一个授权邮箱；也可显式指定：

```bash
python3.12 demo/seed_closed_loop_demo.py \
  --product-id 3 \
  --feedback-email manager@example.com
```

执行后刷新 Streamlit，并在左侧选择相同产品（默认“专用件 C”），打开“结果反馈 → 查看已闭环的历史结果”。脚本重复运行不会创建第二条相同演示记录。

> 该脚本绕过 LLM、审批和真实外部采购系统，只用于本地 UI/闭环演示；不要在生产数据库或真实采购环境执行。

### 3.8 写入完整展示数据集（本地演示专用）

若需要同时演示不同业务阶段，可运行：

```bash
python3.12 demo/seed_showcase_data.py
```

该脚本向当前 SQLite 演示库增量写入三组幂等数据：

| 产品 | 展示状态 | 可演示内容 |
|---|---|---|
| 标准件 A | `HOLD` 且已完成结果反馈 | 暂不补货的已验证长期记忆 |
| 高端件 B | 已创建并发运的关联采购单 | 在 UI 中继续录入到货、质检、关闭及最终反馈 |
| 专用件 C | 已关闭采购单且已完成结果反馈 | “已闭环的历史结果”、评分与长期记忆召回 |

它不会创建可恢复的伪造审批任务。要演示真实审批中断与恢复，请在模型服务可用后，从“决策工作台”运行一份实际高风险方案。

## 4. 常见问题与正确预期

| 现象 | 含义或处理方式 |
|---|---|
| 方案显示“等待审批” | 采购尚未执行。请在“审批任务”完成审核；只有批准且实时复核通过才会建单。 |
| 批准后仍未创建采购单 | 检查审批是否 `STALE`、被拒绝、过期，或执行前实时数据是否变化。打开运行记录查看 `execution_blocked` 事件。 |
| “到货”后库存没变化 | 正常。需要继续录入 `QC_ACCEPTED`，只有合格入库数量会增加在手库存。 |
| 找不到“提交结果反馈”计划 | `REORDER` 采购单必须已 `CLOSED`；并确认决策执行成功且此前未提交最终结果。 |
| 结果反馈被拒绝 | 检查 `.env` 中的 `MEMORY_FEEDBACK_USERS`、反馈邮箱格式、结果说明至少 5 个字符和数量范围。 |
| RAG 没有知识依据 | 先运行 `python3.12 -m src.rag.ingest`；检查文档 front matter 是否有效且适用于当前产品、供应商、区域和日期。确定性计划仍可继续运行。 |
| Streamlit 出现 `st.session_state.page` 错误 | 确保使用已更新的 `ui/app.py`；页面跳转必须用 `on_click` 回调，不能在 widget 创建后直接写 `st.session_state["page"]`。 |

## 5. 验证与离线评测

基础回归测试：

```bash
python3.12 -m unittest discover -s tests -v
```

构建索引并运行 RAG 黄金集：

```bash
python3.12 -m src.rag.ingest --rebuild
PYTHONPATH=src python3.12 -m evaluation.retrieval_evaluator \
  --output artifacts/evaluations/rag_retrieval.json
```

运行 Agent 离线评测（使用临时 SQLite，不写当前演示库）：

```bash
python3.12 src/evaluation/runner.py
```

## 6. 开发者快速理解

### 6.1 建议阅读顺序

1. [src/backend_interface.py](../src/backend_interface.py)：UI 调用的边界、启动和恢复 LangGraph 的方式。
2. [src/graph.py](../src/graph.py)：完整 StateGraph、节点顺序和条件路由。
3. [src/state.py](../src/state.py)：全局工作流状态字段。
4. [src/business_rules.py](../src/business_rules.py)：真正决定补货方案的确定性业务模型。
5. [ui/app.py](../ui/app.py)：业务操作入口与页面渲染。

### 6.2 按改动目标定位文件

| 我要修改 | 优先阅读的真实路径 | 说明 |
|---|---|---|
| 前端 | [ui/app.py](../ui/app.py)、[src/cockpit_view_model.py](../src/cockpit_view_model.py)、[ui/helpers.py](../ui/helpers.py) | `app.py` 是 Streamlit 页面和操作表单；ViewModel 将工作流结果变成业务视图；helpers 是格式化工具。 |
| Agent | [src/agents/demand_agent.py](../src/agents/demand_agent.py)、[src/agents/inventory_agent.py](../src/agents/inventory_agent.py)、[src/agents/risk_agent.py](../src/agents/risk_agent.py)、[src/agents/logistics_agent.py](../src/agents/logistics_agent.py)、[src/agents/coordinator_agent.py](../src/agents/coordinator_agent.py)、[src/agents/reflection_agent.py](../src/agents/reflection_agent.py)、[src/agents/schemas.py](../src/agents/schemas.py)、[src/llm_config.py](../src/llm_config.py) | Specialist Agent 主要生成受限解释；Coordinator 发布受治理方案；Reflection 提出有限修订；schemas 负责结构化输出。 |
| Workflow | [src/graph.py](../src/graph.py)、[src/state.py](../src/state.py)、[src/backend_interface.py](../src/backend_interface.py)、[src/nodes/](../src/nodes/) | `graph.py` 组装边和条件路由；`backend_interface.py` 创建/恢复带 SQLite checkpoint 的运行；`nodes/` 是业务节点实现。 |
| RAG | [src/rag/config.py](../src/rag/config.py)、[src/rag/documents.py](../src/rag/documents.py)、[src/rag/embeddings.py](../src/rag/embeddings.py)、[src/rag/vector_store.py](../src/rag/vector_store.py)、[src/rag/ingest.py](../src/rag/ingest.py)、[src/rag/retriever.py](../src/rag/retriever.py)、[src/nodes/knowledge_retrieval.py](../src/nodes/knowledge_retrieval.py)、[data/knowledge/](../data/knowledge/) | 文档解析、切分、嵌入、Chroma 持久化、检索和进入工作流的节点分别在这些路径。 |
| Tool | [src/autonomous_tools.py](../src/autonomous_tools.py)、[src/agents/tool_investigator.py](../src/agents/tool_investigator.py)、[src/boundary_schemas.py](../src/boundary_schemas.py) | `autonomous_tools.py` 定义白名单的只读工具和参数 Schema；investigator 控制选择、上限、超时、缓存和审计。 |
| Memory | [src/nodes/long_term_memory.py](../src/nodes/long_term_memory.py)、[src/memory_rules.py](../src/memory_rules.py)、[src/db_service.py](../src/db_service.py)、[src/models.py](../src/models.py) | 决策记忆写入/召回在节点中；反馈校验、评分、排序在 rules 中；持久化表和查询在 service/models。 |
| Mock Data | [src/db_init.py](../src/db_init.py)、[src/db_service.py](../src/db_service.py)、[data/knowledge/](../data/knowledge/)、[evals/scenarios.json](../evals/scenarios.json)、[evals/retrieval_cases.json](../evals/retrieval_cases.json) | `db_init.py` 写入演示业务数据；`db_service.py` 的默认策略/报价常量补充运行时业务规则；知识文档和评测场景为可编辑演示素材。 |
| 配置 | [.env](../.env)、[requirements.txt](../requirements.txt)、[src/llm_config.py](../src/llm_config.py)、[src/rag/config.py](../src/rag/config.py)、[src/approval_policy.py](../src/approval_policy.py)、[src/auth_context.py](../src/auth_context.py)、[src/memory_rules.py](../src/memory_rules.py)、[src/observability.py](../src/observability.py) | `.env` 是本机私有配置（通常不提交）；其余文件定义依赖、LLM、RAG、审批、反馈、工具/反思和 trace 的读取规则。 |

### 6.3 关键调用关系

```text
ui/app.py
  └─ src/backend_interface.py
       ├─ src/graph.py
       │    ├─ src/nodes/data_ingestion.py → src/db_service.py
       │    ├─ src/nodes/knowledge_retrieval.py → src/rag/retriever.py
       │    ├─ src/agents/tool_investigator.py → src/autonomous_tools.py
       │    ├─ src/agents/*.py → src/llm_config.py
       │    ├─ src/nodes/long_term_memory.py → src/memory_rules.py / src/db_service.py
       │    ├─ src/nodes/human_approval.py → interrupt / 审批队列
       │    └─ src/nodes/execution.py → src/procurement_adapter.py → src/db_service.py
       └─ 审批恢复、采购履约、结果反馈、运行轨迹接口
```

### 6.4 修改时的边界原则

- 改采购数量、供应商、MOQ、库存策略或成本模型：优先修改 `src/business_rules.py`，并补充 `tests/test_business_rules.py` 与 `tests/test_p1_decision_model.py`。
- 改 LLM 的解释、提示词或输出格式：修改对应 `src/agents/*.py` 和 `src/agents/schemas.py`，不要让模型获得改变确定性决策的能力。
- 增加工具：先在 `src/autonomous_tools.py` 加 Pydantic 参数 Schema 和返回校验，再让 `tool_investigator.py` 使用；默认新增工具应为只读。
- 增加采购/审批写操作：不要放进 Agent 或 UI；通过 `src/nodes/execution.py`、`src/procurement_adapter.py` 和服务端校验实现，并提供幂等键与审计记录。
- 修改知识文件：修改后必须重建或增量发布向量索引，否则在线检索不会自动读取新内容。

## 7. 面试演示建议

选择高缺货风险产品，展示一轮完整但诚实的闭环：生成方案 → 检查知识依据和工具调查 → 展示风险门进入审批 → 批准并恢复工作流 → 逐步录入发运、到货、质检、关闭 → 提交最终结果 → 重新生成相似方案并展示已验证记忆。

演示时应明确说明：LLM 没有采购写权限；真实采购、供应商消息、WMS 质检和成本回传在当前项目中由本地 Adapter/人工录入模拟，生产部署需由 ERP/WMS/SRM/TMS 集成替换。
