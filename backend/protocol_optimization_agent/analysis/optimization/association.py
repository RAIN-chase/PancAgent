"""Extracted association calculations; no agent execution."""

from __future__ import annotations

from math import sqrt
from typing import Any

from protocol_optimization_agent.analysis.optimization._shared import (
    INTERVENTION_FEATURE_PREFIXES,
    MIN_ASSOCIATION_PROTOCOLS,
    NON_INTERVENTION_FEATURES,
    _mean,
    _to_float,
)
from protocol_optimization_agent.analysis.optimization.contracts import ProtocolOptimizingState


def _association_confounding_risk(n_protocols: int) -> str:
    if n_protocols < MIN_ASSOCIATION_PROTOCOLS:
        return "insufficient_sample"
    if n_protocols < 10:
        return "high_small_sample"
    if n_protocols < 30:
        return "medium_small_sample"
    return "unmodeled_protocol_dataset_confounding"


def _feature_prevalence(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value != 0.0) / len(values)


def _association_stability_status(values: list[float], n_protocols: int) -> str:
    prevalence = _feature_prevalence(values)
    if n_protocols < 10:
        return "exploratory_small_sample"
    if prevalence is None or prevalence <= 0.1 or prevalence >= 0.9:
        return "unstable_low_feature_variation"
    return "screening_only_unvalidated"


def _association_confidence(n_protocols: int, values: list[float], confounding_risk: str) -> str:
    stability = _association_stability_status(values, n_protocols)
    if confounding_risk.startswith("high") or stability != "screening_only_unvalidated":
        return "low"
    return "medium"


def _candidate_protocol_features(rows: list[dict[str, Any]]) -> list[str]:
    features: list[str] = []
    for row in rows:
        for key in row:
            if key in features or not _is_intervention_feature(key):
                continue
            if any(_to_float(other.get(key)) is not None for other in rows):
                features.append(key)
    return features


def _is_intervention_feature(feature: str) -> bool:
    if feature in NON_INTERVENTION_FEATURES:
        return False
    if feature.endswith(("_score", "_confidence", "_ready")):
        return False
    return feature.startswith(INTERVENTION_FEATURE_PREFIXES)


def _pearson(xs: list[float], ys: list[float]) -> float:
    x_mean = _mean(xs) or 0.0
    y_mean = _mean(ys) or 0.0
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    x_den = sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_den = sqrt(sum((y - y_mean) ** 2 for y in ys))
    if x_den == 0 or y_den == 0:
        return 0.0
    return numerator / (x_den * y_den)


def analyze_associations(state: ProtocolOptimizingState, max_candidate_features: int) -> ProtocolOptimizingState:
    phenotype_score_by_protocol = {
        row["protocol_id"]: row["objective_weighted_score"]
        for row in state.get("protocol_phenotype_rows", [])
        if row.get("has_numeric_phenotype") and row.get("objective_weighted_score") is not None
    }
    feature_names = _candidate_protocol_features(state.get("protocol_feature_rows", []))
    association_rows: list[dict[str, Any]] = []
    for feature in feature_names:
        paired: list[tuple[float, float]] = []
        for row in state.get("protocol_feature_rows", []):
            protocol_id = str(row.get("protocol_id") or "")
            if protocol_id not in phenotype_score_by_protocol:
                continue
            value = _to_float(row.get(feature))
            if value is None:
                continue
            paired.append((value, float(phenotype_score_by_protocol[protocol_id])))
        if len(paired) < int(state.get("objective", {}).get("comparison", {}).get("minimum_protocols", 3)):
            continue
        xs = [pair[0] for pair in paired]
        ys = [pair[1] for pair in paired]
        effect = _pearson(xs, ys)
        confounding_risk = _association_confounding_risk(len(paired))
        association_rows.append(
            {
                "feature": feature,
                "n_protocols": len(paired),
                "association_method": "pearson_correlation_screen",
                "analysis_dataset_id": state.get("analysis_dataset", {}).get("id", ""),
                "target_endpoint": "objective_weighted_score",
                "effect_estimate": effect,
                "mean_feature_value": _mean(xs),
                "mean_objective_score": _mean(ys),
                "feature_prevalence": _feature_prevalence(xs),
                "causal_interpretation": "association_only",
                "confounding_risk": confounding_risk,
                "stability_status": _association_stability_status(xs, len(paired)),
                "confidence": _association_confidence(len(paired), xs, confounding_risk),
            }
        )
    association_rows.sort(key=lambda row: abs(float(row.get("effect_estimate") or 0.0)), reverse=True)
    return {"association_rows": association_rows[:max_candidate_features]}
