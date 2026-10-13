"""
Supply Chain Control Tower - Async Multi-Product Processing
"""

import os
import sys
from dotenv import load_dotenv
import json
from datetime import datetime
import time
import asyncio
import uuid
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

if __name__ == '__main__':
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)

from graph import create_supply_chain_graph
from state import SupplyChainState
from db_init import init_database, seed_data


def print_section(title):
    """Print formatted section header."""
    print("\n" + "="*70)
    print(f"  {title}")
    print("="*70)


def print_streaming_dots(message, duration=1.5):
    """Print message with animated dots."""
    print(f"{message}", end='', flush=True)
    for _ in range(3):
        time.sleep(duration / 3)
        print(".", end='', flush=True)
    print(" ✓")


async def stream_graph_execution_async(app, initial_state, product_id):
    """Execute graph with async streaming."""
    print(f"\n🔄 Processing Product {product_id}")
    
    node_count = 0
    final_state = initial_state.copy()
    
    # CHANGED: stream → astream
    config = {"configurable": {"thread_id": initial_state["workflow_run_id"]}}
    async for event in app.astream(initial_state, config=config):
        node_count += 1
        
        for node_name, node_output in event.items():
            for key, value in node_output.items():
                if key == 'agent_outputs' and key in final_state:
                    final_state['agent_outputs'].update(value)
                else:
                    final_state[key] = value
            
            if node_name == "ingest_data":
                snapshot = final_state.get('db_snapshot', {})
                if snapshot:
                    product = snapshot.get('product', {})
                    inventory = snapshot.get('inventory', {})
                    print(f"  ✓ Loaded: {product.get('name', 'Unknown')}")
            
            elif node_name == "retrieve_knowledge":
                retrieved = final_state.get('retrieved_knowledge', {})
                stats = retrieved.get('index_stats', {})
                print(f"  RAG retrieved from {stats.get('chunks', 0)} knowledge chunks")

            elif node_name == "recall_memory":
                memory = final_state.get('long_term_memory', {})
                print(f"  Long-term memory recalled: {memory.get('count', 0)} episodes")

            elif node_name == "persist_memory":
                print(f"  Decision memory persisted: #{final_state.get('decision_memory_id', '?')}")

            elif node_name == "autonomous_investigation":
                investigation = final_state.get('agent_outputs', {}).get('tool_investigator', {})
                print(
                    f"  Autonomous investigation: {investigation.get('status', 'unknown')}, "
                    f"{investigation.get('tool_call_count', 0)} tool calls"
                )

            elif node_name == "reflection":
                reflection = final_state.get('agent_outputs', {}).get('reflection', {})
                print(
                    f"  Self-reflection round {reflection.get('round', '?')}: "
                    f"{reflection.get('outcome', 'unknown')}"
                )

            elif node_name in ["demand_agent", "inventory_agent", "risk_agent", "logistics_agent", "coordinator"]:
                print(f"  ✓ {node_name.replace('_', ' ').title()} completed")
            
            elif node_name == "decision_gate":
                risk_level = final_state.get('decision_risk', 'N/A')
                print(f"  ✓ Decision Gate: {risk_level} risk")
            
            elif node_name == "human_approval":
                feedback = final_state.get('human_feedback', 'N/A')
                print(f"  ✓ Human Approval: {feedback}")
            
            elif node_name == "execute":
                exec_data = final_state.get('agent_outputs', {}).get('execution', {})
                if exec_data.get('executed'):
                    print(f"  ✓ Executed: {exec_data.get('message', '')}")
    
    return final_state


async def run_product_workflow_async(app, product_id):
    """Run workflow for single product (async)."""
    initial_state = {
        'product_id': product_id,
        'workflow_run_id': str(uuid.uuid4()),
        'db_snapshot': {},
        'retrieved_knowledge': {},
        'long_term_memory': {},
        'decision_memory_id': None,
        'agent_outputs': {},
        'final_decision': None,
        'decision_risk': None,
        'decision_assessment': None,
        'human_feedback': None,
        'human_review': None,
        'approval_request_id': None,
        'reflection_round': 0,
        'reflection_history': [],
        'reflection_feedback': None,
    }
    
    result = await stream_graph_execution_async(app, initial_state, product_id)
    return result


async def run_all_products_async(app, product_ids):
    """Run workflows for all products in parallel."""
    print_section("Processing All Products (Parallel)")
    
    # Create tasks for all products
    tasks = [run_product_workflow_async(app, pid) for pid in product_ids]
    
    # Run all in parallel
    results = await asyncio.gather(*tasks)
    
    return results


def print_product_summary(result):
    """Print summary for one product."""
    if not result:
        print("\n⚠️  No result available")
        return
    
    snapshot = result.get('db_snapshot', {})
    final_decision = result.get('final_decision', {})
    
    product = snapshot.get('product', {})
    product_name = product.get('name', 'Unknown Product')
    decision_type = final_decision.get('decision_type', 'N/A')
    risk_level = result.get('decision_risk', 'N/A')
    
    print(f"\n📦 {product_name}")
    print(f"   Decision: {decision_type}")
    print(f"   Risk: {risk_level}")
    
    details = final_decision.get('details', {})
    if details.get('supplier_id'):
        print(f"   Supplier: #{details.get('supplier_id')}")
        print(f"   Quantity: {details.get('quantity')} units")
        print(f"   Expedite: {'Yes' if details.get('expedite') else 'No'}")


async def run_supply_chain_cycle_async():
    """Run async decision cycle for all products."""
    
    print_section("SUPPLY CHAIN CONTROL TOWER - ASYNC MODE")
    print(f"Timestamp: {datetime.now().isoformat()}")
    
    # Initialize database
    print_section("Step 1: Database Initialization")
    try:
        print_streaming_dots("Initializing database", 1.0)
        engine = init_database()
        seed_data(engine)
        print("✓ Database ready")
    except Exception as e:
        print(f"✗ Database initialization failed: {e}")
        return
    
    # Create graph with durable checkpoints required by HITL interrupts
    print_section("Step 2: Building LangGraph Workflow")
    try:
        print_streaming_dots("Compiling state graph", 1.0)
        configured = os.getenv("HITL_CHECKPOINT_DB", "data/workflow_checkpoints.sqlite")
        checkpoint_path = Path(configured)
        if not checkpoint_path.is_absolute():
            checkpoint_path = Path(__file__).resolve().parents[1] / checkpoint_path
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"✗ Graph compilation failed: {e}")
        return
    
    # Process all products in parallel
    product_ids = [1, 2, 3]
    
    try:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            await checkpointer.setup()
            app = create_supply_chain_graph(checkpointer=checkpointer)
            print("✓ Graph compiled successfully")
            results = await run_all_products_async(app, product_ids)
        
        # Display summaries
        print_section("Results Summary")
        for result in results:
            print_product_summary(result)
        
    except Exception as e:
        print(f"✗ Workflow failed: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Main entry point."""
    env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path)
    
    from llm_config import validate_llm_config
    try:
        validate_llm_config()
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return
    
    print_section("Observability Check")
    from llm_config import verify_tracing
    verify_tracing()
    
    # CHANGED: Run async function
    asyncio.run(run_supply_chain_cycle_async())


if __name__ == '__main__':
    main()
