# Agent / LLM Application Engineer 面试就绪度审计

> 审计日期：2026-08-30  
> 范围：当前工作区的 `src/`、`tests/`、`evals/`、`ui/`、`requirements.txt`。  
> 方法：代码与测试静态审查；没有将 README、页面文案或文件名作为能力证据。未修改任何业务代码。

## 1. Executive Summary

**总体结论：B. 比较完整的 Agent 学习项目。**

这是一个有价值、可展示的供应链决策原型：它把确定性补货规划、向量知识检索、受限只读工具、LangGraph 审批中断恢复、幂等采购单写入、结果反馈和离线规则评测串成了闭环。它不只是“一个聊天页面”。

但它**还不是中级 Agent Engineer 的核心项目强度**，更不是生产级系统。最关键的事实是：采购决策（动作、供应商、数量、加急）由 `business_rules.calculate_supply_chain_plan()` 确定，四个所谓专业 Agent 和协调 Agent 基本只让 LLM 生成解释；Multi-Agent 是固定串行分工而非协作式自治；RAG 没有权限、版本、混合检索或真正 reranker；真实 ERP/WMS/TMS 集成、身份认证、生产可观测性和端到端集成验证缺失。

**如果候选人拿这个项目面试 Agent Engineer：勉强认可。**

认可的是候选人知道把高风险业务规则从 LLM 中剥离、会用结构化输出、HITL、幂等与离线回归评测；勉强的原因是必须诚实定位为“受治理的供应链决策工作流原型”，不能宣称“LLM 自主多智能体采购系统”或“生产级 RAG”。如果应聘实习/初级 LLM Application Engineer，整改 P0 后可作为核心项目；如果应聘中级及以上，当前会被追问穿。

## 2. 面试 readiness 评分

| 维度 | 分数 | 判定 | 依据摘要 |
|---|---:|---|---|
| Agent 架构 | 5/10 | 🟡 | 有模型、prompt、state、结构化输出；业务动作不由 LLM 决定。 |
| Multi-Agent | 3/10 | 🟠 | 多个 node 有不同 prompt，但固定串行、共享全量 state、无协商/冲突解决。 |
| Workflow / LangGraph | 7/10 | 🟡 | 有 StateGraph、条件边、一次反思回路、SQLite checkpoint、interrupt/resume。 |
| RAG | 6/10 | 🟡 | 真正 Chroma + embedding + chunk；无 ACL、版本、混合检索、评测/rerank。 |
| Tool Calling | 6/10 | 🟡 | LLM 可选择 6 个只读工具，Pydantic schema、allowlist、超时、审计存在。 |
| Memory | 5/10 | 🟡 | SQLite 持久化的 outcome-verified episode；规则匹配，非语义记忆，无过期/纠错。 |
| Reflection | 5/10 | 🟡 | 独立 LLM critic + 硬校验 + 最多 3 轮；仅修改解释，不能改业务方案。 |
| Human-in-the-loop | 7/10 | 🟡 | `interrupt`、SQLite checkpoint、审批恢复和 stale hash 检查真实存在；无真实身份系统。 |
| Guardrail / Safety | 6/10 | 🟡 | 决策重算、白名单、审批门禁、幂等存在；注入/权限/外部副作用控制不足。 |
| Evaluation | 6/10 | 🟡 | versioned scenarios、阈值、离线 runner、回归比较存在；样本少且不是 LLM/RAG 质量评测。 |
| Observability | 2/10 | 🔴 | 只有本地 `.cursor/debug.log` 和可选 LangSmith 配置，无生产 trace/cost/latency 链路。 |
| 工程化 | 5/10 | 🟡 | 模块和单测较好；无 migration、CI、锁文件、部署/API 边界。 |
| 生产可用性 | 3/10 | 🔴 | SQLite/本地文件、模拟数据、无认证、无外部系统、无并发和运维设计。 |
| 面试可讲性 | 6/10 | 🟡 | 治理闭环可讲；“多 Agent 自主决策”不应讲。 |

