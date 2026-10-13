from __future__ import annotations

import ast
import unittest
from pathlib import Path


GRAPH_PATH = Path(__file__).resolve().parents[1] / "src" / "graph.py"


def graph_edges(function_name: str) -> list[tuple[object, object]]:
    """Read declared edges without importing optional LangGraph dependencies."""
    tree = ast.parse(GRAPH_PATH.read_text(encoding="utf-8"))
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    edges = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        call = node.func
        if not isinstance(call, ast.Attribute) or call.attr != "add_edge":
            continue
        try:
            edges.append((ast.literal_eval(node.args[0]), ast.literal_eval(node.args[1])))
        except (ValueError, TypeError):
            # END and other symbolic targets are irrelevant to this topology check.
            continue
    return edges


class ParallelSpecialistTopologyTests(unittest.TestCase):
    def assert_parallel_risk_logistics(self, function_name: str) -> None:
        edges = graph_edges(function_name)
        self.assertIn(("inventory_agent", "risk_agent"), edges)
        self.assertIn(("inventory_agent", "logistics_agent"), edges)
        self.assertIn((["risk_agent", "logistics_agent"], "coordinator"), edges)
        self.assertNotIn(("risk_agent", "logistics_agent"), edges)
        # Tool investigation and demand analysis intentionally remain sequential.
        self.assertIn(("retrieve_knowledge", "autonomous_investigation"), edges)
        self.assertIn(("autonomous_investigation", "demand_agent"), edges)

    def test_primary_graph_uses_parallel_specialists_and_join(self) -> None:
        self.assert_parallel_risk_logistics("create_supply_chain_graph")

    def test_optional_continuous_graph_has_the_same_topology(self) -> None:
        self.assert_parallel_risk_logistics("create_continuous_monitoring_graph")


if __name__ == "__main__":
    unittest.main()
