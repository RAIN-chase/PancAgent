from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict


class ProtocolOptimizingState(TypedDict, total=False):
    protocol_output_dir: str
    data_analysis_output_dir: str
    objective: dict[str, Any]
    new_protocol_features: dict[str, Any]
    protocol_feature_rows: list[dict[str, Any]]
    stage_event_rows: list[dict[str, Any]]
    feature_provenance: dict[str, Any]
    data_tables: dict[str, list[dict[str, Any]]]
    input_gate_report: dict[str, Any]
    phenotype_targets: list[dict[str, Any]]
    protocol_phenotype_rows: list[dict[str, Any]]
    association_rows: list[dict[str, Any]]
    candidate_feature_rows: list[dict[str, Any]]
    protocol_edits: list[dict[str, Any]]
    reference_report: dict[str, Any]
    protocol_proposal: dict[str, Any]
    risk_report: dict[str, Any]
    validation_plan: dict[str, Any]
    scientific_context: dict[str, Any]
    scientific_preconditions: dict[str, Any]
    next_experiments: dict[str, Any]
    analysis_dataset: dict[str, Any]
    endpoint_preconditions: dict[str, dict[str, Any]]
    joint_evidence: dict[str, Any]
    endpoint_rows: list[dict[str, Any]]
    step_annotations: list[dict[str, Any]]
    mechanism_report: dict[str, Any]
    literature_records: list[dict[str, Any]]
    outputs: dict[str, Path]
    step_log: list[dict[str, Any]]
