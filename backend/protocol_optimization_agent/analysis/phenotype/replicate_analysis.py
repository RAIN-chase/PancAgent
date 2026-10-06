"""Sample-aggregated exploratory comparisons, never cell-level inference."""

from __future__ import annotations

import math
from typing import Any


def describe_design_metadata(adata: Any, mask: Any, config: dict[str, Any]) -> dict[str, Any]:
    columns = {
        "study": "study",
        "sample": "sample_id",
        "biological_replicate": "biological_replicate_id",
        "cell_line": "cell_line",
        "culture_batch": "culture_batch",
        "sequencing_batch": "sequencing_batch",
    }
    fields: dict[str, dict[str, Any]] = {}
    unknown: list[str] = []
    for field, default in columns.items():
        column = str(config.get(field + "_column") or default)
        if column not in adata.obs:
            fields[field] = {"column": column, "status": "unknown", "values": []}
            unknown.append(field)
            continue
        values = adata.obs.loc[mask, column]
        known = sorted(
            {
                str(value)
                for value in values.dropna()
                if str(value).strip().lower() not in {"", "unknown", "none", "nan"}
            }
        )
        missing = len(values) - sum(str(value) in known for value in values)
        fields[field] = {
            "column": column,
            "status": "known" if known and not missing else "partial_or_unknown",
            "values": known,
            "n_distinct": len(known),
            "n_cells_missing": missing,
        }
        if missing or not known:
            unknown.append(field)
    return {
        "fields": fields,
        "unknown_fields": unknown,
        "replicate_kind": config.get("replicate_kind") or "unknown",
        "policy": "Observed identifiers are not proof of biological independence.",
    }


def replicate_comparison(
    adata: Any, vector: Any, mask_a: Any, mask_b: Any, config: dict[str, Any], expression_scale: str
) -> dict[str, Any]:
    import numpy as np

    replicate_column = str(config.get("biological_replicate_column") or "")
    result: dict[str, Any] = {
        "p_value": "",
        "adjusted_p_value": "",
        "multiple_testing": "not_applicable",
        "statistical_scope": "descriptive_only",
        "inference_eligible": False,
        "method": "biological_replicate_mean_welch_v1",
        "n_replicates_a": 0,
        "n_replicates_b": 0,
        "replicate_results": [],
        "statistical_caveats": [],
    }
    issues: list[str] = result["statistical_caveats"]
    if not replicate_column or replicate_column not in adata.obs:
        issues.append("identified_biological_replicates_missing; cells_are_not_independent_samples")
        return result
    sample_column = str(config.get("sample_column") or "sample_id")
    if sample_column not in adata.obs:
        issues.append("sample_identity_missing")
        return result
    replicate_ids = adata.obs[replicate_column]
    samples = adata.obs[sample_column]
    selected = mask_a | mask_b
    if replicate_ids[selected].isna().any() or samples[selected].isna().any():
        issues.append("sample_or_replicate_identity_missing")
        return result
    ids = replicate_ids.astype(str).to_numpy()
    if any(not value.strip() or value.lower() in {"unknown", "nan", "none"} for value in ids[selected]):
        issues.append("replicate_identity_unknown")
        return result
    if any(not value.strip() or value.lower() in {"unknown", "nan", "none"} for value in samples[selected].astype(str)):
        issues.append("sample_identity_unknown")
        return result
    if config.get("replicate_kind") != "biological":
        issues.append("replicate_kind_not_confirmed_biological")
    units_a, units_b = set(ids[mask_a]), set(ids[mask_b])
    result.update(n_replicates_a=len(units_a), n_replicates_b=len(units_b))
    if units_a & units_b:
        issues.append("paired_or_overlapping_replicates_require_paired_model")
    if min(len(units_a), len(units_b)) < 2:
        issues.append("insufficient_independent_replicates")
    for sample in set(samples[selected].astype(str)):
        sample_mask = selected & (samples.astype(str).to_numpy() == sample)
        if len(set(ids[sample_mask])) != 1:
            issues.append("sample_mapped_to_multiple_biological_units")
            break
    for field in ("study", "cell_line", "culture_batch", "sequencing_batch"):
        column = str(config.get(field + "_column") or field)
        if column not in adata.obs:
            issues.append(field + "_metadata_unknown")
            continue
        values = adata.obs[column]
        if values[selected].isna().any() or any(
            str(value).strip().lower() in {"", "unknown", "nan", "none"} for value in values[selected]
        ):
            issues.append(field + "_metadata_unknown")
        sets = [set(values[mask].astype(str)) for mask in (mask_a, mask_b)]
        if field in {"study", "cell_line"} and len(sets[0] | sets[1]) != 1:
            issues.append(field + "_requires_stratified_or_adjusted_model")
        if field.endswith("batch") and not sets[0].intersection(sets[1]):
            issues.append(field + "_confounded_with_group")
    if expression_scale.lower() not in {"normalized", "normalized_counts", "cpm", "tpm", "log", "log1p"}:
        issues.append("scale_requires_validated_count_or_other_model")
    arrays = []
    for group, mask, units in (("a", mask_a, units_a), ("b", mask_b, units_b)):
        means = []
        for unit in sorted(units):
            values = vector[mask & (ids == unit)]
            if not np.isfinite(values).all():
                issues.append("nonfinite_expression")
                continue
            mean = float(values.mean())
            means.append(mean)
            result["replicate_results"].append(
                {"group": group, "biological_replicate_id": unit, "mean_expression": mean, "n_cells": int(values.size)}
            )
        arrays.append(means)
    if issues:
        result["statistical_caveats"] = sorted(set(issues))
        return result
    from scipy.stats import ttest_ind

    pvalue = float(ttest_ind(*arrays, equal_var=False).pvalue)
    result.update(
        statistical_scope="exploratory_independent_biological_replicate_means",
        aggregation="equal_weight_per_biological_replicate",
        inference_eligible=math.isfinite(pvalue),
        replicate_mean_a=float(np.mean(arrays[0])),
        replicate_mean_b=float(np.mean(arrays[1])),
        p_value=pvalue if math.isfinite(pvalue) else "",
        statistical_caveats=["exploratory_unadjusted_for_covariates; not_causal; normality_and_power_require_review"],
    )
    return result


def adjust_gene_tests(rows: list[dict[str, Any]]) -> None:
    import json

    from scipy.stats import false_discovery_control

    tested = [
        row
        for row in rows
        if isinstance(row.get("p_value"), (int, float)) and math.isfinite(row["p_value"]) and 0 <= row["p_value"] <= 1
    ]
    if tested:
        adjusted = false_discovery_control([row["p_value"] for row in tested], method="bh")
        for row, value in zip(tested, adjusted, strict=True):
            row.update(
                adjusted_p_value=float(value),
                multiple_testing="benjamini_hochberg",
                multiple_testing_family="valid_requested_genes_in_this_comparison",
                n_tests=len(tested),
            )
    for row in rows:
        for key in ("replicate_results", "statistical_caveats"):
            if key in row:
                row[key] = json.dumps(row[key], sort_keys=True, allow_nan=False)
