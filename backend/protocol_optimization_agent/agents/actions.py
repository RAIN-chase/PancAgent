from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from protocol_optimization_agent.runtime.context import RunContext, append_tool_call_log, evidence_response_envelope


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
        id="data.catalog", agent="Orchestrator", inputs=[], required_inputs=[],
        routing_terms=["有哪些数据", "有什么数据", "哪些数据可用", "可用数据", "可用的数据", "可访问的数据", "数据集清单", "数据目录", "数据清单",
                       "数据有哪些", "数据集有哪些", "有哪些h5ad", "有哪些 h5ad", "列出数据", "列出所有数据",
                       "available datasets", "available data", "list datasets", "what datasets", "which datasets", "what data is available"],
        routing_priority=15,
        execution_preconditions=["server_bound_user_and_project_context"], scientific_preconditions=[],
        outputs=["configured", "catalog_version", "collections", "datasets"], implementation="dataset_catalog",
        notes=["Read-only listing of authorized registered datasets, versions and availability; no directory or dataset ID required.",
               "Query this before requesting a manifest for dataset discovery. Not configured or empty is an informative result.",
               "File readiness is not scientific QC, and no arbitrary server filesystem scan is permitted."],
    ),
    ScientificAction(
        id="protocol.assess", agent="PA", inputs=["protocol_ids"], required_inputs=[],
        execution_preconditions=["authorized_PA_output_snapshot"], scientific_preconditions=["source_details_checked"],
        outputs=["protocol_assessments", "evidence_gaps", "matrix_release_gate"], implementation="assess_protocols",
    ),
    ScientificAction(
        id="protocol.steps", agent="PA", inputs=["protocol_id", "stage_id"], required_inputs=["protocol_id"],
        execution_preconditions=["persisted_semantic_step_annotations"],
        scientific_preconditions=["source_support_and_parameter_scope_checked"],
        outputs=["semantic_steps", "evidence_refs"], implementation="ProtocolQueryEngine.get_protocol_steps",
        notes=["Retrieve original operations, states, conditions and scoped parameters. Interpretations are not new facts."],
    ),
    ScientificAction(
        id="data.assess", agent="DA", inputs=["request", "h5ad_manifest_rows", "h5ad_config"], required_inputs=[],
        routing_terms=["h5ad", "anndata", "scanpy", "single-cell", "scrna", "snrna", "基因", "轨迹"],
        execution_preconditions=["authorized_h5ad_or_DA_snapshot"], scientific_preconditions=["task_validation_and_replicate_structure"],
        outputs=["validation", "dataset_summaries", "statistical_applicability"], implementation="data_analysis_agent", operation="summary",
        notes=["Server catalog datasets can be selected by request.dataset_id and request.dataset_version; do not invent local file paths."],
    ),
    ScientificAction(
        id="protocol.phenotype_compare", agent="DA", inputs=["protocol_ids", "endpoint"],
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
        version="1.2.0",
        agent="PA",
        inputs=["protocol_ids", "protocol_id_a", "protocol_id_b"],
        required_inputs=[],
        execution_preconditions=["compiled_protocol_matrix_available"],
        scientific_preconditions=["cell_line_and_stage_metadata_checked", "descriptive_not_causal"],
        outputs=["feature_diff", "factor_diff", "stage_diff", "event_step_diff", "evidence_refs", "scientific_preconditions"],
        implementation="protocol_analysis_agent",
        notes=["Compare 2 to 100 distinct protocol_ids; a legacy explicit pair is also accepted. Never silently select only two from a larger request."],
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
                else ["composition", "cell type", "proportion", "细胞组成", "细胞类型", "比例"] if operation == "cell_type_composition"
                else []
            ),
        )
        for action_id, operation, checks in (
            ("phenotype.cell_composition", "cell_type_composition", ["annotation_quality_checked"]),
            ("phenotype.gene_expression", "gene_expression", ["expression_scale_and_cell_selection_known"]),
            ("phenotype.marker_score", "marker_score", ["marker_gene_coverage_checked"]),
            ("phenotype.gene_program_score", "gene_program_score", ["program_gene_coverage_checked", "not_calibrated_maturity"]),
            (
                "phenotype.differential_expression",
                "differential_expression",
                ["groups_known", "replicate_and_batch_confounding_reported"],
            ),
        )
    ],
    ScientificAction(
        id="phenotype.beta_maturity", agent="DA", version="2.0.0",
        inputs=["question", "request", "h5ad_manifest_rows", "h5ad_config"], required_inputs=["h5ad_manifest_rows"],
        execution_preconditions=["six_versioned_program_csvs", "authorized_raw_counts_and_annotation"],
        scientific_preconditions=["program_gene_coverage", "descriptive_not_probability", "dataset_dependent_controls"],
        outputs=["numeric_results", "validation", "phenotype_ir", "evidence_refs"],
        implementation="data_analysis_agent", operation="maturity", routing_terms=["maturity", "成熟度"],
        routing_priority=40,
        notes=["Scores Alpha, Beta and Delta via request.cell_type or maturity.cell_types; historical action ID retained."],
    ),
    ScientificAction(
        id="experiment.propose",
        version="2.0.0",
        routing_terms=["优化", "改进", "推荐", "建议", "propose", "improve", "optimize", "optimization", "recommend", "best protocol"],
        routing_priority=100,
        agent="PO",
        inputs=["question", "objective", "new_protocol_features"],
        required_inputs=[],
        execution_preconditions=["validated_joinable_PA_DA_outputs"],
        scientific_preconditions=[
            "cohort_comparability",
            "target_sample_protocol_versions_verified_for_approval",
            "evidence_support",
            "previous_tests_checked",
            "explicit_decision_assessments",
        ],
        outputs=["hypotheses", "next_experiment_candidates", "joint_evidence", "endpoint_associations",
                 "mechanism_evidence", "similar_protocol_reference", "critiques", "ranking", "researcher_review_required"],
        implementation="protocol_optimizing_agent",
        notes=["Raw endpoints are primary; associations and mechanism consistency are not intervention effects.",
               "Literature/model budgets default to zero. Competing hypotheses may remain unranked."],
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
    ScientificAction(
        id="phenotype.reference_similarity", agent="DA", availability="partial",
        inputs=["h5ad_manifest_rows", "h5ad_config"], required_inputs=["h5ad_manifest_rows"],
        execution_preconditions=["validated_reference_mapping_engine_required"],
        scientific_preconditions=["reference_version", "gene_overlap", "matched_lineage_and_stage"],
        outputs=["phenotype_metric", "method", "version", "confidence_caveats"],
        implementation="data_analysis_agent", operation="reference_similarity",
        routing_terms=["similarity", "reference", "相似度", "参考图谱"], routing_priority=10,
    ),
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


def required_action_inputs(action: ScientificAction, arguments: dict[str, Any]) -> list[str]:
    if action.id == "protocol.compare":
        return ["protocol_ids"] if "protocol_ids" in arguments else ["protocol_id_a", "protocol_id_b"]
    return action.required_inputs


def validate_action_arguments(action_id: str, arguments: dict[str, Any], *, pending_inputs: set[str] | None = None) -> ScientificAction:
    action = get_action(action_id)
    if action.availability != "available":
        raise ValueError(f"Action is not available: {action_id} ({action.availability})")
    pending = pending_inputs or set()
    if not isinstance(arguments, dict) or (set(arguments) | pending) - set(action.inputs):
        raise ValueError("Action arguments contain undeclared fields.")
    arguments = {key: value for key, value in arguments.items() if key not in pending or value not in (None, "", [], {})}
    required = required_action_inputs(action, arguments)
    if any(arguments.get(key) in (None, "", [], {}) for key in required if key not in pending):
        raise ValueError(f"Required action inputs: {required}")
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
    if action_id == "protocol.compare" and not pending:
        ids = arguments.get("protocol_ids")
        if ids is not None:
            if "protocol_id_a" in arguments or "protocol_id_b" in arguments:
                raise ValueError("Use protocol_ids or a pair, not both")
            if not 2 <= len(set(ids)) <= 100:
                raise ValueError("Comparison requires 2 to 100 distinct protocol IDs")
        elif not arguments.get("protocol_id_a") or not arguments.get("protocol_id_b") or arguments["protocol_id_a"] == arguments["protocol_id_b"]:
            raise ValueError("Comparison requires protocol_ids or two distinct protocol IDs")
    if action_id == "protocol.phenotype_compare" and "protocol_ids" not in pending and len(set(arguments["protocol_ids"])) < 2:
        raise ValueError("Endpoint comparison requires at least two distinct protocols")
    if "edits" in arguments and any(not isinstance(edit, dict) or not edit.get("candidate_feature") for edit in arguments["edits"]):
        raise ValueError("Each edit must identify its candidate_feature")
    if "limit" in arguments and (isinstance(arguments["limit"], bool) or not isinstance(arguments["limit"], int) or not 1 <= arguments["limit"] <= 100):
        raise ValueError("limit must be between 1 and 100")
    return action


def execute_action(action_id: str, arguments: dict[str, Any], context: RunContext, *,
                   protocol_output_dir: str, data_analysis_output_dir: str,
                   optimization_output_dir: str, max_features: int) -> dict[str, Any]:
    """Validate once and delegate to the domain owning the registered action."""
    action = get_action(action_id)
    if action.availability != "available":
        return evidence_response_envelope(context=context, subagent=action.agent, status=action.availability,
            question=str(arguments.get("question", "")), selected_operation=action_id, result_preview={},
            limitations=["This registered capability has no available execution implementation."],
            validation={"available": False}, extra={"action_id": action_id, "executed": False})
    validate_action_arguments(action_id, arguments)
    if action_id == "data.catalog":
        from protocol_optimization_agent.runtime.data_catalog import dataset_catalog
        try:
            catalog = dataset_catalog(context.user_id, context.project_id)
        except (OSError, ValueError, TypeError, KeyError):
            append_tool_call_log(context, subagent="orchestrator", operation=action.implementation, status="analysis_failed",
                                 question="List authorized project datasets", output_files={})
            return evidence_response_envelope(context=context, subagent=action.agent, status="analysis_failed",
                question="当前项目数据目录", selected_operation=action_id, result_preview={}, used_tools=[action.implementation],
                limitations=["数据目录读取或格式校验失败，请由管理员检查服务器目录配置；不需要用户提供文件路径。"])
        catalog.setdefault("catalog_version", "unconfigured" if not catalog["configured"] else "unknown")
        append_tool_call_log(context, subagent="orchestrator", operation=action.implementation, status="ok",
                             question="List authorized project datasets", output_files={})
        return evidence_response_envelope(context=context, subagent=action.agent, status="ok",
            question="当前项目数据目录", selected_operation=action_id, result_preview=catalog, used_tools=[action.implementation],
            limitations=["仅列出当前用户和项目可访问的已登记数据；不扫描任意服务器目录。",
                         "文件可读取不代表注释、表达尺度、重复结构或统计方法已通过科学验证。"])
    if action.agent == "PA":
        from protocol_optimization_agent.agents.protocol_analysis import execute_protocol_action
        return execute_protocol_action(action, arguments, context, protocol_output_dir, max_features)
    if action.agent == "DA":
        from protocol_optimization_agent.agents.data_analysis import execute_data_action
        return execute_data_action(action, arguments, context, data_analysis_output_dir)
    if action.agent == "PO":
        from protocol_optimization_agent.agents.protocol_optimizing import execute_optimization_action
        return execute_optimization_action(action, arguments, context, protocol_output_dir,
                                           data_analysis_output_dir, optimization_output_dir)
    if action_id == "scientific.state":
        from protocol_optimization_agent.science.state import ScientificWorldModel
        store = ScientificWorldModel(context, context.project_id)
        snapshot = store.snapshot(arguments.get("limit", 20))
        snapshot["conflicts"] = [record for kind in ("claim", "hypothesis") for record in snapshot["records"].get(kind, [])
                                 if record.get("evidence_against") or record.get("opposing_evidence_refs")]
        append_tool_call_log(context, subagent="orchestrator", operation="inspect_scientific_state",
                             status="ok", question="Inspect scientific state", output_files={"scientific_state": str(store.path)})
        return evidence_response_envelope(context=context, subagent=action.agent, status="ok", question="Scientific state",
            selected_operation=action_id, result_preview=snapshot, used_tools=[action.implementation])
    raise ValueError(f"No domain executor for registered action: {action_id}")