**综合面试竞争力：56/100。**

## 3. 当前真实架构

```text
UI / backend_interface.run_one_cycle
  -> AsyncSqliteSaver + LangGraph (thread_id = workflow_run_id)
  -> ingest_data (SQLAlchemy snapshot)
  -> retrieve_knowledge (Chroma vector retrieval)
  -> autonomous_investigation (LLM-selected read-only tools)
  -> demand -> memory recall -> inventory -> risk -> logistics -> coordinator
  -> reflection [最多一次回到 coordinator]
  -> decision_gate -> persist_memory
  -> auto execution OR human_approval interrupt -> Command(resume) -> execution
```

代码证据：`src/graph.py:26-145` 定义固定节点和边；`src/backend_interface.py:69-77` 用 `AsyncSqliteSaver` 执行；`src/state.py:43-77` 是共享 `TypedDict` 状态。

### 真实决策权归属

- ✅ **确定性业务引擎**：`src/business_rules.py:77-154` 计算预测、安全库存、补货点、供应商候选、MOQ/包装倍数、交期、风险成本和最终数量。
- ✅ **执行前再校验**：`src/nodes/execution.py:162-229` 重算方案、验证供应商与数量，防止 LLM 改成 2,000 件。
- 🟡 **LLM explanation agents**：`demand_agent.py:20-35`、`inventory_agent.py:16-31`、`risk_agent.py:28-43`、`logistics_agent.py:16-31` 都明确要求“不得修改”确定性结果。
- 🟠 **Coordinator**：`src/agents/coordinator_agent.py:8-73` 虽称 coordinator，但直接把 `inventory` / `logistics` 的固定字段组装成 `final_decision`；LLM 只产生 `explanation`。

因此：系统是“**规则驱动的采购决策工作流，LLM 负责解释、调查和审阅**”，而非 LLM 自主决定采购的 Agent。

## 4. Agent 能力审计

| 项目 | 判定 | 代码证据 | 严格结论 |
|---|---|---|---|
| Prompt / Model | ✅ | `src/llm_config.py:46-57`，各 `agents/*_agent.py` | 有 `ChatOpenAI` 兼容模型和明确 prompt。 |
| Input / State / Output | 🟡 | `SupplyChainState`；`agents/schemas.py` | 输入输出虽部分结构化，但大部分 state 为嵌套 `dict`。 |
| LLM 决策 | 🟡 | `tool_investigator.py:83-165` | 只在“选哪些只读核查工具”和文本审查上自主。 |
| 业务动作决策 | 🔴 | `business_rules.py:107-154` | LLM 不选择采购动作/数量/供应商；这是刻意安全设计，不应包装为自治决策。 |
| 失败降级 | 🟡 | `tool_investigator.py:172-174`、`reflection_agent.py:58-69` | 工具调查降级继续；反思失败升级审批。普通 explanation agent 无独立降级策略。 |

### Multi-Agent 是否成立？

**结论：弱成立，不能作为“真实协作式 Multi-Agent”宣传。**

存在需求、库存、风险、物流、协调、调查、反思等独立 node 和 prompt；但 `src/graph.py:88-109` 是硬编码直线顺序，所有 node 使用同一 `SupplyChainState`，没有角色级数据权限、没有 agent 间消息协议、没有并发、没有 supervisor 动态路由、没有候选方案冲突解决。四个业务 Agent 的计算也不是各自推理产生，而是读取前序确定性 plan 后生成说明。

### Supervisor / Orchestrator

- 🟠 **固定代码调度**：`graph.py` 的 `add_edge()` 和条件边是唯一的路由者。
- 🔴 **LLM 动态 Orchestration**：不存在。Coordinator 没有任务拆解、选 agent、终止判断或失败补偿权。

## 5. RAG 审计

