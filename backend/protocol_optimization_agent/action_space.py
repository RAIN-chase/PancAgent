from __future__ import annotations

from typing import Literal

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
    notes: list[str] = Field(default_factory=list)


ACTION_SPACE = [
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
        )
        for action_id in ("phenotype.beta_maturity", "phenotype.reference_similarity")
    ],
]


def action_registry() -> dict:
    return {"schema_version": "scientific-action-space-v1", "actions": [action.model_dump() for action in ACTION_SPACE]}


def get_action(action_id: str) -> ScientificAction:
    return next((action for action in ACTION_SPACE if action.id == action_id), None) or _unknown_action(action_id)


def _unknown_action(action_id: str) -> ScientificAction:
    raise ValueError(f"Unknown scientific action: {action_id}")
