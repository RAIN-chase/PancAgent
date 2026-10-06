"""Extracted assessment calculations; no agent execution."""

from __future__ import annotations

from math import sqrt
from typing import Any

from protocol_optimization_agent.analysis.optimization._shared import BOUNDED_SCORE_COLUMNS, _mean, _to_float
from protocol_optimization_agent.analysis.optimization.association import _is_intervention_feature
from protocol_optimization_agent.analysis.optimization.contracts import ProtocolOptimizingState


def _score_columns_for_table(table_name: str) -> list[str]:
    defaults = ["score", "mean_score", "maturity_score", "program_score", "marker_score"]
    if table_name == "similarity_scores":
        return ["cosine_similarity", "similarity_score", *defaults]
    if table_name == "cell_type_composition":
        return ["fraction", "proportion", "percentage", "score"]
    if table_name == "trajectory_scores":
        return ["trajectory_alignment_score", "pseudotime_score", *defaults]
    return defaults


def _first_numeric(row: dict[str, Any], columns: list[str]) -> tuple[float | None, str]:
    for column in columns:
        value = _to_float(row.get(column))
        if value is not None:
            return value, column
    for column, raw_value in row.items():
        if column.endswith("_score") or column.endswith("_similarity"):
            value = _to_float(raw_value)
            if value is not None:
                return value, column
    return None, ""


def _endpoint_name(table_name: str, row: dict[str, Any]) -> str:
    for column in ("endpoint", "gene_program", "marker_set", "marker", "cell_type", "lineage", "regulon", "trajectory"):
        if row.get(column):
            return f"{table_name}:{row[column]}"
    return table_name


def _endpoint_direction(table_name: str, endpoint: str) -> str:
    lowered = endpoint.lower()
    if table_name == "cell_type_composition" and any(
        term in lowered for term in ("off_target", "ductal", "progenitor")
    ):
        return "lower_is_better"
    if any(term in lowered for term in ("stress", "hypoxia", "proliferation", "immature", "off_target", "apoptosis")):
        return "lower_is_better"
    return "higher_is_better"


def _endpoint_weight(table_name: str, endpoint: str, objective: dict[str, Any]) -> float:
    weights = objective.get("endpoint_weights", {})
    if isinstance(weights, dict):
        configured = _configured_weight(weights, endpoint, table_name)
        if configured is not None:
            return configured
    lowered = endpoint.lower()
    if "qc" in lowered:
        return 0.0
    if table_name in {"maturity_scores", "similarity_scores", "regulatory_program_scores"}:
        return 1.0
    if table_name in {"marker_scores", "gene_program_scores", "trajectory_scores"}:
        return 0.75
    if table_name == "cell_type_composition":
        return 0.6
    return 0.5


def _configured_weight(weights: dict[Any, Any], endpoint: str, table_name: str) -> float | None:
    if endpoint in weights:
        return _to_float(weights[endpoint])
    if table_name in weights:
        return _to_float(weights[table_name])
    return None


def _endpoint_confidence(row: dict[str, Any], score: float | None) -> str:
    status = str(row.get("status") or "").lower()
    if score is None or status in {"pending", "missing", "not_available"}:
        return "pending"
    if any(row.get(key) for key in ("qc_pass", "n_cells", "n_cells_used", "n_genes_used")):
        return "medium"
    return "low"