| 链路 | 判定 | 真实实现 |
|---|---|---|
| Document discovery | ✅ | `rag/documents.py` 发现 `.md/.pdf/.docx`，知识源在 `data/knowledge/`。 |
| Chunk | ✅ | `load_and_chunk()` 被 `vector_store.py:99-104` 调用，存文件 hash/section/page 等 metadata。 |
| Embedding | ✅ | `rag/embeddings.py` 的 `sentence-transformers` embedder。 |
| Vector Store | ✅ | `ChromaKnowledgeBase`，`chromadb.PersistentClient`，见 `vector_store.py:20-53`。 |
| 增量同步 | ✅ | hash 比较、删除失效 chunks，`vector_store.py:75-120`。 |
| Retrieval / Top-K | ✅ | cosine query，`vector_store.py:123-150`；默认每 agent Top-3。 |
| Metadata filter | 🟡 | 工具搜索后才按 `document_type` 过滤，`retriever.py:89-110`；不是向量库查询过滤，也无租户/合同 ACL。 |
| Rerank | 🟠 | `retriever.py:60-68` 仅按文档类型乘 1.05，非 cross-encoder/LLM reranker。 |
| Context / citation | 🟡 | `format_retrieved_context()` 把 source ID 放进 prompt；最终 `evidence` 只是检索结果 ID 列表，未验证逐句主张。 |
| Retrieval eval | 🔴 | 无 query-relevance 标注集、Recall@K/MRR/grounded answer 评测。 |

这不是关键词搜索，确实是向量知识库；但**不能称为生产级企业 RAG**。尤其 `retrieve_knowledge_for_snapshot()` 在每次业务运行时执行 `store.sync()`（`retriever.py:46-49`），把索引维护放到了在线关键路径，增加延迟和失败面；检索失败未在 `knowledge_retrieval_node()` 中降级捕获，会直接中断整个流程。

## 6. Tool 审计

### 工具清单

| 分类 | Tool | 判定 |
|---|---|---|
| Read | `inspect_inventory_position`、`forecast_demand`、`inspect_inbound_shipments`、`compare_supplier_options` | ✅ 纯函数/快照读取。 |
| Read | `search_enterprise_knowledge` | 🟡 真向量检索，失败后退回已有 RAG 内容的关键词排序。 |
| Read / simulation | `stress_test_plan` | ✅ 对深拷贝 snapshot 做情景模拟。 |
| Write | `db_service.create_purchase_order()` | 🟡 不暴露给 LLM；由 `execution_node()` 调用。 |
| High-risk | 采购创建、审批批准 | 🟡 分别有重算/审批门禁；没有外部 ERP/真实身份。 |

工具调用证据：`src/autonomous_tools.py:12-32` 定义 Pydantic 严格参数 schema；`build_read_only_tools()` 于 183-228 注册；`tool_investigator.py:112-157` 做 allowlist、`asyncio.wait_for`、缓存、调用上限和 per-call trace。

缺口：返回值没有 Pydantic response schema；没有 retry/backoff/circuit breaker；tool audit trace 只留在图 state/审批快照，未写独立不可变审计表；工具取的是运行开始快照，不能保证执行时最新事实。对真实外部 API 而言，目前也没有 OAuth、请求签名、rate limit、幂等 key 传递或补偿事务。

## 7. Workflow / LangGraph 审计

