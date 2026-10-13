"""Deterministic golden-set evaluation for enterprise knowledge retrieval.

The evaluator reports ranking, governance, leakage, and latency quality.  It
keeps the original two-argument ``evaluate_retrieval_case`` API compatible.
"""
from __future__ import annotations

from datetime import date
import argparse
import json
import math
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any, Iterable


DEFAULT_KS = (1, 3, 5)


def _scope_matches(raw_scope: Any, values: set[str]) -> bool:
    scope = {
        item.strip().lower()
        for item in str(raw_scope or "all").split(",")
        if item.strip()
    }
    return not scope or "all" in scope or bool(scope & values)


def _is_applicable(item: dict[str, Any], context: dict[str, Any]) -> bool:
    if str(item.get("status", "active")).lower() != "active":
        return False
    as_of = str(context.get("as_of") or date.today().isoformat())
    if not str(item.get("effective_from", "2000-01-01")) <= as_of <= str(
        item.get("effective_to", "2099-12-31")
    ):
        return False
    product_values = (
        {str(context["product_id"])} if context.get("product_id") is not None else set()
    )
    supplier_values = {str(value) for value in context.get("supplier_ids", [])}
    region_values = {str(context.get("region", "cn")).lower()}
    return (
        _scope_matches(item.get("product_scope"), product_values)
        and _scope_matches(item.get("supplier_scope"), supplier_values)
        and _scope_matches(item.get("region_scope"), region_values)
    )


def _normalise_ks(case: dict[str, Any]) -> list[int]:
    values = {
        max(1, int(value)) for value in case.get("metric_ks", DEFAULT_KS)
    }
    values.add(max(1, int(case.get("top_k", 3))))
    return sorted(values)


def _document_ids(results: Iterable[dict[str, Any]]) -> list[str]:
    return [
        str(item.get("document_id"))
        for item in results
        if item.get("document_id") is not None
    ]


def _ranking_metrics(
    result_ids: list[str], expected: set[str], ks: list[int]
) -> dict[str, Any]:
    first_relevant_rank = next(
        (rank for rank, document_id in enumerate(result_ids, start=1)
         if document_id in expected),
        None,
    )
    reciprocal_rank = 1.0 / first_relevant_rank if first_relevant_rank else 0.0
    metrics: dict[str, Any] = {
        "first_relevant_rank": first_relevant_rank,
        "reciprocal_rank": round(reciprocal_rank, 4),
    }
    for k in ks:
        top_ids = result_ids[:k]
        unique_hits = expected & set(top_ids)
        recall = len(unique_hits) / len(expected) if expected else 1.0
        precision = (
            len(unique_hits) / k
            if expected else float(not top_ids)
        )
        seen_relevant: set[str] = set()
        dcg = 0.0
        for rank, document_id in enumerate(top_ids, start=1):
            if document_id not in expected or document_id in seen_relevant:
                continue
            seen_relevant.add(document_id)
            dcg += 1.0 / math.log2(rank + 1)
        ideal_hits = min(len(expected), k)
        ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
        ndcg = dcg / ideal_dcg if ideal_dcg else 1.0
        metrics[f"recall_at_{k}"] = round(recall, 4)
        metrics[f"precision_at_{k}"] = round(precision, 4)
        metrics[f"hit_rate_at_{k}"] = float(bool(unique_hits)) if expected else float(not top_ids)
        metrics[f"ndcg_at_{k}"] = round(ndcg, 4)
    return metrics


