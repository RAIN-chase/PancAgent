from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any, TypedDict

from pydantic import ValidationError

from protocol_optimization_agent.audit import audit_protocol_analysis_outputs
from protocol_optimization_agent.feature_discovery import discover_and_compute_features
from protocol_optimization_agent.io import write_csv_dicts, write_json, write_jsonl
from protocol_optimization_agent.llm_client import LLMClient, OfflineLLMClient
from protocol_optimization_agent.normalization import normalize_factor_events
from protocol_optimization_agent.schemas import Confidence, FactorEvent, ProtocolRecord, StageRecord, TemporalAnnotation
from protocol_optimization_agent.semantic_annotation import annotate_protocol_semantics


REAGENT_FAMILIES: dict[str, tuple[str, ...]] = {
    "Activin": ("activin", "acta"),
    "CHIR": ("chir99021", "chir", "gsk3"),
    "KGF": ("kgf", "fgf7"),
    "RA": ("retinoic acid", "ttnpb"),
    "ALK5i": ("alk5", "sb431542", "repsox", "a83-01", "alk inhibitor"),
    "BMP": ("bmp4", "bmp"),
    "FGF": ("fgf10", "fgf2", "bfgf"),
    "EGF": ("egf",),
    "SANT": ("sant-1", "sant1"),
    "LDN": ("ldn", "ldn193189", "bmp inhibitor"),
    "T3": (" t3", "triiodothyronine"),
    "Heparin": ("heparin",),
    "Betacellulin": ("betacellulin",),
    "Nicotinamide": ("nicotinamide",),
    "GammaSecretaseInhibitor": ("gamma-secretase", "gamma secretase", "γ-secretase", "gsi", "ro4929097"),
    "ROCKi": ("y-27632", "rock", "rocki"),
    "Matrigel": ("matrigel",),
    "VitaminC": ("vitamin c", "vitc", "ascorbic"),
    "ZnSO4": ("znso4", "zinc"),
}

KEY_FACTOR_TYPES_REQUIRING_EVIDENCE = {
    "reagent",
    "matrix_or_coating",
    "medium_base",
    "culture_format",
    "operation",
    "aggregation",
    "mechanical_condition",
    "sorting",
    "oxygen_condition",
    "transplantation",
}

DEFAULT_ALLOWED_EVENT_STATUSES = {"evidence_supported"}


