# Agent 开发岗位简历素材：受治理的供应链决策 Copilot

> 本文依据当前仓库代码、测试与 `artifacts/evaluations9.14/` 下的评测产物整理。默认用于中文一页简历，目标岗位为 Agent Engineer、LLM Application Engineer 或 AI 应用开发工程师。

## 一、推荐项目名称

**受治理的供应链决策 Copilot｜LangGraph Agent 应用**

可选名称：

- 企业级供应链智能决策与审批 Agent
- Multi-Agent Supply Chain Control Tower
- 基于 LangGraph 的供应链决策 Agent 工作流

不建议使用“全自主采购机器人”“生产级多智能体采购平台”等名称。系统当前是本地原型，专业 Agent 由固定图编排，采购核心数值由确定性规则计算。

## 二、一页简历可直接使用版

**受治理的供应链决策 Copilot｜核心开发者（个人项目可写“独立开发”）**  
`Python` `LangGraph` `LangChain` `Pydantic` `Chroma` `BGE Embeddings` `FastAPI` `Streamlit` `SQLite` `SQLAlchemy` `LangSmith`

项目简介：面向库存补货与采购风险场景，构建覆盖实时数据读取、企业知识检索、自主调查、规则决策、风险审批、采购执行和结果反馈的 Agent 应用；通过确定性业务规则约束 LLM 权限，使决策过程可解释、可审计、可恢复。

- 设计并实现 **14 节点 LangGraph 状态工作流**，串联数据快照、RAG、6 类只读工具、需求/库存/风险/物流/协调等职责节点、有限反思、风险门禁、人工审批和长期记忆；利用 reducer 与 fan-out/fan-in 并行执行风险、物流分析，并通过条件边控制修订、审批与执行路径。
- 将采购决策拆分为“**确定性规划核心 + 有界 LLM 推理层**”：以 7/14/28 日加权移动平均、安全库存、库存位置、MOQ/包装倍数/供货上限和综合风险成本计算动作、数量、供应商与运输方式；LLM 仅生成结构化解释与审查意见，写入前再次依据最新数据库快照校验，阻断数量篡改、非合格供应商与过期审批。
- 构建基于 **Chroma + BGE Embeddings** 的企业知识 RAG，支持 Markdown/TXT/PDF/DOCX 增量索引，按文档状态、版本、生效期、产品、供应商和区域执行适用性过滤，并保留引用来源；在 59 份合成企业知识文档、121 个 chunk 和 52 条黄金检索用例上取得 **98.08% 用例通过率、98.08% Recall@5、93.59% MRR、0% 禁用文档泄漏率、9.29 ms P95 检索延迟**。
- 实现受限 Tool Calling 与可恢复 HITL：为 6 个结构化只读工具增加 Pydantic 参数边界、白名单、调用预算、超时、缓存、返回值校验和审计 trace；使用 `interrupt`、SQLite checkpointer 与 `Command(resume)` 持久化审批现场，并结合角色映射、决策哈希、快照失效检测和幂等键保证同一决策不会越权或重复创建采购单。
- 打通 FastAPI 服务边界与 Streamlit 控制塔，覆盖决策、审批、采购履约、结果反馈和决策档案；将已验证结果写入长期记忆，并按业务场景、结果质量与时效召回。建立 **89 个单元/集成测试用例**及离线 Agent/RAG 回归评测，覆盖业务正确性、grounding、工具权限、反思上限、HITL、幂等与记忆安全。

## 三、篇幅有限时的三条版本

**受治理的供应链决策 Copilot｜LangGraph / RAG / Tool Calling / HITL**

- 基于 LangGraph 构建 14 节点 Agent 工作流，将企业知识检索、6 类只读工具、5 个专业职责节点、有限反思、风险门禁、人工审批与结果记忆串成可恢复的采购决策闭环。
- 采用确定性补货/寻源模型约束 LLM：综合需求趋势、安全库存、在途、MOQ、供应商交付/质量和风险成本计算采购方案，LLM 仅负责结构化解释与审查；执行前重读实时快照并通过决策哈希、角色校验和幂等键拦截越权、陈旧或重复写入。
- 建设带版本、生效期和业务范围治理的 Chroma RAG；在 52 条合成黄金用例上实现 98.08% Recall@5、93.59% MRR、0% 禁用文档泄漏率，并配套 89 个测试用例和 LangSmith/JSONL 可观测与回归评测。

## 四、极简两条版本

