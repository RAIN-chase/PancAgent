from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from protocol_optimization_agent.analysis.optimization._shared import _input_findings, _read_protocol_event_evidence
from protocol_optimization_agent.analysis.optimization.assessment import (
    assess_similar_protocol_reference,
    build_phenotype_targets,
    join_protocol_phenotypes,
)
from protocol_optimization_agent.analysis.optimization.association import analyze_associations
from protocol_optimization_agent.analysis.optimization.comparability import scientific_preconditions
from protocol_optimization_agent.analysis.optimization.contracts import ProtocolOptimizingState
from protocol_optimization_agent.analysis.optimization.evidence import (
    build_joint_evidence,
    comparison_phenotypes,
    endpoint_preconditions,
    summarize_endpoints,
)
from protocol_optimization_agent.analysis.optimization.experiment_design import design_experiments
from protocol_optimization_agent.analysis.optimization.proposals import (
    _render_protocol_markdown,
    _render_validation_markdown,
    generate_protocol_proposal,
    rank_mechanistic_evidence,
)
from protocol_optimization_agent.analysis.optimization.readiness import build_optimization_orchestration_plan
from protocol_optimization_agent.analysis.phenotype.representation import build_phenotype_ir
from protocol_optimization_agent.runtime.access_control import forbid_client_paths
from protocol_optimization_agent.runtime.archive import (
    audited_agent_run,
    audited_operation,
    audited_tool,
    current_archive,
)
from protocol_optimization_agent.runtime.context import (
    RunContext,
    content_fingerprint,
    evidence_response_envelope,
    record_agent_call,
    resolve_domain_output_dir,
)
from protocol_optimization_agent.runtime.io import read_csv_dicts, read_json, read_jsonl, write_csv_dicts, write_json
from protocol_optimization_agent.runtime.literature import search_pubmed
from protocol_optimization_agent.runtime.llm_client import LLMClient, OfflineLLMClient, build_json_llm_client_from_env
from protocol_optimization_agent.runtime.paths import optimization_code_paths
from protocol_optimization_agent.science.dataset import build_analysis_dataset, build_analysis_result
from protocol_optimization_agent.science.ingestion import (
    ingest_experiment_proposals,
    ingest_phenotype_outputs,
    ingest_protocol_outputs,
)
from protocol_optimization_agent.science.state import ScientificWorldModel