class ProtocolAnalysisState(TypedDict, total=False):
    protocol_records: list[dict[str, Any]]
    factor_events: list[dict[str, Any]]
    source_identity_rows: list[dict[str, Any]]
    protocol_database_path: str
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

    def run(
        self,
        protocol_records: list[dict[str, Any]],
        factor_events: list[dict[str, Any]],
        source_identity_rows: list[dict[str, Any]] | None = None,
        protocol_database_path: Path | None = None,
    ) -> dict[str, Path]:
        initial_state: ProtocolAnalysisState = {
            "protocol_records": protocol_records,
            "factor_events": factor_events,
            "source_identity_rows": source_identity_rows or [],
            "protocol_database_path": str(protocol_database_path) if protocol_database_path else "",
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

    def _node_cross_protocol_feature_matrix(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        stage_rows, factor_report = _build_stage_and_factor_summary(state.get("analysis_events", []))
        feature_rows, feature_provenance = _build_protocol_feature_matrix(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            state.get("source_report", {}),
            state.get("accepted_protocol_ids", set()),
            state.get("semantic_annotations", []),
        )
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
        return {
            **state,
            "stage_rows": stage_rows,
            "factor_report": factor_report,
            "feature_rows": feature_rows,
            "raw_feature_rows": feature_rows,
            "feature_provenance": feature_provenance,
            "workflow_graphs": workflow_graphs,
            "knowledge_graph": knowledge_graph,
            "standardized_protocols": standardized_protocols,
            "step_log": _append_step(state, "cross_protocol_feature_matrix"),
        }

    def _node_feature_discovery(self, state: ProtocolAnalysisState) -> ProtocolAnalysisState:
        final_rows, candidate_rows, cell_provenance, approved_registry, discovery_report = discover_and_compute_features(
            state.get("protocol_records", []),
            state.get("analysis_events", []),
            state.get("feature_rows", []),
            state.get("feature_provenance", {}),
            state.get("accepted_protocol_ids", set()),
            self.quality_filters,
            llm_client=self.llm_client,
        )
        return {
            **state,
            "feature_rows": final_rows,
            "candidate_feature_rows": candidate_rows,
            "feature_provenance": cell_provenance.get("final_matrix", {}).get("feature_provenance", state.get("feature_provenance", {})),
            "feature_cell_provenance": cell_provenance,
            "approved_feature_registry": approved_registry,
            "feature_discovery_audit_report": discovery_report,
            "step_log": _append_step(state, "feature_discovery"),
        }

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
                "protocol_database_path": state.get("protocol_database_path", ""),
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
        write_json(outputs["base_feature_registry"], state.get("feature_discovery_audit_report", {}).get("base_feature_registry", {}))
        write_json(outputs["llm_proposed_feature_registry"], state.get("feature_discovery_audit_report", {}).get("llm_proposed_feature_registry", {}))
        write_json(outputs["approved_feature_registry"], state.get("approved_feature_registry", {}))
        write_csv_dicts(outputs["protocol_candidate_feature_matrix"], state.get("candidate_feature_rows", []))
        write_json(outputs["feature_cell_provenance"], state.get("feature_cell_provenance", {}))
        write_json(outputs["feature_discovery_audit_report"], state.get("feature_discovery_audit_report", {}))
        write_csv_dicts(outputs["protocol_feature_matrix"], state.get("feature_rows", []))
        write_json(outputs["protocol_feature_provenance"], state.get("feature_provenance", {}))
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
            "protocol_candidate_feature_matrix": self.output_dir / "protocol_candidate_feature_matrix.csv",
            "feature_cell_provenance": self.output_dir / "feature_cell_provenance.json",
            "feature_discovery_audit_report": self.output_dir / "feature_discovery_audit_report.json",
            "protocol_feature_matrix": self.output_dir / "protocol_feature_matrix.csv",
            "protocol_feature_provenance": self.output_dir / "protocol_feature_provenance.json",
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
            passed = bool(protocol_id) and confidence_score >= min_conf and (comparison_ready or not require_ready) and not excluded
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
        allowed_statuses = set(self.quality_filters.get("allowed_event_validation_statuses", DEFAULT_ALLOWED_EVENT_STATUSES))
        reject_unparsed = bool(self.quality_filters.get("reject_unparsed_numeric_fields", True))
        require_quote = bool(self.quality_filters.get("require_evidence_quote_for_key_factors", True))

        kept: list[dict[str, Any]] = []
        rejected = Counter()
        for event in factor_events:
            protocol_id = str(event.get("protocol_id") or "")
            if accepted_protocol_ids and protocol_id not in accepted_protocol_ids:
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
            if require_quote and factor_type in KEY_FACTOR_TYPES_REQUIRING_EVIDENCE and not _has_text(event.get("evidence_quote")):
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


def _validate_analysis_inputs(
    protocol_records: list[dict[str, Any]],
    factor_events: list[dict[str, Any]],
    allow_invalid_events: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    valid_protocol_records: list[dict[str, Any]] = []
    valid_factor_events: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for index, record in enumerate(protocol_records):
        protocol_id = str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or "")
        try:
            ProtocolRecord.model_validate(record)
        except ValidationError as exc:
            findings.append(_schema_finding("protocol_record", protocol_id, None, index, exc))
            continue

        sanitized = dict(record)
        valid_stages = []
        for stage_index, stage in enumerate(record.get("stages") or []):
            stage_payload = _stage_schema_payload(stage)
            stage_payload.setdefault("protocol_id", protocol_id)
            try:
                StageRecord.model_validate(stage_payload)
                valid_stages.append(stage)
            except ValidationError as exc:
                findings.append(_schema_finding("stage_record", protocol_id, str(stage.get("stage_id") or ""), stage_index, exc))
        sanitized["stages"] = valid_stages
        valid_protocol_records.append(sanitized)

    for index, event in enumerate(factor_events):
        event_with_schema_status = dict(event)
        try:
            FactorEvent.model_validate(_factor_event_schema_payload(event))
            event_with_schema_status["schema_status"] = "valid"
        except ValidationError as exc:
            findings.append(
                _schema_finding(
                    "factor_event",
                    str(event.get("protocol_id") or ""),
                    str(event.get("stage_id") or ""),
                    index,
                    exc,
                    factor_name=_factor_name(event),
                )
            )
            if not allow_invalid_events:
                continue
            event_with_schema_status["schema_status"] = "accepted_with_schema_findings"
        valid_factor_events.append(event_with_schema_status)

    return valid_protocol_records, valid_factor_events, {
        "summary": {
            "n_input_protocol_records": len(protocol_records),
            "n_valid_protocol_records": len(valid_protocol_records),
            "n_input_factor_events": len(factor_events),
            "n_valid_factor_events": len(valid_factor_events),
            "n_rejected_factor_events": len(factor_events) - len(valid_factor_events),
            "n_findings": len(findings),
            "allow_invalid_events": allow_invalid_events,
            "status": "passed" if not findings else "completed_with_schema_findings",
        },
        "findings": findings,
    }


def _append_step(state: ProtocolAnalysisState, node_name: str) -> list[dict[str, Any]]:
    log = list(state.get("step_log", []))
    log.append({"node": node_name, "status": "completed"})
    return log


def _build_temporal_annotations(
    protocol_records: list[dict[str, Any]],
    accepted_protocol_ids: set[str],
) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not protocol_id or (accepted_protocol_ids and protocol_id not in accepted_protocol_ids):
            continue
        for stage in record.get("stages") or []:
            stage_id = str(stage.get("stage_id") or "")
            if not stage_id:
                continue
            day_start = _safe_float(stage.get("day_start"), None)
            day_end = _safe_float(stage.get("day_end"), None)
            duration_days = _safe_float(stage.get("duration_days"), None)
            notes = []
            if duration_days is None and day_start is not None and day_end is not None:
                duration_days = max(day_end - day_start, 0.0)
                notes.append("duration_inferred_from_day_start_end")
            confidence = Confidence.HIGH if day_start is not None and day_end is not None else Confidence.LOW
            payload = {
                "protocol_id": protocol_id,
                "stage_id": stage_id,
                "day_start": day_start,
                "day_end": day_end,
                "duration_days": duration_days,
                "temporal_confidence": confidence,
                "evidence_quote": stage.get("evidence_quote") or "",
                "source": "rule" if confidence != Confidence.LOW else "fallback",
                "validation_notes": notes if notes else ([] if confidence != Confidence.LOW else ["missing_complete_stage_time"]),
            }
            annotations.append(TemporalAnnotation.model_validate(payload).model_dump(mode="json"))
    return annotations


def _build_protocol_difference_report(
    feature_rows: list[dict[str, Any]],
    relationship_report: dict[str, Any],
    strategy_summary: dict[str, Any],
    factor_report: dict[str, Any],
) -> dict[str, Any]:
    pairwise_summaries = []
    rows_by_protocol = {str(row.get("protocol_id") or ""): row for row in feature_rows}
    for relationship in relationship_report.get("pairwise_relationships", []):
        protocol_a = str(relationship.get("protocol_id_a") or "")
        protocol_b = str(relationship.get("protocol_id_b") or "")
        left = rows_by_protocol.get(protocol_a, {})
        right = rows_by_protocol.get(protocol_b, {})
        differences = _ranked_feature_differences(left, right, max_features=30)
        pairwise_summaries.append(
            {
                "protocol_id_a": protocol_a,
                "protocol_id_b": protocol_b,
                "binary_feature_jaccard_similarity": relationship.get("binary_feature_jaccard_similarity"),
                "difference_groups": _group_protocol_feature_differences(differences),
                "shared_binary_features": relationship.get("shared_binary_features", [])[:30],
                "protocol_a_specific_features": relationship.get("protocol_a_specific_features", [])[:30],
                "protocol_b_specific_features": relationship.get("protocol_b_specific_features", [])[:30],
            }
        )
    return {
        "schema_version": "protocol-factor-difference-report-v2",
        "summary": {
            "n_protocols": len(feature_rows),
            "n_pairwise_comparisons": len(pairwise_summaries),
            "n_unique_factors": factor_report.get("summary", {}).get("n_unique_factors", 0),
            "source": "computed_from_normalized_semantic_feature_matrix",
        },
        "strategy_summary": strategy_summary,
        "pairwise_differences": pairwise_summaries,
        "factor_index": factor_report.get("factor_index", {}),
    }


def _ranked_feature_differences(left: dict[str, Any], right: dict[str, Any], max_features: int) -> list[dict[str, Any]]:
    differences = []
    for key in sorted(set(left).union(right)):
        if key in _metadata_columns():
            continue
        left_value = left.get(key, "")
        right_value = right.get(key, "")
        if left_value == right_value:
            continue
        group = _feature_group(key)
        differences.append(
            {
                "feature": key,
                "protocol_a_value": left_value,
                "protocol_b_value": right_value,
                "difference_group": group,
                "rank": _feature_group_rank(group),
            }
        )
    return sorted(differences, key=lambda item: (item["rank"], item["feature"]))[:max_features]


def _group_protocol_feature_differences(differences: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for difference in differences:
        payload = dict(difference)
        payload.pop("rank", None)
        group = str(payload.pop("difference_group"))
        grouped[group].append(payload)
    ordered_groups = ("strategy_module", "culture_system", "core_signaling_family", "workflow", "timing_or_exposure", "concentration", "audit_or_confidence", "other")
    return {group: grouped[group] for group in ordered_groups if grouped.get(group)}


def _feature_group(feature: str) -> str:
    if feature.startswith("semantic_module_") or feature == "n_strategy_modules":
        return "strategy_module"
    if feature in {"has_2d_adherent_culture", "has_suspension_culture", "has_shaking", "has_aggregation"} or "culture" in feature:
        return "culture_system"
    if feature.startswith("norm_family_") or feature.endswith("_presence"):
        return "core_signaling_family"
    if feature.startswith("num_") or feature.startswith("requires_") or feature.startswith("transition_") or feature in {"number_of_stages", "stage_transition_number"}:
        return "workflow"
    if any(token in feature for token in ("day", "duration", "exposure")):
        return "timing_or_exposure"
    if "concentration" in feature:
        return "concentration"
    if any(token in feature for token in ("confidence", "fallback", "schema", "audit")):
        return "audit_or_confidence"
    return "other"


def _feature_group_rank(group: str) -> int:
    order = {
        "strategy_module": 0,
        "culture_system": 1,
        "core_signaling_family": 2,
        "workflow": 3,
        "timing_or_exposure": 4,
        "concentration": 5,
        "audit_or_confidence": 6,
        "other": 7,
    }
    return order.get(group, 99)


def _stage_schema_payload(stage: dict[str, Any]) -> dict[str, Any]:
    payload = dict(stage)
    for key in ("stage_index", "day_start", "day_end", "duration_days"):
        payload[key] = _safe_float(payload.get(key), None)
    if payload.get("stage_index") is not None:
        payload["stage_index"] = int(payload["stage_index"])
    return payload


def _factor_event_schema_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event)
    for key in ("stage_index", "day_start", "day_end", "duration_days", "concentration_value"):
        payload[key] = _safe_float(payload.get(key), None)
    if payload.get("stage_index") is not None:
        payload["stage_index"] = int(payload["stage_index"])
    return payload


def _schema_finding(
    entity_type: str,
    protocol_id: str,
    stage_id: str | None,
    row_index: int,
    exc: ValidationError,
    factor_name: str | None = None,
) -> dict[str, Any]:
    first_error = exc.errors()[0] if exc.errors() else {"msg": "schema validation failed", "loc": []}
    return {
        "severity": "high",
        "issue_type": "schema_validation_failed",
        "entity_type": entity_type,
        "protocol_id": protocol_id,
        "stage_id": stage_id or "",
        "factor_name": factor_name or "",
        "row_index": row_index,
        "message": str(first_error.get("msg") or "schema validation failed"),
        "field_path": ".".join(str(part) for part in first_error.get("loc", [])),
        "check_source": "schema",
    }


def _build_analysis_events(
    filtered_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    analysis_events: list[dict[str, Any]] = []
    for index, event in enumerate(filtered_events):
        normalized = normalized_events[index] if index < len(normalized_events) else {}
        merged = dict(event)
        if normalized:
            merged.update(
                {
                    "original_name": normalized.get("original_name") or _factor_name(event),
                    "factor_name": normalized.get("original_name") or _factor_name(event),
                    "standardized_name": normalized.get("normalized_name") or _factor_name(event),
                    "normalized_name": normalized.get("normalized_name") or "",
                    "normalized_family": normalized.get("normalized_family") or "",
                    "pathway": normalized.get("pathway") or "",
                    "factor_type": normalized.get("factor_type") or event.get("factor_type"),
                    "action_type": normalized.get("action_type") or _operation_label(event),
                    "normalization_confidence": normalized.get("confidence") or "",
                    "normalization_source": normalized.get("normalization_source") or "",
                    "normalization_status": normalized.get("validation_status") or "",
                    "normalization_notes": "|".join(normalized.get("validation_notes") or []),
                    "evidence_quote": normalized.get("evidence_quote") or event.get("evidence_quote"),
                }
            )
            for key in ("day_start", "day_end", "duration_days", "concentration_value", "concentration_unit"):
                if normalized.get(key) is not None:
                    merged[key] = normalized.get(key)
        merged["analysis_event_source"] = "normalized_factor_events" if normalized else "filtered_protocol_factor_events"
        analysis_events.append(merged)
    return analysis_events


def _build_stage_and_factor_summary(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events_by_stage: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        protocol_id = str(event.get("protocol_id") or "")
        stage = str(event.get("stage_id") or event.get("stage") or event.get("stage_name") or "unknown_stage")
        events_by_stage[(protocol_id, stage)].append(event)

    stage_rows: list[dict[str, Any]] = []
    factor_report: dict[str, Any] = {"protocols": {}, "factor_index": {}}
    for (protocol_id, stage), stage_events in sorted(events_by_stage.items()):
        factors = sorted({_factor_name(event) for event in stage_events if _factor_name(event)})
        stage_rows.append(
            {
                "protocol_id": protocol_id,
                "stage": stage,
                "stage_index": _first_non_empty(stage_events, "stage_index"),
                "day_start": _blank_if_none(_min_numeric(stage_events, "day_start")),
                "day_end": _blank_if_none(_max_numeric(stage_events, "day_end")),
                "n_factor_events": len(stage_events),
                "factors": "|".join(factors),
            }
        )
        factor_report["protocols"].setdefault(protocol_id, {"stages": {}})
        factor_report["protocols"][protocol_id]["stages"][stage] = {
            "n_factor_events": len(stage_events),
            "factors": factors,
            "events": [_compact_event(event) for event in stage_events],
        }
        for factor in factors:
            factor_report["factor_index"].setdefault(factor, [])
            factor_report["factor_index"][factor].append({"protocol_id": protocol_id, "stage": stage})
    factor_report["summary"] = {
        "n_protocols": len(factor_report["protocols"]),
        "n_unique_factors": len(factor_report["factor_index"]),
        "n_filtered_events": len(events),
    }
    return stage_rows, factor_report


def _build_protocol_feature_matrix(
    protocol_records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source_report: dict[str, Any],
    accepted_protocol_ids: set[str],
    semantic_annotations: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _deduplicate_events(events):
        by_protocol[str(event.get("protocol_id") or "")].append(event)

    record_by_protocol = {
        str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or ""): record
        for record in protocol_records
    }
    source_by_protocol = {
        row["protocol_id"]: row for row in source_report.get("protocol_source_checks", []) if row.get("protocol_id")
    }

    dynamic_feature_keys: set[str] = set()
    partial_rows: list[dict[str, Any]] = []
    provenance: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for protocol_id in sorted(set(record_by_protocol).union(by_protocol)):
        if not protocol_id or (accepted_protocol_ids and protocol_id not in accepted_protocol_ids):
            continue
        record = record_by_protocol.get(protocol_id, {})
        protocol = record.get("protocol") or {}
        stages = record.get("stages") or []
        protocol_events = by_protocol.get(protocol_id, [])
        row: dict[str, Any] = {
            "protocol_id": protocol_id,
            "paper_id": str(record.get("paper_id") or protocol.get("paper_id") or ""),
            "dataset_id": _first_non_empty(protocol_events, "dataset_id"),
            "protocol_name": str(protocol.get("protocol_name") or ""),
            "source_identity_status": source_by_protocol.get(protocol_id, {}).get("source_identity_status", ""),
            "confidence_score": round(_protocol_confidence_score(record), 3),
            "comparison_ready": bool(record.get("comparison_ready", True)),
        }
        _add_temporal_features(row, protocol, stages, protocol_events, provenance[protocol_id])
        _add_action_and_operation_features(row, protocol_events, stages, provenance[protocol_id])
        _add_reagent_family_features(row, protocol_events, provenance[protocol_id])
        _add_normalized_factor_features(row, protocol_events, provenance[protocol_id])
        _add_semantic_strategy_features(row, semantic_annotations or [], protocol_id, provenance[protocol_id])
        _add_order_features(row, protocol_events, provenance[protocol_id])
        _add_dependency_features(row, protocol_events, stages, provenance[protocol_id])
        dynamic_feature_keys.update(key for key in row if key not in _metadata_columns())
        partial_rows.append(row)

    ordered_columns = list(_metadata_columns()) + sorted(dynamic_feature_keys)
    rows = [
        {column: row.get(column, 0 if column not in _metadata_columns() else "") for column in ordered_columns}
        for row in partial_rows
    ]
    return rows, {"protocols": dict(provenance)}


def _add_temporal_features(
    row: dict[str, Any],
    protocol: dict[str, Any],
    stages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    stage_count = _stage_count(protocol, stages, events)
    row["total_days"] = _blank_if_none(_total_days(protocol, stages, events))
    row["number_of_stages"] = stage_count
    row["stage_transition_number"] = max(stage_count - 1, 0)
    row["mean_stage_duration"] = _blank_if_none(_mean_stage_duration(stages, events))
    row["max_stage_duration"] = _blank_if_none(_max_stage_duration(stages, events))
    refs = [_stage_ref(stage) for stage in stages] or [_event_ref(event) for event in events[:5]]
    for feature in ("total_days", "number_of_stages", "stage_transition_number", "mean_stage_duration", "max_stage_duration"):
        provenance[feature] = refs


def _add_action_and_operation_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    text_blocks = [_event_text(event) for event in events] + [str(stage.get("key_operations") or "") for stage in stages]
    text = " ".join(text_blocks).lower()
    row["num_add_reagent_actions"] = sum(1 for event in events if str(event.get("factor_type") or "") == "reagent")
    row["num_wash_actions"] = _keyword_count(text_blocks, ("wash", "dpbs"))
    row["num_dissociation_actions"] = _keyword_count(text_blocks, ("dissociat", "accutase", "trypsin"))
    row["num_aggregation_actions"] = _keyword_count(text_blocks, ("aggregat", "cluster", "spheroid"))
    row["has_medium_change"] = _binary_any(text, ("medium change", "changed medium", "fresh medium"))
    row["has_cell_passage"] = _binary_any(text, ("passage", "passaged", "split"))
    row["has_suspension_culture"] = _binary_any(text, ("suspension", "ultra-low attachment", "low attachment"))
    row["has_shaking"] = _binary_any(text, ("shaker", "shaking", "orbital"))
    row["has_dissociation"] = int(row["num_dissociation_actions"] > 0)
    row["has_aggregation"] = int(row["num_aggregation_actions"] > 0)
    row["has_2d_adherent_culture"] = _binary_any(text, ("plate", "coated", "monolayer", "adherent"))
    refs = [_stage_ref(stage) for stage in stages if _has_text(stage.get("key_operations"))]
    refs.extend(_event_ref(event) for event in events if str(event.get("factor_type") or "") in {"operation", "aggregation", "mechanical_condition"})
    refs = refs[:20]
    for feature in (
        "num_add_reagent_actions",
        "num_wash_actions",
        "num_dissociation_actions",
        "num_aggregation_actions",
        "has_medium_change",
        "has_cell_passage",
        "has_suspension_culture",
        "has_shaking",
        "has_dissociation",
        "has_aggregation",
        "has_2d_adherent_culture",
    ):
        provenance[feature] = refs


def _add_reagent_family_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    for family, aliases in REAGENT_FAMILIES.items():
        family_events = [event for event in events if _matches_aliases(_factor_identity_text(event), aliases)]
        prefix = _feature_name(family)
        row[f"{prefix}_presence"] = int(bool(family_events))
        row[f"{prefix}_n_events"] = len(family_events)
        row[f"{prefix}_total_exposure"] = round(_total_exposure_days(family_events), 3)
        row[f"{prefix}_start_day"] = _blank_if_none(_min_numeric(family_events, "day_start"))
        row[f"{prefix}_end_day"] = _blank_if_none(_max_numeric(family_events, "day_end"))
        row[f"{prefix}_max_concentration"] = _blank_if_none(_max_numeric(family_events, "concentration_value"))
        refs = [_event_ref(event) for event in family_events[:20]]
        for suffix in ("presence", "n_events", "total_exposure", "start_day", "end_day", "max_concentration"):
            provenance[f"{prefix}_{suffix}"] = refs


def _add_normalized_factor_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    families = sorted({str(event.get("normalized_family") or "") for event in events if event.get("normalized_family")})
    for family in families:
        family_events = [event for event in events if str(event.get("normalized_family") or "") == family]
        prefix = "norm_family_" + _feature_name(family)
        row[f"{prefix}_presence"] = int(bool(family_events))
        row[f"{prefix}_n_events"] = len(family_events)
        row[f"{prefix}_total_exposure"] = round(_total_exposure_days(family_events), 3)
        row[f"{prefix}_start_day"] = _blank_if_none(_min_numeric(family_events, "day_start"))
        row[f"{prefix}_end_day"] = _blank_if_none(_max_numeric(family_events, "day_end"))
        row[f"{prefix}_fallback_fraction"] = round(
            sum(1 for event in family_events if event.get("normalization_source") == "fallback") / len(family_events),
            3,
        )
        refs = [_event_ref(event) for event in family_events[:20]]
        for suffix in ("presence", "n_events", "total_exposure", "start_day", "end_day", "fallback_fraction"):
            provenance[f"{prefix}_{suffix}"] = refs


def _add_semantic_strategy_features(
    row: dict[str, Any],
    semantic_annotations: list[dict[str, Any]],
    protocol_id: str,
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    annotations = [annotation for annotation in semantic_annotations if str(annotation.get("protocol_id") or "") == protocol_id]
    modules = sorted(
        {
            str(module)
            for annotation in annotations
            for module in (annotation.get("strategy_modules") or [])
            if module and str(module) != "unknown"
        }
    )
    row["n_semantic_stage_annotations"] = len(annotations)
    row["n_strategy_modules"] = len(modules)
    provenance["n_semantic_stage_annotations"] = [_semantic_ref(annotation) for annotation in annotations[:20]]
    provenance["n_strategy_modules"] = [_semantic_ref(annotation) for annotation in annotations[:20]]
    for module in modules:
        feature = "semantic_module_" + _feature_name(module) + "_presence"
        row[feature] = 1
        provenance[feature] = [_semantic_ref(annotation) for annotation in annotations if module in (annotation.get("strategy_modules") or [])][:20]


def _add_order_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    ordered: list[tuple[float, str]] = []
    refs_by_family: dict[str, list[dict[str, Any]]] = {}
    for family, aliases in REAGENT_FAMILIES.items():
        family_events = [event for event in events if _matches_aliases(_factor_identity_text(event), aliases)]
        refs_by_family[_feature_name(family)] = [_event_ref(event) for event in family_events[:5]]
        start = _min_numeric(family_events, "day_start")
        if start is None:
            start = _min_numeric(family_events, "stage_index")
        if start is not None:
            ordered.append((float(start), _feature_name(family)))
    deduped: list[str] = []
    for _, family in sorted(ordered):
        if not deduped or deduped[-1] != family:
            deduped.append(family)
    row["ordered_reagent_program"] = ">".join(deduped)
    provenance["ordered_reagent_program"] = [ref for family in deduped for ref in refs_by_family.get(family, [])]
    for previous, current in zip(deduped, deduped[1:]):
        feature = f"transition_{previous}_to_{current}"
        row[feature] = 1
        provenance[feature] = refs_by_family.get(previous, []) + refs_by_family.get(current, [])


def _add_dependency_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    text = " ".join([_event_text(event) for event in events] + [str(stage.get("key_operations") or "") for stage in stages]).lower()
    row["requires_previous_stage_completion"] = _binary_any(text, ("after ", "following ", "on stage", "during stage"))
    row["requires_cell_dissociation"] = _binary_any(text, ("dissociat", "accutase", "trypsin"))
    row["requires_cell_number_threshold"] = _binary_any(text, ("cells/cm", "cells per", "x10", "×10", "cell density", "seeded at"))
    refs = [_stage_ref(stage) for stage in stages if _has_text(stage.get("key_operations"))]
    refs.extend(_event_ref(event) for event in events[:10])
    for feature in ("requires_previous_stage_completion", "requires_cell_dissociation", "requires_cell_number_threshold"):
        provenance[feature] = refs[:20]


def _build_protocol_relationships(feature_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not feature_rows:
        return [], {"pairwise_relationships": [], "binary_feature_keys": []}
    binary_keys = [
        key
        for key in feature_rows[0]
        if key not in _metadata_columns()
        and (key.startswith("has_") or key.endswith("_presence") or key.startswith("transition_") or key.startswith("requires_"))
    ]
    rows: list[dict[str, Any]] = []
    pair_reports: list[dict[str, Any]] = []
    for left, right in combinations(feature_rows, 2):
        left_set = {key for key in binary_keys if _truthy_feature(left.get(key))}
        right_set = {key for key in binary_keys if _truthy_feature(right.get(key))}
        union = left_set.union(right_set)
        shared = sorted(left_set.intersection(right_set))
        left_only = sorted(left_set - right_set)
        right_only = sorted(right_set - left_set)
        jaccard = len(shared) / len(union) if union else 1.0
        row = {
            "protocol_id_a": left["protocol_id"],
            "protocol_id_b": right["protocol_id"],
            "binary_feature_jaccard_similarity": round(jaccard, 4),
            "n_shared_binary_features": len(shared),
            "n_protocol_a_specific_features": len(left_only),
            "n_protocol_b_specific_features": len(right_only),
        }
        rows.append(row)
        pair_reports.append(
            {
                **row,
                "shared_binary_features": shared[:50],
                "protocol_a_specific_features": left_only[:50],
                "protocol_b_specific_features": right_only[:50],
            }
        )
    return rows, {"pairwise_relationships": pair_reports, "binary_feature_keys": binary_keys}


def _build_protocol_workflow_graphs(
    protocol_records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source_report: dict[str, Any],
    accepted_protocol_ids: set[str],
) -> dict[str, Any]:
    by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _deduplicate_events(events):
        by_protocol[str(event.get("protocol_id") or "")].append(event)
    source_by_protocol = {
        row["protocol_id"]: row for row in source_report.get("protocol_source_checks", []) if row.get("protocol_id")
    }

    graphs: dict[str, Any] = {}
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not protocol_id or (accepted_protocol_ids and protocol_id not in accepted_protocol_ids):
            continue
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        nodes[protocol_id] = {
            "id": protocol_id,
            "type": "protocol",
            "label": protocol.get("protocol_name") or protocol_id,
            "paper_id": record.get("paper_id") or protocol.get("paper_id"),
            "source_identity_status": source_by_protocol.get(protocol_id, {}).get("source_identity_status", ""),
        }

        sorted_stages = sorted(record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999)
        for stage in sorted_stages:
            stage_id = str(stage.get("stage_id") or "")
            if not stage_id:
                continue
            nodes[stage_id] = {
                "id": stage_id,
                "type": "stage",
                "label": stage.get("stage_name") or f"Stage {stage.get('stage_index', '')}".strip(),
                "stage_index": stage.get("stage_index"),
                "day_start": stage.get("day_start"),
                "day_end": stage.get("day_end"),
                "duration_days": stage.get("duration_days"),
                "input_cell_state": stage.get("input_cell_state"),
                "output_cell_state": stage.get("output_cell_state"),
                "evidence_quote": stage.get("evidence_quote"),
            }
            edges.append({"source": protocol_id, "target": stage_id, "type": "contains_stage"})
            if _has_text(stage.get("key_operations")):
                action_id = f"action:{stage_id}:operation_summary"
                nodes[action_id] = {
                    "id": action_id,
                    "type": "action",
                    "label": "stage operation summary",
                    "operation": stage.get("key_operations"),
                    "evidence_quote": stage.get("evidence_quote"),
                }
                edges.append({"source": stage_id, "target": action_id, "type": "has_action"})
        for previous, current in zip(sorted_stages, sorted_stages[1:]):
            if previous.get("stage_id") and current.get("stage_id"):
                edges.append({"source": previous["stage_id"], "target": current["stage_id"], "type": "next_stage"})

        for index, event in enumerate(by_protocol.get(protocol_id, [])):
            stage_id = str(event.get("stage_id") or protocol_id)
            action_id = f"action:{event.get('event_id') or protocol_id + ':' + str(index)}"
            factor_name = _factor_name(event)
            operation_label = _operation_label(event)
            nodes[action_id] = {
                "id": action_id,
                "type": "action",
                "label": operation_label,
                "factor_type": event.get("factor_type"),
                "operation": event.get("operation"),
                "timing": event.get("timing"),
                "day_start": event.get("day_start"),
                "day_end": event.get("day_end"),
                "evidence_quote": event.get("evidence_quote"),
            }
            edges.append({"source": stage_id if stage_id in nodes else protocol_id, "target": action_id, "type": "has_action"})
            if factor_name:
                reagent_id = f"reagent:{_feature_name(factor_name)}"
                nodes[reagent_id] = {
                    "id": reagent_id,
                    "type": "reagent_or_factor",
                    "label": factor_name,
                    "original_name": event.get("original_name") or event.get("factor_name") or "",
                    "normalized_family": event.get("normalized_family") or "",
                    "pathway": event.get("pathway") or "",
                    "normalization_source": event.get("normalization_source") or "",
                    "confidence": event.get("normalization_confidence") or "",
                    "concentration_value": event.get("concentration_value"),
                    "concentration_unit": event.get("concentration_unit") or event.get("unit"),
                }
                edges.append(
                    {
                        "source": action_id,
                        "target": reagent_id,
                        "type": "uses_reagent_or_factor",
                        "day_start": event.get("day_start"),
                        "day_end": event.get("day_end"),
                    }
                )

        graphs[protocol_id] = {
            "protocol_id": protocol_id,
            "schema_version": "protocol-workflow-graph-v1",
            "nodes": list(nodes.values()),
            "edges": edges,
            "summary": {
                "n_nodes": len(nodes),
                "n_edges": len(edges),
                "n_stages": sum(1 for node in nodes.values() if node["type"] == "stage"),
                "n_actions": sum(1 for node in nodes.values() if node["type"] == "action"),
            },
        }
    return {"protocols": graphs, "schema_version": "protocol-workflow-graph-v1"}


def _build_protocol_knowledge_graph(
    protocol_records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source_report: dict[str, Any],
    accepted_protocol_ids: set[str],
    semantic_annotations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not protocol_id or (accepted_protocol_ids and protocol_id not in accepted_protocol_ids):
            continue
        paper_id = str(record.get("paper_id") or protocol.get("paper_id") or "")
        nodes[protocol_id] = {"id": protocol_id, "type": "protocol", "label": protocol.get("protocol_name") or protocol_id}
        if paper_id:
            nodes[paper_id] = {"id": paper_id, "type": "paper", "label": paper_id}
            edges.append({"source": protocol_id, "target": paper_id, "type": "derived_from_paper"})
        sorted_stages = sorted(record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999)
        for stage in sorted_stages:
            stage_id = str(stage.get("stage_id") or "")
            if not stage_id:
                continue
            nodes[stage_id] = {"id": stage_id, "type": "stage", "label": stage.get("stage_name") or stage_id}
            edges.append({"source": protocol_id, "target": stage_id, "type": "has_stage"})
        for previous, current in zip(sorted_stages, sorted_stages[1:]):
            if previous.get("stage_id") and current.get("stage_id"):
                edges.append({"source": previous["stage_id"], "target": current["stage_id"], "type": "next_stage"})

    for event in _deduplicate_events(events):
        protocol_id = str(event.get("protocol_id") or "")
        stage_id = str(event.get("stage_id") or "")
        factor = _factor_name(event)
        if not protocol_id or not factor:
            continue
        factor_id = "factor:" + _feature_name(factor)
        nodes[factor_id] = {
            "id": factor_id,
            "type": "factor",
            "label": factor,
            "original_name": event.get("original_name") or event.get("factor_name") or "",
            "normalized_family": event.get("normalized_family") or "",
            "pathway": event.get("pathway") or "",
            "normalization_source": event.get("normalization_source") or "",
            "confidence": event.get("normalization_confidence") or "",
        }
        source = stage_id if stage_id in nodes else protocol_id
        edges.append(
            {
                "source": source,
                "target": factor_id,
                "type": "uses_factor",
                "day_start": event.get("day_start"),
                "day_end": event.get("day_end"),
                "concentration": event.get("concentration_raw") or event.get("concentration_value"),
                "evidence_quote": event.get("evidence_quote"),
            }
        )
        family = str(event.get("normalized_family") or "")
        if family:
            family_id = "factor_family:" + _feature_name(family)
            nodes[family_id] = {"id": family_id, "type": "factor_family", "label": family}
            edges.append({"source": factor_id, "target": family_id, "type": "normalized_to_family"})

    for annotation in semantic_annotations or []:
        protocol_id = str(annotation.get("protocol_id") or "")
        stage_id = str(annotation.get("stage_id") or "")
        if not protocol_id:
            continue
        for module in annotation.get("strategy_modules") or []:
            module_id = "strategy_module:" + _feature_name(str(module))
            nodes[module_id] = {"id": module_id, "type": "strategy_module", "label": str(module)}
            source = stage_id if stage_id in nodes else protocol_id
            edges.append(
                {
                    "source": source,
                    "target": module_id,
                    "type": "annotated_as_strategy_module",
                    "confidence": annotation.get("confidence") or "",
                    "source_method": annotation.get("source") or "",
                    "evidence_quote": annotation.get("evidence_quote") or "",
                }
            )

    for check in source_report.get("protocol_source_checks", []):
        source_id = check.get("canonical_source_id") or check.get("source_file")
        protocol_id = check.get("protocol_id")
        if source_id and protocol_id:
            nodes[source_id] = {"id": source_id, "type": "source", "label": check.get("canonical_filename") or source_id}
            edges.append({"source": protocol_id, "target": source_id, "type": "mapped_to_source"})

    return {"nodes": list(nodes.values()), "edges": edges, "summary": {"n_nodes": len(nodes), "n_edges": len(edges)}}


def _build_protocol_embeddings_and_clusters(feature_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    numeric_keys = _numeric_feature_keys(feature_rows)
    normalized = _normalized_numeric_features(feature_rows, numeric_keys)
    embeddings: list[dict[str, Any]] = []
    cluster_members: dict[str, list[str]] = defaultdict(list)
    for row in feature_rows:
        protocol_id = row["protocol_id"]
        values = normalized.get(protocol_id, {})
        reagent_presence_keys = [key for key in numeric_keys if key.endswith("_presence") and _safe_float(row.get(key), 0.0)]
        workflow_keys = [key for key in numeric_keys if key.startswith("has_") and _safe_float(row.get(key), 0.0)]
        embedding_1 = round(sum(values.get(key, 0.0) for key in reagent_presence_keys), 6)
        embedding_2 = round(sum(values.get(key, 0.0) for key in workflow_keys), 6)
        embedding_3 = round(sum(values.values()) / len(values), 6) if values else 0.0
        cluster = _assign_protocol_cluster(row)
        cluster_members[cluster].append(protocol_id)
        embeddings.append(
            {
                "protocol_id": protocol_id,
                "embedding_1_reagent_strategy": embedding_1,
                "embedding_2_workflow_strategy": embedding_2,
                "embedding_3_overall_feature_load": embedding_3,
                "protocol_cluster": cluster,
            }
        )
    return embeddings, {
        "method": "deterministic_feature_embedding_v1",
        "note": "This is a lightweight deterministic embedding. Replace with PCA/UMAP after the protocol cohort is large enough.",
        "numeric_feature_count": len(numeric_keys),
        "clusters": dict(cluster_members),
    }


def _build_standardized_protocol_representations(
    protocol_records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    workflow_graphs: dict[str, Any],
    accepted_protocol_ids: set[str],
) -> list[dict[str, Any]]:
    by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_protocol[str(event.get("protocol_id") or "")].append(event)
    records: list[dict[str, Any]] = []
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not protocol_id or (accepted_protocol_ids and protocol_id not in accepted_protocol_ids):
            continue
        stage_representations = []
        for stage in sorted(record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999):
            stage_id = str(stage.get("stage_id") or "")
            stage_events = [event for event in by_protocol.get(protocol_id, []) if str(event.get("stage_id") or "") == stage_id]
            stage_representations.append(
                {
                    "stage_id": stage_id,
                    "stage_index": stage.get("stage_index"),
                    "stage_name": stage.get("stage_name"),
                    "day_start": stage.get("day_start"),
                    "day_end": stage.get("day_end"),
                    "duration_days": stage.get("duration_days"),
                    "input_cell_state": stage.get("input_cell_state"),
                    "output_cell_state": stage.get("output_cell_state"),
                    "actions": [_event_to_action(event) for event in stage_events],
                    "evidence": _stage_ref(stage),
                }
            )
        records.append(
            {
                "schema_version": "standardized-protocol-representation-v1",
                "protocol_id": protocol_id,
                "paper_id": record.get("paper_id") or protocol.get("paper_id"),
                "protocol_name": protocol.get("protocol_name"),
                "comparison_ready": record.get("comparison_ready", True),
                "confidence_score": round(_protocol_confidence_score(record), 3),
                "stages": stage_representations,
                "workflow_graph_summary": workflow_graphs.get("protocols", {}).get(protocol_id, {}).get("summary", {}),
            }
        )
    return records


def _build_source_identity_report(
    protocol_records: list[dict[str, Any]],
    source_identity_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_identity_rows:
        by_paper[str(row.get("canonical_paper_id") or row.get("guessed_paper_key") or "")].append(row)

    checks: list[dict[str, Any]] = []
    status_counts = Counter()
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        paper_id = str(record.get("paper_id") or protocol.get("paper_id") or "")
        source_file = str(record.get("source_doc_id") or protocol.get("source_file") or "")
        candidates = by_paper.get(paper_id, [])
        matched = _best_source_match(source_file, candidates)
        status = "matched" if matched else ("paper_id_not_in_inventory" if not candidates else "source_file_not_matched")
        status_counts[status] += 1
        checks.append(
            {
                "protocol_id": protocol_id,
                "paper_id": paper_id,
                "source_file": source_file,
                "source_identity_status": status,
                "canonical_source_id": matched.get("canonical_source_id", "") if matched else "",
                "canonical_paper_id": matched.get("canonical_paper_id", "") if matched else "",
                "canonical_filename": matched.get("canonical_filename", "") if matched else "",
                "source_role": matched.get("source_role", "") if matched else "",
            }
        )
    return {
        "summary": {
            "n_source_inventory_rows": len(source_identity_rows),
            "n_protocols_checked": len(protocol_records),
            "status_counts": dict(status_counts),
        },
        "protocol_source_checks": checks,
    }


def _best_source_match(source_file: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    source_norm = _norm_text(source_file)
    for candidate in candidates:
        if _norm_text(str(candidate.get("canonical_filename") or "")) == source_norm:
            return candidate
    for candidate in candidates:
        candidate_name = _norm_text(str(candidate.get("canonical_filename") or candidate.get("name") or ""))
        if candidate_name and (candidate_name in source_norm or source_norm in candidate_name):
            return candidate
    return candidates[0] if len(candidates) == 1 else None


def _event_to_action(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": event.get("event_id") or "",
        "action_type": _operation_label(event),
        "factor_type": event.get("factor_type"),
        "factor_name": _factor_name(event),
        "original_name": event.get("original_name") or event.get("factor_name") or "",
        "normalized_family": event.get("normalized_family") or "",
        "pathway": event.get("pathway") or "",
        "normalization_source": event.get("normalization_source") or "",
        "confidence": event.get("normalization_confidence") or "",
        "day_start": event.get("day_start"),
        "day_end": event.get("day_end"),
        "duration_days": event.get("duration_days"),
        "concentration_value": event.get("concentration_value"),
        "concentration_unit": event.get("concentration_unit") or event.get("unit"),
        "parse_status": event.get("parse_status"),
        "validation_status": event.get("validation_status"),
        "evidence": _event_ref(event),
    }


def _operation_label(event: dict[str, Any]) -> str:
    action_type = str(event.get("action_type") or "").strip()
    if action_type:
        return action_type
    operation = str(event.get("operation") or "").strip()
    factor_type = str(event.get("factor_type") or "").strip()
    if factor_type == "reagent":
        return "add_reagent"
    if factor_type == "matrix_or_coating":
        return "set_matrix_or_coating"
    if factor_type == "medium_base":
        return "set_medium"
    if factor_type == "culture_format":
        return "set_culture_format"
    if factor_type == "cell_state_transition":
        return "cell_state_transition"
    if factor_type in {"assay", "qc_marker"}:
        return "assay"
    operation_text = operation.lower()
    if "wash" in operation_text:
        return "wash"
    if any(token in operation_text for token in ("dissociat", "accutase", "trypsin")):
        return "dissociate"
    if any(token in operation_text for token in ("transfer", "resuspend", "seed")):
        return "transfer_or_seed"
    if any(token in operation_text for token in ("aggregat", "cluster", "spheroid")):
        return "aggregate"
    if any(token in operation_text for token in ("culture", "incubat", "differentiat")):
        return "culture"
    if operation:
        return "operation"
    if factor_type:
        return factor_type
    return "protocol_action"


def _stage_ref(stage: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "stage",
        "entity_id": stage.get("stage_id") or "",
        "stage_index": stage.get("stage_index"),
        "quote": stage.get("evidence_quote") or "",
        "source_location": stage.get("source_location") or "",
    }


def _event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "protocol_factor_event",
        "event_id": event.get("event_id") or "",
        "stage_id": event.get("stage_id") or "",
        "factor_type": event.get("factor_type") or "",
        "factor_name": _factor_name(event),
        "normalized_family": event.get("normalized_family") or "",
        "normalization_source": event.get("normalization_source") or "",
        "confidence": event.get("normalization_confidence") or "",
        "quote": event.get("evidence_quote") or "",
        "source_doc_id": event.get("source_doc_id") or "",
        "source_location": event.get("source_location") or "",
    }


def _semantic_ref(annotation: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "semantic_stage_annotation",
        "stage_id": annotation.get("stage_id") or "",
        "stage_index": annotation.get("stage_index"),
        "biological_role": annotation.get("biological_role") or "",
        "strategy_modules": annotation.get("strategy_modules") or [],
        "quote": annotation.get("evidence_quote") or "",
        "source": annotation.get("source") or "",
        "confidence": annotation.get("confidence") or "",
    }


def _numeric_feature_keys(feature_rows: list[dict[str, Any]]) -> list[str]:
    if not feature_rows:
        return []
    keys: list[str] = []
    for key in feature_rows[0]:
        if key in _metadata_columns():
            continue
        if any(_safe_float(row.get(key), None) is not None for row in feature_rows):
            keys.append(key)
    return keys


def _normalized_numeric_features(feature_rows: list[dict[str, Any]], numeric_keys: list[str]) -> dict[str, dict[str, float]]:
    mins: dict[str, float] = {}
    maxs: dict[str, float] = {}
    for key in numeric_keys:
        values = [_safe_float(row.get(key), None) for row in feature_rows]
        values = [value for value in values if value is not None]
        if values:
            mins[key] = min(values)
            maxs[key] = max(values)
    normalized: dict[str, dict[str, float]] = {}
    for row in feature_rows:
        protocol_id = str(row.get("protocol_id") or "")
        normalized[protocol_id] = {}
        for key in numeric_keys:
            value = _safe_float(row.get(key), None)
            if value is None:
                normalized[protocol_id][key] = 0.0
                continue
            low = mins.get(key, 0.0)
            high = maxs.get(key, low)
            normalized[protocol_id][key] = 0.0 if high == low else (value - low) / (high - low)
    return normalized


def _assign_protocol_cluster(row: dict[str, Any]) -> str:
    if _truthy_feature(row.get("has_aggregation")) or _truthy_feature(row.get("has_suspension_culture")):
        culture = "3d_or_suspension"
    elif _truthy_feature(row.get("has_2d_adherent_culture")):
        culture = "2d_adherent"
    else:
        culture = "culture_unspecified"

    maturation_markers = (
        "alk5i_presence",
        "gammasecretaseinhibitor_presence",
        "t3_presence",
        "betacellulin_presence",
        "heparin_presence",
    )
    has_maturation = any(_truthy_feature(row.get(feature)) for feature in maturation_markers)
    has_patterning = any(_truthy_feature(row.get(feature)) for feature in ("activin_presence", "chir_presence", "kgf_presence", "ra_presence"))
    if has_maturation and has_patterning:
        strategy = "patterning_plus_maturation"
    elif has_patterning:
        strategy = "patterning_focused"
    elif has_maturation:
        strategy = "maturation_focused"
    else:
        strategy = "low_factor_resolution"
    return f"{culture}:{strategy}"


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for event in events:
        key = (
            event.get("protocol_id"),
            event.get("stage_id"),
            event.get("factor_type"),
            _factor_name(event).lower(),
            event.get("day_start"),
            event.get("day_end"),
            event.get("concentration_raw") or event.get("concentration_value"),
            event.get("operation"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _protocol_confidence_score(record: dict[str, Any]) -> float:
    confidence = record.get("confidence")
    protocol = record.get("protocol") or {}
    if isinstance(confidence, (int, float)):
        return float(confidence)
    if isinstance(confidence, dict):
        return max(_confidence_label_score(confidence.get("extraction")), _confidence_label_score(protocol.get("confidence_level")))
    return _confidence_label_score(protocol.get("confidence_level"), default=1.0)


def _confidence_label_score(value: Any, default: float = 0.5) -> float:
    label = str(value or "").lower()
    if label == "high":
        return 0.9
    if label == "medium":
        return 0.7
    if label == "low":
        return 0.4
    if label in {"not_evaluated", ""}:
        return default
    return default


def _validation_flag_names(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, list):
        names = set()
        for item in value:
            if isinstance(item, dict):
                names.add(str(item.get("issue_type") or item.get("flag") or item.get("message") or "unknown_flag"))
            else:
                names.add(str(item))
        return names
    if isinstance(value, str):
        return {part.strip() for part in value.replace(";", "|").split("|") if part.strip()}
    return {str(value)}


def _has_unparsed_numeric_field(event: dict[str, Any]) -> bool:
    return str(event.get("parse_status") or "").lower() == "unparsed" or str(
        event.get("concentration_parse_status") or ""
    ).lower() == "unparsed"


def _factor_name(event: dict[str, Any]) -> str:
    return str(
        event.get("normalized_name")
        or event.get("standardized_name")
        or event.get("factor_name")
        or event.get("factor")
        or event.get("reagent")
        or event.get("reagent_name")
        or ""
    ).strip()


def _factor_search_text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in ("normalized_name", "normalized_family", "standardized_name", "factor_name", "value_raw", "concentration_raw", "timing", "operation")
    ).lower()


def _factor_identity_text(event: dict[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("normalized_name", "normalized_family", "standardized_name", "factor_name")).lower()


def _event_text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in ("factor_type", "action_type", "factor_name", "standardized_name", "normalized_name", "normalized_family", "operation", "timing", "value_raw")
    )


def _matches_aliases(text: str, aliases: tuple[str, ...]) -> bool:
    return any(alias.strip().lower() in text for alias in aliases)


def _feature_name(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    name = "".join(chars).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    return name


def _metadata_columns() -> tuple[str, ...]:
    return (
        "protocol_id",
        "paper_id",
        "dataset_id",
        "protocol_name",
        "source_identity_status",
        "confidence_score",
        "comparison_ready",
        "ordered_reagent_program",
    )


def _stage_count(protocol: dict[str, Any], stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> int:
    from_protocol = _safe_float(protocol.get("number_of_stages"), None)
    if from_protocol is not None:
        return int(from_protocol)
    if stages:
        return len(stages)
    return len({event.get("stage_id") for event in events if event.get("stage_id")})


def _total_days(protocol: dict[str, Any], stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> float | None:
    from_protocol = _safe_float(protocol.get("total_duration_days"), None)
    if from_protocol is not None:
        return from_protocol
    ends = [_safe_float(stage.get("day_end"), None) for stage in stages] + [_safe_float(event.get("day_end"), None) for event in events]
    values = [value for value in ends if value is not None]
    return max(values) if values else None


def _mean_stage_duration(stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> float | None:
    durations = [_safe_float(stage.get("duration_days"), None) for stage in stages]
    if not any(value is not None for value in durations):
        durations = [_safe_float(event.get("duration_days"), None) for event in events]
    values = [value for value in durations if value is not None]
    return round(sum(values) / len(values), 3) if values else None


def _max_stage_duration(stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> float | None:
    durations = [_safe_float(stage.get("duration_days"), None) for stage in stages]
    if not any(value is not None for value in durations):
        durations = [_safe_float(event.get("duration_days"), None) for event in events]
    values = [value for value in durations if value is not None]
    return max(values) if values else None


def _total_exposure_days(events: list[dict[str, Any]]) -> float:
    intervals: set[tuple[float, float]] = set()
    fallback_count = 0
    for event in events:
        start = _safe_float(event.get("day_start"), None)
        end = _safe_float(event.get("day_end"), None)
        if start is not None and end is not None and end >= start:
            intervals.add((start, end))
        else:
            fallback_count += 1
    exposure = sum(end - start for start, end in intervals)
    return exposure if intervals else float(fallback_count)


def _keyword_count(text_blocks: list[str], keywords: tuple[str, ...]) -> int:
    return sum(1 for text in text_blocks if any(keyword in text.lower() for keyword in keywords))


def _binary_any(text: str, keywords: tuple[str, ...]) -> int:
    return int(any(keyword in text for keyword in keywords))


def _safe_float(value: Any, default: float | None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _blank_if_none(value: Any) -> Any:
    return "" if value is None else value


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _first_non_empty(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        if _has_text(row.get(key)):
            return row.get(key)
    return ""


def _min_numeric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_safe_float(row.get(key), None) for row in rows]
    values = [value for value in values if value is not None]
    return min(values) if values else None


def _max_numeric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_safe_float(row.get(key), None) for row in rows]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _truthy_feature(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "") not in {"", "0", "False", "false"}


def _norm_text(value: str) -> str:
    return " ".join(value.replace("\\", "/").split("/")[-1].lower().split())


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "event_id",
        "protocol_id",
        "paper_id",
        "dataset_id",
        "stage_id",
        "stage_index",
        "day_start",
        "day_end",
        "duration_days",
        "factor_type",
        "factor_name",
        "standardized_name",
        "value_raw",
        "concentration_value",
        "concentration_unit",
        "unit",
        "operation",
        "confidence",
        "validation_status",
        "parse_status",
        "evidence_quote",
        "source_doc_id",
    ]
    return {key: event.get(key) for key in keys if key in event}