- ✅ State：`SupplyChainState` 有产品、快照、RAG、agent outputs、decision、risk、approval、reflection、memory 等字段。
- ✅ Node / Edge：`graph.py` 有完整节点、条件审批分支。
- ✅ Loop：`reflection_route` 只允许最多一次 coordinator 重写解释后再判断，反思最大轮数受 `reflection_rules.py` 控制（硬上限 3）。
- ✅ Checkpoint / interrupt / resume：`human_approval_node.py:62-74` 调用 `interrupt()`；`backend_interface.py:200-231` 用 `Command(resume=...)` 恢复同一 SQLite checkpoint thread。
- ⚠️ State 质量：绝大多数 payload 都是无版本、嵌套任意 `dict`；`TypedDict` 仅声明顶层，不能保障 nested data 的运行时合法性。`
- ⚠️ Continuous graph：`create_continuous_monitoring_graph()` 执行后无限回到 ingest（`graph.py:211-216`），无 sleep、事件触发、termination 或调度器，不可直接启用。

**LangGraph 是否必要？** 对持久化审批中断/恢复很合适，普通 Python pipeline 不容易安全保存与恢复执行位置。对于其余固定顺序的读、算、解释节点，普通 pipeline 已足够；当前把每个“解释函数”拆为 agent node 有演示价值，但也有一定过度设计。

## 8. Memory 审计

- ✅ **Working state**：LangGraph checkpoint 保存单次工作流状态。
- ✅ **Business long-term memory**：`DecisionMemory` / `OutcomeFeedback` SQL 表（`models.py:172-220`）是服务重启后仍保存的事件记忆，不是 session_state。
- ✅ **写入与召回控制**：`persist_decision_memory_node()` 在决策后写入；`recall_long_term_memory_node()` 只调用 outcome-verified 记录；`memory_rules.py` 依据结果评分、产品/决策类型/供应商/波动度排序。
- 🟡 **避免错误记忆**：要求反馈用户白名单并根据实际成本、交期、缺货、质量、服务水平打分；但实际结果可人工输入，未与收货/财务系统对账，无法防止虚假反馈。
- 🔴 **过期/遗忘/修正**：没有 TTL、版本、撤销、更正或偏差漂移策略。
- 🔴 **语义召回**：不是向量/embedding memory；只是一张带规则排序的业务事件表。

这应表述为“有结果验证门槛的持久化业务事件记忆”，不要宣传为通用长期记忆系统。

## 9. Reflection 审计

✅ `reflection_agent_node()` 独立于 coordinator，先运行 `deterministic_reflection_checks()`，再让 LLM 输出严格 `ReflectionOutput`，失败会 `REVISE`，达到上限会 `ESCALATE` 并由决策门禁转人工审批。证据：`reflection_agent.py:13-105`、`reflection_rules.py`、`graph.py:105-112`。

⚠️ 它的业务收益有限：Coordinator 在 `coordinator_agent.py:45-72` 只重建固定 action/supplier/quantity/expedite；反思 prompt 也禁止修改它们。因此反思只能改善说明、证据覆盖和风险表达，不能纠正预测、供应商成本模型或采购数量。将其称为“真正的自我反思”会夸大，应称为“有界的解释质量审查与升级门禁”。

## 10. HITL 审计

✅ 完整的技术链路存在：决策 hash/snapshot hash/角色阈值在 `approval_policy.py`；任务持久化在 `ApprovalQueue`；`interrupt` 后等待；`review_decision()` 使用 `Command(resume=...)`；`post_approval_routing()` 只允许 `APPROVED` 到执行。审批数据变化会标为 stale（`db_service.review_approval_request()`）。

⚠️ 不是企业级权限体系：身份来源只是 UI 提交的 email + 服务端环境变量 `HITL_APPROVER_BINDINGS` 白名单，非 SSO/OIDC/JWT；无组织、岗位、委托、审批流版本、电子签名或不可篡改审计。`requested_by` 固定为 `system-agent`（`human_approval_node.py:53`），并非真实申请人分离。

## 11. Safety 审计与故障推演

| 场景 | 当前行为 | 结论 |
|---|---|---|
| 本应采购 20，却被 LLM 写成 2000 | `execution_node.validate_final_decision()` 重算并要求 quantity 等于确定性 plan，且必须在供应商白名单。 | ✅ 在当前单进程模型内能阻止。 |
| 重试两次创建两张 PO | `ExecutionRecord.decision_key` 唯一约束 + `_execution_lock`，`create_purchase_order()` 复用已有记录。 | 🟡 同库并发有保护；没有分布式锁/外部 ERP 幂等键。 |
| 批准后库存变化 | `review_approval_request()` 比较 snapshot hash，变更时拒绝批准/标 stale。 | 🟡 批准时检查；执行前只用原 state 重算，未强制重新读取 DB，所以批准与执行之间仍有 TOCTOU 窗口。 |
| Tool API timeout | 只读工具通过 `asyncio.wait_for` 标记 error 并继续；真实外部 API 不存在。 | 🟡 有 timeout，无 retry/circuit breaker。 |
| LLM malformed JSON | `invoke_structured()` 依赖 structured output / Pydantic；反思有 fail-closed；业务 decision 不依赖 LLM JSON。 | 🟡 explanation agent 抛错会使流程失败，缺少统一重试/降级。 |
| RAG 找错合同 | 仅相似度阈值 + 类型加权 + citation ID；无合同生效期、供应商/产品 metadata 过滤、人工复核或 claim-level grounding。 | 🔴 不能可靠阻止。 |
| Prompt / tool injection | 文档内容和工具结果原样拼入 prompt；没有不可信内容分隔、注入检测、来源权限或敏感字段脱敏。 | 🔴 明显缺口。 |

## 12. Evaluation 审计

✅ `evals/scenarios.json` 有 3 个固定场景；`src/evaluation/runner.py` 用临时 SQLite 库隔离运行；`evaluator.py` 评估业务一致性、工具 allowlist/trace、citation ID、反思边界、HITL、长期记忆，支持 baseline score regression。单测 `tests/test_p6_agent_evaluation.py` 覆盖篡改 quantity、伪造 citation、未验证 memory 等。

⚠️ 这是一套**规则回归测试**，不是完整 Agent Eval：没有人工标注 retrieval benchmark、没有 Recall@K/MRR、没有 LLM-as-a-Judge 校准、没有真实 tool success rate、没有任务成功率跨真实数据的统计、没有幻觉文本检测，也无线上采样评测。仅 3 个 demo 产品，且 `expected_min_tool_calls` 都为 0，未真正断言 LLM 会自主正确选工具。

## 13. Engineering / Observability / Persistence 审计

### Persistence

- ✅ 业务数据、approval、execution ledger、decision memory/outcome：SQLAlchemy SQLite 表。
- ✅ graph checkpoint：`data/workflow_checkpoints.sqlite`，通过 `AsyncSqliteSaver`。
- 🟡 RAG index：Chroma 本地 persist directory。
- 🔴 数据库迁移：无 Alembic/版本化 migration；`ensure_runtime_schema()` 运行时 `ALTER TABLE`，难以生产演进。
- 🔴 外部集成：无 ERP/WMS/TMS、消息队列、对象存储或真实供应商 API。种子数据为三产品演示场景（`db_init.py:25-129`）。

### Observability

- 🟠 有：LLM `run_name/tags`，以及 `backend_interface._agent_debug_log()` 写入 `.cursor/debug.log`。
- 🔴 缺：统一 trace/run correlation、持久化 step transition、prompt/model version、token、cost、latency、tool 输入输出脱敏审计、error dashboard、告警、SLO。`llm_config.verify_tracing()` 只打印 LangSmith 是否配置，并不保证运行 trace 已采集。

### 验证状态

本次在当前环境执行 `PYTHONPATH=src python -m unittest discover -s tests -v`：**66 tests OK，13 skipped**。跳过项包括 SQLAlchemy 数据库安全、Chroma 向量集成和 LangChain `StructuredTool` 集成，因此不能把结果表达为“完整生产链路已验证”。

## 14. Top 10 Weaknesses

| # | 问题与代码证据 | 面试官为何在意 / 当前风险 | 必须修改 | 难度 | 建议 |
|---:|---|---|---|---|---|
| 1 | “多 Agent 决策”实际是固定规则 + LLM 文案：`business_rules.py`、`coordinator_agent.py:45-72` | 叙事与代码不一致会直接伤害可信度。 | 是（至少改表述） | 低 | 明确定位为 governed workflow；若需自治，只允许 LLM 在受约束候选方案中提出建议并由规则验证。 |
| 2 | 无真实身份/RBAC：`approval_policy.py:107-142` 依赖 env email whitelist | 高风险采购审批不能信任 UI 身份字段。 | 是 | 中 | FastAPI auth middleware + JWT/OIDC mock、server-side principal、审计 actor。 |
| 3 | 批准与执行间未刷新实时数据 | 库存变化仍可能按旧快照下 PO。 | 是 | 中 | execution 前重新拉取快照、重新计算 decision hash；不一致则 stale 并重审。 |
| 4 | RAG 无 ACL/版本/合同适用性 | 检到错误或过期合同会造成错误建议。 | 是 | 中 | metadata 加 supplier/product/region/effective dates/version/owner；检索前 filter，返回证据版本。 |
| 5 | 无完整运行可观测性：`backend_interface.py:28-54` | 无法回答“为什么这次 Agent 做错”。 | 是 | 中 | OpenTelemetry/LangSmith 任选其一，记录 run/node/model/token/latency/tool/error/cost，敏感脱敏。 |
| 6 | 集成测试被跳过 | 关键承诺没在 Python 3.12 target 环境验证。 | 是 | 低 | 建 venv/CI，`pip install -r requirements.txt` 后强制零 skip 的核心集成测试。 |
| 7 | RAG 在线 `sync()`：`retriever.py:46-49` | 延迟、索引竞态和线上故障耦合。 | 强烈建议 | 中 | 单独 ingestion job，在线只读 versioned index。 |
| 8 | Eval 仅 3 demo case，工具期望为 0 | 无法证明 RAG/tool/LLM 的实际质量。 | 强烈建议 | 中 | 20-50 条脱敏黄金集，分别量 retrieval、tool selection、decision safety、HITL。 |
| 9 | 嵌套 dict state 无 version/schema | 恢复/演进时难发现脏数据或字段漂移。 | 强烈建议 | 中 | Pydantic state DTO / node contracts，关键边界 fail closed。 |
| 10 | 采购后生命周期是模拟的 | 没有 supplier confirm、ASN、收货、质检、入库、结算；结果反馈可手填。 | 视岗位 | 中 | 最少增加事件驱动 mock adapter 与状态机，不要伪装 ERP 集成。 |

## 15. P0 / P1 / P2 整改计划

### P0：面试前必须修

1. **纠正系统定位和演示口径**：将“自主 Multi-Agent 采购”改成“规则引擎主导、LLM 辅助调查/解释/审查的受治理工作流”；在 UI 和 README 说明 LLM 不拥有写采购权限。
2. **执行前 freshness check**：审批恢复后的 `execution_node` 必须重新读取当前 snapshot、重算决策并验证 hash；变化则冻结旧审批并创建新审批任务。
3. **完成目标环境集成验证**：Python 3.12 中装全依赖，CI 中跑 RAG、SQLite checkpoint、PO 幂等、审批 resume 的真实集成测试，核心测试不允许 skip。
4. **服务端身份边界**：至少使用服务端 session/JWT mock 提供 `actor`，移除由 UI 提交角色作为身份来源的设计；审批白名单只作授权映射。
5. **基础可观测性**：每次 workflow 一条 run_id trace，记录 node 状态、tool call、LLM 模型/耗时/token（或明确不可得）、错误和审批/执行事件。

### P1：强烈建议

1. 企业知识 metadata 与适用性过滤：供应商、产品、区域、生效/失效日期、文档版本/owner；执行前显示引用版本。
2. 从在线路径移出索引同步；加入 RAG freshness/status 和重建任务。
3. 用 Pydantic 定义关键 state、tool response、审批 request、execution request，建立 schema version。
4. 扩大评测为可复现实例集：retrieval、tool-selection、反思、HITL、端到端安全五类分别出指标。
5. 采购单后事件状态机与 adapter interface：只做本地 mock adapter，但明确接口与幂等/失败补偿。

### P2：锦上添花

1. 在“候选方案”范围内让 LLM 提出策略偏好，并以规则/优化器严格验证；不要让 LLM 直接写单。
2. 并行执行独立只读分析，增加 deadline/budget，降低长串行 latency。
3. Cross-encoder reranker、混合检索、引用 claim verifier。
4. PostgreSQL、任务队列、分布式锁、事件总线、多租户 ACL。

## 16. 面试风险问题（攻击清单）

### 当前可以较好回答

1. 为什么不让 LLM 决定采购数量？
2. 如何防止 LLM 把 20 改为 2,000？
3. 如何保证采购单幂等？
4. 高风险采购如何审批并恢复？
5. 审批后库存改变怎么办？（可回答“批准阶段检查，但执行前 freshness 尚需 P0 修复”。）
6. RAG 文档如何入库、分块和增量更新？
7. 工具为什么只读、如何控制参数/超时/上限？
8. 长期记忆为什么只召回有结果的记录？
9. 反思为什么有上限且失败升级？
10. 如何隔离离线 eval 不产生真实采购单？
11. 为什么 LangGraph 有价值？（HITL checkpoint/resume。）
12. 为什么不把业务规则交给 LLM？

### 30 个面试官会追问的问题

1. 这个项目去掉 LLM 后还剩下多少能力？
2. 既然采购决策全由规则引擎决定，为什么叫 Agent？
3. 为什么不是一个 Agent + 若干 deterministic service，而要四个 Agent？
4. 各 Agent 的独立权限和私有 context 是什么？
5. 谁在动态决定下一步调用哪个 Agent？
6. Coordinator 为什么不真正协调候选方案或解决冲突？
7. 为什么固定 pipeline 不用普通 Python，而要 LangGraph？
8. continuous graph 如何避免无限循环和重复下单？
9. RAG 是向量检索还是关键词检索，能否展示 index/query 证据？
10. 合同检索如何确保是该供应商、该地区、且仍生效的版本？
11. RAG 召回错了会怎样？citation 如何证明文本结论受它支持？
12. 文档中存在 prompt injection 时如何防护？
13. 为什么每次在线查询前同步索引？
14. Tool 参数和返回值分别如何校验？
15. Tool timeout 后是否重试、是否产生半完成副作用？
16. LLM 被 prompt injection 后能否绕过工具白名单？
17. 谁授权 Agent 调用采购系统，权限如何按人/租户/产品隔离？
18. 如何防止 20 件被 hallucination 成 2,000 件？
19. 两次 resume 或多个审批人同时批准会怎样？
20. 批准后库存/供应商报价变化，执行前如何保证批准仍有效？
21. 真实 ERP 创建采购单超时但实际成功时如何避免重复？
22. 如何处理供应商拒单、部分到货、质检失败和撤单？
23. Long-term memory 何时写入、何时召回、如何删除错误记忆？
24. 为什么规则排序的历史记录值得叫 memory，而非普通数据库查询？
25. reflection 哪些错误真能纠正？不能纠正时为什么保留它？
26. malformed structured output 时每个 node 的降级策略是什么？
27. 你的 eval 黄金集来自哪里，如何避免只测自己写的规则？
28. RAG Recall@K、tool accuracy、task success、hallucination rate 分别是多少？
29. 如何定位某次错误的 prompt、token、模型版本、tool 结果和 state transition？
30. 如何部署、扩展、迁移数据库、保护密钥并控制 token/cost/latency？
31. Python 3.12 与 Chroma/torch/sentence-transformers 的兼容性在 CI 中如何验证？
32. 多租户下如何隔离合同、审批和记忆？

当前最容易陷入被动的是 2-6、10-13、17、20-22、27-32。不要用“未来会做”硬撑；应诚实解释当前边界和优先级。

## 17. 推荐目标架构（在现有项目上演进）

```text
实时业务快照（ERP/WMS adapter，read-only）
  -> deterministic planning service（预测 / 约束 / 候选方案 / 风险）
  -> RAG service（离线 ingest；metadata ACL + version/effective-date filter）
  -> LLM investigator（受限 read tools） + LLM explanation/reviewer
  -> policy gate（freshness / spending / risk / evidence）
  -> LangGraph HITL checkpoint
  -> execution adapter（idempotency key + pre-execution revalidation）
  -> PO lifecycle events / outcome capture
  -> durable memory + offline evaluation + trace store