def evaluate_retrieval_case(
    case: dict,
    results: list[dict[str, Any]],
    *,
    latency_ms: float | None = None,
    applicability_context: dict[str, Any] | None = None,
) -> dict:
    """Evaluate one ranked result against relevance and forbidden judgements."""
    top_k = max(1, int(case.get("top_k", 3)))
    expected = {str(value) for value in case.get("expected_document_ids") or []}
    forbidden = {str(value) for value in case.get("forbidden_document_ids") or []}
    ks = _normalise_ks(case)
    result_ids = _document_ids(results)
    ranking = _ranking_metrics(result_ids, expected, ks)
    top = results[:top_k]
    top_ids = result_ids[:top_k]
    context = applicability_context or {
        "product_id": case.get("product_id"),
        "supplier_ids": case.get("supplier_ids", []),
        "region": case.get("region", "cn"),
        "as_of": case.get("as_of", date.today().isoformat()),
    }
    invalid = [item.get("document_id") for item in top if not _is_applicable(item, context)]
    forbidden_hits = [document_id for document_id in top_ids if document_id in forbidden]
    expected_types = {str(value) for value in case.get("expected_document_types") or []}
    type_passed = not expected_types or any(
        str(item.get("document_type")) in expected_types
        and str(item.get("document_id")) in expected
        for item in top
    )
    recall_at_k = float(ranking[f"recall_at_{top_k}"])
    passed = (
        recall_at_k >= float(case.get("minimum_recall", 1.0))
        and float(ranking["reciprocal_rank"])
        >= float(case.get("minimum_reciprocal_rank", 0.0))
        and not invalid
        and not forbidden_hits
        and type_passed
    )
    return {
        "case_id": case.get("id"),
        "name": case.get("name"),
        "query": case.get("query"),
        "tags": case.get("tags", []),
        "difficulty": case.get("difficulty", "standard"),
        "capabilities": case.get("capabilities", []),
        "top_k": top_k,
        "metric_ks": ks,
        "expected_document_ids": sorted(expected),
        "forbidden_document_ids": sorted(forbidden),
        "retrieved_document_ids": top_ids,
        "recall_at_k": round(recall_at_k, 4),
        **ranking,
        "applicability_passed": not invalid,
        "invalid_document_ids": invalid,
        "forbidden_leakage_passed": not forbidden_hits,
        "forbidden_hits": forbidden_hits,
        "document_type_passed": type_passed,
        "duplicate_document_count": len(top_ids) - len(set(top_ids)),
        "latency_ms": round(float(latency_ms), 2) if latency_ms is not None else None,
        "passed": passed,
    }


def _slice_summary(reports: list[dict]) -> dict[str, Any]:
    """Summarise one labelled evaluation slice without hiding hard-case misses."""
    if not reports:
        return {
            "case_count": 0,
            "passed_case_count": 0,
            "pass_rate": 0.0,
            "mean_recall_at_k": 0.0,
            "mean_reciprocal_rank": 0.0,
            "forbidden_leakage_rate": 0.0,
        }
    return {
        "case_count": len(reports),
        "passed_case_count": sum(bool(item["passed"]) for item in reports),
        "pass_rate": round(
            sum(bool(item["passed"]) for item in reports) / len(reports), 4
        ),
        "mean_recall_at_k": round(
            mean(float(item["recall_at_k"]) for item in reports), 4
        ),
        "mean_reciprocal_rank": round(
            mean(float(item.get("reciprocal_rank", 0.0)) for item in reports), 4
        ),
        "forbidden_leakage_rate": round(
            sum(bool(item.get("forbidden_hits")) for item in reports) / len(reports), 4
        ),
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower, upper = math.floor(index), math.ceil(index)
    if lower == upper:
        return round(ordered[lower], 2)
    return round(
        ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower), 2
    )


