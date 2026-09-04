from __future__ import annotations

from math import sqrt
from pathlib import Path
from typing import Any, TypedDict

from protocol_optimization_agent.io import read_csv_dicts, read_json, read_jsonl, write_csv_dicts, write_json


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
    prediction_report: dict[str, Any]
    protocol_proposal: dict[str, Any]
    risk_report: dict[str, Any]
    validation_plan: dict[str, Any]
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
    ) -> dict[str, Path]:
        initial_state: ProtocolOptimizingState = {
            "protocol_output_dir": str(protocol_output_dir),
            "data_analysis_output_dir": str(data_analysis_output_dir),
            "objective": objective or self.objective,
            "new_protocol_features": new_protocol_features or {},
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
        graph.add_node("optimization_audit", self._node_optimization_audit)
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "integration_input_gate")
        graph.add_edge("integration_input_gate", "phenotype_target_builder")
        graph.add_edge("phenotype_target_builder", "protocol_phenotype_joiner")
        graph.add_edge("protocol_phenotype_joiner", "protocol_phenotype_association_analyzer")
        graph.add_edge("protocol_phenotype_association_analyzer", "mechanistic_evidence_ranker")
        graph.add_edge("mechanistic_evidence_ranker", "counterfactual_protocol_scorer")
        graph.add_edge("counterfactual_protocol_scorer", "optimized_protocol_generator")
        graph.add_edge("optimized_protocol_generator", "optimization_audit")
        graph.add_edge("optimization_audit", "export_results")
        graph.add_edge("export_results", END)
        return graph.compile() if compile_graph else graph

    def _node_integration_input_gate(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        protocol_dir = Path(state["protocol_output_dir"])
        data_dir = Path(state["data_analysis_output_dir"])
        protocol_feature_rows = read_csv_dicts(protocol_dir / "protocol_feature_matrix.csv")
        stage_event_rows = read_csv_dicts(protocol_dir / "stage_factor_matrix_or_events_summary.csv")
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
        return {
            **state,
            "protocol_feature_rows": protocol_feature_rows,
            "stage_event_rows": stage_event_rows,
            "feature_provenance": feature_provenance,
            "data_tables": data_tables,
            "input_gate_report": report,
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
                targets.append(
                    {
                        "protocol_id": protocol_id,
                        "dataset_id": row.get("dataset_id", ""),
                        "endpoint_family": table_name,
                        "endpoint": endpoint,
                        "score": score,
                        "score_column": score_column,
                        "status": row.get("status", "numeric" if score is not None else "pending"),
                        "direction": _endpoint_direction(table_name, endpoint),
                        "raw_basis": _compact_row(row),
                    }
                )
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
            numeric_scores = [float(target["score"]) for target in targets if target.get("score") is not None]
            rows.append(
                {
                    "protocol_id": protocol_id,
                    "n_phenotype_targets": len(targets),
                    "n_numeric_phenotype_targets": len(numeric_scores),
                    "mean_numeric_phenotype_score": _mean(numeric_scores),
                    "has_numeric_phenotype": bool(numeric_scores),
                    "phenotype_status": "numeric_available" if numeric_scores else "numeric_pending",
                }
            )
        return {
            **state,
            "protocol_phenotype_rows": rows,
            "step_log": _append_step(state, "protocol_phenotype_joiner"),
        }

    def _node_protocol_phenotype_association_analyzer(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        phenotype_score_by_protocol = {
            row["protocol_id"]: row["mean_numeric_phenotype_score"]
            for row in state.get("protocol_phenotype_rows", [])
            if row.get("has_numeric_phenotype") and row.get("mean_numeric_phenotype_score") is not None
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
            if len(paired) < 3:
                continue
            xs = [pair[0] for pair in paired]
            ys = [pair[1] for pair in paired]
            association_rows.append(
                {
                    "feature": feature,
                    "n_protocols": len(paired),
                    "association_method": "pearson_correlation_screen",
                    "effect_estimate": _pearson(xs, ys),
                    "mean_feature_value": _mean(xs),
                    "mean_phenotype_score": _mean(ys),
                    "causal_interpretation": "association_only",
                    "confidence": "low" if len(paired) < 10 else "medium",
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
            evidence = _feature_evidence(feature, state.get("feature_provenance", {}))
            candidates.append(
                {
                    "candidate_id": f"candidate_{index:03d}",
                    "feature": feature,
                    "rank": index,
                    "effect_estimate": row.get("effect_estimate", ""),
                    "n_protocols": row.get("n_protocols", ""),
                    "mechanistic_relevance": _mechanistic_relevance(feature),
                    "evidence_status": "provenance_available" if evidence else "feature_level_only",
                    "evidence_summary": evidence[:500],
                    "recommendation_role": _recommendation_role(feature, row),
                    "confidence": _candidate_confidence(row, evidence),
                    "causal_interpretation": "hypothesis_requires_experimental_validation",
                }
            )
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
                "prediction_policy": "new protocol scoring is similarity-based and hypothesis-only",
                "nearest_protocols": [],
            }
        else:
            nearest = _nearest_protocols(new_features, state.get("protocol_feature_rows", []), limit=5)
            numeric_scores = {
                row["protocol_id"]: row.get("mean_numeric_phenotype_score")
                for row in state.get("protocol_phenotype_rows", [])
                if row.get("has_numeric_phenotype")
            }
            neighbor_scores = _neighbor_scores(nearest, numeric_scores)
            report = {
                "schema_version": "counterfactual-protocol-prediction-v1",
                "status": "similarity_scored",
                "prediction_policy": "relative ranking only; not causal proof",
                "predicted_relative_maturity": _relative_label(neighbor_scores),
                "confidence": "low" if len(neighbor_scores) < 3 else "medium",
                "nearest_protocols": nearest,
                "neighbor_mean_phenotype_score": _mean(neighbor_scores),
                "uncertainty_drivers": _uncertainty_drivers(state, len(neighbor_scores)),
            }
        return {
            **state,
            "prediction_report": report,
            "step_log": _append_step(state, "counterfactual_protocol_scorer"),
        }

    def _node_optimized_protocol_generator(self, state: ProtocolOptimizingState) -> ProtocolOptimizingState:
        recommendations = _build_recommendations(state.get("candidate_feature_rows", []), state.get("objective", {}))
        proposal = {
            "schema_version": "optimized-protocol-proposal-v1",
            "objective": state.get("objective", {}),
            "recommendation_policy": "associations_are_hypotheses_not_causal_proof",
            "summary": {
                "n_candidate_features": len(state.get("candidate_feature_rows", [])),
                "n_recommendations": len(recommendations),
                "prediction_status": state.get("prediction_report", {}).get("status", "not_requested"),
            },
            "recommendations": recommendations,
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
        }
        return {
            **state,
            "protocol_proposal": proposal,
            "validation_plan": validation_plan,
            "step_log": _append_step(state, "optimized_protocol_generator"),
        }

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
        write_json(outputs["counterfactual_protocol_predictions"], state.get("prediction_report", {}))
        write_json(outputs["proposed_improved_protocol_json"], state.get("protocol_proposal", {}))
        write_json(outputs["evidence_and_risk_report"], state.get("risk_report", {}))
        write_json(outputs["experimental_validation_plan_json"], state.get("validation_plan", {}))
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
            "counterfactual_protocol_predictions": self.output_dir / "counterfactual_protocol_predictions.json",
            "proposed_improved_protocol_md": self.output_dir / "proposed_improved_protocol.md",
            "proposed_improved_protocol_json": self.output_dir / "proposed_improved_protocol.json",
            "factor_recommendation_table": self.output_dir / "factor_recommendation_table.csv",
            "evidence_and_risk_report": self.output_dir / "evidence_and_risk_report.json",
            "experimental_validation_plan_md": self.output_dir / "experimental_validation_plan.md",
            "experimental_validation_plan_json": self.output_dir / "experimental_validation_plan.json",
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
    for column in ("endpoint", "gene_program", "marker", "cell_type", "lineage", "regulon", "trajectory"):
        if row.get(column):
            return f"{table_name}:{row[column]}"
    return table_name


def _endpoint_direction(table_name: str, endpoint: str) -> str:
    lowered = endpoint.lower()
    if any(term in lowered for term in ("stress", "hypoxia", "proliferation", "immature", "off_target")):
        return "lower_is_better"
    return "higher_is_better"


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if value not in ("", None)}


def _candidate_protocol_features(rows: list[dict[str, Any]]) -> list[str]:
    excluded = {"protocol_id", "paper_id", "dataset_id", "protocol_name", "comparison_ready"}
    features: list[str] = []
    for row in rows:
        for key in row:
            if key in excluded or key in features:
                continue
            if any(_to_float(other.get(key)) is not None for other in rows):
                features.append(key)
    return features


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
    raw = provenance.get(feature) or provenance.get("features", {}).get(feature)
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
    return "candidate_to_add_or_increase" if effect > 0 else "candidate_to_reduce_or_avoid"


def _candidate_confidence(row: dict[str, Any], evidence: str) -> str:
    n_protocols = int(_to_float(row.get("n_protocols")) or 0)
    if n_protocols >= 20 and evidence:
        return "medium"
    return "low"


def _nearest_protocols(new_features: dict[str, Any], rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    new_numeric = _numeric_items(new_features)
    for row in rows:
        protocol_id = str(row.get("protocol_id") or "")
        if not protocol_id:
            continue
        row_numeric = _numeric_items(row)
        shared = [key for key in new_numeric if key in row_numeric]
        if not shared:
            continue
        distance = sqrt(sum((new_numeric[key] - row_numeric[key]) ** 2 for key in shared))
        similarity = 1.0 / (1.0 + distance)
        scored.append({"protocol_id": protocol_id, "similarity": similarity, "n_shared_features": len(shared)})
    scored.sort(key=lambda row: float(row["similarity"]), reverse=True)
    return scored[:limit]


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
        return "likely_higher_than_baseline_neighbors"
    if mean_score <= 0.33:
        return "likely_lower_than_baseline_neighbors"
    return "similar_or_uncertain_relative_to_neighbors"


def _build_recommendations(candidates: list[dict[str, Any]], objective: dict[str, Any]) -> list[dict[str, Any]]:
    if not candidates:
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
    for index, candidate in enumerate(candidates[:10], start=1):
        role = candidate.get("recommendation_role", "prioritize_for_review")
        feature = candidate.get("feature", "")
        recommendations.append(
            {
                "recommendation_id": f"rec_{index:03d}",
                "target": objective.get("primary", "increase_beta_like_maturity"),
                "candidate_feature": feature,
                "proposed_change": _change_text(str(feature), str(role)),
                "source_basis": candidate.get("evidence_summary", "") or "ranked from protocol feature representation",
                "mechanistic_relevance": candidate.get("mechanistic_relevance", ""),
                "effect_estimate": candidate.get("effect_estimate", ""),
                "confidence": candidate.get("confidence", "low"),
                "risk": "Feature may be confounded with other protocol differences; validate in controlled experiments.",
                "requires_experimental_validation": "true",
            }
        )
    return recommendations


def _change_text(feature: str, role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return f"Consider adding, preserving, or increasing the protocol module represented by `{feature}`."
    if role == "candidate_to_reduce_or_avoid":
        return f"Consider reducing, removing, or delaying the protocol module represented by `{feature}`."
    return f"Review `{feature}` as a candidate optimization variable before proposing a concrete protocol edit."


def _render_protocol_markdown(protocol_doc: dict[str, Any]) -> str:
    lines = [
        "# Proposed Improved Pancreatic / Islet Organoid Protocol",
        "",
        "## Policy",
        "",
        "Associations are optimization hypotheses, not causal proof. Experimental validation is required.",
        "",
        "## Objective",
        "",
        f"- Primary: `{protocol_doc.get('objective', {}).get('primary', 'increase_beta_like_maturity')}`",
        "",
        "## Recommendations",
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
