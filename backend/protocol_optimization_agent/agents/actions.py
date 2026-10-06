from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ScientificAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    agent: str
    version: str = "1.0.0"
    availability: Literal["available", "partial", "deferred"] = "available"
    inputs: list[str]
    required_inputs: list[str]
    execution_preconditions: list[str]
    scientific_preconditions: list[str]
    outputs: list[str]
    numeric_policy: str = "deterministic_tools_only"
    implementation: str
    operation: str = ""
    routing_terms: list[str] = Field(default_factory=list)
    routing_priority: int = 0
    notes: list[str] = Field(default_factory=list)


ACTION_SPACE = [
    ScientificAction(
        id="protocol.assess", agent="PA", inputs=["protocol_ids"], required_inputs=[],
        execution_preconditions=["authorized_PA_output_snapshot"], scientific_preconditions=["source_details_checked"],
        outputs=["protocol_assessments", "evidence_gaps", "matrix_release_gate"], implementation="assess_protocols",
    ),
    ScientificAction(
        id="data.assess", agent="DA", inputs=["h5ad_manifest_rows", "h5ad_config"], required_inputs=[],
        routing_terms=["h5ad", "anndata", "scanpy", "single-cell", "scrna", "snrna", "基因", "轨迹"],
        execution_preconditions=["authorized_h5ad_or_DA_snapshot"], scientific_preconditions=["task_validation_and_replicate_structure"],
        outputs=["validation", "dataset_summaries", "statistical_applicability"], implementation="data_analysis_agent", operation="summary",
    ),
    ScientificAction(
        id="protocol.phenotype_compare", agent="Orchestrator", inputs=["protocol_ids", "endpoint"],
        required_inputs=["protocol_ids", "endpoint"], execution_preconditions=["phenotype_ir_snapshot"],
        scientific_preconditions=["endpoint_scale_and_context_checked"],
        outputs=["cohorts", "observations", "evidence_gaps"], implementation="compare_endpoint",
    ),
    ScientificAction(
        id="hypothesis.generate", agent="PO", inputs=["edits", "objective"], required_inputs=["edits", "objective"],
        execution_preconditions=["explicit_intervention_specs"], scientific_preconditions=["unknown_conditions_remain_unknown"],
        outputs=["hypotheses", "candidates", "ranking_status"], implementation="design_experiments",
        notes=["Hypothesis generation does not approve or execute experiments and may remain unranked."],
    ),
    ScientificAction(
        id="protocol.compare",
        agent="PA",
        inputs=["protocol_id_a", "protocol_id_b"],
        required_inputs=["protocol_id_a", "protocol_id_b"],
        execution_preconditions=["compiled_protocol_matrix_available"],
        scientific_preconditions=["cell_line_and_stage_metadata_checked", "descriptive_not_causal"],
        outputs=["feature_diff", "factor_diff", "stage_diff", "evidence_refs", "scientific_preconditions"],
        implementation="protocol_analysis_agent",
    ),
    ScientificAction(
        id="protocol.lookup",
        agent="PA",
        inputs=["question"],
        required_inputs=["question"],
        execution_preconditions=["compiled_protocol_matrix_available"],
        scientific_preconditions=["source_identity_resolved"],
        outputs=["protocol_candidates", "evidence_refs"],
        implementation="protocol_analysis_agent",
    ),
    *[
        ScientificAction(
            id=action_id,
            agent="DA",
            version="2.0.0" if operation == "differential_expression" else "1.0.0",
            inputs=["question", "request", "h5ad_manifest_rows", "h5ad_config"],
            required_inputs=[],
            execution_preconditions=[
                "readable_authorized_h5ad_or_matching_cached_results",
                "required_metadata_available",
            ],
            scientific_preconditions=checks,
            outputs=["numeric_results", "validation", "phenotype_ir", "evidence_refs"],
            implementation="data_analysis_agent",
            operation=operation,
            routing_priority=30 if operation == "differential_expression" else 20,
            routing_terms=(
                ["de", "differential", "差异表达", "差异基因"] if operation == "differential_expression"
                else ["gene expression", "expression", "marker", "表达量", "基因表达", "标记基因"] if operation == "gene_expression"
                else ["composition", "cell type", "proportion", "细胞组成", "细胞类型", "比例"]
            ),
        )
        for action_id, operation, checks in (
            ("phenotype.cell_composition", "cell_type_composition", ["annotation_quality_checked"]),
            ("phenotype.gene_expression", "gene_expression", ["expression_scale_and_cell_selection_known"]),
            (
                "phenotype.differential_expression",
                "differential_expression",
                ["groups_known", "replicate_and_batch_confounding_reported"],
            ),
        )
    ],
    ScientificAction(
        id="experiment.propose",
        routing_terms=["优化", "改进", "推荐", "建议", "propose", "improve", "optimize", "optimization", "recommend", "best protocol"],
        routing_priority=100,
        agent="PO",
        inputs=["question", "objective", "new_protocol_features"],
        required_inputs=[],
        execution_preconditions=["validated_joinable_PA_DA_outputs"],
        scientific_preconditions=[
            "cohort_comparability",
            "evidence_support",
            "previous_tests_checked",
            "explicit_decision_assessments",
        ],
        outputs=["hypotheses", "next_experiment_candidates", "critiques", "ranking", "researcher_review_required"],
        implementation="protocol_optimizing_agent",
    ),
    ScientificAction(
        id="scientific.state",
        routing_terms=["world model", "scientific state", "科研状态", "查看假设", "实验历史", "证据冲突"],
        routing_priority=90,
        agent="Orchestrator",
        inputs=["limit"],
        required_inputs=[],
        execution_preconditions=["server_bound_user_and_project_context"],
        scientific_preconditions=[],
        outputs=["versioned_world_model", "hypotheses", "conflicts", "history"],
        implementation="inspect_scientific_state",
    ),
    ScientificAction(
        id="protocol.extract",
        agent="PA",
        availability="deferred",
        inputs=["source_artifact_id"],
        required_inputs=["source_artifact_id"],
        execution_preconditions=["Protocol_extraction_workflow_registered"],
        scientific_preconditions=["source_evidence_preserved"],
        outputs=["structured_protocol_artifacts"],
        implementation="Protocol_extraction",
        notes=["Protocol IR design and workflow integration are deferred by project owner."],
    ),
    *[
        ScientificAction(
            id=action_id,
            agent="DA",
            availability="partial",
            inputs=["h5ad_manifest_rows", "h5ad_config"],
            required_inputs=["h5ad_manifest_rows"],
            execution_preconditions=["validated_scoring_or_reference_mapping_engine_required"],
            scientific_preconditions=["reference_version", "gene_overlap", "matched_lineage_and_stage"],
            outputs=["phenotype_metric", "method", "version", "confidence_caveats"],
            implementation="data_analysis_agent",
            operation="maturity" if action_id == "phenotype.beta_maturity" else "reference_similarity",
            routing_terms=["maturity", "成熟度"] if action_id == "phenotype.beta_maturity" else ["similarity", "reference", "相似度", "参考图谱"],
            routing_priority=10,
        )
        for action_id in ("phenotype.beta_maturity", "phenotype.reference_similarity")
    ],
]


