from state import SupplyChainState
from db_service import read_supply_chain_snapshot


def data_ingestion_node(state: SupplyChainState) -> dict:
    """
    Load current supply chain state from database.
    
    Processes one product at a time based on state['product_id'].
    If not set, defaults to product_id=1.
    """
    # Get product ID from state, default to 1
    product_id = state.get('product_id', 1)
    
    # Read snapshot for specific product
    snapshot = read_supply_chain_snapshot(product_id=product_id)
    
    return {
        'db_snapshot': snapshot,
        'agent_outputs': {}
    }
