"""Declared sample designs and deterministic leave-one-biological-replicate-out checks."""
from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path


def summarize_samples(payload: dict) -> dict:
    import statistics

    groups = sorted({row["group"] for row in payload["replicates"]})
    results = []
    for group in groups:
        selected = [row for row in payload["replicates"] if row["group"] == group]
        mean = statistics.fmean(row["value"] for row in selected)
        omitted = [{"replicate_id": row["replicate_id"],
                    "mean": statistics.fmean(other["value"] for other in selected if other is not row)} for row in selected]
        results.append({"group": group, "n_independent_replicates": len(selected), "mean": mean,
                        "min_after_omit": min(row["mean"] for row in omitted),
                        "max_after_omit": max(row["mean"] for row in omitted),
                        "max_abs_shift": max(abs(row["mean"] - mean) for row in omitted), "omissions": omitted})
    return {"schema_version": "sample-sensitivity-v1", "method": "leave_one_replicate_out_mean", "groups": results}


def sample_input(path: Path, request: dict) -> dict:
    import h5py

    with h5py.File(path, "r") as handle:
        obs = handle["obs"]
        count = obs[obs.attrs.get("_index", "_index")].shape[0]
        if count > 2_000_000:
            raise ValueError("Metadata precheck limit exceeded; explicitly subset/register the dataset first")
        columns = {key: request[key] for key in ("sample_column", "replicate_column", "study_column", "metric_column")}
        if request.get("group_column"):
            columns["group_column"] = request["group_column"]
        if any(name not in obs for name in columns.values()):
            raise ValueError("Requested obs column is absent; no inferred replacement")
        metric = obs[request["metric_column"]]
        if not isinstance(metric, h5py.Dataset) or metric.dtype.kind not in "fiu":
            raise ValueError("Metric must be a numeric obs column, not an expression matrix or category")
        def read(name, start, end):
            item = obs[name]
            if isinstance(item, h5py.Group):
                codes = item["codes"][start:end]
                categories = item["categories"][:]
                return [categories[code] if 0 <= code < len(categories) else None for code in codes]
            return item[start:end]
        samples, membership, missing = {}, {}, 0
        for start in range(0, count, 8192):
            values = {key: read(name, start, min(start + 8192, count)) for key, name in columns.items()}
            for i, raw in enumerate(values["metric_column"]):
                labels = {}
                for key in columns.keys() - {"metric_column"}:
                    value = values[key][i]
                    label = value.decode("utf-8") if isinstance(value, bytes) else str(value) if value is not None else ""
                    if not label.strip() or label.lower() in {"nan", "none", "unknown"} or len(label) > 200:
                        raise ValueError("Sample, study, replicate and group identifiers must be explicitly known")
                    labels[key] = label
                sample = (labels["study_column"], labels["sample_column"])
                rep = (labels["study_column"], labels["replicate_column"])
                group = labels.get("group_column", "all")
                if sample in membership and membership[sample] != (rep, group):
                    raise ValueError("A sample maps to conflicting replicates or groups")
                membership[sample] = (rep, group)
                if not math.isfinite(float(raw)):
                    missing += 1
                    continue
                total, n = samples.get(sample, (0.0, 0))
                samples[sample] = (total + float(raw), n + 1)
        reps = defaultdict(list)
        rep_groups = {}
        for sample, (total, n) in samples.items():
            rep, group = membership[sample]
            if rep in rep_groups and rep_groups[rep] != group:
                raise ValueError("A biological replicate spans groups; paired designs need a different method")
            rep_groups[rep] = group
            reps[rep].append(total / n)
        if set(samples) != set(membership):
            raise ValueError("A sample has no finite metric values")
        if len(reps) > 1000:
            raise ValueError("Replicate budget exceeded")
        rows = [{"replicate_id": str(list(rep)), "study_id": rep[0], "group": rep_groups[rep],
                 "value": math.fsum(values) / len(values), "n_samples": len(values)} for rep, values in sorted(reps.items())]
        if not rows or any(sum(row["group"] == group for row in rows) < 2 for group in rep_groups.values()):
            raise ValueError("At least two declared biological replicates per group are required; cell counts are not replicates")
        if any(not math.isfinite(row["value"]) for row in rows):
            raise ValueError("Nonfinite aggregate")
        return {"replicates": rows, "n_cells": count, "n_missing_metric": missing, "n_samples": len(samples),
                "aggregation": "cell mean within sample; equal sample means within biological replicate",
                "scale": request["scale"], "endpoint": request["endpoint"], "seed": request["seed"]}


def validate_sensitivity(actual: dict, expected: dict) -> None:
    def compare(value, reference):
        if isinstance(reference, dict):
            if not isinstance(value, dict) or set(value) != set(reference):
                raise ValueError("Output schema or fields disagree with the approved method")
            for key in reference:
                compare(value[key], reference[key])
        elif isinstance(reference, list):
            if not isinstance(value, list) or len(value) != len(reference):
                raise ValueError("Output cardinality mismatch")
            for item, target in zip(value, reference, strict=True):
                compare(item, target)
        elif isinstance(reference, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not math.isclose(value, reference, rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError("Numeric output disagrees with trusted recomputation")
        elif type(value) is not type(reference) or value != reference:
            raise ValueError("Output identity, count or method mismatch")
    compare(actual, expected)