- 基于 LangGraph 构建 14 节点供应链决策 Agent，融合 RAG、6 类只读 Tool Calling、有限反思、可中断恢复的人工审批与结果记忆；以确定性规则约束采购动作、供应商和数量，执行前进行实时重校验与幂等保护。
- 构建带版本/有效期/业务范围过滤的 Chroma 企业知识库；52 条合成黄金检索用例取得 98.08% Recall@5、93.59% MRR 和 0% 禁用文档泄漏率，并以 89 个测试用例覆盖 grounding、工具权限、HITL、反思与执行安全。

## 五、30 秒面试介绍

我做的是一个受治理的供应链决策 Copilot。系统用 LangGraph 编排从数据读取、RAG 检索、Agent 工具调查到风险审批和采购执行的完整流程。和普通聊天机器人不同，我把采购动作、供应商和数量交给可复算的业务规则，LLM 只负责从 SOP、合同和历史事件中补充证据、调用白名单内的只读工具并生成解释。高风险方案会通过 interrupt 持久化暂停，人工批准后系统还会根据最新库存重新校验，最后以幂等方式创建本地模拟采购单，并把实际履约结果沉淀为后续可召回的业务记忆。

## 六、两分钟项目讲述框架

1. **问题**：补货决策同时依赖结构化的库存、需求、在途和供应商数据，以及非结构化的合同、SOP 和历史事件；纯规则难以解释长尾情况，让 LLM 直接下单又存在幻觉和越权风险。
2. **架构**：LangGraph 管理显式 state 和 14 个节点。流程先读取一致性快照，再做 RAG 和只读工具调查，随后由多个专业职责节点生成结构化分析，协调节点发布规则已经算好的方案，critic 最多反思 2 轮。
3. **关键取舍**：LLM 不拥有采购写权限。数量、供应商、是否加急由确定性模型计算；RAG 证据必须通过版本、生效期与业务范围过滤；高风险决策必须经过持久化审批。
4. **可靠性**：审批使用 checkpoint 恢复原工作流，批准时校验角色、决策哈希和快照；执行前再次读取实时数据，库存变化会使旧审批失效；采购写入使用 decision key 防止重试产生重复订单。
5. **评测**：用黄金集分别评估 Agent 行为和 RAG 排名，检查引用真实性、工具白名单、调用预算、反思上限、HITL 和长期记忆；RAG 在 52 条合成用例上通过 51 条，Recall@5 为 98.08%，无失效/草稿文档泄漏。

## 七、项目架构速记

```text
FastAPI / Streamlit
        ↓
Data Snapshot → Governed RAG → Read-only Tool Agent
        ↓
Demand → Memory Recall → Inventory → [Risk || Logistics]
        ↓
Coordinator ↔ Bounded Reflection
        ↓
Risk Gate → Decision Memory → Auto Execute / HITL Interrupt
        ↓
Live Revalidation → Idempotent Procurement Adapter → Outcome Feedback
```

## 八、与 Agent 岗位的能力映射

| 岗位关键词 | 项目中的可验证实现 |
|---|---|
| Agent Workflow | LangGraph StateGraph、显式状态、条件边、并行 fan-out/fan-in、checkpoint |
| Tool Calling | 6 个 StructuredTool、动态选择、allowlist、预算、超时、缓存、schema 校验 |
| Structured Output | Pydantic schema、`with_structured_output`、未知字段拒绝 |
| RAG | Chroma、BGE、离线发布、增量索引、metadata 过滤、来源引用、黄金集评测 |
| Guardrails | 确定性业务规则、供应商白名单、数量一致性、证据真实性与执行前重校验 |
| Human-in-the-loop | `interrupt`、持久化审批任务、`Command(resume)`、角色与决策哈希校验 |
| Reflection | 独立 critic、确定性检查、最多 2 轮修订、异常时 fail-closed 升级 |
| Memory | 决策/采购/结果关联，只召回已有实际结果的业务案例 |
| Reliability | 幂等键、陈旧审批失效、受限采购状态机、不可变履约事件 |
| Evaluation | Agent 多维规则评测、RAG Recall/MRR/NDCG/泄漏/延迟评测、基线回归 |
| Observability | LangSmith 集成、按 workflow run 记录 JSONL 节点/模型/工具事件与耗时 |
| Application Engineering | FastAPI、Streamlit、SQLite/SQLAlchemy、可替换 Procurement Adapter |

