from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, TypedDict

from protocol_optimization_agent.analysis.protocol._shared import (
    DEFAULT_ALLOWED_EVENT_STATUSES,
    KEY_FACTOR_TYPES_REQUIRING_EVIDENCE,
    _build_analysis_events,
    _build_temporal_annotations,
    _has_text,
    _has_unparsed_numeric_field,
    _is_accepted_protocol,
    _protocol_confidence_score,
    _validation_flag_names,
)
from protocol_optimization_agent.analysis.protocol.audit import audit_protocol_analysis_outputs
from protocol_optimization_agent.analysis.protocol.comparison import (
    _build_protocol_difference_report,
    _build_protocol_embeddings_and_clusters,
    _build_protocol_relationships,
)
from protocol_optimization_agent.analysis.protocol.feature_compiler import (
    _build_predefined_quantification_registry,
    _build_protocol_feature_matrix,
    _build_stage_and_factor_summary,
)
from protocol_optimization_agent.analysis.protocol.feature_discovery import discover_and_compute_features
from protocol_optimization_agent.analysis.protocol.graph_builder import (
    _build_protocol_knowledge_graph,
    _build_protocol_workflow_graphs,
    _build_standardized_protocol_representations,
)
from protocol_optimization_agent.analysis.protocol.normalization import normalize_factor_events
from protocol_optimization_agent.analysis.protocol.semantic_annotation import annotate_protocol_semantics
from protocol_optimization_agent.analysis.protocol.validation import (
    _build_field_quantification_map,
    _build_lossless_protocol_representation_audit,
    _build_matrix_release_gate_report,
    _build_quantification_coverage_report,
    _build_source_identity_report,
    _validate_analysis_inputs,
)
from protocol_optimization_agent.runtime.archive import audited_agent_run, audited_operation
from protocol_optimization_agent.runtime.io import write_csv_dicts, write_json, write_jsonl
from protocol_optimization_agent.runtime.llm_client import LLMClient, OfflineLLMClient


class ProtocolAnalysisState(TypedDict, total=False):
    protocol_records: list[dict[str, Any]]
    factor_events: list[dict[str, Any]]
    source_identity_rows: list[dict[str, Any]]
    schema_validation_report: dict[str, Any]
    protocol_selection: dict[str, Any]
    accepted_protocol_ids: set[str]
    filtered_events: list[dict[str, Any]]
    event_filter_report: dict[str, Any]
    source_report: dict[str, Any]
    temporal_annotations: list[dict[str, Any]]
    normalized_events: list[dict[str, Any]]
    normalization_report: dict[str, Any]
    semantic_annotations: list[dict[str, Any]]
    strategy_summary: dict[str, Any]
    semantic_report: dict[str, Any]
    analysis_events: list[dict[str, Any]]
    stage_rows: list[dict[str, Any]]
    factor_report: dict[str, Any]
    difference_report: dict[str, Any]
    feature_rows: list[dict[str, Any]]
    raw_feature_rows: list[dict[str, Any]]
    candidate_feature_rows: list[dict[str, Any]]
    feature_provenance: dict[str, Any]
    feature_cell_provenance: dict[str, Any]
    feature_discovery_audit_report: dict[str, Any]
    quantification_coverage_report: dict[str, Any]
    lossless_protocol_representation_audit: dict[str, Any]
    field_quantification_map: dict[str, Any]
    matrix_release_gate_report: dict[str, Any]
    predefined_feature_registry: dict[str, Any]
    approved_feature_registry: dict[str, Any]
    similarity_rows: list[dict[str, Any]]
    relationship_report: dict[str, Any]
    workflow_graphs: dict[str, Any]
    knowledge_graph: dict[str, Any]
    embeddings: list[dict[str, Any]]
    cluster_report: dict[str, Any]
    standardized_protocols: list[dict[str, Any]]
    llm_audit_report: dict[str, Any]
    outputs: dict[str, Path]
    step_log: list[dict[str, Any]]


