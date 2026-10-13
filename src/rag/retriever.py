from __future__ import annotations

from functools import lru_cache
from datetime import date
from typing import Any

from .config import RAGSettings
from .embeddings import create_embedder
from .vector_store import ChromaKnowledgeBase


SOURCE_DISPLAY_NAMES = {
    "inventory_policy.md": "库存补货策略",
    "supplier_sla.md": "供应商服务等级与风险策略",
    "logistics_policy.md": "物流与加急运输策略",
    "approval_policy.md": "人工审批策略",
    "historical_cases.md": "历史供应链案例",
}

AGENT_QUERIES = {
    "demand": "需求风险、缺货、补货点、采购单和在途供应的企业规则与历史事件",
    "inventory": "库存补货、补货数量、安全库存、采购单和库存恢复 SOP",
    "risk": "供应商可靠性、合同 SLA、延迟违约、物流风险及人工审批规则",
    "logistics": "加急运输条件、运输时效、成本权衡、延误风险和历史事件",
    "coordinator": "采购决策、合同约束、供应商选择、风险接受、审批与历史案例",
}

PREFERRED_TYPES = {
    "demand": {"sop", "historical_event"},
    "inventory": {"sop", "historical_event"},
    "risk": {"contract", "sop", "historical_event"},
    "logistics": {"sop", "historical_event"},
    "coordinator": {"contract", "sop", "historical_event"},
}


def _deduplicate_documents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest-ranked chunk per governed document.

    RAG context benefits more from five distinct documents than five nearby
    chunks from one document.  Chunks are still the indexed/searchable unit;
    this is result diversification after scoring and applicability filtering.
    """
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda value: value["score"], reverse=True):
        key = str(item.get("document_id") or item["id"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


@lru_cache(maxsize=1)
def get_knowledge_base() -> ChromaKnowledgeBase:
    settings = RAGSettings.from_env()
    return ChromaKnowledgeBase(settings, create_embedder(settings))


def retrieve_knowledge_for_snapshot(
    snapshot: dict[str, Any], top_k: int = 3
) -> dict[str, Any]:
    """Retrieve relevant SOPs, contracts, and historical events for each agent."""
    settings = RAGSettings.from_env()
    store = get_knowledge_base()  # 获取向量知识库
    if store.count == 0:  # 检查索引是否为空
        raise RuntimeError(
            "Enterprise knowledge index is empty. Publish it offline with: "
            "python -m src.rag.ingest --rebuild"
        )
    scenario_context = _build_scenario_context(snapshot)  # 构造自然语言场景
    applicability = _applicability_context(snapshot)  # 构造业务适用性条件
    by_agent: dict[str, list[dict[str, Any]]] = {}

    for agent_name, intent in AGENT_QUERIES.items():  # 遍历所有角色 的检索意图
        query = f"{intent}\n当前业务场景：{scenario_context}"  # 拼接最终query
        candidates = store.search(  # 执行向量召回
            query,
            top_k=top_k,
            candidates=max(top_k, top_k * settings.candidate_multiplier),
            where={"status": "active"},
        )
        candidates = [  # 相似度与业务适用性过滤
            item for item in candidates
            if item["score"] >= settings.min_score and _is_applicable(item, applicability)
        ]
        preferred = PREFERRED_TYPES.get(agent_name, set())  # 获取agent偏好的文档类型
        for item in candidates:  # 保存原始向量分数
            item["vector_score"] = item["score"]
            if item.get("document_type") in preferred:
                item["score"] = round(min(1.0, item["score"] * 1.05), 4)  # 根据偏好轻量重排
            item["display_id"] = _display_source_id(item)  # 生成可读的来源id
        by_agent[agent_name] = _deduplicate_documents(candidates)[:top_k]

    unique = {chunk["id"]: chunk for chunks in by_agent.values() for chunk in chunks}  # 合并所有agent结果并去重
    return {
        "query_context": scenario_context,
        "applicability_context": applicability,
        "by_agent": by_agent,
        "all": sorted(unique.values(), key=lambda item: item["score"], reverse=True),
        "index_stats": {
            "knowledge_dir": str(settings.knowledge_dir),
            "persist_dir": str(settings.persist_dir),
            "collection": settings.collection_name,
            "documents": None,
            "chunks": store.count,
            "updated_chunks": 0,
            "embedding_model": store.embedder.identity,
            "min_score": settings.min_score,
            "retrieval": "离线发布 Chroma 索引 + 版本/生效期/业务范围过滤 + 类型轻量重排",
            "index_mode": "offline_published_read_only",
        },
    }


def search_enterprise_knowledge(
    query: str, top_k: int = 3, document_type: str = "all",
    applicability_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run an on-demand semantic search for the autonomous investigation agent."""
    settings = RAGSettings.from_env()
    store = get_knowledge_base()
    where = {"status": "active"}
    if document_type != "all":
        where = {"$and": [{"status": "active"}, {"document_type": document_type}]}
    candidates = store.search(
        query,
        top_k=top_k,
        candidates=max(top_k, top_k * settings.candidate_multiplier),
        where=where,
    )
    results = []
    for item in candidates:
        if item["score"] < settings.min_score:
            continue
        if not _is_applicable(item, applicability_context or {}):
            continue
        enriched = dict(item)
        enriched["vector_score"] = item["score"]
        enriched["display_id"] = _display_source_id(item)
        results.append(enriched)
    return _deduplicate_documents(results)[:top_k]


