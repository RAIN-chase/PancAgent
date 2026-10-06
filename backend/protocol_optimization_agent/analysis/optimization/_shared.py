"""Extracted  shared calculations; no agent execution."""

from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any

from protocol_optimization_agent.runtime.io import read_csv_dicts, read_jsonl

MIN_ASSOCIATION_PROTOCOLS = 5

INTERVENTION_FEATURE_PREFIXES = (
    "all_reagent_",
    "reagent_",
    "factor_type_",
    "action_category_",
    "normalized_family_",
    "semantic_",
    "temporal_",
    "motif_action_",
    "transition_",
    "action_bigram_",
    "factor_type_bigram_",
)

NON_INTERVENTION_FEATURES = {
    "confidence_score",
    "comparison_ready",
    "n_stages",
    "n_factor_events",
    "n_unique_factors",
    "n_sources",
}

BOUNDED_SCORE_COLUMNS = {
    "maturity_score",
    "cosine_similarity",
    "similarity_score",
    "fraction",
    "proportion",
    "percentage",
    "trajectory_alignment_score",
}


def _input_findings(
    protocol_feature_rows: list[dict[str, Any]],
    data_tables: dict[str, list[dict[str, Any]]],
    protocol_ids: set[str],
    data_protocol_ids: set[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not protocol_feature_rows:
        findings.append({"severity": "high", "issue_type": "missing_protocol_feature_matrix"})
    if not any(data_tables.values()):
        findings.append({"severity": "high", "issue_type": "missing_data_analysis_outputs"})
    if protocol_ids and data_protocol_ids and not protocol_ids.intersection(data_protocol_ids):
        findings.append({"severity": "high", "issue_type": "no_joinable_protocol_ids"})
    if len(protocol_ids.intersection(data_protocol_ids)) < 10:
        findings.append(
            {
                "severity": "medium",
                "issue_type": "small_joinable_sample_size",
                "message": "Optimization should be interpreted as hypothesis ranking, not model-based causal inference.",
            }
        )
    return findings


def _read_protocol_event_evidence(protocol_dir: Path) -> list[dict[str, Any]]:
    normalized_events = read_jsonl(protocol_dir / "normalized_factor_events.jsonl")
    filtered_events = read_csv_dicts(protocol_dir / "filtered_protocol_factor_events.csv")
    if normalized_events:
        return [_canonical_event_row(event, "normalized_factor_events") for event in normalized_events]
    if filtered_events:
        return [_canonical_event_row(event, "filtered_protocol_factor_events") for event in filtered_events]
    return _stage_summary_event_rows(read_csv_dicts(protocol_dir / "stage_factor_matrix_or_events_summary.csv"))


def _canonical_event_row(event: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": source,
        "protocol_id": event.get("protocol_id", ""),
        "paper_id": event.get("paper_id", ""),
        "dataset_id": event.get("dataset_id", ""),
        "stage_id": event.get("stage_id") or event.get("stage") or event.get("stage_name") or "",
        "stage": event.get("stage") or event.get("stage_name") or event.get("stage_id") or "",
        "stage_index": event.get("stage_index", ""),
        "day_start": event.get("day_start", ""),
        "day_end": event.get("day_end", ""),
        "duration_days": event.get("duration_days", ""),
        "factor_type": event.get("factor_type", ""),
        "factor_name": event.get("factor_name") or event.get("original_name") or event.get("factor") or "",
        "standardized_name": event.get("standardized_name") or event.get("normalized_name") or "",
        "normalized_family": event.get("normalized_family", ""),
        "operation": event.get("operation") or event.get("action_type") or "",
        "concentration_value": event.get("concentration_value", ""),
        "concentration_unit": event.get("concentration_unit") or event.get("unit") or "",
        "evidence_quote": event.get("evidence_quote") or event.get("quote") or "",
        "source_doc_id": event.get("source_doc_id", ""),
    }


def _stage_summary_event_rows(stage_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage_row in stage_rows:
        for factor in str(stage_row.get("factors") or "").split("|"):
            if factor:
                rows.append(
                    {
                        "source": "stage_factor_matrix_or_events_summary",
                        "protocol_id": stage_row.get("protocol_id", ""),
                        "stage_id": stage_row.get("stage") or stage_row.get("stage_id") or "",
                        "stage": stage_row.get("stage") or stage_row.get("stage_id") or "",
                        "stage_index": stage_row.get("stage_index", ""),
                        "day_start": stage_row.get("day_start", ""),
                        "day_end": stage_row.get("day_end", ""),
                        "factor_name": factor,
                        "standardized_name": factor,
                        "evidence_quote": "",
                    }
                )
    return rows


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in ("", None)}


def _to_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)
