"""Profile and gate the reproducible enterprise-shaped demo dataset."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any


SRC_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SRC_ROOT.parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from rag.documents import discover_documents, load_and_chunk


DEFAULT_THRESHOLDS = {
    "minimum_products": 100,
    "minimum_suppliers": 25,
    "minimum_demand_rows": 36000,
    "minimum_verified_memories": 200,
    "minimum_inventory_coverage": 1.0,
    "minimum_policy_coverage": 1.0,
    "minimum_offer_coverage": 1.0,
    "minimum_history_180d_coverage": 0.95,
    "minimum_knowledge_documents": 50,
    "minimum_knowledge_chunks": 50,
    "minimum_governance_metadata_coverage": 1.0,
    "maximum_foreign_key_violations": 0,
    "maximum_duplicate_document_ids": 0,
}


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _count(connection: sqlite3.Connection, table: str) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def profile_database(database_path: Path) -> dict[str, Any]:
    if not database_path.exists():
        raise FileNotFoundError(f"Database does not exist: {database_path}")
    connection = sqlite3.connect(str(database_path))
    try:
        counts = {
            table: _count(connection, table)
            for table in (
                "products", "inventory", "inventory_policies", "suppliers",
                "product_suppliers", "supplier_offers", "demand_history",
                "purchase_orders", "shipments", "decision_memories", "outcome_feedback",
            )
        }
        products = max(1, counts["products"])
        active_mappings = int(connection.execute(
            "SELECT COUNT(*) FROM product_suppliers WHERE is_active = 1"
        ).fetchone()[0]) if _table_exists(connection, "product_suppliers") else 0
        mapped_products = int(connection.execute(
            "SELECT COUNT(DISTINCT product_id) FROM product_suppliers WHERE is_active = 1"
        ).fetchone()[0]) if _table_exists(connection, "product_suppliers") else 0
        active_offer_matches = int(connection.execute(
            """SELECT COUNT(*) FROM product_suppliers ps
               JOIN supplier_offers so
                 ON so.product_id = ps.product_id AND so.supplier_id = ps.supplier_id
               WHERE ps.is_active = 1"""
        ).fetchone()[0]) if _table_exists(connection, "supplier_offers") else 0
        history_180 = int(connection.execute(
            """SELECT COUNT(*) FROM (
                 SELECT product_id FROM demand_history
                 GROUP BY product_id HAVING COUNT(DISTINCT demand_date) >= 180
               )"""
        ).fetchone()[0]) if _table_exists(connection, "demand_history") else 0
        verified_memories = int(connection.execute(
            """SELECT COUNT(*) FROM decision_memories dm
               JOIN outcome_feedback ofe ON ofe.memory_id = dm.id
               WHERE dm.status = 'OUTCOME_RECORDED'"""
        ).fetchone()[0]) if _table_exists(connection, "outcome_feedback") else 0
        outcome_distribution = dict(connection.execute(
            "SELECT outcome_label, COUNT(*) FROM outcome_feedback GROUP BY outcome_label"
        ).fetchall()) if _table_exists(connection, "outcome_feedback") else {}
        reliability_distribution = dict(connection.execute(
            """SELECT CASE
                 WHEN reliability_score >= 0.95 THEN 'high'
                 WHEN reliability_score >= 0.85 THEN 'medium'
                 ELSE 'low' END, COUNT(*)
               FROM suppliers GROUP BY 1"""
        ).fetchall()) if _table_exists(connection, "suppliers") else {}
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        return {
            "database_path": str(database_path.resolve()),
            "counts": counts,
            "active_supplier_mappings": active_mappings,
            "coverage": {
                "inventory": round(counts["inventory"] / products, 4),
                "inventory_policy": round(counts["inventory_policies"] / products, 4),
                "active_supplier_mapping": round(mapped_products / products, 4),
                "supplier_offer": round(
                    active_offer_matches / max(1, active_mappings), 4
                ),
                "demand_history_180_days": round(history_180 / products, 4),
            },
            "verified_memories": verified_memories,
            "outcome_distribution": outcome_distribution,
            "supplier_reliability_distribution": reliability_distribution,
            "foreign_key_violation_count": len(foreign_key_violations),
            "foreign_key_violations": foreign_key_violations[:20],
        }
    finally:
        connection.close()


def profile_knowledge(knowledge_dir: Path) -> dict[str, Any]:
    paths = discover_documents(knowledge_dir)
    chunks = []
    source_documents: dict[str, dict[str, Any]] = {}
    for path in paths:
        loaded = load_and_chunk(path, knowledge_dir)
        chunks.extend(loaded)
        if loaded:
            source_documents[loaded[0].source] = loaded[0].metadata()
    required = {
        "document_id", "version", "status", "effective_from", "effective_to",
        "owner", "product_scope", "supplier_scope", "region_scope",
    }
    complete = sum(not (required - set(chunk.metadata())) for chunk in chunks)
    sources_by_document_id: dict[str, set[str]] = defaultdict(set)
    for source, metadata in source_documents.items():
        sources_by_document_id[str(metadata.get("document_id"))].add(source)
    duplicate_ids = {
        document_id: sorted(sources)
        for document_id, sources in sources_by_document_id.items()
        if len(sources) > 1
    }
    today = date.today().isoformat()
    status_distribution = Counter(
        str(metadata.get("status")) for metadata in source_documents.values()
    )
    expired_documents = sum(
        str(metadata.get("effective_to", "2099-12-31")) < today
        for metadata in source_documents.values()
    )
    return {
        "knowledge_dir": str(knowledge_dir.resolve()),
        "documents": len(paths),
        "chunks": len(chunks),
        "document_type_distribution": dict(Counter(chunk.document_type for chunk in chunks)),
        "status_distribution": dict(status_distribution),
        "expired_document_count": expired_documents,
        "governance_metadata_coverage": round(complete / max(1, len(chunks)), 4),
        "duplicate_document_id_count": len(duplicate_ids),
        "duplicate_document_ids": duplicate_ids,
        "average_chunk_characters": round(
            sum(len(chunk.content) for chunk in chunks) / max(1, len(chunks)), 1
        ),
    }


def evaluate_data_quality(
    database: dict[str, Any], knowledge: dict[str, Any], thresholds: dict[str, Any]
) -> dict[str, Any]:
    counts, coverage = database["counts"], database["coverage"]
    gates = {
        "minimum_products": counts["products"] >= int(thresholds["minimum_products"]),
        "minimum_suppliers": counts["suppliers"] >= int(thresholds["minimum_suppliers"]),
        "minimum_demand_rows": counts["demand_history"] >= int(thresholds["minimum_demand_rows"]),
        "minimum_verified_memories": database["verified_memories"] >= int(thresholds["minimum_verified_memories"]),
        "minimum_inventory_coverage": coverage["inventory"] >= float(thresholds["minimum_inventory_coverage"]),
        "minimum_policy_coverage": coverage["inventory_policy"] >= float(thresholds["minimum_policy_coverage"]),
        "minimum_offer_coverage": coverage["supplier_offer"] >= float(thresholds["minimum_offer_coverage"]),
        "minimum_history_180d_coverage": coverage["demand_history_180_days"] >= float(thresholds["minimum_history_180d_coverage"]),
        "minimum_knowledge_documents": knowledge["documents"] >= int(thresholds["minimum_knowledge_documents"]),
        "minimum_knowledge_chunks": knowledge["chunks"] >= int(thresholds["minimum_knowledge_chunks"]),
        "minimum_governance_metadata_coverage": knowledge["governance_metadata_coverage"] >= float(thresholds["minimum_governance_metadata_coverage"]),
        "maximum_foreign_key_violations": database["foreign_key_violation_count"] <= int(thresholds["maximum_foreign_key_violations"]),
        "maximum_duplicate_document_ids": knowledge["duplicate_document_id_count"] <= int(thresholds["maximum_duplicate_document_ids"]),
    }
    return {"passed": all(gates.values()), "quality_gates": gates}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Profile and gate enterprise demo data")
    parser.add_argument("--database", default=str(REPO_ROOT / "data" / "supply_chain.db"))
    parser.add_argument("--knowledge-dir", default=str(REPO_ROOT / "data" / "knowledge"))
    parser.add_argument("--thresholds", default=str(REPO_ROOT / "evals" / "data_quality_thresholds.json"))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    threshold_path = Path(args.thresholds)
    thresholds = dict(DEFAULT_THRESHOLDS)
    if threshold_path.exists():
        thresholds.update(json.loads(threshold_path.read_text(encoding="utf-8")))
    database = profile_database(Path(args.database))
    knowledge = profile_knowledge(Path(args.knowledge_dir))
    report = {
        "schema_version": "enterprise-data-quality/v1",
        "dataset_notice": "Enterprise-scale additions are deterministic synthetic data.",
        "thresholds": thresholds,
        "database": database,
        "knowledge": knowledge,
        **evaluate_data_quality(database, knowledge, thresholds),
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
