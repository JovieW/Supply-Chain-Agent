from state import SupplyChainState
from rag.retriever import retrieve_knowledge_for_snapshot


def knowledge_retrieval_node(state: SupplyChainState) -> dict:
    """
    Retrieve supply chain policies and historical cases for the current product.

    This node is read-only. It converts the structured database snapshot into a
    retrieval query and returns the relevant non-structured knowledge snippets
    for downstream agents.
    """
    snapshot = state.get("db_snapshot", {})  # 获取数据库快照
    try:
        retrieved_knowledge = retrieve_knowledge_for_snapshot(snapshot)  # rag检索
        retrieved_knowledge["status"] = "READY"  # 成功标记为ready
        retrieved_knowledge["error"] = None
    except Exception as exc:
        retrieved_knowledge = {
            "status": "DEGRADED",
            "error": f"{type(exc).__name__}: {exc}",
            "query_context": "",
            "applicability_context": {},
            "by_agent": {},
            "all": [],
            "index_stats": {"index_mode": "offline_published_unavailable"},
        }

    return {
        "retrieved_knowledge": retrieved_knowledge,
    }