class ProtocolAnalysisAgent:
    """Build evidence-grounded protocol features and comparisons from Agent1 outputs."""

    def __init__(
        self,
        output_dir: Path,
        quality_filters: dict[str, Any],
        llm_client: LLMClient | None = None,
    ) -> None:
        self.output_dir = output_dir
        self.quality_filters = quality_filters
        self.llm_client = llm_client or OfflineLLMClient()

    @audited_agent_run
    def run(
        self,
        protocol_records: list[dict[str, Any]],
        factor_events: list[dict[str, Any]],
        source_identity_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Path]:
        initial_state: ProtocolAnalysisState = {
            "protocol_records": protocol_records,
            "factor_events": factor_events,
            "source_identity_rows": source_identity_rows or [],
            "step_log": [],
        }
        final_state = self.build_langgraph().invoke(initial_state)
        return final_state["outputs"]

    def build_langgraph(self, compile_graph: bool = True) -> Any:
        """Build the executable LangGraph for this agent."""
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as exc:
            raise RuntimeError("LangGraph is required to run ProtocolAnalysisAgent.") from exc

        graph = StateGraph(ProtocolAnalysisState)
        graph.add_node("protocol_input_quality_gate", self._node_protocol_input_quality_gate)
        graph.add_node("protocol_schema_validator", self._node_protocol_schema_validator)
        graph.add_node("stage_timeline_builder", self._node_stage_timeline_builder)
        graph.add_node("factor_event_normalizer", self._node_factor_event_normalizer)
        graph.add_node("protocol_semantic_interpreter", self._node_protocol_semantic_interpreter)
        graph.add_node("cross_protocol_feature_matrix", self._node_cross_protocol_feature_matrix)
        graph.add_node("feature_discovery", self._node_feature_discovery)
        graph.add_node("protocol_relationship_analyzer", self._node_protocol_relationship_analyzer)
        graph.add_node("protocol_difference_report", self._node_protocol_difference_report)
        graph.add_node("quality_and_evidence_auditor", self._node_quality_and_evidence_auditor)
        graph.add_node("export_results", self._node_export_results)

        graph.add_edge(START, "protocol_input_quality_gate")
        graph.add_edge("protocol_input_quality_gate", "protocol_schema_validator")
        graph.add_edge("protocol_schema_validator", "stage_timeline_builder")
        graph.add_edge("stage_timeline_builder", "factor_event_normalizer")
        graph.add_edge("factor_event_normalizer", "protocol_semantic_interpreter")
        graph.add_edge("protocol_semantic_interpreter", "cross_protocol_feature_matrix")
        graph.add_edge("cross_protocol_feature_matrix", "feature_discovery")
        graph.add_edge("feature_discovery", "protocol_relationship_analyzer")
        graph.add_edge("protocol_relationship_analyzer", "protocol_difference_report")
        graph.add_edge("protocol_difference_report", "quality_and_evidence_auditor")
        graph.add_edge("quality_and_evidence_auditor", "export_results")
        graph.add_edge("export_results", END)
        return graph.compile() if compile_graph else graph

    def _node_protocol_input_quality_gate(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        protocol_selection = self._select_protocols(state.get("protocol_records", []))
        accepted_protocol_ids = {
            row["protocol_id"] for row in protocol_selection["protocols"] if row["passed_quality_filter"]
        }
        return {
            **state,
            "protocol_selection": protocol_selection,
            "accepted_protocol_ids": accepted_protocol_ids,
            "step_log": _append_step(state, "protocol_input_quality_gate"),
        }

    @audited_operation
    def _node_protocol_schema_validator(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        protocol_records, factor_events, schema_validation_report = _validate_analysis_inputs(
            state.get("protocol_records", []),
            state.get("factor_events", []),
            allow_invalid_events=bool(self.quality_filters.get("allow_schema_invalid_events", False)),
        )
        protocol_selection = self._select_protocols(protocol_records)
        accepted_protocol_ids = {
            row["protocol_id"] for row in protocol_selection["protocols"] if row["passed_quality_filter"]
        }
        filtered_events, event_filter_report = self._filter_factor_events(factor_events, accepted_protocol_ids)
        source_report = _build_source_identity_report(protocol_records, state.get("source_identity_rows", []))
        return {
            **state,
            "protocol_records": protocol_records,
            "factor_events": factor_events,
            "schema_validation_report": schema_validation_report,
            "protocol_selection": protocol_selection,
            "accepted_protocol_ids": accepted_protocol_ids,
            "filtered_events": filtered_events,
            "event_filter_report": event_filter_report,
            "source_report": source_report,
            "step_log": _append_step(state, "protocol_schema_validator"),
        }

    @audited_operation
    def _node_stage_timeline_builder(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        temporal_annotations = _build_temporal_annotations(
            state.get("protocol_records", []),
            state.get("accepted_protocol_ids", set()),
        )
        return {
            **state,
            "temporal_annotations": temporal_annotations,
            "step_log": _append_step(state, "stage_timeline_builder"),
        }

    @audited_operation
    def _node_factor_event_normalizer(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        normalized_events, normalization_report = normalize_factor_events(
            state.get("filtered_events", []),
            llm_client=self.llm_client,
            max_llm_events=int(self.quality_filters.get("max_llm_normalization_events", 0)),
        )
        analysis_events = _build_analysis_events(state.get("filtered_events", []), normalized_events)
        return {
            **state,
            "normalized_events": normalized_events,
            "normalization_report": normalization_report,
            "analysis_events": analysis_events,
            "step_log": _append_step(state, "factor_event_normalizer"),
        }

    @audited_operation
    def _node_protocol_semantic_interpreter(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        semantic_annotations, strategy_summary, semantic_report = annotate_protocol_semantics(
            state.get("protocol_records", []),
            state.get("normalized_events", []),
            state.get("accepted_protocol_ids", set()),
            llm_client=self.llm_client,
            max_llm_stages=int(self.quality_filters.get("max_llm_semantic_stages", 0)),
        )
        return {
            **state,
            "semantic_annotations": semantic_annotations,
            "strategy_summary": strategy_summary,
            "semantic_report": semantic_report,
            "step_log": _append_step(state, "protocol_semantic_interpreter"),
        }

    @audited_operation
    def _node_cross_protocol_feature_matrix(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        stage_rows, factor_report = _build_stage_and_factor_summary(state.get("analysis_events", []))
        feature_rows, feature_provenance = _build_protocol_feature_matrix(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            state.get("source_report", {}),
            state.get("accepted_protocol_ids", set()),
            state.get("semantic_annotations", []),
        )
        quantification_coverage_report = _build_quantification_coverage_report(
            state.get("analysis_events", []),
            feature_rows,
            feature_provenance,
        )
        predefined_feature_registry = _build_predefined_quantification_registry(state.get("analysis_events", []))
        workflow_graphs = _build_protocol_workflow_graphs(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            state.get("source_report", {}),
            state.get("accepted_protocol_ids", set()),
        )
        knowledge_graph = _build_protocol_knowledge_graph(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            state.get("source_report", {}),
            state.get("accepted_protocol_ids", set()),
            state.get("semantic_annotations", []),
        )
        standardized_protocols = _build_standardized_protocol_representations(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            workflow_graphs,
            state.get("accepted_protocol_ids", set()),
        )
        lossless_audit = _build_lossless_protocol_representation_audit(
            state.get("protocol_records", []),
            state.get("factor_events", []),
            state.get("analysis_events", []),
            state.get("event_filter_report", {}),
            feature_provenance,
            workflow_graphs,
            state.get("accepted_protocol_ids", set()),
        )
        field_quantification_map = _build_field_quantification_map(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            feature_provenance,
            workflow_graphs,
            state.get("accepted_protocol_ids", set()),
        )
        matrix_release_gate_report = _build_matrix_release_gate_report(
            quantification_coverage_report,
            lossless_audit,
            field_quantification_map,
            self.quality_filters,
        )
        return {
            **state,
            "stage_rows": stage_rows,
            "factor_report": factor_report,
            "feature_rows": feature_rows,
            "raw_feature_rows": feature_rows,
            "feature_provenance": feature_provenance,
            "quantification_coverage_report": quantification_coverage_report,
            "predefined_feature_registry": predefined_feature_registry,
            "workflow_graphs": workflow_graphs,
            "knowledge_graph": knowledge_graph,
            "standardized_protocols": standardized_protocols,
            "lossless_protocol_representation_audit": lossless_audit,
            "field_quantification_map": field_quantification_map,
            "matrix_release_gate_report": matrix_release_gate_report,
            "step_log": _append_step(state, "cross_protocol_feature_matrix"),
        }

    @audited_operation
    def _node_feature_discovery(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        final_rows, candidate_rows, cell_provenance, approved_registry, discovery_report = (
            discover_and_compute_features(
                state.get("protocol_records", []),
                state.get("analysis_events", []),
                state.get("feature_rows", []),
                state.get("feature_provenance", {}),
                state.get("accepted_protocol_ids", set()),
                self.quality_filters,
                llm_client=self.llm_client,
            )
        )
        final_feature_provenance = cell_provenance.get("final_matrix", {}).get(
            "feature_provenance", state.get("feature_provenance", {})
        )
        quantification_coverage_report = _build_quantification_coverage_report(
            state.get("analysis_events", []),
            final_rows,
            final_feature_provenance,
        )
        lossless_audit = _build_lossless_protocol_representation_audit(
            state.get("protocol_records", []),
            state.get("factor_events", []),
            state.get("analysis_events", []),
            state.get("event_filter_report", {}),
            final_feature_provenance,
            state.get("workflow_graphs", {}),
            state.get("accepted_protocol_ids", set()),
        )
        field_quantification_map = _build_field_quantification_map(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            final_feature_provenance,
            state.get("workflow_graphs", {}),
            state.get("accepted_protocol_ids", set()),
        )
        matrix_release_gate_report = _build_matrix_release_gate_report(
            quantification_coverage_report,
            lossless_audit,
            field_quantification_map,
            self.quality_filters,
        )
        return {
            **state,
            "feature_rows": final_rows,
            "candidate_feature_rows": candidate_rows,
            "feature_provenance": final_feature_provenance,
            "feature_cell_provenance": cell_provenance,
            "approved_feature_registry": approved_registry,
            "feature_discovery_audit_report": discovery_report,
            "quantification_coverage_report": quantification_coverage_report,
            "lossless_protocol_representation_audit": lossless_audit,
            "field_quantification_map": field_quantification_map,
            "matrix_release_gate_report": matrix_release_gate_report,
            "predefined_feature_registry": state.get("predefined_feature_registry", {}),
            "step_log": _append_step(state, "feature_discovery"),
        }

    @audited_operation
    def _node_protocol_relationship_analyzer(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        similarity_rows, relationship_report = _build_protocol_relationships(state.get("feature_rows", []))
        embeddings, cluster_report = _build_protocol_embeddings_and_clusters(state.get("feature_rows", []))
        return {
            **state,
            "similarity_rows": similarity_rows,
            "relationship_report": relationship_report,
            "embeddings": embeddings,
            "cluster_report": cluster_report,
            "step_log": _append_step(state, "protocol_relationship_analyzer"),
        }

    @audited_operation
    def _node_protocol_difference_report(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        difference_report = _build_protocol_difference_report(
            state.get("feature_rows", []),
            state.get("relationship_report", {}),
            state.get("strategy_summary", {}),
            state.get("factor_report", {}),
        )
        return {
            **state,
            "difference_report": difference_report,
            "step_log": _append_step(state, "protocol_difference_report"),
        }

    @audited_operation
    def _node_quality_and_evidence_auditor(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        llm_audit_report = audit_protocol_analysis_outputs(
            state.get("protocol_records", []),
            state.get("filtered_events", []),
            state.get("normalized_events", []),
            state.get("semantic_annotations", []),
        )
        llm_audit_report["normalization_report"] = state.get("normalization_report", {})
        llm_audit_report["semantic_report"] = state.get("semantic_report", {})
        return {
            **state,
            "llm_audit_report": llm_audit_report,
            "step_log": _append_step(state, "quality_and_evidence_auditor"),
        }

    @audited_operation
    def _node_export_results(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        outputs = self._output_paths()
        write_json(outputs["protocol_factor_difference_report"], state.get("difference_report", {}))
        write_csv_dicts(outputs["stage_factor_matrix_or_events_summary"], state.get("stage_rows", []))
        write_json(outputs["schema_validation_report"], state.get("schema_validation_report", {}))
        write_json(
            outputs["protocol_quality_filter_report"],
            {
                "quality_filter": self.quality_filters,
                "protocols": state.get("protocol_selection", {}).get("protocols", []),
                "summary": state.get("protocol_selection", {}).get("summary", {}),
                "event_filter_report": state.get("event_filter_report", {}),
                "normalization_report": state.get("normalization_report", {}),
                "semantic_report": state.get("semantic_report", {}),
                "execution_engine": "langgraph",
                "step_log": state.get("step_log", []),
            },
        )
        write_csv_dicts(outputs["filtered_protocol_factor_events"], state.get("filtered_events", []))
        write_jsonl(outputs["normalized_factor_events"], state.get("normalized_events", []))
        write_jsonl(outputs["semantic_stage_annotations"], state.get("semantic_annotations", []))
        write_json(outputs["protocol_strategy_summary"], state.get("strategy_summary", {}))
        write_json(outputs["llm_audit_report"], state.get("llm_audit_report", {}))
        write_jsonl(outputs["standardized_protocol_representations"], state.get("standardized_protocols", []))
        write_json(outputs["protocol_workflow_graphs"], state.get("workflow_graphs", {}))
        write_csv_dicts(outputs["protocol_feature_matrix_raw"], state.get("raw_feature_rows", []))
        write_json(
            outputs["base_feature_registry"],
            state.get("feature_discovery_audit_report", {}).get("base_feature_registry", {}),
        )
        write_json(
            outputs["llm_proposed_feature_registry"],
            state.get("feature_discovery_audit_report", {}).get("llm_proposed_feature_registry", {}),
        )
        write_json(outputs["approved_feature_registry"], state.get("approved_feature_registry", {}))
        write_json(outputs["predefined_feature_registry"], state.get("predefined_feature_registry", {}))
        write_csv_dicts(outputs["protocol_candidate_feature_matrix"], state.get("candidate_feature_rows", []))
        write_json(outputs["feature_cell_provenance"], state.get("feature_cell_provenance", {}))
        write_json(outputs["feature_discovery_audit_report"], state.get("feature_discovery_audit_report", {}))
        write_json(
            outputs["feature_spec_validation_report"],
            state.get("feature_discovery_audit_report", {}).get("feature_spec_validation_report", {}),
        )
        write_json(outputs["protocol_feature_provenance"], state.get("feature_provenance", {}))
        write_json(outputs["quantification_coverage_report"], state.get("quantification_coverage_report", {}))
        write_json(
            outputs["lossless_protocol_representation_audit"],
            state.get("lossless_protocol_representation_audit", {}),
        )
        write_json(outputs["field_quantification_map"], state.get("field_quantification_map", {}))
        release_gate = state.get("matrix_release_gate_report", {})
        write_json(outputs["matrix_release_gate_report"], release_gate)
        write_csv_dicts(
            outputs["protocol_feature_matrix"],
            state.get("feature_rows", []) if release_gate.get("status") != "blocked" else [],
        )
        write_csv_dicts(outputs["protocol_similarity_matrix"], state.get("similarity_rows", []))
        write_json(outputs["protocol_relationship_report"], state.get("relationship_report", {}))
        write_csv_dicts(outputs["protocol_embedding"], state.get("embeddings", []))
        write_json(outputs["protocol_cluster_report"], state.get("cluster_report", {}))
        write_json(outputs["protocol_knowledge_graph"], state.get("knowledge_graph", {}))
        write_json(outputs["source_identity_check_report"], state.get("source_report", {}))
        return {**state, "outputs": outputs, "step_log": _append_step(state, "export_results")}

    def _output_paths(self) -> dict[str, Path]:
        return {
            "protocol_factor_difference_report": self.output_dir / "protocol_factor_difference_report.json",
            "stage_factor_matrix_or_events_summary": self.output_dir / "stage_factor_matrix_or_events_summary.csv",
            "protocol_quality_filter_report": self.output_dir / "protocol_quality_filter_report.json",
            "schema_validation_report": self.output_dir / "schema_validation_report.json",
            "filtered_protocol_factor_events": self.output_dir / "filtered_protocol_factor_events.csv",
            "normalized_factor_events": self.output_dir / "normalized_factor_events.jsonl",
            "semantic_stage_annotations": self.output_dir / "semantic_stage_annotations.jsonl",
            "protocol_strategy_summary": self.output_dir / "protocol_strategy_summary.json",
            "llm_audit_report": self.output_dir / "llm_audit_report.json",
            "standardized_protocol_representations": self.output_dir / "standardized_protocol_representations.jsonl",
            "protocol_workflow_graphs": self.output_dir / "protocol_workflow_graphs.json",
            "protocol_feature_matrix_raw": self.output_dir / "protocol_feature_matrix_raw.csv",
            "base_feature_registry": self.output_dir / "base_feature_registry.json",
            "llm_proposed_feature_registry": self.output_dir / "llm_proposed_feature_registry.json",
            "approved_feature_registry": self.output_dir / "approved_feature_registry.json",
            "predefined_feature_registry": self.output_dir / "predefined_feature_registry.json",
            "protocol_candidate_feature_matrix": self.output_dir / "protocol_candidate_feature_matrix.csv",
            "feature_cell_provenance": self.output_dir / "feature_cell_provenance.json",
            "feature_discovery_audit_report": self.output_dir / "feature_discovery_audit_report.json",
            "feature_spec_validation_report": self.output_dir / "feature_spec_validation_report.json",
            "protocol_feature_matrix": self.output_dir / "protocol_feature_matrix.csv",
            "protocol_feature_provenance": self.output_dir / "protocol_feature_provenance.json",
            "quantification_coverage_report": self.output_dir / "quantification_coverage_report.json",
            "lossless_protocol_representation_audit": self.output_dir / "lossless_protocol_representation_audit.json",
            "field_quantification_map": self.output_dir / "field_quantification_map.json",
            "matrix_release_gate_report": self.output_dir / "matrix_release_gate_report.json",
            "protocol_similarity_matrix": self.output_dir / "protocol_similarity_matrix.csv",
            "protocol_relationship_report": self.output_dir / "protocol_relationship_report.json",
            "protocol_embedding": self.output_dir / "protocol_embedding.csv",
            "protocol_cluster_report": self.output_dir / "protocol_cluster_report.json",
            "protocol_knowledge_graph": self.output_dir / "protocol_knowledge_graph.json",
            "source_identity_check_report": self.output_dir / "source_identity_check_report.json",
        }

    def _select_protocols(self, protocol_records: list[dict[str, Any]]) -> dict[str, Any]:
        min_conf = float(self.quality_filters.get("min_protocol_confidence", 0.0))
        require_ready = bool(self.quality_filters.get("require_comparison_ready", True))
        excluded_flags = set(self.quality_filters.get("exclude_validation_flags", []))

        quality_rows: list[dict[str, Any]] = []
        for record in protocol_records:
            protocol = record.get("protocol") or {}
            protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or record.get("id") or "")
            confidence_score = _protocol_confidence_score(record)
            comparison_ready = bool(record.get("comparison_ready", True))
            validation_flags = _validation_flag_names(record.get("validation_flags"))
            excluded = sorted(validation_flags.intersection(excluded_flags))
            passed = (
                bool(protocol_id)
                and confidence_score >= min_conf
                and (comparison_ready or not require_ready)
                and not excluded
            )
            quality_rows.append(
                {
                    "protocol_id": protocol_id,
                    "paper_id": str(record.get("paper_id") or protocol.get("paper_id") or ""),
                    "protocol_name": str(protocol.get("protocol_name") or ""),
                    "confidence_score": round(confidence_score, 3),
                    "comparison_ready": comparison_ready,
                    "validation_flags": "|".join(sorted(validation_flags)),
                    "excluded_flags": "|".join(excluded),
                    "passed_quality_filter": passed,
                }
            )

        return {
            "protocols": quality_rows,
            "summary": {
                "n_protocol_records": len(protocol_records),
                "n_passed_quality_filter": sum(1 for row in quality_rows if row["passed_quality_filter"]),
                "require_comparison_ready": require_ready,
                "min_protocol_confidence": min_conf,
            },
        }

    def _filter_factor_events(
        self,
        factor_events: list[dict[str, Any]],
        accepted_protocol_ids: set[str],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        allowed_statuses = set(
            self.quality_filters.get("allowed_event_validation_statuses", DEFAULT_ALLOWED_EVENT_STATUSES)
        )
        reject_unparsed = bool(self.quality_filters.get("reject_unparsed_numeric_fields", True))
        require_quote = bool(self.quality_filters.get("require_evidence_quote_for_key_factors", True))

        kept: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        for event in factor_events:
            protocol_id = str(event.get("protocol_id") or "")
            if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
                rejected["protocol_failed_quality_filter"] += 1
                continue
            validation_status = str(event.get("validation_status") or "").strip()
            if allowed_statuses and validation_status not in allowed_statuses:
                rejected[f"validation_status:{validation_status or 'missing'}"] += 1
                continue
            if reject_unparsed and _has_unparsed_numeric_field(event):
                rejected["unparsed_numeric_field"] += 1
                continue
            factor_type = str(event.get("factor_type") or "").strip()
            if (
                require_quote
                and factor_type in KEY_FACTOR_TYPES_REQUIRING_EVIDENCE
                and not _has_text(event.get("evidence_quote"))
            ):
                rejected["missing_evidence_quote_for_key_factor"] += 1
                continue
            kept.append(dict(event))

        return kept, {
            "n_input_events": len(factor_events),
            "n_kept_events": len(kept),
            "n_rejected_events": sum(rejected.values()),
            "rejection_reasons": dict(rejected),
            "allowed_event_validation_statuses": sorted(allowed_statuses),
            "reject_unparsed_numeric_fields": reject_unparsed,
            "require_evidence_quote_for_key_factors": require_quote,
        }


def _append_step(state: ProtocolAnalysisState, node_name: str) -> list[dict[str, Any]]:
    log = list(state.get("step_log", []))
    log.append({"node": node_name, "status": "completed"})
    return log