def aggregate_retrieval_reports(
    reports: list[dict], thresholds: dict[str, Any] | None = None
) -> dict:
    if not reports:
        raise ValueError("At least one retrieval report is required")
    thresholds = thresholds or {}
    metric_keys = sorted({
        key
        for report in reports
        for key in report
        if key.startswith(("recall_at_", "precision_at_", "hit_rate_at_", "ndcg_at_"))
        and key != "recall_at_k"
    })
    aggregate_metrics = {
        f"mean_{key}": round(mean(float(report.get(key, 0.0)) for report in reports), 4)
        for key in metric_keys
    }
    latencies = [
        float(report["latency_ms"])
        for report in reports
        if report.get("latency_ms") is not None
    ]
    case_pass_rate = sum(bool(item["passed"]) for item in reports) / len(reports)
    applicability_pass_rate = (
        sum(bool(item["applicability_passed"]) for item in reports) / len(reports)
    )
    forbidden_leakage_rate = (
        sum(bool(item.get("forbidden_hits")) for item in reports) / len(reports)
    )
    difficulty_labels = sorted({
        str(item.get("difficulty", "standard")) for item in reports
    })
    difficulty_breakdown = {
        label: _slice_summary([
            item for item in reports
            if str(item.get("difficulty", "standard")) == label
        ])
        for label in difficulty_labels
    }
    capability_labels = sorted({
        str(capability)
        for item in reports
        for capability in item.get("capabilities", [])
    })
    capability_breakdown = {
        label: _slice_summary([
            item for item in reports if label in item.get("capabilities", [])
        ])
        for label in capability_labels
    }
    aggregate = {
        "case_count": len(reports),
        "passed_case_count": sum(bool(item["passed"]) for item in reports),
        "pass_rate": round(case_pass_rate, 4),
        "mean_recall_at_k": round(mean(float(item["recall_at_k"]) for item in reports), 4),
        "mean_reciprocal_rank": round(
            mean(float(item.get("reciprocal_rank", 0.0)) for item in reports), 4
        ),
        "applicability_pass_rate": round(applicability_pass_rate, 4),
        "forbidden_leakage_rate": round(forbidden_leakage_rate, 4),
        "mean_duplicate_document_count": round(
            mean(float(item.get("duplicate_document_count", 0)) for item in reports), 4
        ),
        "difficulty_breakdown": difficulty_breakdown,
        "capability_breakdown": capability_breakdown,
        "latency_ms": {
            "mean": round(mean(latencies), 2) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": round(max(latencies), 2) if latencies else None,
        },
        **aggregate_metrics,
    }
    gates = {
        "minimum_case_pass_rate": case_pass_rate >= float(
            thresholds.get("minimum_case_pass_rate", 0.0)
        ),
        "minimum_mean_recall_at_k": aggregate["mean_recall_at_k"] >= float(
            thresholds.get("minimum_mean_recall_at_k", 0.0)
        ),
        "minimum_mean_reciprocal_rank": aggregate["mean_reciprocal_rank"] >= float(
            thresholds.get("minimum_mean_reciprocal_rank", 0.0)
        ),
        "minimum_applicability_pass_rate": applicability_pass_rate >= float(
            thresholds.get("minimum_applicability_pass_rate", 0.0)
        ),
        "maximum_forbidden_leakage_rate": forbidden_leakage_rate <= float(
            thresholds.get("maximum_forbidden_leakage_rate", 1.0)
        ),
    }
    maximum_p95 = thresholds.get("maximum_p95_latency_ms")
    if maximum_p95 is not None and aggregate["latency_ms"]["p95"] is not None:
        gates["maximum_p95_latency_ms"] = (
            aggregate["latency_ms"]["p95"] <= float(maximum_p95)
        )
    slice_gates = {
        "minimum_hard_case_pass_rate": ("difficulty", "hard"),
        "minimum_semantic_hard_positive_pass_rate": (
            "capability", "semantic-hard-positive"
        ),
        "minimum_cross_domain_pass_rate": ("capability", "cross-domain"),
    }
    for threshold_name, (slice_type, label) in slice_gates.items():
        if threshold_name not in thresholds:
            continue
        breakdown = (
            difficulty_breakdown if slice_type == "difficulty" else capability_breakdown
        )
        summary = breakdown.get(label)
        gates[threshold_name] = bool(summary) and summary["pass_rate"] >= float(
            thresholds[threshold_name]
        )
    aggregate["quality_gates"] = gates
    # The configured gates define the release policy. Requiring every case to
    # pass here would make pass-rate thresholds meaningless and would hide
    # whether a regression is isolated or systemic.
    aggregate["passed"] = all(gates.values())
    return aggregate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RAG golden retrieval set")
    parser.add_argument(
        "--cases",
        default=str(Path(__file__).resolve().parents[2] / "evals" / "retrieval_cases.json"),
    )
    parser.add_argument(
        "--thresholds",
        default=str(Path(__file__).resolve().parents[2] / "evals" / "retrieval_thresholds.json"),
    )
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    with Path(args.cases).open("r", encoding="utf-8") as handle:
        cases = json.load(handle).get("cases", [])
    threshold_path = Path(args.thresholds)
    thresholds = (
        json.loads(threshold_path.read_text(encoding="utf-8"))
        if threshold_path.exists()
        else {}
    )
    from rag.retriever import search_enterprise_knowledge

    reports = []
    for case in cases:
        context = {
            "product_id": case.get("product_id"),
            "supplier_ids": case.get("supplier_ids", []),
            "region": case.get("region", "cn"),
            "as_of": case.get("as_of", date.today().isoformat()),
        }
        search_k = max(
            [int(case.get("top_k", 3))]
            + [int(value) for value in case.get("metric_ks", DEFAULT_KS)]
        )
        started = perf_counter()
        results = search_enterprise_knowledge(
            case["query"], top_k=search_k,
            document_type=case.get("document_type", "all"),
            applicability_context=context,
        )
        latency_ms = (perf_counter() - started) * 1000
        reports.append(evaluate_retrieval_case(
            case, results, latency_ms=latency_ms, applicability_context=context
        ))
    aggregate = aggregate_retrieval_reports(reports, thresholds)
    output = {
        "schema_version": "rag-retrieval-report/v3",
        "thresholds": thresholds,
        "aggregate": aggregate,
        "cases": reports,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if aggregate["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