class ProtocolOptimizingAgent:
    """Generate evidence-labeled protocol optimization hypotheses.

    This agent deliberately treats protocol-to-phenotype links as small-sample
    associations unless external experimental validation is supplied.
    """

    def __init__(
        self,
        output_dir: Path,
        objective: dict[str, Any] | None = None,
        max_candidate_features: int = 50,
        llm_client: LLMClient | None = None,
        literature_search: Callable[[str, int], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.objective = objective or {"primary": "increase_beta_like_maturity"}
        self.max_candidate_features = max_candidate_features
        self.llm_client = llm_client or (build_json_llm_client_from_env()
            if int(self.objective.get("mechanism", {}).get("max_llm_calls", 0)) > 0 else OfflineLLMClient())
        self.literature_search = literature_search

    @audited_agent_run
    def run(
        self,
        protocol_output_dir: Path | str,
        data_analysis_output_dir: Path | str,
        objective: dict[str, Any] | None = None,
        new_protocol_features: dict[str, Any] | None = None,
        scientific_context: dict[str, Any] | None = None,
    ) -> dict[str, Path]:
        initial_state: ProtocolOptimizingState = {
            "protocol_output_dir": str(protocol_output_dir),
            "data_analysis_output_dir": str(data_analysis_output_dir),
            "objective": objective or self.objective,
            "new_protocol_features": new_protocol_features or {},
            "scientific_context": scientific_context or {},
            "step_log": [],
        }
        final_state = self.build_langgraph().invoke(initial_state)
        return final_state["outputs"]

    def build_langgraph(self, compile_graph: bool = True) -> Any:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError("LangGraph is required to run ProtocolOptimizingAgent.") from exc

        graph = StateGraph(ProtocolOptimizingState)
        graph.add_node("integration_input_gate", self._node_integration_input_gate)
        graph.add_node("phenotype_target_builder", self._node_phenotype_target_builder)
        graph.add_node("protocol_phenotype_joiner", self._node_protocol_phenotype_joiner)
        graph.add_node("protocol_phenotype_association_analyzer", self._node_protocol_phenotype_association_analyzer)
        graph.add_node("mechanistic_evidence_ranker", self._node_mechanistic_evidence_ranker)
        graph.add_node("similar_protocol_reference", self._node_similar_protocol_reference)
        graph.add_node("optimized_protocol_generator", self._node_optimized_protocol_generator)
        graph.add_node("experiment_design", self._node_experiment_design)
        graph.add_node("optimization_audit", self._node_optimization_audit)
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "integration_input_gate")
        graph.add_edge("integration_input_gate", "phenotype_target_builder")
        graph.add_edge("phenotype_target_builder", "protocol_phenotype_joiner")
        graph.add_edge("protocol_phenotype_joiner", "protocol_phenotype_association_analyzer")
        graph.add_edge("protocol_phenotype_association_analyzer", "mechanistic_evidence_ranker")
        graph.add_edge("mechanistic_evidence_ranker", "similar_protocol_reference")
        graph.add_edge("similar_protocol_reference", "optimized_protocol_generator")
        graph.add_edge("optimized_protocol_generator", "experiment_design")
        graph.add_edge("experiment_design", "optimization_audit")
        graph.add_edge("optimization_audit", "export_results")
        graph.add_edge("export_results", END)
        return graph.compile() if compile_graph else graph

    def _node_integration_input_gate(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        protocol_dir = Path(state["protocol_output_dir"])
        data_dir = Path(state["data_analysis_output_dir"])
        protocol_feature_rows = read_csv_dicts(protocol_dir / "protocol_feature_matrix.csv")
        stage_event_rows = _read_protocol_event_evidence(protocol_dir)
        feature_provenance = read_json(protocol_dir / "protocol_feature_provenance.json")
        data_tables = {
            "maturity_scores": read_csv_dicts(data_dir / "maturity_scores.csv"),
            "similarity_scores": read_csv_dicts(data_dir / "similarity_scores.csv"),
            "cell_type_composition": read_csv_dicts(data_dir / "cell_type_composition.csv"),
            "marker_scores": read_csv_dicts(data_dir / "marker_scores.csv"),
            "gene_program_scores": read_csv_dicts(data_dir / "gene_program_scores.csv"),
            "regulatory_program_scores": read_csv_dicts(data_dir / "regulatory_program_scores.csv"),
            "trajectory_scores": read_csv_dicts(data_dir / "trajectory_scores.csv"),
            "data_analysis_protocol_summary": read_csv_dicts(data_dir / "data_analysis_protocol_summary.csv"),
            "h5ad_summary_by_protocol": read_jsonl(data_dir / "h5ad_summary_by_protocol.jsonl"),
        }
        protocol_ids = {str(row.get("protocol_id") or "") for row in protocol_feature_rows if row.get("protocol_id")}
        data_protocol_ids = {
            str(row.get("protocol_id") or "") for rows in data_tables.values() for row in rows if row.get("protocol_id")
        }
        report = {
            "schema_version": "protocol-optimization-input-gate-v1",
            "summary": {
                "n_protocol_feature_rows": len(protocol_feature_rows),
                "n_stage_event_rows": len(stage_event_rows),
                "n_protocols_with_features": len(protocol_ids),
                "n_protocols_with_data": len(data_protocol_ids),
                "n_protocols_joinable": len(protocol_ids.intersection(data_protocol_ids)),
                "has_new_protocol_features": bool(state.get("new_protocol_features")),
            },
            "findings": _input_findings(protocol_feature_rows, data_tables, protocol_ids, data_protocol_ids),
        }
        phenotype_ir = read_json(data_dir / "phenotype_ir.json") or build_phenotype_ir(
            data_tables,
            data_tables["h5ad_summary_by_protocol"],
            read_json(data_dir / "data_analysis_validation.json"),
            str(data_dir),
        )
        dataset = build_analysis_dataset(
            protocol_feature_rows, phenotype_ir, state.get("scientific_context", {}).get("admitted_feedback", [])
        )
        protocol_feature_rows = dataset.protocol_rows
        comparison = state.get("objective", {}).get("comparison", {})
        preconditions = scientific_preconditions(
            protocol_feature_rows, comparison_phenotypes([p.model_dump(mode="json") for p in dataset.phenotypes],
                comparison.get("endpoints", [])), comparison=comparison
        )
        if not comparison.get("endpoints"):
            preconditions["checks"].append({"name": "explicit_optimization_endpoints", "status": "unknown"})
            preconditions.update({"scientific_ready": False, "status": "human_review_required"})
        interpretation = read_json(data_dir / "phenotype_interpretation.json")
        if interpretation.get("status") == "human_review_required":
            preconditions["checks"].append({"name": "interpretation_consensus", "status": "human_review_required"})
            preconditions.update({"status": "human_review_required", "scientific_ready": False})
        report["execution_preconditions"] = {
            "joinable": bool(protocol_ids & data_protocol_ids),
            "has_protocol_features": bool(protocol_feature_rows),
        }
        report["scientific_preconditions"] = preconditions
        endpoint_gates = endpoint_preconditions(protocol_feature_rows, dataset.model_dump(mode="json")["phenotypes"], comparison)
        evidence_state: ProtocolOptimizingState = {**state, "analysis_dataset": dataset.model_dump(mode="json"),
            "stage_event_rows": stage_event_rows, "endpoint_preconditions": endpoint_gates,
            "step_annotations": read_jsonl(protocol_dir / "semantic_step_annotations.jsonl")}
        package = build_joint_evidence(evidence_state)
        preconditions["endpoint_preconditions"] = endpoint_gates
        preconditions["checks"].append({"name": "sample_protocol_version_identity",
            "status": "passed" if package["identity_versions_verified"] else "unknown",
            "counts": package["identity_summary"]})
        if not package["identity_versions_verified"]:
            preconditions.update({"scientific_ready": False, "status": "human_review_required"})
        return {
            **state,
            "protocol_feature_rows": protocol_feature_rows,
            "stage_event_rows": stage_event_rows,
            "feature_provenance": feature_provenance,
            "data_tables": data_tables,
            "input_gate_report": report,
            "scientific_preconditions": preconditions,
            "analysis_dataset": dataset.model_dump(mode="json"),
            "endpoint_preconditions": endpoint_gates,
            "joint_evidence": package,
            "endpoint_rows": summarize_endpoints(package),
            "step_annotations": evidence_state["step_annotations"],
            "literature_records": read_json(data_dir / "mechanism_literature_evidence.json").get("records", []),
            "step_log": _append_step(state, "integration_input_gate"),
        }

    @audited_operation
    def _node_phenotype_target_builder(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {**state, **build_phenotype_targets(state), "step_log": _append_step(state, "phenotype_target_builder")}

    @audited_operation
    def _node_protocol_phenotype_joiner(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {
            **state,
            **join_protocol_phenotypes(state),
            "step_log": _append_step(state, "protocol_phenotype_joiner"),
        }

    @audited_operation
    def _node_protocol_phenotype_association_analyzer(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {
            **state,
            **analyze_associations(state, self.max_candidate_features),
            "step_log": _append_step(state, "protocol_phenotype_association_analyzer"),
        }

    @audited_operation
    def _node_mechanistic_evidence_ranker(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {
            **state,
            **rank_mechanistic_evidence(state, self.max_candidate_features, self.llm_client,
                self.literature_search or (search_pubmed if int(state.get("objective", {}).get("mechanism", {}).get("max_literature_queries", 0)) > 0 else None)),
            "step_log": _append_step(state, "mechanistic_evidence_ranker"),
        }

    @audited_operation
    def _node_similar_protocol_reference(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {
            **state,
            **assess_similar_protocol_reference(state),
            "step_log": _append_step(state, "similar_protocol_reference"),
        }

    @audited_operation
    def _node_optimized_protocol_generator(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {
            **state,
            **generate_protocol_proposal(state),
            "step_log": _append_step(state, "optimized_protocol_generator"),
        }

    @audited_operation
    def _node_experiment_design(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        report = design_experiments(
            state.get("protocol_edits", []),
            state.get("objective", {}),
            state.get("scientific_preconditions", {}),
            state.get("scientific_context", {}).get("hypotheses", []),
        )
        report["analysis_dataset_id"] = state.get("analysis_dataset", {}).get("id")
        for candidate in report["candidates"]:
            candidate["input_snapshot"]["analysis_dataset_id"] = report["analysis_dataset_id"]
        return {
            **state,
            "next_experiments": report,
            "protocol_proposal": {**state.get("protocol_proposal", {}), "next_experiment_candidates": report},
            "step_log": _append_step(state, "experiment_design"),
        }

    @audited_operation
    def _node_optimization_audit(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        numeric_protocols = sum(
            1 for row in state.get("protocol_phenotype_rows", []) if row.get("has_numeric_phenotype")
        )
        risk_report = {
            "schema_version": "optimization-evidence-risk-report-v1",
            "risk_policy": "All recommendations require experimental validation before adoption.",
            "global_limitations": [
                "small protocol-data sample size limits statistical power",
                "protocol features may be highly collinear across papers",
                "current Data Analysis Agent outputs may be pending until real h5ad scoring is connected",
                "associations are not causal effects",
                "composite min-max scores are cohort-relative auxiliary summaries, not absolute quality",
                "leave-study-out sensitivity is not an intervention prediction validation",
            ],
            "input_gate_report": state.get("input_gate_report", {}),
            "numeric_protocols_available": numeric_protocols,
            "candidate_features": state.get("candidate_feature_rows", []),
            "identity_summary": state.get("joint_evidence", {}).get("identity_summary", {}),
            "mechanism_budget_and_errors": {key: state.get("mechanism_report", {}).get(key) for key in ("budget", "errors")},
        }
        return {
            **state,
            "risk_report": risk_report,
            "step_log": _append_step(state, "optimization_audit"),
        }

    @audited_operation
    def _node_export_results(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        outputs = self._output_paths()
        write_json(outputs["optimization_input_gate_report"], state.get("input_gate_report", {}))
        write_csv_dicts(outputs["phenotype_targets"], state.get("phenotype_targets", []))
        write_csv_dicts(outputs["protocol_phenotype_summary"], state.get("protocol_phenotype_rows", []))
        write_csv_dicts(outputs["protocol_phenotype_associations"], state.get("association_rows", []))
        write_csv_dicts(outputs["optimization_candidate_features"], state.get("candidate_feature_rows", []))
        write_json(outputs["optimized_protocol_edits"], state.get("protocol_edits", []))
        write_json(outputs["similar_protocol_reference"], state.get("reference_report", {}))
        write_json(outputs["joint_evidence"], state.get("joint_evidence", {}))
        write_json(outputs["endpoint_associations"], {"analysis_dataset_id": state["analysis_dataset"]["id"],
            "endpoint_summaries": state.get("endpoint_rows", []), "associations": state.get("association_rows", [])})
        write_json(outputs["mechanism_evidence"], state.get("mechanism_report", {}))
        write_json(outputs["proposed_improved_protocol_json"], state.get("protocol_proposal", {}))
        write_json(outputs["evidence_and_risk_report"], state.get("risk_report", {}))
        write_json(outputs["experimental_validation_plan_json"], state.get("validation_plan", {}))
        write_json(outputs["next_experiment_candidates"], state.get("next_experiments", {}))
        write_json(outputs["analysis_dataset"], state.get("analysis_dataset", {}))
        result = build_analysis_result(
            state["analysis_dataset"],
            state.get("objective", {}),
            state.get("protocol_phenotype_rows", []),
            state.get("association_rows", []),
            state.get("scientific_context", {}).get("previous_analysis_result"),
            content_fingerprint(optimization_code_paths()),
        )
        write_json(outputs["analysis_result"], result.model_dump(mode="json"))
        write_csv_dicts(
            outputs["factor_recommendation_table"], state.get("protocol_proposal", {}).get("recommendations", [])
        )
        outputs["proposed_improved_protocol_md"].write_text(
            _render_protocol_markdown(state.get("protocol_proposal", {})),
            encoding="utf-8",
        )
        outputs["experimental_validation_plan_md"].write_text(
            _render_validation_markdown(state.get("validation_plan", {})),
            encoding="utf-8",
        )
        return {
            **state,
            "outputs": outputs,
            "step_log": _append_step(state, "export_results"),
        }

    def _output_paths(self) -> dict[str, Path]:
        return {
            "optimization_input_gate_report": self.output_dir / "optimization_input_gate_report.json",
            "phenotype_targets": self.output_dir / "phenotype_targets.csv",
            "protocol_phenotype_summary": self.output_dir / "protocol_phenotype_summary.csv",
            "protocol_phenotype_associations": self.output_dir / "protocol_phenotype_associations.csv",
            "optimization_candidate_features": self.output_dir / "optimization_candidate_features.csv",
            "optimized_protocol_edits": self.output_dir / "optimized_protocol_edits.json",
            "similar_protocol_reference": self.output_dir / "similar_protocol_reference.json",
            "joint_evidence": self.output_dir / "joint_evidence.json",
            "endpoint_associations": self.output_dir / "endpoint_associations.json",
            "mechanism_evidence": self.output_dir / "mechanism_evidence.json",
            "proposed_improved_protocol_md": self.output_dir / "proposed_improved_protocol.md",
            "proposed_improved_protocol_json": self.output_dir / "proposed_improved_protocol.json",
            "factor_recommendation_table": self.output_dir / "factor_recommendation_table.csv",
            "evidence_and_risk_report": self.output_dir / "evidence_and_risk_report.json",
            "experimental_validation_plan_md": self.output_dir / "experimental_validation_plan.md",
            "experimental_validation_plan_json": self.output_dir / "experimental_validation_plan.json",
            "next_experiment_candidates": self.output_dir / "next_experiment_candidates.json",
            "analysis_dataset": self.output_dir / "analysis_dataset.json",
            "analysis_result": self.output_dir / "analysis_result.json",
        }


def _append_step(state: ProtocolOptimizingState, node: str) -> list[dict[str, Any]]:
    return [*state.get("step_log", []), {"node": node, "status": "completed"}]


@tool
@audited_tool
def protocol_optimizing_agent(
    question: str,
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    optimization_output_dir: Annotated[str, InjectedState("optimization_output_dir")],
    objective: dict[str, Any] | None = None,
    new_protocol_features: dict[str, Any] | None = None,
    force_optimization_rerun: bool = False,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Delegate integrated protocol-improvement, ranking, recommendation, or optimization questions to Protocol Optimizing Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root, project_id=project_id)
    forbid_client_paths(objective or {})
    protocol_dir = Path(resolve_domain_output_dir("protocol_analysis", protocol_output_dir, context))
    data_dir = Path(resolve_domain_output_dir("data_analysis", data_analysis_output_dir, context))
    optimization_dir = Path(resolve_domain_output_dir("protocol_optimization", optimization_output_dir, context))
    archive = current_archive()
    if archive:
        archive.record("effective_config", {"objective": objective or {}, "new_protocol_features": new_protocol_features or {}})
    store = ScientificWorldModel(context, project_id)
    orchestration_plan = build_optimization_orchestration_plan(
        protocol_dir,
        data_dir,
        objective=objective or {},
        new_protocol_features=new_protocol_features or {},
        force_optimization_rerun=force_optimization_rerun,
    )
    ingest_protocol_outputs(store, protocol_dir)
    ingest_phenotype_outputs(store, data_dir)
    scientific_context = store.reasoning_context()
    cache_key = _optimization_cache_key(protocol_dir, data_dir, objective or {}, new_protocol_features or {}, store.feedback_fingerprint())
    cache_meta = _read_optional_json(optimization_dir / "optimization_cache_metadata.json")
    has_cached_optimization = (
        all((optimization_dir / name).exists() for name in (
            "proposed_improved_protocol.json", "next_experiment_candidates.json", "joint_evidence.json",
            "endpoint_associations.json", "mechanism_evidence.json", "similar_protocol_reference.json"))
        and cache_meta.get("cache_key") == cache_key
    )
    should_run = orchestration_plan["can_run_protocol_optimizing_agent"] and (
        new_protocol_features or force_optimization_rerun or not has_cached_optimization
    )
    if should_run:
        ProtocolOptimizingAgent(output_dir=optimization_dir, objective=objective).run(
            protocol_dir,
            data_dir,
            objective=objective,
            new_protocol_features=new_protocol_features,
            scientific_context=scientific_context,
        )
        _write_optimization_cache_metadata(optimization_dir, cache_key, objective or {}, new_protocol_features or {})
    current_outputs = bool(should_run or (has_cached_optimization and orchestration_plan["can_run_protocol_optimizing_agent"]))
    recommendation_json = _read_optional_json(optimization_dir / "proposed_improved_protocol.json") if current_outputs else {}
    recommendation_md = _read_optional_text(optimization_dir / "proposed_improved_protocol.md") if current_outputs else ""
    risk_report = _read_optional_json(optimization_dir / "evidence_and_risk_report.json") if current_outputs else {}
    candidates = read_csv_dicts(optimization_dir / "optimization_candidate_features.csv") if current_outputs else []
    reference = _read_optional_json(optimization_dir / "similar_protocol_reference.json") if current_outputs else {}
    next_experiments = _read_optional_json(optimization_dir / "next_experiment_candidates.json") if current_outputs else {}
    world_update = ingest_experiment_proposals(store, optimization_dir) if next_experiments else {"path": str(store.path), "status": "not_generated"}
    if next_experiments:
        next_experiments = read_json(optimization_dir / "next_experiment_candidates.json")
        recommendation_json = read_json(optimization_dir / "proposed_improved_protocol.json")
        recommendation_md = _read_optional_text(optimization_dir / "proposed_improved_protocol.md")
    status = _optimization_tool_status(
        bool(orchestration_plan["can_run_protocol_optimizing_agent"]),
        bool(recommendation_json or recommendation_md),
        bool(should_run),
    )
    output_files = {
        "proposed_protocol_json": str(optimization_dir / "proposed_improved_protocol.json"),
        "proposed_protocol_md": str(optimization_dir / "proposed_improved_protocol.md"),
        "evidence_and_risk_report": str(optimization_dir / "evidence_and_risk_report.json"),
        "candidate_features": str(optimization_dir / "optimization_candidate_features.csv"),
        "next_experiment_candidates": str(optimization_dir / "next_experiment_candidates.json"),
        "joint_evidence": str(optimization_dir / "joint_evidence.json"),
        "endpoint_associations": str(optimization_dir / "endpoint_associations.json"),
        "mechanism_evidence": str(optimization_dir / "mechanism_evidence.json"),
    }
    record_agent_call(
        context,
        subagent="protocol_optimizing_agent",
        operation="protocol_optimization",
        status=status,
        question=question,
        output_files=output_files,
        output_dirs={
            "protocol_analysis": str(protocol_dir),
            "data_analysis": str(data_dir),
            "protocol_optimization": str(optimization_dir),
        },
        inputs={
            "protocol_analysis_output_dir": str(protocol_dir),
            "data_analysis_output_dir": str(data_dir),
        },
        extra={"entrypoint": "orchestrator", "last_subagent": "protocol_optimizing_agent"},
    )
    inputs = {
            "protocol_output_dir": str(protocol_dir),
            "data_analysis_output_dir": str(data_dir),
            "optimization_output_dir": str(optimization_dir),
            "protocol_matrix_exists": (protocol_dir / "protocol_feature_matrix.csv").exists(),
            "data_summary_exists": (data_dir / "data_analysis_protocol_summary.csv").exists(),
    }
    return evidence_response_envelope(
        context=context,
        subagent="protocol_optimizing_agent",
        status=status,
        question=question,
        selected_operation="protocol_optimization",
        used_tools=_optimization_used_tools(bool(should_run), bool(has_cached_optimization and current_outputs)),
        used_features=[str(row.get("feature") or "") for row in candidates[:20] if row.get("feature")],
        evidence_refs=_optimization_evidence_refs(candidates[:20], risk_report),
        confidence_caveats=_optimization_confidence_caveats(status, candidates[:20], risk_report),
        limitations=["Optimization recommendations are hypotheses and require experimental validation."],
        validation={"upstream_status": orchestration_plan["upstream_status"]},
        provenance={"inputs": inputs, "orchestration_plan": orchestration_plan},
        result_preview={
            "proposed_improved_protocol": recommendation_json,
            "proposed_improved_protocol_markdown": recommendation_md,
            "evidence_and_risk_report": risk_report,
            "top_candidate_features": candidates[:20],
            "similar_protocol_reference": reference,
            "joint_evidence": _scientific_artifact_preview("joint", read_json(optimization_dir / "joint_evidence.json")) if current_outputs else {},
            "endpoint_associations": _scientific_artifact_preview("endpoints", read_json(optimization_dir / "endpoint_associations.json")) if current_outputs else {},
            "mechanism_evidence": _scientific_artifact_preview("mechanism", read_json(optimization_dir / "mechanism_evidence.json")) if current_outputs else {},
            "cache_key": cache_key,
            "cache_status": "hit" if has_cached_optimization and not should_run else "miss_or_rerun",
            "next_experiment_candidates": next_experiments,
            "analysis_result": read_json(optimization_dir / "analysis_result.json") if next_experiments else {},
        },
        output_files=output_files,
        extra={
            "orchestration_plan": orchestration_plan,
            "inputs": inputs,
            "recommendation_policy": "optimization hypotheses only; experimental validation required",
            "scientific_state": world_update,
        },
    )


def _scientific_artifact_preview(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Do not append another full sample/literature corpus to the chat context."""
    common: dict[str, Any] = {key: payload[key] for key in ("id", "schema_version", "analysis_dataset_id", "evidence_package_id", "input_version")
              if key in payload}
    if kind == "joint":
        return {**common, "identity_summary": payload.get("identity_summary", {}),
            "identity_versions_verified": payload.get("identity_versions_verified", False),
            "n_observations": len(payload.get("observations", [])),
            "observations": payload.get("observations", [])[:10], "limitations": payload.get("limitations", []),
            "complete_evidence_artifact": "joint_evidence.json", "preview_only": True}
    if kind == "endpoints":
        keys = ("association_id", "feature", "target_endpoint", "effect_estimate", "effect_type", "analysis_role",
                "n_protocols", "confidence", "stability_status", "causal_interpretation", "confounding_risk")
        return {**common, "n_associations": len(payload.get("associations", [])),
            "associations": [{key: row[key] for key in keys if key in row} for row in payload.get("associations", [])[:10]],
            "complete_evidence_artifact": "endpoint_associations.json", "preview_only": True}
    interpretations = []
    for row in payload.get("interpretations", [])[:5]:
        interpretations.append({"feature": row["feature"], "status": row["status"],
            "mechanism_explanation": row["mechanism_explanation"],
            "supporting_source_ids": [ref["id"] for ref in row.get("supporting_evidence_refs", [])],
            "opposing_source_ids": [ref["id"] for ref in row.get("opposing_evidence_refs", [])],
            "alternative_explanations": row["alternative_explanations"],
            "falsification_conditions": row["falsification_conditions"],
            "endpoint_expectations": row.get("endpoint_expectations", {})})
    return {**common, "interpretations": interpretations, "n_literature_records": len(payload.get("literature", [])),
        "budget": payload.get("budget", {}), "errors": payload.get("errors", []),
        "challenge_search_performed": payload.get("challenge_search_performed", False),
        "complete_evidence_artifact": "mechanism_evidence.json", "preview_only": True}


def _optimization_tool_status(can_run: bool, has_outputs: bool, ran_now: bool) -> str:
    if not can_run:
        return "upstream_rerun_required"
    if ran_now:
        return "optimization_ran"
    if has_outputs:
        return "ok_cached_optimization_outputs"
    return "no_prior_optimization_outputs"


def _optimization_cache_key(
    protocol_dir: Path,
    data_dir: Path,
    objective: dict[str, Any],
    new_protocol_features: dict[str, Any],
    feedback_fingerprint: str = "",
) -> str:
    client = build_json_llm_client_from_env() if int(objective.get("mechanism", {}).get("max_llm_calls", 0)) > 0 else OfflineLLMClient()
    return content_fingerprint(
        {
            "protocol_feature_matrix": str(protocol_dir / "protocol_feature_matrix.csv"),
            "protocol_feature_provenance": str(protocol_dir / "protocol_feature_provenance.json"),
            "stage_factor_summary": str(protocol_dir / "stage_factor_matrix_or_events_summary.csv"),
            "data_protocol_summary": str(data_dir / "data_analysis_protocol_summary.csv"),
            "marker_scores": str(data_dir / "marker_scores.csv"),
            "gene_expression_summary": str(data_dir / "gene_expression_summary.csv"),
            "differential_expression": str(data_dir / "differential_expression.csv"),
            "maturity_scores": str(data_dir / "maturity_scores.csv"),
            "similarity_scores": str(data_dir / "similarity_scores.csv"),
            "phenotype_ir": str(data_dir / "phenotype_ir.json"),
            "data_validation": str(data_dir / "data_analysis_validation.json"),
            "phenotype_interpretation": str(data_dir / "phenotype_interpretation.json"),
            "cell_type_composition": str(data_dir / "cell_type_composition.csv"),
            "gene_program_scores": str(data_dir / "gene_program_scores.csv"),
            "regulatory_program_scores": str(data_dir / "regulatory_program_scores.csv"),
            "trajectory_scores": str(data_dir / "trajectory_scores.csv"),
            "h5ad_dataset_metadata": str(data_dir / "h5ad_summary_by_protocol.jsonl"),
            "normalized_events": str(protocol_dir / "normalized_factor_events.jsonl"),
            "semantic_step_annotations": str(protocol_dir / "semantic_step_annotations.jsonl"),
            "mechanism_literature_evidence": str(data_dir / "mechanism_literature_evidence.json"),
            "filtered_events": str(protocol_dir / "filtered_protocol_factor_events.csv"),
            **optimization_code_paths(),
        },
        extra={
            "objective": objective,
            "new_protocol_features": new_protocol_features,
            "output_contract": "protocol-optimization-scientific-v4",
            "experimental_feedback": feedback_fingerprint,
            "mechanism_model": {"client": type(client).__name__, **{key: getattr(client, key, None)
                for key in ("model", "base_url", "timeout_seconds")}},
        },
    )


def _write_optimization_cache_metadata(
    optimization_dir: Path,
    cache_key: str,
    objective: dict[str, Any],
    new_protocol_features: dict[str, Any],
) -> None:
    optimization_dir.mkdir(parents=True, exist_ok=True)
    (optimization_dir / "optimization_cache_metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "optimization-cache-metadata-v1",
                "cache_key": cache_key,
                "objective": objective,
                "new_protocol_features": new_protocol_features,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _optimization_used_tools(ran_now: bool, cache_hit: bool) -> list[str]:
    tools = ["build_optimization_orchestration_plan"]
    if ran_now:
        tools.append("ProtocolOptimizingAgent.run")
    elif cache_hit:
        tools.append("read_cached_protocol_optimization_outputs")
    return tools


def _optimization_evidence_refs(candidates: list[dict[str, str]], risk_report: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in candidates[:20]:
        refs.append(
            {
                "evidence_type": "optimization_candidate_feature",
                "feature": row.get("feature", ""),
                "evidence_status": row.get("evidence_status", ""),
                "confidence": row.get("confidence", ""),
            }
        )
    for rec in risk_report.get("recommendations", [])[:20] if isinstance(risk_report, dict) else []:
        refs.append(
            {
                "evidence_type": "optimization_recommendation",
                "recommendation_id": rec.get("recommendation_id", ""),
                "confidence": rec.get("confidence", ""),
                "requires_experimental_validation": rec.get("requires_experimental_validation", ""),
            }
        )
    return refs


def _optimization_confidence_caveats(
    status: str,
    candidates: list[dict[str, str]],
    risk_report: dict[str, Any],
) -> list[str]:
    caveats = ["Optimization outputs are hypotheses, not causal protocol-change conclusions."]
    if status != "optimization_ran" and status != "ok_cached_optimization_outputs":
        caveats.append(f"Protocol Optimizing Agent returned status={status}.")
    if not candidates:
        caveats.append("No optimization candidate feature table was available.")
    policy = risk_report.get("recommendation_policy") or risk_report.get("risk_policy") if isinstance(risk_report, dict) else ""
    if policy:
        caveats.append(str(policy))
    return caveats


def _read_optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def execute_optimization_action(action, arguments: dict[str, Any], context: RunContext,
                                protocol_dir: str, data_dir: str, output_dir: str) -> dict[str, Any]:
    if action.id == "hypothesis.generate":
        prior = ScientificWorldModel(context, context.project_id).reasoning_context()["hypotheses"]
        result = design_experiments(arguments["edits"], arguments["objective"],
                                   {"scientific_ready": False, "status": "hypothesis_generation_only"}, prior)
        result["status"] = "ok" if result["hypotheses"] else "evidence_insufficient"
        evidence = [ref for edit in arguments["edits"] for ref in edit.get("evidence_refs", [])]
        return evidence_response_envelope(context=context, subagent=action.agent, status=result["status"],
            question=str(arguments.get("question", "")), selected_operation=action.id, result_preview=result,
            used_tools=[action.implementation], evidence_refs=evidence,
            validation={"evidence_gaps": result.get("evidence_gaps", [])},
            confidence_caveats=["Descriptive assessment or unvalidated hypotheses, not causal effects."],
            extra={"action_id": action.id})
    return protocol_optimizing_agent.invoke({**arguments, "question": arguments.get("question", "Propose next experiments"),
        "protocol_output_dir": protocol_dir, "data_analysis_output_dir": data_dir,
        "optimization_output_dir": output_dir, **context.tool_inputs()})
