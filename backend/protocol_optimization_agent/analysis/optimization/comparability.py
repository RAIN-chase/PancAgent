"""Extracted comparability calculations; no agent execution."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from protocol_optimization_agent.analysis.optimization._values import _known, _number
from protocol_optimization_agent.science.identity import stable_id


def scientific_preconditions(
    protocols: list[dict[str, Any]],
    phenotypes: list[dict[str, Any]],
    purpose: str = "association",
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    comparison = comparison or {}
    if int(comparison.get("minimum_protocols", 3)) < 3 or int(comparison.get("minimum_replicates_per_protocol", 2)) < 2:
        raise ValueError(
            "Exploratory screening requires at least 3 protocols and 2 identified biological replicates per protocol."
        )
    endpoints = comparison.get("endpoints") or sorted(
        {m.get("endpoint", "") for p in phenotypes for m in p.get("metrics", [])}
    )
    stratum = comparison.get("stratum", {})
    selected, excluded = [], []
    protocol_ids = {p.get("protocol_id") for p in protocols}
    for phenotype in phenotypes:
        metrics = []
        for metric in phenotype.get("metrics", []):
            context = {**phenotype.get("metadata", {}), **metric.get("context", {})}
            if (
                metric.get("endpoint") in endpoints
                and _number(metric.get("value")) is not None
                and metric.get("status") in {"ok", "numeric", "computed", "passed"}
                and all(context.get(k) == v for k, v in stratum.items())
            ):
                metrics.append(metric)
        if phenotype.get("protocol_id") in protocol_ids and phenotype.get("validation_status") == "passed" and metrics:
            selected.append({**phenotype, "metrics": metrics})
        else:
            excluded.append(
                {"phenotype_id": phenotype.get("id"), "reason": "no_valid_target_endpoint_or_outside_stratum"}
            )
    phenotypes = selected
    observations = [
        (p["protocol_id"], p["dataset_id"], p.get("sample_id"), m["endpoint"]) for p in phenotypes for m in p["metrics"]
    ]
    unique_observations = len(observations) == len(set(observations))
    metric_ids = [m["id"] for p in phenotypes for m in p["metrics"]]
    unique_metrics = len(metric_ids) == len(set(metric_ids))
    selected_ids = {p.get("protocol_id") for p in selected}
    if purpose != "descriptive_comparison":
        protocols = [p for p in protocols if p.get("protocol_id") in selected_ids]
    checks = []
    metadata = [{**p, **p.get("metadata", {})} for p in protocols]
    if phenotypes:
        metadata.extend([{**p.get("metadata", {})} for p in phenotypes])
    for field in ("cell_line", "stage", "sequencing_modality", "reference_version"):
        # Unknown metadata is not evidence of comparability.
        relevant = metadata if field in {"cell_line", "stage"} else [p.get("metadata", {}) for p in phenotypes]
        values = {str(p[field]) for p in relevant if p.get(field) not in (None, "")}
        status = (
            "unknown"
            if not relevant or any(not _known(p.get(field)) for p in relevant)
            else "matched"
            if len(values) == 1
            else "mismatch"
        )
        checks.append({"name": field, "status": status, "values": sorted(values)})
    joinable = {str(p.get("protocol_id") or "") for p in protocols} & {
        str(p.get("protocol_id") or "") for p in phenotypes
    }
    checks.append(
        {
            "name": "joinable_protocols",
            "status": "passed" if len(joinable) >= int(comparison.get("minimum_protocols", 3)) else "insufficient",
            "count": len(joinable),
        }
    )
    checks.append(
        {
            "name": "phenotype_validation",
            "status": "passed"
            if phenotypes and all(p.get("validation_status") == "passed" for p in phenotypes)
            else "unknown",
        }
    )
    if phenotypes:
        endpoint_scales: dict[str, set[tuple[str, ...]]] = defaultdict(set)
        for phenotype in phenotypes:
            for metric in phenotype.get("metrics", []):
                if metric.get("value") is not None:
                    context = {**phenotype.get("metadata", {}), **metric.get("context", {})}
                    endpoint_scales[str(metric.get("endpoint") or "")].add(
                        tuple(
                            str(v or "")
                            for v in (
                                metric.get("method"),
                                metric.get("unit"),
                                context.get("expression_scale"),
                                context.get("normalization"),
                                context.get("method_version"),
                                context.get("cell_population"),
                                context.get("sampling_condition"),
                            )
                        )
                    )
        compatible = bool(endpoint_scales) and all(
            len(scales) == 1 and all(all(_known(v) for v in scale) for scale in scales)
            for scales in endpoint_scales.values()
        )
        checks.append(
            {
                "name": "endpoint_method_and_scale",
                "status": "passed" if compatible else "unknown_or_mismatch",
                "profiles": {
                    endpoint: [list(scale) for scale in sorted(scales)] for endpoint, scales in endpoint_scales.items()
                },
                "profile_fields": [
                    "method",
                    "unit",
                    "expression_scale",
                    "normalization",
                    "method_version",
                    "cell_population",
                    "sampling_condition",
                ],
            }
        )
    cohort_counts = {}
    for endpoint in endpoints:
        rows = [p for p in phenotypes if any(m["endpoint"] == endpoint for m in p["metrics"])]
        replicates: dict[str, set[str]] = defaultdict(set)
        for p in rows:
            for m in p["metrics"]:
                ctx = {**p.get("metadata", {}), **m.get("context", {})}
                if m["endpoint"] == endpoint and p.get("sample_id") and ctx.get("biological_replicate_id"):
                    replicates[p["protocol_id"]].add(str(ctx["biological_replicate_id"]))
        counts = {
            "protocols": len({p["protocol_id"] for p in rows}),
            "samples": len({(p["dataset_id"], p.get("sample_id")) for p in rows if p.get("sample_id")}),
            "biological_replicates_by_protocol": {key: len(value) for key, value in replicates.items()},
        }
        cohort_counts[endpoint] = counts
        complete = counts["protocols"] == len(joinable) and counts["protocols"] >= int(
            comparison.get("minimum_protocols", 3)
        )
        repeats = all(
            len(replicates.get(pid, set())) >= int(comparison.get("minimum_replicates_per_protocol", 2))
            for pid in joinable
        )
        checks.append(
            {
                "name": f"endpoint_cohort:{endpoint}",
                "status": "passed" if complete and repeats else "insufficient_or_unknown_replicates",
                **counts,
            }
        )
    checks.append({"name": "target_endpoints", "status": "passed" if endpoints else "unknown"})
    checks.append(
        {
            "name": "unique_sample_endpoint_observations",
            "status": "passed" if unique_observations and unique_metrics else "duplicate",
        }
    )
    ready = all(c["status"] in {"matched", "passed"} for c in checks)
    return {
        "schema_version": "scientific-preconditions-v2",
        "purpose": purpose,
        "status": "passed"
        if ready
        else "descriptive_only"
        if purpose == "descriptive_comparison"
        else "human_review_required",
        "scientific_ready": ready,
        "causal_claim_allowed": False,
        "checks": checks,
        "cohort": {
            "endpoints": endpoints,
            "stratum": stratum,
            "counts": cohort_counts,
            "included_metric_ids": sorted(m["id"] for p in phenotypes for m in p["metrics"]),
            "excluded": excluded,
        },
        "input_fingerprint": stable_id(
            "cohort", {"protocols": protocols, "phenotypes": phenotypes, "comparison": comparison}
        ),
        "protocol_fingerprints": {p["protocol_id"]: stable_id("protocol_snapshot", p) for p in protocols},
        "limitations": [
            "Sample-count thresholds permit exploratory screening only; they do not establish statistical power or causal effects.",
            "No implicit batch correction or cross-scale transformation is performed.",
        ],
    }
