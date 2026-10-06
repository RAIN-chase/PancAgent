from __future__ import annotations

from copy import deepcopy
from typing import Any

from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import AnalysisDataset, AnalysisResult


def build_analysis_dataset(protocol_rows: list[dict[str, Any]], phenotype_ir: dict[str, Any],
                           admissions: list[dict[str, Any]]) -> AnalysisDataset:
    protocols = {p["protocol_id"]: deepcopy(p) for p in protocol_rows}
    phenotypes = {p["id"]: deepcopy(p) for p in phenotype_ir.get("phenotypes", [])}
    experiments = []
    for admission in admissions:
        for actual in (admission["executed_protocol"], admission.get("control_protocol")):
            if actual is None:
                raise ValueError("Feedback admission lacks a versioned control protocol snapshot.")
            row = {**actual["features"], **actual["metadata"], "protocol_id": actual["protocol_id"]}
            if row["protocol_id"] in protocols:
                original = protocols[row["protocol_id"]]
                if any(str(original.get(k, "")) != str(v) for k, v in row.items()):
                    raise ValueError("Executed/control protocol conflicts with source features; assign a distinct protocol version/id.")
            else:
                protocols[row["protocol_id"]] = row
        for phenotype in admission["phenotypes"]:
            old = phenotypes.get(phenotype["id"])
            if old and old["metrics"] != phenotype["metrics"]:
                raise ValueError("Admitted sample snapshot conflicts with current DA values; resolve the dataset revision.")
            phenotypes[phenotype["id"]] = deepcopy(phenotype)
        experiments.append(admission["experiment_id"])
    payload = {"protocol_rows": sorted(protocols.values(), key=lambda p: p["protocol_id"]),
               "phenotypes": sorted(phenotypes.values(), key=lambda p: p["id"]),
               "admitted_experiment_ids": sorted(experiments),
               "base_source_fingerprint": stable_id("base_source", {"protocols": protocol_rows, "phenotype_ir": phenotype_ir})}
    fingerprint = stable_id("analysis_input", payload)
    return AnalysisDataset(id=stable_id("analysis_dataset", payload), source_fingerprint=fingerprint, **payload)


def build_analysis_result(dataset: dict[str, Any], objective: dict[str, Any],
                          scores: list[dict[str, Any]], associations: list[dict[str, Any]],
                          previous: dict[str, Any] | None, computation_fingerprint: str = "") -> AnalysisResult:
    previous = previous or {}
    old_scores = {p["protocol_id"]: p.get("objective_weighted_score") for p in previous.get("protocol_scores", [])}
    changes = [{"protocol_id": p["protocol_id"], "previous_score": old_scores.get(p["protocol_id"]),
                "current_score": p.get("objective_weighted_score"),
                "delta": p["objective_weighted_score"] - old_scores[p["protocol_id"]]
                if old_scores.get(p["protocol_id"]) is not None and p.get("objective_weighted_score") is not None else None}
               for p in scores]
    payload = {"analysis_dataset_id": dataset["id"], "base_source_fingerprint": dataset["base_source_fingerprint"],
               "objective": objective, "protocol_scores": scores, "associations": associations,
               "admitted_experiment_ids": dataset["admitted_experiment_ids"], "computation_fingerprint": computation_fingerprint}
    fingerprint = stable_id("result_content", payload)
    if fingerprint == previous.get("result_fingerprint"):
        return AnalysisResult.model_validate(previous)
    return AnalysisResult(id=stable_id("analysis_result", [fingerprint, previous.get("id", "")]),
        result_fingerprint=fingerprint, previous_result_id=previous.get("id", ""), change_summary={
        "base_source_changed": bool(previous) and previous.get("base_source_fingerprint") != dataset["base_source_fingerprint"],
        "objective_changed": bool(previous) and previous.get("objective") != objective,
        "admitted_experiments_added": sorted(set(dataset["admitted_experiment_ids"]) - set(previous.get("admitted_experiment_ids", []))),
        "protocol_score_changes": changes,
        "attribution": "input/version changes, not causal effects; ordinal design assessments still require researcher review",
    }, **payload)
