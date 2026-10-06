"""Compare a named endpoint without ranking protocols or merging unknown scales."""

from __future__ import annotations

import math
from typing import Any


def compare_endpoint(phenotype_ir: dict[str, Any], protocol_ids: list[str], endpoint: str) -> dict[str, Any]:
    observations = []
    for phenotype in phenotype_ir.get("phenotypes", []):
        if phenotype.get("protocol_id") not in protocol_ids:
            continue
        for metric in phenotype.get("metrics", []):
            value = metric.get("value")
            if (
                metric.get("endpoint") != endpoint
                or isinstance(value, bool)
                or not isinstance(value, (float, int))
                or not math.isfinite(value)
            ):
                continue
            if metric.get("status") not in {"ok", "numeric", "computed", "passed"}:
                continue
            observations.append(
                {
                    "protocol_id": phenotype["protocol_id"],
                    "sample_id": phenotype.get("sample_id"),
                    "phenotype_id": phenotype["id"],
                    "metric_id": metric["id"],
                    "metric": metric,
                    "context": {**phenotype.get("metadata", {}), **metric.get("context", {})},
                }
            )
    gaps = []
    for protocol_id in protocol_ids:
        if not any(row["protocol_id"] == protocol_id for row in observations):
            gaps.append("target_endpoint_unavailable:" + protocol_id)
    signatures = set()
    for row in observations:
        context, metric = row["context"], row["metric"]
        if metric.get("unit") in {"fraction", "percent"}:
            context = {**context, "expression_scale": metric["unit"], "normalization": "cell_fraction"}
        signature = tuple(
            str(context.get(key) or "unknown")
            for key in (
                "expression_scale",
                "normalization",
                "method_version",
                "cell_population",
                "sampling_condition",
                "cell_line",
                "stage",
            )
        )
        if (
            "unknown" in signature
            or metric.get("unit") in {None, "", "source_scale", "unknown"}
            or not metric.get("method")
        ):
            gaps.append("endpoint_scale_or_context_unknown:" + row["metric_id"])
        signatures.add((*signature, str(metric.get("method")), str(metric.get("unit"))))
    if len(signatures) > 1:
        gaps.append("cohort_requires_stratification_or_validated_adjustment")
    cohorts = []
    if not gaps:
        for protocol_id in protocol_ids:
            selected = [row for row in observations if row["protocol_id"] == protocol_id]
            if len({row["sample_id"] for row in selected}) != len(selected) or any(
                str(row["sample_id"] or "").lower() in {"", "unknown", "none", "nan"} for row in selected
            ):
                gaps.append("sample_identity_missing_or_duplicated:" + protocol_id)
                continue
            cohorts.append(
                {
                    "protocol_id": protocol_id,
                    "endpoint": endpoint,
                    "sample_mean": sum(row["metric"]["value"] for row in selected) / len(selected),
                    "n_samples": len(selected),
                    "n_identified_biological_replicates": len(
                        {
                            row["context"].get("biological_replicate_id")
                            for row in selected
                            if str(row["context"].get("biological_replicate_id") or "").lower()
                            not in {"", "unknown", "none", "nan"}
                        }
                    ),
                    "metric_refs": [row["metric_id"] for row in selected],
                }
            )
    return {
        "status": "ok" if not gaps and len(cohorts) >= 2 else "partial",
        "endpoint": endpoint,
        "observations": observations,
        "cohorts": cohorts if not gaps else [],
        "evidence_gaps": sorted(set(gaps)),
        "conclusion_type": "descriptive_difference",
        "ranking": None,
        "policy": "Endpoint-specific descriptive summaries only; no unconditional superiority or causal inference.",
    }
