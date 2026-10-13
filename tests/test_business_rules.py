from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from business_rules import (
    calculate_demand_risk,
    calculate_inventory_decision,
    calculate_logistics_risk,
    calculate_supplier_risk,
)


class BusinessRuleTests(unittest.TestCase):
    def test_low_inventory_requires_replenishment(self) -> None:
        inventory = {"quantity": 20, "reorder_point": 100}
        self.assertEqual(calculate_demand_risk(inventory, []), "HIGH")
        self.assertEqual(calculate_inventory_decision(inventory), ("REORDER", 180))

    def test_sufficient_inventory_is_held(self) -> None:
        inventory = {"quantity": 120, "reorder_point": 100}
        self.assertEqual(calculate_inventory_decision(inventory), ("HOLD", 0))

    def test_supplier_threshold_is_deterministic(self) -> None:
        self.assertEqual(
            calculate_supplier_risk([{"reliability_score": 0.95}]), "LOW"
        )
        self.assertEqual(
            calculate_supplier_risk([{"reliability_score": 0.89}]), "HIGH"
        )

    def test_overdue_or_late_shipment_is_high_risk(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, 0)
        late = [{"expected_arrival": (now + timedelta(days=8)).isoformat()}]
        overdue = [{"expected_arrival": (now - timedelta(hours=1)).isoformat()}]
        self.assertEqual(calculate_logistics_risk(late, now), "HIGH")
        self.assertEqual(calculate_logistics_risk(overdue, now), "HIGH")

    def test_shipment_within_window_is_low_risk(self) -> None:
        now = datetime(2026, 8, 26, 12, 0, 0)
        shipments = [{"expected_arrival": (now + timedelta(days=3)).isoformat()}]
        self.assertEqual(calculate_logistics_risk(shipments, now), "LOW")


if __name__ == "__main__":
    unittest.main()
