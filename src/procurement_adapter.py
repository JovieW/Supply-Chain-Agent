"""Replaceable procurement-system boundary with a local SQLite implementation."""
from __future__ import annotations

from typing import Protocol


class ProcurementAdapter(Protocol):
    def create_order(self, **kwargs): ...
    def transition_order(self, **kwargs) -> dict: ...
    def list_orders(self, product_id: int | None = None) -> list[dict]: ...


class LocalSqliteProcurementAdapter:
    name = "local_sqlite_demo"

    def create_order(self, **kwargs):
        from db_service import create_purchase_order
        return create_purchase_order(**kwargs)

    def transition_order(self, **kwargs) -> dict:
        from db_service import transition_purchase_order
        return transition_purchase_order(**kwargs)

    def list_orders(self, product_id: int | None = None) -> list[dict]:
        from db_service import list_purchase_orders
        return list_purchase_orders(product_id=product_id)


def get_procurement_adapter() -> ProcurementAdapter:
    """Production deployments can replace this factory with an ERP adapter."""
    return LocalSqliteProcurementAdapter()
