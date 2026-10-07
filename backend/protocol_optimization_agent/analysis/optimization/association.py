"""Extracted association calculations; no agent execution."""

from __future__ import annotations

from collections import defaultdict
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
from protocol_optimization_agent.science.identity import stable_id


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


def _composite_associations(state: ProtocolOptimizingState) -> list[dict[str, Any]]:
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
                "analysis_role": "cohort_relative_auxiliary",
                "effect_type": "pearson_r_not_intervention_effect",
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
    return association_rows


def leave_study_out(points: list[dict[str, Any]], minimum: int = 3) -> dict[str, Any]:
    studies = {study for point in points for study in point["study_ids"] if study != "unknown"}
    unknown = any("unknown" in point["study_ids"] or not point["study_ids"] for point in points)
    folds = []
    full_r = _pearson([p["x"] for p in points], [p["y"] for p in points])
    for study in sorted(studies):
        # A protocol cannot be on both sides of a fold, even when it occurs in multiple studies.
        held_out = [p for p in points if study in p["study_ids"]]
        retained = [p for p in points if study not in p["study_ids"]]
        valid = (len(retained) >= minimum and len({p["x"] for p in retained}) > 1
                 and len({p["y"] for p in retained}) > 1)
        folds.append({"held_out_study": study, "excluded_protocol_ids": [p["protocol_id"] for p in held_out],
            "retained_protocol_ids": [p["protocol_id"] for p in retained],
            "status": "computed" if valid else "insufficient_protocols_or_variation",
            "pearson_r": _pearson([p["x"] for p in retained], [p["y"] for p in retained]) if valid else None})
    valid_folds = [f for f in folds if f["status"] == "computed"]
    stable = (bool(valid_folds) and full_r != 0 and all(f["pearson_r"] * full_r > 0 for f in valid_folds))
    status = "direction_stable_exploratory" if stable else "direction_unstable"
    if unknown or len(studies) < 3 or len(valid_folds) != len(studies):
        status = "not_evaluable_unknown_study_or_insufficient_folds"
    return {"status": status, "n_studies": len(studies), "unknown_study_present": unknown,
            "n_valid_folds": len(valid_folds), "folds": folds,
            "policy": "Descriptive coefficient sensitivity, not held-out intervention prediction or confounder adjustment."}


def analyze_associations(state: ProtocolOptimizingState, max_candidate_features: int) -> ProtocolOptimizingState:
    endpoint_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in state.get("endpoint_rows", []):
        if _to_float(row.get("raw_mean")) is not None:
            endpoint_groups[(row["endpoint"], row["protocol_id"])].append(row)
    features = _candidate_protocol_features(state.get("protocol_feature_rows", []))
    protocols = {row["protocol_id"]: row for row in state.get("protocol_feature_rows", [])}
    minimum = int(state.get("objective", {}).get("comparison", {}).get("minimum_protocols", 3))
    associations = []
    for endpoint in sorted({endpoint for endpoint, _ in endpoint_groups}):
        for feature in features:
            points: list[dict[str, Any]] = []
            for (target, pid), rows in sorted(endpoint_groups.items()):
                if target != endpoint or pid not in protocols:
                    continue
                x = _to_float(protocols[pid].get(feature))
                mean_y = _mean([float(row["raw_mean"]) for row in rows])
                if x is not None and mean_y is not None:
                    points.append({"protocol_id": pid, "x": x, "y": mean_y,
                        "study_ids": sorted({row["study_id"] for row in rows}),
                        "metric_ids": [mid for row in rows for mid in row["metric_ids"]],
                        "observation_ids": [oid for row in rows for oid in row["observation_ids"]]})
            xs, ys = [p["x"] for p in points], [p["y"] for p in points]
            if len(points) < minimum or len(set(xs)) < 2 or len(set(ys)) < 2:
                continue
            sensitivity = leave_study_out(points, minimum)
            collinear = []
            for other in features:
                if other == feature:
                    continue
                pairs = [(float(p["x"]), y) for p in points
                         if (y := _to_float(protocols[p["protocol_id"]].get(other))) is not None]
                if len(pairs) >= minimum and len({y for _, y in pairs}) > 1:
                    r = _pearson([x for x, _ in pairs], [y for _, y in pairs])
                    if abs(r) >= 0.9:
                        collinear.append({"feature": other, "pearson_r": r, "n_protocols": len(pairs)})
            associations.append({"feature": feature, "target_endpoint": endpoint, "n_protocols": len(points),
                "analysis_role": "primary_raw_endpoint", "association_method": "pearson_correlation_screen",
                "effect_estimate": _pearson(xs, ys), "effect_type": "pearson_r_not_intervention_effect",
                "analysis_dataset_id": state["analysis_dataset"]["id"], "points": points,
                "association_id": stable_id("association", [state["analysis_dataset"]["id"], endpoint, feature, points]),
                "stability_status": sensitivity["status"], "leave_study_out": sensitivity,
                "collinear_features": collinear, "confidence": "low", "causal_interpretation": "association_only",
                "confounding_risk": "cell_line_batch_time_and_cointerventions_not_adjusted",
                "multiplicity_policy": "exploratory_screen_not_calibrated_significance_or_discovery_claim"})
    associations.sort(key=lambda row: abs(row["effect_estimate"]), reverse=True)
    # Retain the original composite screen only as an explicitly auxiliary comparison baseline.
    return {"association_rows": [*associations[:max_candidate_features], *_composite_associations(state)[:max_candidate_features]]}