def _apply_endpoint_normalization(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoint_values: dict[str, list[float]] = {}
    for target in targets:
        score = _to_float(target.get("score"))
        if score is not None:
            endpoint_values.setdefault(str(target["endpoint"]), []).append(score)
    normalized_targets: list[dict[str, Any]] = []
    common_endpoints = _common_numeric_endpoints(targets)
    for target in targets:
        raw_score = _to_float(target.get("score"))
        endpoint = str(target["endpoint"])
        score_column = str(target.get("score_column") or "")
        values = endpoint_values.get(endpoint, [])
        normalized_score, normalization = _normalized_endpoint_score(raw_score, values, score_column)
        target = {
            **target,
            "normalization": normalization,
            "normalized_score": normalized_score,
            "objective_score": _objective_score(normalized_score, str(target["direction"])),
            "in_objective_endpoint_set": endpoint in common_endpoints,
        }
        normalized_targets.append(target)
    return normalized_targets


def _common_numeric_endpoints(targets: list[dict[str, Any]]) -> set[str]:
    protocol_ids = {
        str(target.get("protocol_id") or "")
        for target in targets
        if _to_float(target.get("score")) is not None and _to_float(target.get("weight")) not in (None, 0.0)
    }
    endpoint_protocols: dict[str, set[str]] = {}
    for target in targets:
        if _to_float(target.get("score")) is None or _to_float(target.get("weight")) in (None, 0.0):
            continue
        endpoint_protocols.setdefault(str(target["endpoint"]), set()).add(str(target.get("protocol_id") or ""))
    return {endpoint for endpoint, present in endpoint_protocols.items() if present == protocol_ids}


def _normalized_endpoint_score(
    raw_score: float | None, values: list[float], score_column: str
) -> tuple[float | None, str]:
    if raw_score is None:
        return None, "missing_numeric_score"
    if score_column in BOUNDED_SCORE_COLUMNS and all(0.0 <= value <= 1.0 for value in values):
        return raw_score, "bounded_0_1"
    if len(set(values)) <= 1:
        return 0.5, "constant_endpoint_midpoint"
    min_value = min(values)
    max_value = max(values)
    return (raw_score - min_value) / (max_value - min_value), f"minmax_by_endpoint:{min_value}:{max_value}"


def _objective_score(normalized_score: float | None, direction: str) -> float | None:
    if normalized_score is None:
        return None
    if direction == "lower_is_better":
        return 1.0 - normalized_score
    return normalized_score


def _target_confounders(row: dict[str, Any]) -> dict[str, str]:
    keys = (
        "dataset_id",
        "paper_id",
        "cell_line",
        "assay",
        "timepoint",
        "batch",
        "batch_id",
        "condition_id",
        "sample_id",
    )
    return {key: str(row.get(key) or "") for key in keys if row.get(key)}


def _weighted_objective_scores(targets: list[dict[str, Any]]) -> list[tuple[float, float]]:
    endpoints: dict[str, list[tuple[float, float]]] = {}
    for target in targets:
        if not target.get("in_objective_endpoint_set"):
            continue
        objective_score = _to_float(target.get("objective_score"))
        weight = _to_float(target.get("weight"))
        if objective_score is not None and weight is not None and weight > 0:
            endpoints.setdefault(target["endpoint"], []).append((objective_score, weight))
    # More samples for one endpoint must not implicitly increase that endpoint's objective weight.
    return [(sum(score for score, _ in rows) / len(rows), rows[0][1]) for rows in endpoints.values()]


def _confidence_weight(confidence: str) -> float:
    if confidence == "high":
        return 1.0
    if confidence == "medium":
        return 0.75
    if confidence == "pending":
        return 0.0
    return 0.5


def _weighted_mean(values: list[tuple[float, float]]) -> float | None:
    denominator = sum(weight for _, weight in values)
    if denominator == 0:
        return None
    return sum(value * weight for value, weight in values) / denominator


def _target_values(targets: list[dict[str, Any]], key: str) -> set[str]:
    return {str(target.get(key) or "") for target in targets if target.get(key)}


def _protocol_confounder_flags(protocol_row: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    if protocol_row.get("paper_id"):
        flags.append("paper")
    if len(_target_values(targets, "dataset_id")) > 1:
        flags.append("multiple_datasets")
    for target in targets:
        confounders = target.get("confounders", {})
        if isinstance(confounders, dict):
            for key in ("cell_line", "assay", "timepoint", "batch", "batch_id"):
                if confounders.get(key) and key not in flags:
                    flags.append(key)
    return flags


def _nearest_protocols(new_features: dict[str, Any], rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    feature_scales = _feature_scales(rows)
    intervention_features = sorted(feature for feature in feature_scales if _is_intervention_feature(feature))
    new_numeric = _numeric_items(new_features)
    for row in rows:
        protocol_id = str(row.get("protocol_id") or "")
        if not protocol_id:
            continue
        row_numeric = _numeric_items(row)
        shared = [key for key in intervention_features if key in new_numeric and key in row_numeric]
        if not shared:
            continue
        distance = sqrt(
            sum(
                (
                    _scaled_value(new_numeric[key], feature_scales[key])
                    - _scaled_value(row_numeric[key], feature_scales[key])
                )
                ** 2
                for key in shared
            )
        )
        similarity = 1.0 / (1.0 + distance)
        coverage = len(shared) / len(intervention_features) if intervention_features else 0.0
        scored.append(
            {
                "protocol_id": protocol_id,
                "similarity": similarity,
                "n_shared_features": len(shared),
                "feature_coverage": coverage,
                "out_of_distribution_features": _out_of_distribution_features(
                    new_numeric,
                    feature_scales,
                    intervention_features,
                ),
                "scoring_policy": "minmax_scaled_intervention_features_only",
            }
        )
    scored.sort(key=lambda row: float(row["similarity"]), reverse=True)
    return scored[:limit]


def _feature_scales(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    values_by_feature: dict[str, list[float]] = {}
    for row in rows:
        for feature, value in _numeric_items(row).items():
            if _is_intervention_feature(feature):
                values_by_feature.setdefault(feature, []).append(value)
    return {feature: (min(values), max(values)) for feature, values in values_by_feature.items() if values}


def _scaled_value(value: float, scale: tuple[float, float]) -> float:
    min_value, max_value = scale
    if min_value == max_value:
        return 0.5
    return (value - min_value) / (max_value - min_value)


def _out_of_distribution_features(
    new_numeric: dict[str, float],
    feature_scales: dict[str, tuple[float, float]],
    allowed_features: list[str],
) -> list[str]:
    features: list[str] = []
    for feature in allowed_features:
        value = new_numeric.get(feature)
        if value is None:
            continue
        scale = feature_scales.get(feature)
        if scale is None:
            features.append(feature)
            continue
        min_value, max_value = scale
        if value < min_value or value > max_value:
            features.append(feature)
    return sorted(features)


def _nearest_feature_coverage(nearest: list[dict[str, Any]]) -> dict[str, Any]:
    coverages = [_to_float(row.get("feature_coverage")) for row in nearest]
    numeric_coverages = [coverage for coverage in coverages if coverage is not None]
    return {
        "mean": _mean(numeric_coverages),
        "min": min(numeric_coverages) if numeric_coverages else None,
        "nearest_neighbor_count": len(nearest),
    }


def _nearest_out_of_distribution(nearest: list[dict[str, Any]]) -> dict[str, Any]:
    features = {
        str(feature) for row in nearest for feature in row.get("out_of_distribution_features", []) if str(feature)
    }
    return {"status": "out_of_distribution" if features else "in_distribution", "features": sorted(features)}


def _neighbor_scores(nearest: list[dict[str, Any]], numeric_scores: dict[str, Any]) -> list[float]:
    scores: list[float] = []
    for row in nearest:
        protocol_id = str(row.get("protocol_id") or "")
        score = _to_float(numeric_scores.get(protocol_id))
        if score is not None:
            scores.append(score)
    return scores


def _numeric_items(row: dict[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in row.items():
        parsed = _to_float(value)
        if parsed is not None:
            numeric[key] = parsed
    return numeric


def _uncertainty_drivers(state: ProtocolOptimizingState, n_neighbors_with_scores: int) -> list[str]:
    drivers = ["protocol-feature associations are not causal"]
    if n_neighbors_with_scores < 3:
        drivers.append("fewer than three nearest protocols have numeric phenotype scores")
    if not state.get("association_rows"):
        drivers.append("no numeric association screen was possible from current Data Analysis outputs")
    return drivers


def _relative_label(scores: list[float]) -> str:
    if not scores:
        return "unknown_numeric_scores_pending"
    mean_score = _mean(scores)
    if mean_score is None:
        return "unknown_numeric_scores_pending"
    if mean_score >= 0.67:
        return "nearest_protocols_have_high_objective_scores"
    if mean_score <= 0.33:
        return "nearest_protocols_have_low_objective_scores"
    return "nearest_protocols_have_intermediate_or_uncertain_objective_scores"


def build_phenotype_targets(state: ProtocolOptimizingState) -> ProtocolOptimizingState:
    targets: list[dict[str, Any]] = []
    gate = state.get("scientific_preconditions", {})
    included = set(gate.get("cohort", {}).get("included_metric_ids", []))
    for phenotype in state.get("analysis_dataset", {}).get("phenotypes", []):
        for metric in phenotype["metrics"]:
            row = {**phenotype.get("metadata", {}), **metric.get("context", {})}
            table_name = row.get("source_table") or metric["family"]
            protocol_id = phenotype["protocol_id"]
            endpoint = metric["endpoint"]
            score_column = row.get("source_column", "score")
            score = metric["value"] if gate.get("scientific_ready") and metric["id"] in included else None
            direction = _endpoint_direction(table_name, endpoint)
            weight = _endpoint_weight(table_name, endpoint, state.get("objective", {}))
            confidence = _endpoint_confidence(row, score)
            targets.append(
                {
                    "protocol_id": protocol_id,
                    "dataset_id": phenotype["dataset_id"],
                    "sample_id": phenotype.get("sample_id", ""),
                    "metric_id": metric["id"],
                    "endpoint_family": table_name,
                    "endpoint": endpoint,
                    "score": score,
                    "raw_score": metric["value"],
                    "numeric_merge_allowed": score is not None,
                    "score_column": score_column,
                    "status": row.get("status", "numeric" if score is not None else "pending"),
                    "direction": direction,
                    "weight": weight,
                    "confidence": confidence,
                    "normalization": "",
                    "normalized_score": None,
                    "objective_score": None,
                    "confounders": _target_confounders(row),
                    "raw_basis": metric,
                }
            )
    targets = _apply_endpoint_normalization(targets)
    return {"phenotype_targets": targets}


def join_protocol_phenotypes(state: ProtocolOptimizingState) -> ProtocolOptimizingState:
    phenotype_by_protocol: dict[str, list[dict[str, Any]]] = {}
    for target in state.get("phenotype_targets", []):
        phenotype_by_protocol.setdefault(str(target.get("protocol_id") or ""), []).append(target)
    rows: list[dict[str, Any]] = []
    for protocol_row in state.get("protocol_feature_rows", []):
        protocol_id = str(protocol_row.get("protocol_id") or "")
        targets = phenotype_by_protocol.get(protocol_id, [])
        numeric_scores = _weighted_objective_scores(targets)
        rows.append(
            {
                "protocol_id": protocol_id,
                "paper_id": protocol_row.get("paper_id", ""),
                "dataset_ids": "|".join(sorted(_target_values(targets, "dataset_id"))),
                "n_phenotype_targets": len(targets),
                "n_numeric_phenotype_targets": len(numeric_scores),
                "objective_weighted_score": _weighted_mean(numeric_scores),
                "mean_numeric_phenotype_score": _weighted_mean(numeric_scores),
                "has_numeric_phenotype": bool(numeric_scores),
                "phenotype_status": "numeric_available" if numeric_scores else "numeric_pending",
                "confounder_flags": "|".join(_protocol_confounder_flags(protocol_row, targets)),
            }
        )
    return {"protocol_phenotype_rows": rows}


def score_counterfactual_protocol(state: ProtocolOptimizingState) -> ProtocolOptimizingState:
    new_features = state.get("new_protocol_features", {})
    if not new_features:
        report: dict[str, Any] = {
            "schema_version": "counterfactual-protocol-prediction-v1",
            "status": "not_requested",
            "prediction_policy": "similar-protocol reference only; no intervention outcome model fitted",
            "nearest_protocols": [],
        }
    else:
        nearest = _nearest_protocols(new_features, state.get("protocol_feature_rows", []), limit=5)
        numeric_scores = {
            row["protocol_id"]: row.get("objective_weighted_score")
            for row in state.get("protocol_phenotype_rows", [])
            if row.get("has_numeric_phenotype")
        }
        neighbor_scores = _neighbor_scores(nearest, numeric_scores)
        report = {
            "schema_version": "counterfactual-protocol-prediction-v1",
            "status": "similar_protocol_reference_scored",
            "prediction_policy": "similar-protocol reference only; not a causal or supervised prediction model",
            "relative_assessment": _relative_label(neighbor_scores),
            "confidence": "low" if len(neighbor_scores) < 3 else "medium",
            "nearest_protocols": nearest,
            "neighbor_mean_objective_score": _mean(neighbor_scores),
            "feature_coverage": _nearest_feature_coverage(nearest),
            "out_of_distribution": _nearest_out_of_distribution(nearest),
            "uncertainty_drivers": _uncertainty_drivers(state, len(neighbor_scores)),
        }
    return {"prediction_report": report}