def action_registry() -> dict:
    return {"schema_version": "scientific-action-space-v1", "actions": [action.model_dump() for action in ACTION_SPACE]}


def select_action(question: str) -> ScientificAction:
    """Offline routing uses registry metadata, including unavailable capabilities."""
    for action in sorted(ACTION_SPACE, key=lambda item: item.routing_priority, reverse=True):
        for term in action.routing_terms:
            pattern = r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])" if term.isascii() else re.escape(term)
            if re.search(pattern, question, re.IGNORECASE):
                return action
    return get_action("protocol.lookup")


def get_action(action_id: str) -> ScientificAction:
    return next((action for action in ACTION_SPACE if action.id == action_id), None) or _unknown_action(action_id)


def _unknown_action(action_id: str) -> ScientificAction:
    raise ValueError(f"Unknown scientific action: {action_id}")


def validate_action_arguments(action_id: str, arguments: dict[str, Any]) -> ScientificAction:
    action = get_action(action_id)
    if action.availability != "available":
        raise ValueError(f"Action is not available: {action_id} ({action.availability})")
    if not isinstance(arguments, dict) or set(arguments) - set(action.inputs):
        raise ValueError("Action arguments contain undeclared fields.")
    if any(arguments.get(key) in (None, "", [], {}) for key in action.required_inputs):
        raise ValueError(f"Required action inputs: {action.required_inputs}")
    for key in ("question", "endpoint", "protocol_id_a", "protocol_id_b"):
        if key in arguments and (not isinstance(arguments[key], str) or not arguments[key].strip()):
            raise ValueError(f"{key} must be a nonempty string")
    for key in ("request", "h5ad_config", "objective", "new_protocol_features"):
        if key in arguments and not isinstance(arguments[key], dict):
            raise ValueError(f"{key} must be an object")
    for key in ("protocol_ids", "edits", "h5ad_manifest_rows"):
        if key in arguments and not isinstance(arguments[key], list):
            raise ValueError(f"{key} must be a list")
    if "protocol_ids" in arguments and any(not isinstance(value, str) or not value for value in arguments["protocol_ids"]):
        raise ValueError("protocol_ids must contain nonempty strings")
    if action_id == "protocol.phenotype_compare" and len(set(arguments["protocol_ids"])) < 2:
        raise ValueError("Endpoint comparison requires at least two distinct protocols")
    if "edits" in arguments and any(not isinstance(edit, dict) or not edit.get("candidate_feature") for edit in arguments["edits"]):
        raise ValueError("Each edit must identify its candidate_feature")
    if "limit" in arguments and (isinstance(arguments["limit"], bool) or not isinstance(arguments["limit"], int) or not 1 <= arguments["limit"] <= 100):
        raise ValueError("limit must be between 1 and 100")
    return action