```

这个目标保留当前最有价值的设计：**规则决定高风险事实、LLM 受边界约束、LangGraph 只承担需要持久化的工作流状态**。不需要为了“高级”再造 LLM supervisor、无限反思、全自动写工具或复杂 Multi-Agent 市场机制。

## 18. 不值得继续投入的方向

1. 不要继续增加“需求 Agent / 库存 Agent / 风险 Agent”数量；当前没有独立决策权，新增角色只会更像堆概念。
2. 不要让 LLM 直接决定数量、供应商或写采购单来换取“自治”标签；这会破坏当前最成熟的安全卖点。
3. 不要扩展无限循环监控 graph；应先实现事件触发、freshness 和幂等。
4. 不要先做复杂的向量化长期记忆；先保证真实 outcome、TTL 和修正流程。
5. 不要先接入多种模型/多 Agent 框架；先完成一条可靠、可观测、可评测的端到端链路。
6. 不要优先美化 UI 或继续堆标签；面试竞争力更取决于 P0 的证据链。

## 19. 最终面试准备建议

### 现在就去面试：重点讲

- 高风险领域中，LLM 不拥有最终写权限；规则引擎与执行前校验是 guardrail。
- LangGraph checkpoint + interrupt/resume 实现了可恢复的人工审批，而不是单纯“等待审批”文案。
- `ExecutionRecord` / decision key 实现单库幂等，outcome-verified memory 避免把未验证建议当经验。
- Chroma 增量向量知识库和 LLM 只读工具调查如何补充实时快照。
- 离线隔离评测如何检查安全不变量与回归。

### 现在不要重点讲

- “真正的自治 Multi-Agent 协作”。
- “生产级企业 RAG”。
- “完善的 RBAC/企业级审批”。
- “自我反思会修正采购策略”。
- “已接入真实采购/物流系统”。

### 面试前必须完成（最多 5 项）

1. 执行前实时重验 + stale re-approval。
2. Python 3.12 完整依赖环境的端到端 CI，消除核心 skip。
3. 服务端 actor/auth mock 与审批身份边界。
4. 最小 trace/metrics/audit 体系。
5. RAG 文档适用性 metadata filter + 小型黄金检索集。

### 时间规划

| 时间 | 优先工作 |
|---|---|
| 只有 3 天 | P0 第 1、2、3 项；录制一条从决策、审批、恢复到幂等执行的可复现 demo。 |
| 只有 1 周 | 完成全部 P0；补 20 条 RAG/tool/安全场景；把 UI 证据和真实 trace 对齐。 |
| 有 2 周 | 完成 P0 + P1 的 RAG metadata、离线 ingest、采购生命周期 mock adapter 和更完整评测报告。 |

## 20. 最终建议

面试时最专业的叙事不是“我做了一个全自动多 Agent 采购员”，而是：

> 我把 LLM 放在它擅长且可控的位置——知识检索、只读调查、解释和审查；把采购数量、供应商约束、审批和写操作放在可复算、可审计、可恢复的确定性边界内。当前是单租户原型，我已识别并优先补齐 freshness、身份、RAG 适用性、观测和集成验证。

这会比堆砌 Agent 名词更能获得 Agent Engineer 面试官认可。