def format_retrieved_context(
    retrieved_knowledge: dict[str, Any] | None,
    agent_name: str,
    max_chars: int = 2200,
    supplier_id: int | None = None,
) -> str:
    chunks = _agent_chunks(retrieved_knowledge, agent_name, supplier_id)  # 过滤块
    if not chunks:
        return "未检索到相关企业知识，请仅依据实时数据库和确定性业务规则判断。"
    lines: list[str] = []
    used_chars = 0
    for chunk in chunks:
        text = (
            f"[{chunk.get('display_id', chunk['id'])} | 类型={chunk.get('document_type', 'general')} "
            f"| 文档={chunk.get('document_id', 'unknown')} v{chunk.get('version', 'unknown')} "
            f"| 有效期={chunk.get('effective_from', '?')}~{chunk.get('effective_to', '?')} "
            f"| 向量相似度={chunk.get('vector_score', chunk['score']):.3f}]\n"
            f"标题：{chunk.get('title', '')} / {chunk.get('section', '')}\n"
            f"内容：{chunk['content']}"
        )
        if used_chars + len(text) > max_chars:
            break
        lines.append(text)
        used_chars += len(text)
    return "\n\n".join(lines) if lines else "未检索到相关企业知识。"


def get_retrieved_source_ids(
    retrieved_knowledge: dict[str, Any] | None,
    agent_name: str,
    supplier_id: int | None = None,
) -> list[str]:
    chunks = _agent_chunks(retrieved_knowledge, agent_name, supplier_id)
    return [chunk.get("display_id", chunk["id"]) for chunk in chunks]


def _agent_chunks(
    retrieved_knowledge: dict[str, Any] | None,
    agent_name: str,
    supplier_id: int | None,
) -> list[dict[str, Any]]:
    retrieved_knowledge = retrieved_knowledge or {}
    chunks = retrieved_knowledge.get("by_agent", {}).get(agent_name, [])
    if supplier_id is None:
        return chunks
    context = dict(retrieved_knowledge.get("applicability_context") or {})
    context["supplier_ids"] = [supplier_id]
    return [item for item in chunks if _is_applicable(item, context)]


def _display_source_id(item: dict[str, Any]) -> str:
    source = str(item.get("source", "unknown"))
    source_name = source.rsplit("/", 1)[-1]
    display_name = SOURCE_DISPLAY_NAMES.get(source_name, source_name)
    location = f"第{item['page']}页" if item.get("page") else str(item.get("section", "片段"))
    index = int(item.get("chunk_index", 0)) + 1
    return f"{display_name}#{location}-片段{index}"


def _build_scenario_context(snapshot: dict[str, Any]) -> str:
    product = snapshot.get("product", {})  # 产品信息
    inventory = snapshot.get("inventory", {})  # 库存信息
    suppliers = snapshot.get("suppliers", [])  # 供应商列表
    purchase_orders = snapshot.get("purchase_orders", [])  # 采购单列表
    shipments = snapshot.get("shipments", [])  # 运输单列表
    supplier_text = "；".join(  # 拼接供应商描述
        f"{s.get('name')}（可靠性 {s.get('reliability_score')}，交付 {s.get('lead_time_days')} 天）"
        for s in suppliers
    ) or "无"
    shipment_text = "；".join(  # 运输单描述
        f"运输单 {s.get('id')}，状态 {s.get('status')}，预计到达 {s.get('expected_arrival')}"
        for s in shipments
    ) or "无"
    return (
        f"产品 {product.get('name')}，SKU {product.get('sku')}，"
        f"库存 {inventory.get('quantity')}，补货点 {inventory.get('reorder_point')}，"
        f"活跃采购单 {len(purchase_orders)} 个，运输单 {len(shipments)} 个；"
        f"供应商：{supplier_text}；运输：{shipment_text}。"
    )


def _applicability_context(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_id": (snapshot.get("product") or {}).get("id"),
        "supplier_ids": sorted(
            supplier.get("id") for supplier in snapshot.get("suppliers", [])
            if supplier.get("id") is not None
        ),
        "region": str((snapshot.get("product") or {}).get("region") or "cn").lower(),
        "as_of": date.today().isoformat(),
    }


def _scope_matches(raw_scope: Any, values: set[str]) -> bool:
    scope = {item.strip().lower() for item in str(raw_scope or "all").split(",") if item.strip()}
    return not scope or "all" in scope or bool(scope & values)


def _is_applicable(item: dict[str, Any], context: dict[str, Any]) -> bool:
    """Fail closed for status/date/scope metadata before a chunk reaches an LLM."""
    if str(item.get("status", "active")).lower() != "active":
        return False
    as_of = str(context.get("as_of") or date.today().isoformat())
    if not str(item.get("effective_from", "2000-01-01")) <= as_of <= str(
        item.get("effective_to", "2099-12-31")
    ):
        return False
    product_values = {str(context["product_id"])} if context.get("product_id") is not None else set()
    supplier_values = {str(value) for value in context.get("supplier_ids", [])}
    region_values = {str(context.get("region", "cn")).lower()}
    return (
        _scope_matches(item.get("product_scope"), product_values)
        and _scope_matches(item.get("supplier_scope"), supplier_values)
        and _scope_matches(item.get("region_scope"), region_values)
    )
