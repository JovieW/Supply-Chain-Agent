from langgraph.graph import StateGraph, END
from state import SupplyChainState

# Import all nodes
from nodes.data_ingestion import data_ingestion_node
from nodes.knowledge_retrieval import knowledge_retrieval_node
from nodes.decision_gate import decision_gate_node, should_request_human_approval
from nodes.execution import execution_node
from nodes.human_approval import human_approval_node, post_approval_routing
from nodes.long_term_memory import (
    persist_decision_memory_node,
    recall_long_term_memory_node,
)

# Import all agents
from agents.demand_agent import demand_agent_node
from agents.inventory_agent import inventory_agent_node
from agents.risk_agent import risk_agent_node
from agents.logistics_agent import logistics_agent_node
from agents.coordinator_agent import coordinator_agent_node
from agents.tool_investigator import autonomous_tool_investigation_node
from agents.reflection_agent import reflection_agent_node
from reflection_rules import reflection_route


def create_supply_chain_graph(checkpointer=None):
    """
    Create the LangGraph workflow for the supply chain control tower.
    
    Flow:
    1. Data Ingestion (read DB snapshot)
    2. RAG Knowledge Retrieval (policies, playbooks, and historical cases)
    3. Autonomous Investigation (bounded read-only tool calls)
    4. Demand Analysis + Outcome-verified Long-term Memory Recall
    5. Agent Analysis (specialist reasoning):
       - Demand Agent
       - Inventory Agent
       - Risk Agent
       - Logistics Agent
    6. Coordinator Agent (synthesize decision)
    7. Self-Reflection (validate, revise once or escalate)
    8. Decision Gate + Durable Decision Memory
    9. Execute OR Human Approval
    
    Returns:
        Compiled LangGraph StateGraph
    """
    
    # Initialize graph with state schema
    workflow = StateGraph(SupplyChainState)
    
    # ================================================================
    # ADD NODES
    # ================================================================
    
    # Data ingestion (entry point)
    workflow.add_node("ingest_data", data_ingestion_node)  #
    workflow.add_node("retrieve_knowledge", knowledge_retrieval_node)  #
    workflow.add_node("autonomous_investigation", autonomous_tool_investigation_node)
    workflow.add_node("recall_memory", recall_long_term_memory_node)
    
    # Agent nodes (reasoning layer)
    workflow.add_node("demand_agent", demand_agent_node)
    workflow.add_node("inventory_agent", inventory_agent_node)
    workflow.add_node("risk_agent", risk_agent_node)
    workflow.add_node("logistics_agent", logistics_agent_node)
    workflow.add_node("coordinator", coordinator_agent_node)
    workflow.add_node("reflection", reflection_agent_node)
    
    # Decision and execution nodes
    workflow.add_node("decision_gate", decision_gate_node)
    workflow.add_node("execute", execution_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("persist_memory", persist_decision_memory_node)
    
    # ================================================================
    # SET ENTRY POINT
    # ================================================================
    
    workflow.set_entry_point("ingest_data")
    
    # ================================================================
    # ADD EDGES (sequential flow with one safe fan-out/fan-in)
    # ================================================================
    
    # Data ingestion → Demand agent
    workflow.add_edge("ingest_data", "retrieve_knowledge")
    workflow.add_edge("retrieve_knowledge", "autonomous_investigation")
    workflow.add_edge("autonomous_investigation", "demand_agent")
    
    # Demand agent → Inventory agent
    workflow.add_edge("demand_agent", "recall_memory")
    workflow.add_edge("recall_memory", "inventory_agent")
    
    # Risk and logistics both read the completed inventory plan and write
    # different agent_outputs keys, so they can run in the same graph step.
    workflow.add_edge("inventory_agent", "risk_agent")
    workflow.add_edge("inventory_agent", "logistics_agent")

    # The list-valued start edge is a barrier: coordinator runs once only after
    # both parallel specialists have completed and their reducer updates merged.
    workflow.add_edge(["risk_agent", "logistics_agent"], "coordinator")
    
    # Coordinator → reflection; failed reviews loop back for bounded revision
    workflow.add_edge("coordinator", "reflection")
    workflow.add_conditional_edges(  # 条件分支
        "reflection",
        reflection_route,  # 路由函数
        {"revise": "coordinator", "continue": "decision_gate"},
    )
    
    # ================================================================
    # ADD CONDITIONAL EDGES (Branching Logic)
    # ================================================================
    
    workflow.add_edge("decision_gate", "persist_memory")

    # Persisted decision memory → Execute OR Human approval
    workflow.add_conditional_edges(
        "persist_memory",
        should_request_human_approval,  # Returns "auto_execute" or "human_approval"
        {
            "auto_execute": "execute",
            "human_approval": "human_approval"
        }
    )
    
    # Human approval → Execute OR End
    workflow.add_conditional_edges(
        "human_approval",
        post_approval_routing,  # Returns "execute" or "end"
        {
            "execute": "execute",
            "end": END
        }
    )
    
    # Execute → End (could loop back to ingest_data for continuous monitoring)
    workflow.add_edge("execute", END)
    
    # ================================================================
    # COMPILE GRAPH
    # ================================================================
    
    return workflow.compile(checkpointer=checkpointer)


# ================================================================
# OPTIONAL: Continuous Monitoring Loop
# ================================================================

def create_continuous_monitoring_graph(checkpointer=None):
    """
    Alternative graph with continuous loop.
    After execution, loops back to data ingestion for next cycle.
    
    Use this for real-time monitoring scenarios.
    """
    workflow = StateGraph(SupplyChainState)
    
    # Add all nodes (same as above)
    workflow.add_node("ingest_data", data_ingestion_node)
    workflow.add_node("retrieve_knowledge", knowledge_retrieval_node)
    workflow.add_node("autonomous_investigation", autonomous_tool_investigation_node)
    workflow.add_node("recall_memory", recall_long_term_memory_node)
    workflow.add_node("demand_agent", demand_agent_node)
    workflow.add_node("inventory_agent", inventory_agent_node)
    workflow.add_node("risk_agent", risk_agent_node)
    workflow.add_node("logistics_agent", logistics_agent_node)
    workflow.add_node("coordinator", coordinator_agent_node)
    workflow.add_node("reflection", reflection_agent_node)
    workflow.add_node("decision_gate", decision_gate_node)
    workflow.add_node("execute", execution_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("persist_memory", persist_decision_memory_node)
    
    workflow.set_entry_point("ingest_data")
    
    # Sequential edges with parallel risk/logistics analysis
    workflow.add_edge("ingest_data", "retrieve_knowledge")
    workflow.add_edge("retrieve_knowledge", "autonomous_investigation")
    workflow.add_edge("autonomous_investigation", "demand_agent")
    workflow.add_edge("demand_agent", "recall_memory")
    workflow.add_edge("recall_memory", "inventory_agent")
    workflow.add_edge("inventory_agent", "risk_agent")
    workflow.add_edge("inventory_agent", "logistics_agent")
    workflow.add_edge(["risk_agent", "logistics_agent"], "coordinator")
    workflow.add_edge("coordinator", "reflection")
    workflow.add_conditional_edges(
        "reflection",
        reflection_route,
        {"revise": "coordinator", "continue": "decision_gate"},
    )
    
    # Conditional edges
    workflow.add_edge("decision_gate", "persist_memory")
    workflow.add_conditional_edges(
        "persist_memory",
        should_request_human_approval,
        {
            "auto_execute": "execute",
            "human_approval": "human_approval"
        }
    )
    
    workflow.add_conditional_edges(
        "human_approval",
        post_approval_routing,
        {
            "execute": "execute",
            "end": END
        }
    )
    
    # LOOP BACK: Execute → Ingest (continuous monitoring)
    workflow.add_edge("execute", "ingest_data")
    
    return workflow.compile(checkpointer=checkpointer)
