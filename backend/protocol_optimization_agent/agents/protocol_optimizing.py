from __future__ import annotations

from math import sqrt
from pathlib import Path
from typing import Any, TypedDict

from protocol_optimization_agent.io import read_csv_dicts, read_json, read_jsonl, write_csv_dicts, write_json
from protocol_optimization_agent.scientific_reasoning import (
    build_phenotype_ir,
    design_experiments,
    scientific_preconditions,
)

MIN_ASSOCIATION_PROTOCOLS = 5
INTERVENTION_FEATURE_PREFIXES = (
    "all_reagent_",
    "reagent_",
    "factor_type_",
    "action_category_",
    "normalized_family_",
    "semantic_",
    "temporal_",
    "motif_action_",
    "transition_",
    "action_bigram_",
    "factor_type_bigram_",
)
NON_INTERVENTION_FEATURES = {
    "confidence_score",
    "comparison_ready",
    "n_stages",
    "n_factor_events",
    "n_unique_factors",
    "n_sources",
}
BOUNDED_SCORE_COLUMNS = {
    "maturity_score",
    "cosine_similarity",
    "similarity_score",
    "fraction",
    "proportion",
    "percentage",
    "trajectory_alignment_score",
}


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
    prediction_report: dict[str, Any]
    protocol_proposal: dict[str, Any]
    risk_report: dict[str, Any]
    validation_plan: dict[str, Any]
    scientific_context: dict[str, Any]
    scientific_preconditions: dict[str, Any]
    next_experiments: dict[str, Any]
    outputs: dict[str, Path]
    step_log: list[dict[str, Any]]


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
            data_tables, data_tables["h5ad_summary_by_protocol"], read_json(data_dir / "data_analysis_validation.json"), str(data_dir),
        )
        preconditions = scientific_preconditions(protocol_feature_rows, phenotype_ir.get("phenotypes", []))
        interpretation = read_json(data_dir / "phenotype_interpretation.json")
        if interpretation.get("status") == "human_review_required":
            preconditions["checks"].append({"name": "interpretation_consensus", "status": "human_review_required"})
            preconditions.update({"status": "human_review_required", "scientific_ready": False})
        report["execution_preconditions"] = {"joinable": bool(protocol_ids & data_protocol_ids),
                                             "has_protocol_features": bool(protocol_feature_rows)}
        report["scientific_preconditions"] = preconditions
        return {
            **state,
            "protocol_feature_rows": protocol_feature_rows,
            "stage_event_rows": stage_event_rows,
            "feature_provenance": feature_provenance,
            "data_tables": data_tables,
            "input_gate_report": report,
            "scientific_preconditions": preconditions,
            "step_log": _append_step(state, "integration_input_gate"),
        }

    def _node_phenotype_target_builder(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        targets: list[dict[str, Any]] = []
        for table_name, rows in state.get("data_tables", {}).items():
            if table_name in {"data_analysis_protocol_summary", "h5ad_summary_by_protocol"}:
                continue
            for row in rows:
                protocol_id = str(row.get("protocol_id") or "")
                if not protocol_id:
                    continue
                score, score_column = _first_numeric(row, _score_columns_for_table(table_name))
                endpoint = _endpoint_name(table_name, row)
                direction = _endpoint_direction(table_name, endpoint)
                weight = _endpoint_weight(table_name, endpoint, state.get("objective", {}))
                confidence = _endpoint_confidence(row, score)
                targets.append(
                    {
                        "protocol_id": protocol_id,
                        "dataset_id": row.get("dataset_id", ""),
                        "endpoint_family": table_name,
                        "endpoint": endpoint,
                        "score": score,
                        "raw_score": score,
                        "score_column": score_column,
                        "status": row.get("status", "numeric" if score is not None else "pending"),
                        "direction": direction,
                        "weight": weight,
                        "confidence": confidence,
                        "normalization": "",
                        "normalized_score": None,
                        "objective_score": None,
                        "confounders": _target_confounders(row),
                        "raw_basis": _compact_row(row),
                    }
                )
        targets = _apply_endpoint_normalization(targets)
        return {
            **state,
            "phenotype_targets": targets,
            "step_log": _append_step(state, "phenotype_target_builder"),
        }

    def _node_protocol_phenotype_joiner(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        phenotype_by_protocol: dict[str, list[dict[str, Any]]] = {}
        for target in state.get("phenotype_targets", []):
            phenotype_by_protocol.setdefault(str(target.get("protocol_id") or ""), []).append(target)
        rows: list[dict[str, Any]] = []
        for protocol_row in state.get("protocol_feature_rows", []):
            protocol_id = str(protocol_row.get("protocol_id") or "")
            targets = phenotype_by_protocol.get(protocol_id, [])
            numeric_scores = _weighted_objective_scores(targets)
            rows.append(
                {
                    "protocol_id": protocol_id,
                    "paper_id": protocol_row.get("paper_id", ""),
                    "dataset_ids": "|".join(sorted(_target_values(targets, "dataset_id"))),
                    "n_phenotype_targets": len(targets),
                    "n_numeric_phenotype_targets": len(numeric_scores),
                    "objective_weighted_score": _weighted_mean(numeric_scores),
                    "mean_numeric_phenotype_score": _weighted_mean(numeric_scores),
                    "has_numeric_phenotype": bool(numeric_scores),
                    "phenotype_status": "numeric_available" if numeric_scores else "numeric_pending",
                    "confounder_flags": "|".join(_protocol_confounder_flags(protocol_row, targets)),
                }
            )
        return {
            **state,
            "protocol_phenotype_rows": rows,
            "step_log": _append_step(state, "protocol_phenotype_joiner"),
        }

    def _node_protocol_phenotype_association_analyzer(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        phenotype_score_by_protocol = {
            row["protocol_id"]: row["objective_weighted_score"]
            for row in state.get("protocol_phenotype_rows", [])
            if row.get("has_numeric_phenotype") and row.get("objective_weighted_score") is not None
        }
        feature_names = _candidate_protocol_features(state.get("protocol_feature_rows", []))
        association_rows: list[dict[str, Any]] = []
        for feature in feature_names:
            paired: list[tuple[float, float]] = []
            for row in state.get("protocol_feature_rows", []):
                protocol_id = str(row.get("protocol_id") or "")
                if protocol_id not in phenotype_score_by_protocol:
                    continue
                value = _to_float(row.get(feature))
                if value is None:
                    continue
                paired.append((value, float(phenotype_score_by_protocol[protocol_id])))
            if len(paired) < MIN_ASSOCIATION_PROTOCOLS:
                continue
            xs = [pair[0] for pair in paired]
            ys = [pair[1] for pair in paired]
            effect = _pearson(xs, ys)
            confounding_risk = _association_confounding_risk(len(paired))
            association_rows.append(
                {
                    "feature": feature,
                    "n_protocols": len(paired),
                    "association_method": "pearson_correlation_screen",
                    "target_endpoint": "objective_weighted_score",
                    "effect_estimate": effect,
                    "mean_feature_value": _mean(xs),
                    "mean_objective_score": _mean(ys),
                    "feature_prevalence": _feature_prevalence(xs),
                    "causal_interpretation": "association_only",
                    "confounding_risk": confounding_risk,
                    "stability_status": _association_stability_status(xs, len(paired)),
                    "confidence": _association_confidence(len(paired), xs, confounding_risk),
                }
            )
        association_rows.sort(key=lambda row: abs(float(row.get("effect_estimate") or 0.0)), reverse=True)
        return {
            **state,
            "association_rows": association_rows[: self.max_candidate_features],
            "step_log": _append_step(state, "protocol_phenotype_association_analyzer"),
        }

    def _node_mechanistic_evidence_ranker(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        candidates: list[dict[str, Any]] = []
        if state.get("association_rows"):
            source_rows = state["association_rows"]
        else:
            source_rows = _fallback_candidate_rows(state.get("protocol_feature_rows", []), self.max_candidate_features)
        for index, row in enumerate(source_rows, start=1):
            feature = str(row.get("feature") or "")
            evidence_refs = _feature_evidence_refs(feature, state.get("feature_provenance", {}), state.get("stage_event_rows", []))
            evidence_summary = _evidence_summary(evidence_refs)
            candidates.append(
                {
                    "candidate_id": f"candidate_{index:03d}",
                    "feature": feature,
                    "rank": index,
                    "effect_estimate": row.get("effect_estimate", ""),
                    "n_protocols": row.get("n_protocols", ""),
                    "mechanistic_relevance": _mechanistic_relevance(feature),
                    "evidence_status": "provenance_available" if evidence_refs else "feature_level_only",
                    "evidence_summary": evidence_summary[:500],
                    "evidence_refs": evidence_refs,
                    "recommendation_role": _recommendation_role(feature, row),
                    "confidence": _candidate_confidence(row, evidence_summary),
                    "confounding_risk": row.get("confounding_risk", "high_unknown"),
                    "causal_interpretation": "hypothesis_requires_experimental_validation",
                }
            )
        candidates.sort(key=lambda candidate: (not bool(candidate.get("evidence_refs")), int(candidate["rank"])))
        for index, candidate in enumerate(candidates, start=1):
            candidate["rank"] = index
        return {
            **state,
            "candidate_feature_rows": candidates,
            "step_log": _append_step(state, "mechanistic_evidence_ranker"),
        }

    def _node_counterfactual_protocol_scorer(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        new_features = state.get("new_protocol_features", {})
        if not new_features:
            report: dict[str, Any] = {
                "schema_version": "counterfactual-protocol-prediction-v1",
                "status": "not_requested",
                "prediction_policy": "similar-protocol reference only; no intervention outcome model fitted",
                "nearest_protocols": [],
            }
        else:
            nearest = _nearest_protocols(new_features, state.get("protocol_feature_rows", []), limit=5)
            numeric_scores = {
                row["protocol_id"]: row.get("objective_weighted_score")
                for row in state.get("protocol_phenotype_rows", [])
                if row.get("has_numeric_phenotype")
            }
            neighbor_scores = _neighbor_scores(nearest, numeric_scores)
            report = {
                "schema_version": "counterfactual-protocol-prediction-v1",
                "status": "similar_protocol_reference_scored",
                "prediction_policy": "similar-protocol reference only; not a causal or supervised prediction model",
                "relative_assessment": _relative_label(neighbor_scores),
                "confidence": "low" if len(neighbor_scores) < 3 else "medium",
                "nearest_protocols": nearest,
                "neighbor_mean_objective_score": _mean(neighbor_scores),
                "feature_coverage": _nearest_feature_coverage(nearest),
                "out_of_distribution": _nearest_out_of_distribution(nearest),
                "uncertainty_drivers": _uncertainty_drivers(state, len(neighbor_scores)),
            }
        return {
            **state,
            "prediction_report": report,
            "step_log": _append_step(state, "counterfactual_protocol_scorer"),
        }

    def _node_optimized_protocol_generator(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        edits = _build_protocol_edits(state.get("candidate_feature_rows", []), state.get("objective", {}))
        recommendations = _build_recommendations(edits, state.get("objective", {}))
        proposal = {
            "schema_version": "optimized-protocol-proposal-v1",
            "objective": state.get("objective", {}),
            "recommendation_policy": "associations_are_hypotheses_not_causal_proof",
            "summary": {
                "n_candidate_features": len(state.get("candidate_feature_rows", [])),
                "n_recommendations": len(recommendations),
                "n_stage_level_edits": len(edits),
                "prediction_status": state.get("prediction_report", {}).get("status", "not_requested"),
            },
            "recommendations": recommendations,
            "protocol_edits": edits,
            "counterfactual_prediction": state.get("prediction_report", {}),
        }
        validation_plan = {
            "schema_version": "experimental-validation-plan-v1",
            "primary_readouts": [
                "beta-cell identity marker expression",
                "adult-like beta-cell gene program score",
                "in vivo reference atlas similarity",
                "GRN/regulon similarity",
                "off-target lineage and stress program burden",
            ],
            "recommended_comparison": "test proposed modifications against nearest source protocols with matched batches and timepoints",
            "minimum_reporting": [
                "protocol feature changes",
                "cell-type composition",
                "marker and gene-program scores",
                "reference similarity",
                "QC limitations",
            ],
            "negative_controls": _default_negative_controls(),
        }
        return {
            **state,
            "protocol_edits": edits,
            "protocol_proposal": proposal,
            "validation_plan": validation_plan,
            "step_log": _append_step(state, "optimized_protocol_generator"),
        }

    def _node_experiment_design(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        report = design_experiments(state.get("protocol_edits", []), state.get("objective", {}),
                                   state.get("scientific_preconditions", {}),
                                   state.get("scientific_context", {}).get("hypotheses", []))
        return {**state, "next_experiments": report,
                "protocol_proposal": {**state.get("protocol_proposal", {}), "next_experiment_candidates": report},
                "step_log": _append_step(state, "experiment_design")}

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
        }


def _input_findings(
    protocol_feature_rows: list[dict[str, Any]],
    data_tables: dict[str, list[dict[str, Any]]],
    protocol_ids: set[str],
    data_protocol_ids: set[str],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not protocol_feature_rows:
        findings.append({"severity": "high", "issue_type": "missing_protocol_feature_matrix"})
    if not any(data_tables.values()):
        findings.append({"severity": "high", "issue_type": "missing_data_analysis_outputs"})
    if protocol_ids and data_protocol_ids and not protocol_ids.intersection(data_protocol_ids):
        findings.append({"severity": "high", "issue_type": "no_joinable_protocol_ids"})
    if len(protocol_ids.intersection(data_protocol_ids)) < 10:
        findings.append(
            {
                "severity": "medium",
                "issue_type": "small_joinable_sample_size",
                "message": "Optimization should be interpreted as hypothesis ranking, not model-based causal inference.",
            }
        )
    return findings


def _read_protocol_event_evidence(protocol_dir: Path) -> list[dict[str, Any]]:
    normalized_events = read_jsonl(protocol_dir / "normalized_factor_events.jsonl")
    filtered_events = read_csv_dicts(protocol_dir / "filtered_protocol_factor_events.csv")
    if normalized_events:
        return [_canonical_event_row(event, "normalized_factor_events") for event in normalized_events]
    if filtered_events:
        return [_canonical_event_row(event, "filtered_protocol_factor_events") for event in filtered_events]
    return _stage_summary_event_rows(read_csv_dicts(protocol_dir / "stage_factor_matrix_or_events_summary.csv"))


def _canonical_event_row(event: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "source": source,
        "protocol_id": event.get("protocol_id", ""),
        "paper_id": event.get("paper_id", ""),
        "dataset_id": event.get("dataset_id", ""),
        "stage_id": event.get("stage_id") or event.get("stage") or event.get("stage_name") or "",
        "stage": event.get("stage") or event.get("stage_name") or event.get("stage_id") or "",
        "stage_index": event.get("stage_index", ""),
        "day_start": event.get("day_start", ""),
        "day_end": event.get("day_end", ""),
        "duration_days": event.get("duration_days", ""),
        "factor_type": event.get("factor_type", ""),
        "factor_name": event.get("factor_name") or event.get("original_name") or event.get("factor") or "",
        "standardized_name": event.get("standardized_name") or event.get("normalized_name") or "",
        "normalized_family": event.get("normalized_family", ""),
        "operation": event.get("operation") or event.get("action_type") or "",
        "concentration_value": event.get("concentration_value", ""),
        "concentration_unit": event.get("concentration_unit") or event.get("unit") or "",
        "evidence_quote": event.get("evidence_quote") or event.get("quote") or "",
        "source_doc_id": event.get("source_doc_id", ""),
    }


def _stage_summary_event_rows(stage_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stage_row in stage_rows:
        for factor in str(stage_row.get("factors") or "").split("|"):
            if factor:
                rows.append(
                    {
                        "source": "stage_factor_matrix_or_events_summary",
                        "protocol_id": stage_row.get("protocol_id", ""),
                        "stage_id": stage_row.get("stage") or stage_row.get("stage_id") or "",
                        "stage": stage_row.get("stage") or stage_row.get("stage_id") or "",
                        "stage_index": stage_row.get("stage_index", ""),
                        "day_start": stage_row.get("day_start", ""),
                        "day_end": stage_row.get("day_end", ""),
                        "factor_name": factor,
                        "standardized_name": factor,
                        "evidence_quote": "",
                    }
                )
    return rows


def _score_columns_for_table(table_name: str) -> list[str]:
    defaults = ["score", "mean_score", "maturity_score", "program_score", "marker_score"]
    if table_name == "similarity_scores":
        return ["cosine_similarity", "similarity_score", *defaults]
    if table_name == "cell_type_composition":
        return ["fraction", "proportion", "percentage", "score"]
    if table_name == "trajectory_scores":
        return ["trajectory_alignment_score", "pseudotime_score", *defaults]
    return defaults


def _first_numeric(row: dict[str, Any], columns: list[str]) -> tuple[float | None, str]:
    for column in columns:
        value = _to_float(row.get(column))
        if value is not None:
            return value, column
    for column, raw_value in row.items():
        if column.endswith("_score") or column.endswith("_similarity"):
            value = _to_float(raw_value)
            if value is not None:
                return value, column
    return None, ""


def _endpoint_name(table_name: str, row: dict[str, Any]) -> str:
    for column in ("endpoint", "gene_program", "marker_set", "marker", "cell_type", "lineage", "regulon", "trajectory"):
        if row.get(column):
            return f"{table_name}:{row[column]}"
    return table_name


def _endpoint_direction(table_name: str, endpoint: str) -> str:
    lowered = endpoint.lower()
    if table_name == "cell_type_composition" and any(term in lowered for term in ("off_target", "ductal", "progenitor")):
        return "lower_is_better"
    if any(term in lowered for term in ("stress", "hypoxia", "proliferation", "immature", "off_target", "apoptosis")):
        return "lower_is_better"
    return "higher_is_better"


def _endpoint_weight(table_name: str, endpoint: str, objective: dict[str, Any]) -> float:
    weights = objective.get("endpoint_weights", {})
    if isinstance(weights, dict):
        configured = _configured_weight(weights, endpoint, table_name)
        if configured is not None:
            return configured
    lowered = endpoint.lower()
    if "qc" in lowered:
        return 0.0
    if table_name in {"maturity_scores", "similarity_scores", "regulatory_program_scores"}:
        return 1.0
    if table_name in {"marker_scores", "gene_program_scores", "trajectory_scores"}:
        return 0.75
    if table_name == "cell_type_composition":
        return 0.6
    return 0.5


def _configured_weight(weights: dict[Any, Any], endpoint: str, table_name: str) -> float | None:
    if endpoint in weights:
        return _to_float(weights[endpoint])
    if table_name in weights:
        return _to_float(weights[table_name])
    return None


def _endpoint_confidence(row: dict[str, Any], score: float | None) -> str:
    status = str(row.get("status") or "").lower()
    if score is None or status in {"pending", "missing", "not_available"}:
        return "pending"
    if any(row.get(key) for key in ("qc_pass", "n_cells", "n_cells_used", "n_genes_used")):
        return "medium"
    return "low"


def _apply_endpoint_normalization(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    endpoint_values: dict[str, list[float]] = {}
    for target in targets:
        score = _to_float(target.get("raw_score"))
        if score is not None:
            endpoint_values.setdefault(str(target["endpoint"]), []).append(score)
    normalized_targets: list[dict[str, Any]] = []
    common_endpoints = _common_numeric_endpoints(targets)
    for target in targets:
        raw_score = _to_float(target.get("raw_score"))
        endpoint = str(target["endpoint"])
        score_column = str(target.get("score_column") or "")
        values = endpoint_values.get(endpoint, [])
        normalized_score, normalization = _normalized_endpoint_score(raw_score, values, score_column)
        target = {
            **target,
            "normalization": normalization,
            "normalized_score": normalized_score,
            "objective_score": _objective_score(normalized_score, str(target["direction"])),
            "in_objective_endpoint_set": endpoint in common_endpoints,
        }
        normalized_targets.append(target)
    return normalized_targets


def _common_numeric_endpoints(targets: list[dict[str, Any]]) -> set[str]:
    protocol_ids = {
        str(target.get("protocol_id") or "")
        for target in targets
        if _to_float(target.get("raw_score")) is not None and _to_float(target.get("weight")) not in (None, 0.0)
    }
    endpoint_protocols: dict[str, set[str]] = {}
    for target in targets:
        if _to_float(target.get("raw_score")) is None or _to_float(target.get("weight")) in (None, 0.0):
            continue
        endpoint_protocols.setdefault(str(target["endpoint"]), set()).add(str(target.get("protocol_id") or ""))
    return {endpoint for endpoint, present in endpoint_protocols.items() if present == protocol_ids}


def _normalized_endpoint_score(raw_score: float | None, values: list[float], score_column: str) -> tuple[float | None, str]:
    if raw_score is None:
        return None, "missing_numeric_score"
    if score_column in BOUNDED_SCORE_COLUMNS and all(0.0 <= value <= 1.0 for value in values):
        return raw_score, "bounded_0_1"
    if len(set(values)) <= 1:
        return 0.5, "constant_endpoint_midpoint"
    min_value = min(values)
    max_value = max(values)
    return (raw_score - min_value) / (max_value - min_value), f"minmax_by_endpoint:{min_value}:{max_value}"


def _objective_score(normalized_score: float | None, direction: str) -> float | None:
    if normalized_score is None:
        return None
    if direction == "lower_is_better":
        return 1.0 - normalized_score
    return normalized_score



def _target_confounders(row: dict[str, Any]) -> dict[str, str]:
    keys = ("dataset_id", "paper_id", "cell_line", "assay", "timepoint", "batch", "batch_id", "condition_id", "sample_id")
    return {key: str(row.get(key) or "") for key in keys if row.get(key)}


def _weighted_objective_scores(targets: list[dict[str, Any]]) -> list[tuple[float, float]]:
    scores: list[tuple[float, float]] = []
    for target in targets:
        if not target.get("in_objective_endpoint_set"):
            continue
        objective_score = _to_float(target.get("objective_score"))
        weight = _to_float(target.get("weight"))
        if objective_score is not None and weight is not None and weight > 0:
            scores.append((objective_score, weight * _confidence_weight(str(target.get("confidence") or ""))))
    return scores


def _confidence_weight(confidence: str) -> float:
    if confidence == "high":
        return 1.0
    if confidence == "medium":
        return 0.75
    if confidence == "pending":
        return 0.0
    return 0.5


def _weighted_mean(values: list[tuple[float, float]]) -> float | None:
    denominator = sum(weight for _, weight in values)
    if denominator == 0:
        return None
    return sum(value * weight for value, weight in values) / denominator


def _target_values(targets: list[dict[str, Any]], key: str) -> set[str]:
    return {str(target.get(key) or "") for target in targets if target.get(key)}


def _protocol_confounder_flags(protocol_row: dict[str, Any], targets: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    if protocol_row.get("paper_id"):
        flags.append("paper")
    if len(_target_values(targets, "dataset_id")) > 1:
        flags.append("multiple_datasets")
    for target in targets:
        confounders = target.get("confounders", {})
        if isinstance(confounders, dict):
            for key in ("cell_line", "assay", "timepoint", "batch", "batch_id"):
                if confounders.get(key) and key not in flags:
                    flags.append(key)
    return flags


def _association_confounding_risk(n_protocols: int) -> str:
    if n_protocols < MIN_ASSOCIATION_PROTOCOLS:
        return "insufficient_sample"
    if n_protocols < 10:
        return "high_small_sample"
    if n_protocols < 30:
        return "medium_small_sample"
    return "unmodeled_protocol_dataset_confounding"


def _feature_prevalence(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(1 for value in values if value != 0.0) / len(values)


def _association_stability_status(values: list[float], n_protocols: int) -> str:
    prevalence = _feature_prevalence(values)
    if n_protocols < 10:
        return "exploratory_small_sample"
    if prevalence is None or prevalence <= 0.1 or prevalence >= 0.9:
        return "unstable_low_feature_variation"
    return "screening_only_unvalidated"


def _association_confidence(n_protocols: int, values: list[float], confounding_risk: str) -> str:
    stability = _association_stability_status(values, n_protocols)
    if confounding_risk.startswith("high") or stability != "screening_only_unvalidated":
        return "low"
    return "medium"


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in ("", None)}


def _candidate_protocol_features(rows: list[dict[str, Any]]) -> list[str]:
    features: list[str] = []
    for row in rows:
        for key in row:
            if key in features or not _is_intervention_feature(key):
                continue
            if any(_to_float(other.get(key)) is not None for other in rows):
                features.append(key)
    return features


def _is_intervention_feature(feature: str) -> bool:
    if feature in NON_INTERVENTION_FEATURES:
        return False
    if feature.endswith(("_score", "_confidence", "_ready")):
        return False
    return feature.startswith(INTERVENTION_FEATURE_PREFIXES)


def _fallback_candidate_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    features = _candidate_protocol_features(rows)
    ranked = sorted(features, key=_feature_priority, reverse=True)
    return [{"feature": feature, "effect_estimate": "", "n_protocols": ""} for feature in ranked[:limit]]


def _feature_priority(feature: str) -> int:
    lowered = feature.lower()
    score = 0
    for term in ("maturation", "endocrine", "beta", "notch", "wnt", "tgf", "egf", "fgf", "ra", "matrix"):
        if term in lowered:
            score += 2
    if lowered.startswith(("reagent_", "all_reagent_", "factor_type_", "semantic_")):
        score += 1
    return score


def _feature_evidence(feature: str, provenance: dict[str, Any]) -> str:
    raw = _feature_provenance_payloads(feature, provenance)
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        return "; ".join(str(item) for item in raw[:3])
    if isinstance(raw, dict):
        quote = raw.get("evidence_quote") or raw.get("quote") or raw.get("summary")
        if quote:
            return str(quote)
        return "; ".join(f"{key}={value}" for key, value in list(raw.items())[:5])
    return str(raw)


def _feature_evidence_refs(
    feature: str,
    provenance: dict[str, Any],
    stage_event_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = _provenance_refs(feature, provenance)
    refs.extend(_stage_event_refs(feature, stage_event_rows))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for ref in refs:
        key = (
            str(ref.get("protocol_id") or ""),
            str(ref.get("stage_id") or ""),
            str(ref.get("event_id") or ""),
            str(ref.get("evidence_quote") or ""),
        )
        if key not in seen:
            deduped.append(ref)
            seen.add(key)
    return deduped[:10]


def _provenance_refs(feature: str, provenance: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for protocol_id, item in _feature_provenance_items(feature, provenance):
        if isinstance(item, dict):
            refs.append(
                {
                    "source": "protocol_feature_provenance",
                    "protocol_id": item.get("protocol_id") or protocol_id,
                    "stage_id": item.get("stage_id") or item.get("stage") or "",
                    "event_id": item.get("event_id", item.get("entity_id", "")),
                    "day_start": item.get("day_start", ""),
                    "day_end": item.get("day_end", ""),
                    "factor_name": item.get("factor_name") or item.get("reagent") or item.get("normalized_family") or "",
                    "operation": item.get("operation", item.get("action_type", "")),
                    "concentration": _concentration_text(item),
                    "evidence_quote": item.get("evidence_quote") or item.get("quote") or item.get("summary") or "",
                }
            )
        elif item:
            refs.append({"source": "protocol_feature_provenance", "protocol_id": protocol_id, "evidence_quote": str(item)})
    return refs


def _feature_provenance_payloads(feature: str, provenance: dict[str, Any]) -> Any:
    items = [item for _, item in _feature_provenance_items(feature, provenance)]
    if not items:
        return None
    return items


def _feature_provenance_items(feature: str, provenance: dict[str, Any]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    protocols = provenance.get("protocols", {})
    if isinstance(protocols, dict):
        for protocol_id, protocol_features in protocols.items():
            if isinstance(protocol_features, dict) and feature in protocol_features:
                for item in _as_ref_items(protocol_features[feature]):
                    items.append((str(protocol_id), item))
    for item in _as_ref_items(provenance.get(feature)):
        items.append(("", item))
    features = provenance.get("features", {})
    if isinstance(features, dict):
        for item in _as_ref_items(features.get(feature)):
            items.append(("", item))
    return items


def _stage_event_refs(feature: str, stage_event_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    feature_tokens = _feature_tokens(feature)
    for row in stage_event_rows:
        searchable = " ".join(str(row.get(key) or "") for key in row)
        if not _feature_matches_text(feature, feature_tokens, searchable):
            continue
        refs.append(
            {
                "source": "stage_factor_matrix_or_events_summary",
                "protocol_id": row.get("protocol_id", ""),
                "stage_id": row.get("stage_id") or row.get("stage") or "",
                "event_id": row.get("event_id", ""),
                "day_start": row.get("day_start", ""),
                "day_end": row.get("day_end", ""),
                "factor_name": row.get("factor_name") or row.get("standardized_name") or row.get("factor") or "",
                "operation": row.get("operation", row.get("action_type", "")),
                "concentration": _concentration_text(row),
                "evidence_quote": row.get("evidence_quote", ""),
            }
        )
    return refs


def _feature_matches_text(feature: str, feature_tokens: set[str], text: str) -> bool:
    compact_feature = _compact_feature_name(feature)
    compact_text = _compact_feature_name(text)
    if compact_feature and (compact_feature in compact_text or compact_text in compact_feature):
        return True
    for prefix in ("allreagent", "reagent", "semantic", "factor", "motifaction"):
        stripped = compact_feature.removeprefix(prefix)
        if stripped and len(stripped) > 1 and stripped in compact_text:
            return True
    return bool(feature_tokens.intersection(_feature_tokens(text)))


def _compact_feature_name(text: str) -> str:
    return "".join(character for character in text.lower() if character.isalnum())


def _as_ref_items(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("refs", "references", "evidence_refs", "provenance"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return nested
        return [raw]
    return [raw]


def _concentration_text(row: dict[str, Any]) -> str:
    value = row.get("concentration") or row.get("concentration_value") or row.get("dose")
    unit = row.get("concentration_unit") or row.get("unit") or row.get("dose_unit")
    if value in ("", None):
        return ""
    return f"{value} {unit}".strip()


def _feature_tokens(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ").replace("-", " ").replace("/", " ")
    return {token for token in normalized.split() if len(token) > 2 and token not in {"feature", "reagent", "factor"}}


def _evidence_summary(evidence_refs: list[dict[str, Any]]) -> str:
    quotes = [str(ref.get("evidence_quote") or "") for ref in evidence_refs if ref.get("evidence_quote")]
    if quotes:
        return "; ".join(quotes[:3])
    factors = [str(ref.get("factor_name") or "") for ref in evidence_refs if ref.get("factor_name")]
    return "; ".join(factors[:3])


def _mechanistic_relevance(feature: str) -> str:
    lowered = feature.lower()
    if "maturation" in lowered or "beta" in lowered:
        return "direct_maturity_or_beta_identity_axis"
    if any(term in lowered for term in ("notch", "wnt", "tgf", "fgf", "egf", "ra")):
        return "developmental_pathway_axis"
    if any(term in lowered for term in ("matrix", "ecm", "aggregation", "culture_format")):
        return "culture_microenvironment_axis"
    if any(term in lowered for term in ("sequence", "bigram", "transition", "motif")):
        return "workflow_sequence_axis"
    return "protocol_feature_axis"


def _recommendation_role(feature: str, row: dict[str, Any]) -> str:
    effect = _to_float(row.get("effect_estimate"))
    if effect is None:
        return "prioritize_for_review"
    if row.get("confidence") == "low" or row.get("stability_status") == "exploratory_small_sample":
        return "study_variable_only"
    return "candidate_to_add_or_increase" if effect > 0 else "candidate_to_reduce_or_avoid"


def _candidate_confidence(row: dict[str, Any], evidence: str) -> str:
    n_protocols = int(_to_float(row.get("n_protocols")) or 0)
    if n_protocols >= 20 and evidence:
        return "medium"
    return "low"


def _nearest_protocols(new_features: dict[str, Any], rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    feature_scales = _feature_scales(rows)
    intervention_features = sorted(feature for feature in feature_scales if _is_intervention_feature(feature))
    new_numeric = _numeric_items(new_features)
    for row in rows:
        protocol_id = str(row.get("protocol_id") or "")
        if not protocol_id:
            continue
        row_numeric = _numeric_items(row)
        shared = [key for key in intervention_features if key in new_numeric and key in row_numeric]
        if not shared:
            continue
        distance = sqrt(
            sum(
                (_scaled_value(new_numeric[key], feature_scales[key]) - _scaled_value(row_numeric[key], feature_scales[key]))
                ** 2
                for key in shared
            )
        )
        similarity = 1.0 / (1.0 + distance)
        coverage = len(shared) / len(intervention_features) if intervention_features else 0.0
        scored.append(
            {
                "protocol_id": protocol_id,
                "similarity": similarity,
                "n_shared_features": len(shared),
                "feature_coverage": coverage,
                "out_of_distribution_features": _out_of_distribution_features(
                    new_numeric,
                    feature_scales,
                    intervention_features,
                ),
                "scoring_policy": "minmax_scaled_intervention_features_only",
            }
        )
    scored.sort(key=lambda row: float(row["similarity"]), reverse=True)
    return scored[:limit]


def _feature_scales(rows: list[dict[str, Any]]) -> dict[str, tuple[float, float]]:
    values_by_feature: dict[str, list[float]] = {}
    for row in rows:
        for feature, value in _numeric_items(row).items():
            if _is_intervention_feature(feature):
                values_by_feature.setdefault(feature, []).append(value)
    return {feature: (min(values), max(values)) for feature, values in values_by_feature.items() if values}


def _scaled_value(value: float, scale: tuple[float, float]) -> float:
    min_value, max_value = scale
    if min_value == max_value:
        return 0.5
    return (value - min_value) / (max_value - min_value)


def _out_of_distribution_features(
    new_numeric: dict[str, float],
    feature_scales: dict[str, tuple[float, float]],
    allowed_features: list[str],
) -> list[str]:
    features: list[str] = []
    for feature in allowed_features:
        value = new_numeric.get(feature)
        if value is None:
            continue
        scale = feature_scales.get(feature)
        if scale is None:
            features.append(feature)
            continue
        min_value, max_value = scale
        if value < min_value or value > max_value:
            features.append(feature)
    return sorted(features)


def _nearest_feature_coverage(nearest: list[dict[str, Any]]) -> dict[str, Any]:
    coverages = [_to_float(row.get("feature_coverage")) for row in nearest]
    numeric_coverages = [coverage for coverage in coverages if coverage is not None]
    return {
        "mean": _mean(numeric_coverages),
        "min": min(numeric_coverages) if numeric_coverages else None,
        "nearest_neighbor_count": len(nearest),
    }


def _nearest_out_of_distribution(nearest: list[dict[str, Any]]) -> dict[str, Any]:
    features = {
        str(feature)
        for row in nearest
        for feature in row.get("out_of_distribution_features", [])
        if str(feature)
    }
    return {"status": "out_of_distribution" if features else "in_distribution", "features": sorted(features)}


def _neighbor_scores(nearest: list[dict[str, Any]], numeric_scores: dict[str, Any]) -> list[float]:
    scores: list[float] = []
    for row in nearest:
        protocol_id = str(row.get("protocol_id") or "")
        score = _to_float(numeric_scores.get(protocol_id))
        if score is not None:
            scores.append(score)
    return scores


def _numeric_items(row: dict[str, Any]) -> dict[str, float]:
    numeric: dict[str, float] = {}
    for key, value in row.items():
        parsed = _to_float(value)
        if parsed is not None:
            numeric[key] = parsed
    return numeric


def _uncertainty_drivers(state: ProtocolOptimizingState, n_neighbors_with_scores: int) -> list[str]:
    drivers = ["protocol-feature associations are not causal"]
    if n_neighbors_with_scores < 3:
        drivers.append("fewer than three nearest protocols have numeric phenotype scores")
    if not state.get("association_rows"):
        drivers.append("no numeric association screen was possible from current Data Analysis outputs")
    return drivers


def _relative_label(scores: list[float]) -> str:
    if not scores:
        return "unknown_numeric_scores_pending"
    mean_score = _mean(scores)
    if mean_score is None:
        return "unknown_numeric_scores_pending"
    if mean_score >= 0.67:
        return "nearest_protocols_have_high_objective_scores"
    if mean_score <= 0.33:
        return "nearest_protocols_have_low_objective_scores"
    return "nearest_protocols_have_intermediate_or_uncertain_objective_scores"


def _build_protocol_edits(candidates: list[dict[str, Any]], objective: dict[str, Any]) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:10], start=1):
        feature = str(candidate.get("feature") or "")
        evidence_refs = _candidate_evidence_refs(candidate)
        role = str(candidate.get("recommendation_role") or "prioritize_for_review")
        representative_ref = _representative_evidence_ref(evidence_refs)
        edits.append(
            {
                "edit_id": f"edit_{index:03d}",
                "edit_type": _edit_type(role),
                "target": objective.get("primary", "increase_beta_like_maturity"),
                "candidate_feature": feature,
                "target_stage": _target_stage(feature, representative_ref),
                "day_start": representative_ref.get("day_start", ""),
                "day_end": representative_ref.get("day_end", ""),
                "reagent_or_operation": representative_ref.get("factor_name") or _feature_label(feature),
                "operation": representative_ref.get("operation") or _operation_from_role(role),
                "dose_or_condition_range": representative_ref.get("concentration", "derive from source protocol range"),
                "source_protocols": _source_protocols(evidence_refs),
                "rationale": _change_text(feature, role),
                "mechanistic_relevance": candidate.get("mechanistic_relevance", ""),
                "effect_estimate": candidate.get("effect_estimate", ""),
                "confidence": candidate.get("confidence", "low"),
                "confounding_risk": candidate.get("confounding_risk", "high_unknown"),
                "evidence_refs": evidence_refs,
                "negative_controls": _default_negative_controls(),
                "validation_readouts": _validation_readouts(feature),
                "causal_interpretation": "hypothesis_requires_experimental_validation",
            }
        )
    return edits


def _candidate_evidence_refs(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    refs = candidate.get("evidence_refs", [])
    if isinstance(refs, list):
        return [ref for ref in refs if isinstance(ref, dict)]
    return []


def _representative_evidence_ref(evidence_refs: list[dict[str, Any]]) -> dict[str, Any]:
    for ref in evidence_refs:
        if ref.get("stage_id") or ref.get("day_start") or ref.get("factor_name"):
            return ref
    return evidence_refs[0] if evidence_refs else {}


def _edit_type(role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return "add_or_increase"
    if role == "candidate_to_reduce_or_avoid":
        return "reduce_or_remove"
    if role == "study_variable_only":
        return "study_variable_before_intervention"
    return "review_before_edit"


def _target_stage(feature: str, evidence_ref: dict[str, Any]) -> str:
    if evidence_ref.get("stage_id"):
        return str(evidence_ref["stage_id"])
    lowered = feature.lower()
    if "maturation" in lowered or "beta" in lowered:
        return "beta_cell_maturation"
    if "endocrine" in lowered:
        return "endocrine_induction"
    if "progenitor" in lowered:
        return "pancreatic_progenitor_specification"
    return "stage_to_be_determined_from_source_protocol"


def _feature_label(feature: str) -> str:
    return feature.replace("all_reagent_", "").replace("reagent_", "").replace("semantic_", "").replace("_", " ")


def _operation_from_role(role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return "add_or_extend"
    if role == "candidate_to_reduce_or_avoid":
        return "remove_reduce_or_delay"
    if role == "study_variable_only":
        return "do_not_edit_until_validated"
    return "review"


def _source_protocols(evidence_refs: list[dict[str, Any]]) -> list[str]:
    protocols = sorted({str(ref.get("protocol_id") or "") for ref in evidence_refs if ref.get("protocol_id")})
    return protocols


def _validation_readouts(feature: str) -> list[str]:
    lowered = feature.lower()
    readouts = ["objective-weighted phenotype score", "cell-type composition", "QC pass rate"]
    if "beta" in lowered or "maturation" in lowered:
        readouts.extend(["MAFA", "INS", "IAPP", "adult beta gene program score"])
    if any(term in lowered for term in ("grn", "regulatory", "notch", "wnt", "tgf", "fgf", "egf", "ra")):
        readouts.append("GRN/regulon similarity")
    if "stress" in lowered or "off_target" in lowered:
        readouts.extend(["stress program score", "off-target lineage burden"])
    return readouts


def _default_negative_controls() -> list[str]:
    return [
        "source protocol without the proposed edit",
        "vehicle or matched media-change control when applicable",
        "timepoint-matched batch control",
    ]


def _build_recommendations(edits: list[dict[str, Any]], objective: dict[str, Any]) -> list[dict[str, Any]]:
    if not edits:
        return [
            {
                "recommendation_id": "rec_001",
                "target": objective.get("primary", "increase_beta_like_maturity"),
                "proposed_change": "Connect numeric h5ad maturity and reference similarity scoring before ranking protocol modifications.",
                "source_basis": "No candidate protocol features could be ranked from current inputs.",
                "confidence": "low",
                "risk": "Optimization would otherwise rely only on incomplete inputs.",
                "requires_experimental_validation": "true",
            }
        ]
    recommendations: list[dict[str, Any]] = []
    for index, edit in enumerate(edits, start=1):
        recommendations.append(
            {
                "recommendation_id": f"rec_{index:03d}",
                "target": objective.get("primary", "increase_beta_like_maturity"),
                "candidate_feature": edit.get("candidate_feature", ""),
                "proposed_change": edit.get("rationale", ""),
                "stage_level_edit": edit,
                "source_basis": _evidence_summary(_candidate_evidence_refs(edit))
                or "ranked from protocol feature representation",
                "mechanistic_relevance": edit.get("mechanistic_relevance", ""),
                "effect_estimate": edit.get("effect_estimate", ""),
                "confidence": edit.get("confidence", "low"),
                "risk": "Feature may be confounded with paper, dataset, batch, cell line, assay, or timepoint differences.",
                "evidence_refs": edit.get("evidence_refs", []),
                "negative_controls": edit.get("negative_controls", []),
                "validation_readouts": edit.get("validation_readouts", []),
                "requires_experimental_validation": "true",
            }
        )
    return recommendations


def _change_text(feature: str, role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return f"Consider adding, preserving, or increasing the protocol module represented by `{feature}`."
    if role == "candidate_to_reduce_or_avoid":
        return f"Consider reducing, removing, or delaying the protocol module represented by `{feature}`."
    if role == "study_variable_only":
        return f"Treat `{feature}` as a study variable; current evidence is insufficient for a concrete protocol edit."
    return f"Review `{feature}` as a candidate optimization variable before proposing a concrete protocol edit."


def _render_protocol_markdown(protocol_doc: dict[str, Any]) -> str:
    lines = [
        "# Candidate Pancreatic / Islet Organoid Experiments",
        "",
        "## Policy",
        "",
        "Associations are hypotheses, not causal proof. Candidates require researcher review and experimental validation.",
        "",
        "## Objective",
        "",
        f"- Primary: `{protocol_doc.get('objective', {}).get('primary', 'increase_beta_like_maturity')}`",
        "",
        "## Hypothesis-Based Study Variables",
        "",
    ]
    for rec in protocol_doc.get("recommendations", []):
        lines.extend(
            [
                f"### {rec.get('recommendation_id', 'recommendation')}",
                "",
                f"- Candidate feature: `{rec.get('candidate_feature', '')}`",
                f"- Proposed change: {rec.get('proposed_change', '')}",
                f"- Source basis: {rec.get('source_basis', '')}",
                f"- Confidence: `{rec.get('confidence', 'low')}`",
                f"- Risk: {rec.get('risk', '')}",
                "",
            ]
        )
    experiment_report = protocol_doc.get("next_experiment_candidates", {})
    if experiment_report:
        lines.extend(["## Experiment Selection", "", "Candidates remain pending researcher review.", "",
                      f"- Scientific preconditions: `{experiment_report.get('scientific_preconditions', {}).get('status', 'unknown')}`",
                      f"- Selected for review: {', '.join(experiment_report.get('selected_for_review', [])) or 'none; resolve missing evidence and assessments'}", ""])
        for candidate in experiment_report.get("candidates", []):
            lines.extend([f"### {candidate['id']}", "", f"- Feature: `{candidate['intervention'].get('candidate_feature', '')}`",
                          f"- Ordinal decision utility: `{candidate.get('utility')}`",
                          f"- Critique: {', '.join(candidate.get('critique', [])) or 'no unresolved gate findings'}", ""])
    return "\n".join(lines)


def _render_validation_markdown(plan: dict[str, Any]) -> str:
    lines = ["# Experimental Validation Plan", "", "## Primary Readouts", ""]
    for readout in plan.get("primary_readouts", []):
        lines.append(f"- {readout}")
    lines.extend(["", "## Recommended Comparison", "", str(plan.get("recommended_comparison", "")), ""])
    return "\n".join(lines)


def _to_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _pearson(xs: list[float], ys: list[float]) -> float:
    x_mean = _mean(xs) or 0.0
    y_mean = _mean(ys) or 0.0
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    x_den = sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_den = sqrt(sum((y - y_mean) ** 2 for y in ys))
    if x_den == 0 or y_den == 0:
        return 0.0
    return numerator / (x_den * y_den)


def _append_step(state: ProtocolOptimizingState, node: str) -> list[dict[str, Any]]:
    return [*state.get("step_log", []), {"node": node, "status": "completed"}]
