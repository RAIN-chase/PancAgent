from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_optimization_agent.analysis.optimization._shared import _input_findings, _read_protocol_event_evidence
from protocol_optimization_agent.analysis.optimization.assessment import (
    build_phenotype_targets,
    join_protocol_phenotypes,
    score_counterfactual_protocol,
)
from protocol_optimization_agent.analysis.optimization.association import analyze_associations
from protocol_optimization_agent.analysis.optimization.comparability import scientific_preconditions
from protocol_optimization_agent.analysis.optimization.contracts import ProtocolOptimizingState
from protocol_optimization_agent.analysis.optimization.experiment_design import design_experiments
from protocol_optimization_agent.analysis.optimization.proposals import (
    _render_protocol_markdown,
    _render_validation_markdown,
    generate_protocol_proposal,
    rank_mechanistic_evidence,
)
from protocol_optimization_agent.analysis.phenotype.representation import build_phenotype_ir
from protocol_optimization_agent.runtime.archive import audited_agent_run, audited_operation
from protocol_optimization_agent.runtime.context import content_fingerprint
from protocol_optimization_agent.runtime.io import read_csv_dicts, read_json, read_jsonl, write_csv_dicts, write_json
from protocol_optimization_agent.runtime.paths import optimization_code_paths
from protocol_optimization_agent.science.dataset import build_analysis_dataset, build_analysis_result


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
    ) -> None:
        self.output_dir = output_dir
        self.objective = objective or {"primary": "increase_beta_like_maturity"}
        self.max_candidate_features = max_candidate_features

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
        graph.add_node("counterfactual_protocol_scorer", self._node_counterfactual_protocol_scorer)
        graph.add_node("optimized_protocol_generator", self._node_optimized_protocol_generator)
        graph.add_node("experiment_design", self._node_experiment_design)
        graph.add_node("optimization_audit", self._node_optimization_audit)
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "integration_input_gate")
        graph.add_edge("integration_input_gate", "phenotype_target_builder")
        graph.add_edge("phenotype_target_builder", "protocol_phenotype_joiner")
        graph.add_edge("protocol_phenotype_joiner", "protocol_phenotype_association_analyzer")
        graph.add_edge("protocol_phenotype_association_analyzer", "mechanistic_evidence_ranker")
        graph.add_edge("mechanistic_evidence_ranker", "counterfactual_protocol_scorer")
        graph.add_edge("counterfactual_protocol_scorer", "optimized_protocol_generator")
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
            protocol_feature_rows, [p.model_dump(mode="json") for p in dataset.phenotypes], comparison=comparison
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
        return {
            **state,
            "protocol_feature_rows": protocol_feature_rows,
            "stage_event_rows": stage_event_rows,
            "feature_provenance": feature_provenance,
            "data_tables": data_tables,
            "input_gate_report": report,
            "scientific_preconditions": preconditions,
            "analysis_dataset": dataset.model_dump(mode="json"),
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
            **rank_mechanistic_evidence(state, self.max_candidate_features),
            "step_log": _append_step(state, "mechanistic_evidence_ranker"),
        }

    @audited_operation
    def _node_counterfactual_protocol_scorer(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        return {
            **state,
            **score_counterfactual_protocol(state),
            "step_log": _append_step(state, "counterfactual_protocol_scorer"),
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
            ],
            "input_gate_report": state.get("input_gate_report", {}),
            "numeric_protocols_available": numeric_protocols,
            "candidate_features": state.get("candidate_feature_rows", []),
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
        write_json(outputs["counterfactual_protocol_predictions"], state.get("prediction_report", {}))
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
            "counterfactual_protocol_predictions": self.output_dir / "counterfactual_protocol_predictions.json",
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
