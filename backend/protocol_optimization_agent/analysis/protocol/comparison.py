"""Extracted comparison calculations; no agent execution."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import Any

from protocol_optimization_agent.analysis.protocol._shared import _metadata_columns, _safe_float, _truthy_feature
from protocol_optimization_agent.analysis.protocol.feature_groups import FEATURE_GROUP_ORDER
from protocol_optimization_agent.analysis.protocol.feature_groups import feature_group as _feature_group
from protocol_optimization_agent.analysis.protocol.feature_groups import feature_group_rank as _feature_group_rank


def _build_protocol_difference_report(
    feature_rows: list[dict[str, Any]],
    relationship_report: dict[str, Any],
    strategy_summary: dict[str, Any],
    factor_report: dict[str, Any],
) -> dict[str, Any]:
    pairwise_summaries = []
    rows_by_protocol = {str(row.get("protocol_id") or ""): row for row in feature_rows}
    for relationship in relationship_report.get("pairwise_relationships", []):
        protocol_a = str(relationship.get("protocol_id_a") or "")
        protocol_b = str(relationship.get("protocol_id_b") or "")
        left = rows_by_protocol.get(protocol_a, {})
        right = rows_by_protocol.get(protocol_b, {})
        differences = _ranked_feature_differences(left, right, max_features=30)
        pairwise_summaries.append(
            {
                "protocol_id_a": protocol_a,
                "protocol_id_b": protocol_b,
                "binary_feature_jaccard_similarity": relationship.get("binary_feature_jaccard_similarity"),
                "difference_groups": _group_protocol_feature_differences(differences),
                "shared_binary_features": relationship.get("shared_binary_features", [])[:30],
                "protocol_a_specific_features": relationship.get("protocol_a_specific_features", [])[:30],
                "protocol_b_specific_features": relationship.get("protocol_b_specific_features", [])[:30],
            }
        )
    return {
        "schema_version": "protocol-factor-difference-report-v2",
        "summary": {
            "n_protocols": len(feature_rows),
            "n_pairwise_comparisons": len(pairwise_summaries),
            "n_unique_factors": factor_report.get("summary", {}).get("n_unique_factors", 0),
            "source": "computed_from_normalized_semantic_feature_matrix",
        },
        "strategy_summary": strategy_summary,
        "pairwise_differences": pairwise_summaries,
        "factor_index": factor_report.get("factor_index", {}),
    }


def _ranked_feature_differences(left: dict[str, Any], right: dict[str, Any], max_features: int) -> list[dict[str, Any]]:
    differences = []
    for key in sorted(set(left).union(right)):
        if key in _metadata_columns():
            continue
        left_value = left.get(key, "")
        right_value = right.get(key, "")
        if left_value == right_value:
            continue
        group = _feature_group(key)
        differences.append(
            {
                "feature": key,
                "protocol_a_value": left_value,
                "protocol_b_value": right_value,
                "difference_group": group,
                "rank": _feature_group_rank(group),
            }
        )
    return sorted(differences, key=lambda item: (item["rank"], item["feature"]))[:max_features]


def _group_protocol_feature_differences(differences: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for difference in differences:
        payload = dict(difference)
        payload.pop("rank", None)
        group = str(payload.pop("difference_group"))
        grouped[group].append(payload)
    return {group: grouped[group] for group in FEATURE_GROUP_ORDER if grouped.get(group)}


def _build_protocol_relationships(feature_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not feature_rows:
        return [], {"pairwise_relationships": [], "binary_feature_keys": []}
    binary_keys = [
        key
        for key in feature_rows[0]
        if key not in _metadata_columns()
        and (
            key.startswith("has_")
            or key.endswith("_presence")
            or key.startswith("transition_")
            or key.startswith("requires_")
        )
    ]
    rows: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    for left, right in combinations(feature_rows, 2):
        left_set = {key for key in binary_keys if _truthy_feature(left.get(key))}
        right_set = {key for key in binary_keys if _truthy_feature(right.get(key))}
        union = left_set.union(right_set)
        shared = sorted(left_set.intersection(right_set))
        left_only = sorted(left_set - right_set)
        right_only = sorted(right_set - left_set)
        jaccard = len(shared) / len(union) if union else 1.0
        row = {
            "protocol_id_a": left["protocol_id"],
            "protocol_id_b": right["protocol_id"],
            "binary_feature_jaccard_similarity": round(jaccard, 4),
            "n_shared_binary_features": len(shared),
            "n_protocol_a_specific_features": len(left_only),
            "n_protocol_b_specific_features": len(right_only),
        }
        rows.append(row)
        pair_reports.append(
            {
                **row,
                "shared_binary_features": shared[:50],
                "protocol_a_specific_features": left_only[:50],
                "protocol_b_specific_features": right_only[:50],
            }
        )
    return rows, {"pairwise_relationships": pair_reports, "binary_feature_keys": binary_keys}


def _build_protocol_embeddings_and_clusters(
    feature_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    numeric_keys = _numeric_feature_keys(feature_rows)
    normalized = _normalized_numeric_features(feature_rows, numeric_keys)
    embeddings: list[dict[str, Any]] = []
    cluster_members: dict[str, list[str]] = defaultdict(list)
    for row in feature_rows:
        protocol_id = row["protocol_id"]
        values = normalized.get(protocol_id, {})
        reagent_presence_keys = [
            key for key in numeric_keys if key.endswith("_presence") and _safe_float(row.get(key), 0.0)
        ]
        workflow_keys = [key for key in numeric_keys if key.startswith("has_") and _safe_float(row.get(key), 0.0)]
        embedding_1 = round(sum(values.get(key, 0.0) for key in reagent_presence_keys), 6)
        embedding_2 = round(sum(values.get(key, 0.0) for key in workflow_keys), 6)
        embedding_3 = round(sum(values.values()) / len(values), 6) if values else 0.0
        cluster = _assign_protocol_cluster(row)
        cluster_members[cluster].append(protocol_id)
        embeddings.append(
            {
                "protocol_id": protocol_id,
                "embedding_1_reagent_strategy": embedding_1,
                "embedding_2_workflow_strategy": embedding_2,
                "embedding_3_overall_feature_load": embedding_3,
                "protocol_cluster": cluster,
            }
        )
    return embeddings, {
        "method": "deterministic_feature_embedding_v1",
        "note": "This is a lightweight deterministic embedding. Replace with PCA/UMAP after the protocol cohort is large enough.",
        "numeric_feature_count": len(numeric_keys),
        "clusters": dict(cluster_members),
    }


def _numeric_feature_keys(feature_rows: list[dict[str, Any]]) -> list[str]:
    if not feature_rows:
        return []
    keys: list[str] = []
    for key in feature_rows[0]:
        if key in _metadata_columns():
            continue
        if any(_safe_float(row.get(key), None) is not None for row in feature_rows):
            keys.append(key)
    return keys


def _normalized_numeric_features(
    feature_rows: list[dict[str, Any]], numeric_keys: list[str]
) -> dict[str, dict[str, float]]:
    mins: dict[str, float] = {}
    maxs: dict[str, float] = {}
    for key in numeric_keys:
        values = [value for row in feature_rows if (value := _safe_float(row.get(key), None)) is not None]
        if values:
            mins[key] = min(values)
            maxs[key] = max(values)
    normalized: dict[str, dict[str, float]] = {}
    for row in feature_rows:
        protocol_id = str(row.get("protocol_id") or "")
        normalized[protocol_id] = {}
        for key in numeric_keys:
            value = _safe_float(row.get(key), None)
            if value is None:
                normalized[protocol_id][key] = 0.0
                continue
            low = mins.get(key, 0.0)
            high = maxs.get(key, low)
            normalized[protocol_id][key] = 0.0 if high == low else (value - low) / (high - low)
    return normalized


def _assign_protocol_cluster(row: dict[str, Any]) -> str:
    if _truthy_feature(row.get("has_aggregation")) or _truthy_feature(row.get("has_suspension_culture")):
        culture = "3d_or_suspension"
    elif _truthy_feature(row.get("has_2d_adherent_culture")):
        culture = "2d_adherent"
    else:
        culture = "culture_unspecified"

    maturation_markers = (
        "alk5i_presence",
        "gammasecretaseinhibitor_presence",
        "t3_presence",
        "betacellulin_presence",
        "heparin_presence",
    )
    has_maturation = any(_truthy_feature(row.get(feature)) for feature in maturation_markers)
    has_patterning = any(
        _truthy_feature(row.get(feature))
        for feature in ("activin_presence", "chir_presence", "kgf_presence", "ra_presence")
    )
    if has_maturation and has_patterning:
        strategy = "patterning_plus_maturation"
    elif has_patterning:
        strategy = "patterning_focused"
    elif has_maturation:
        strategy = "maturation_focused"
    else:
        strategy = "low_factor_resolution"
    return f"{culture}:{strategy}"
