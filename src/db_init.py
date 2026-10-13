from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
try:
    from .models import Base, Product, ProductSupplier, Inventory, Supplier, PurchaseOrder, Shipment
except ImportError:  # Supports running as `python src/db_init.py`.
    from models import Base, Product, ProductSupplier, Inventory, Supplier, PurchaseOrder, Shipment
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv


load_dotenv()


def init_database():
    """Initialize database and create all tables"""
    os.makedirs('data', exist_ok=True)
    engine = create_engine(
        os.getenv('DATABASE_URL', 'sqlite:///data/supply_chain.db'), echo=False
    )
    Base.metadata.create_all(engine)
    return engine


def seed_data(engine):
    """
    Seed database with controlled multi-product scenarios.
    
    3 products, 3 suppliers, approved supplier mappings, and risk profiles.
    """
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        if session.query(Product).count() > 0:
            _seed_product_suppliers(session)
            session.commit()
            print("Database already seeded; supplier eligibility verified")
            return
        
        # 3 PRODUCTS
        products = [
            Product(id=1, name='Widget A - Standard', sku='WGT-A-001'),
            Product(id=2, name='Widget B - Premium', sku='WGT-B-002'),
            Product(id=3, name='Widget C - Specialized', sku='WGT-C-003')
        ]
        session.add_all(products)
        
        # 3 SUPPLIERS
        suppliers = [
            Supplier(id=1, name='Reliable Corp', lead_time_days=5, reliability_score=0.95),
            Supplier(id=2, name='Fast Shipping Inc', lead_time_days=2, reliability_score=0.85),
            Supplier(id=3, name='Budget Supplies', lead_time_days=10, reliability_score=0.75)
        ]
        session.add_all(suppliers)
        session.flush()

        session.add_all([
            ProductSupplier(product_id=1, supplier_id=1),
            ProductSupplier(product_id=1, supplier_id=3),
            ProductSupplier(product_id=2, supplier_id=1),
            ProductSupplier(product_id=2, supplier_id=2),
            ProductSupplier(product_id=3, supplier_id=1),
            ProductSupplier(product_id=3, supplier_id=2),
            ProductSupplier(product_id=3, supplier_id=3),
        ])
        
        # INVENTORY (1 warehouse, 3 products)
        # Product A: STABLE - comfortable stock
        # Product B: VOLATILE - below reorder point
        # Product C: HIGH RISK - critically low
        inventories = [
            Inventory(id=1, product_id=1, quantity=250, reorder_point=100),
            Inventory(id=2, product_id=2, quantity=50, reorder_point=100),
            Inventory(id=3, product_id=3, quantity=20, reorder_point=80)
        ]
        session.add_all(inventories)
        
        # PURCHASE ORDERS
        # Product A: Active order with reliable supplier
        # Product B: No order (triggers HIGH risk)
        # Product C: Order with risky supplier
        purchase_orders = [
            PurchaseOrder(
                id=1,
                supplier_id=1,
                product_id=1,
                quantity=150,
                status='confirmed',
                created_at=datetime.utcnow() - timedelta(days=2)
            ),
            PurchaseOrder(
                id=2,
                supplier_id=3,
                product_id=3,
                quantity=100,
                status='confirmed',
                created_at=datetime.utcnow() - timedelta(days=5)
            )
        ]
        session.add_all(purchase_orders)
        
        # SHIPMENTS
        # Product A: Arriving soon (LOW RISK)
        # Product C: Delayed (HIGH RISK)
        shipments = [
            Shipment(
                id=1,
                po_id=1,
                status='in_transit',
                expected_arrival=datetime.utcnow() + timedelta(days=3)
            ),
            Shipment(
                id=2,
                po_id=2,
                status='in_transit',
                expected_arrival=datetime.utcnow() + timedelta(days=12)
            )
        ]
        session.add_all(shipments)
        
        session.commit()
        
        print("="*70)
        print("Database seeded successfully")
        print("="*70)
        print("\nProduct A (Stable): 250 units, PO arriving in 3 days → LOW risk")
        print("Product B (Volatile): 50 units, no PO → HIGH risk")
        print("Product C (High Risk): 20 units, delayed shipment → HIGH risk")
        print("="*70)
        
    except Exception as e:
        session.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        session.close()


def _seed_product_suppliers(session):
    """Backfill eligibility mappings for databases created before P0."""
    if session.query(ProductSupplier).count() > 0:
        return
    product_ids = {row.id for row in session.query(Product).all()}
    supplier_ids = {row.id for row in session.query(Supplier).all()}
    defaults = {1: {1, 3}, 2: {1, 2}, 3: {1, 2, 3}}
    session.add_all([
        ProductSupplier(product_id=product_id, supplier_id=supplier_id)
        for product_id, eligible_ids in defaults.items()
        if product_id in product_ids
        for supplier_id in eligible_ids
        if supplier_id in supplier_ids
    ])


if __name__ == '__main__':
    engine = init_database()
    seed_data(engine)
    from db_service import ensure_runtime_schema
    ensure_runtime_schema()
    print("\nDatabase initialized at data/supply_chain.db")