## 九、项目数据与指标的正确说法

当前数据与知识库为确定性生成的企业规模**合成演示数据**，可以证明工程链路和评测方法，不能表述为真实客户或生产数据。

- 数据规模：123 个产品、33 个供应商、487 个供应商报价、43,968 条需求历史、242 个采购单、240 条已反馈决策记忆。
- 知识规模：59 份文档、121 个 chunk，覆盖政策、合同、事件、处置手册和负向治理样本。
- RAG 结果：52 条用例通过 51 条；Recall@5 98.08%，MRR 93.59%，适用性通过率 100%，禁用文档泄漏率 0%，P95 9.29 ms。
- Agent 结果：当前归档评测包含 3 条场景，7 个维度均为 100 分且无关键安全失败；样本量较小，适合说明评测框架，不适合宣称模型已经达到生产准确率。
- 测试资产：仓库包含 89 个测试方法，覆盖业务规则、API 合约、图拓扑、RAG、工具、反思、HITL、长期记忆、评测和可观测性。

## 十、建议删除或避免的表述

- “多个 Agent 自主协商并给出全局最优采购策略”：当前是固定工作流中的多职责节点，没有自治协商或最优化证明。
- “LLM 自动预测需求并决定采购量”：预测、采购量、供应商和运输方式由 Python 规则计算。
- “生产级系统 / 已接入 ERP、WMS、TMS”：当前采购 Adapter 使用本地 SQLite，真实外部系统尚未接入。
- “RAG 准确率 100%”：最新 52 条黄金集有 1 条失败；应写具体的 Recall@5、MRR 与泄漏率。
- “系统会自主学习”：当前长期记忆是结果已验证的 SQL 案例召回，不会自动更新模型参数或策略。
- “全部 89 个测试通过”：本次 Windows/Python 3.9 环境未完整通过测试，修复并用项目要求的 Python 3.12 复验后再使用。

## 十一、投递不同岗位时的侧重点

### Agent / LLM 应用工程师

优先保留 LangGraph、Tool Calling、结构化输出、RAG、HITL、Reflection、Memory 和 Evaluation，弱化供应链算法细节。

### Python 后端 + AI 工程师

优先保留 FastAPI API 合约、Pydantic 边界、SQLAlchemy 持久化、幂等、状态机、Adapter 和测试，再补一条 Agent 工作流。

### RAG 工程师

优先保留离线发布/增量索引、文档治理 metadata、适用性过滤、引用、52 条黄金集及 Recall/MRR/NDCG/泄漏/延迟指标，同时说明目前没有独立 reranker 和权限隔离。

## 十二、面试前应完成的可信度修复

1. 使用 README 要求的 Python 3.12 新建干净环境并执行全量测试；当前 `.venv` 是 Python 3.9，导致 `dict | None` 注解在一个模块导入时失败。
2. 处理 Windows 下 Chroma 测试结束时临时索引文件仍被占用的问题，确保测试命令可稳定得到绿色结果。
3. 修复 RAG 黄金集唯一失败用例 `governance_no_expired_contract`，重新生成评测报告后再决定是否更新简历数字。
4. 准备一个高风险 SKU 的现场演示：生成方案 → 查看引用和工具 trace → 进入审批 → 恢复执行 → 录入结果反馈。
5. 在 GitHub 提交前清理 `.env`、本地数据库、索引与 IDE 文件，补充架构图、评测报告和一键启动说明。

## 十三、英文简历短版

**Governed Supply Chain Decision Copilot** — Python, LangGraph, LangChain, Chroma, FastAPI, Streamlit

- Built a 14-node LangGraph workflow spanning governed RAG, six read-only tools, specialist analysis, bounded reflection, risk-based human approval, idempotent execution, and outcome-verified memory.
- Kept procurement actions deterministic by calculating demand, safety stock, inventory position, supplier constraints, and risk-adjusted cost in Python; restricted the LLM to structured explanation and critique, with live pre-execution revalidation against stale or tampered decisions.
- Implemented metadata-governed Chroma retrieval over 59 synthetic enterprise documents; achieved 98.08% Recall@5, 93.59% MRR, zero forbidden-document leakage, and 9.29 ms P95 latency across 52 golden retrieval cases.
- Added durable HITL with LangGraph interrupt/resume, role and decision-hash validation, tool allowlists and budgets, JSONL/LangSmith tracing, and 89 unit/integration test cases covering safety and workflow behavior.
