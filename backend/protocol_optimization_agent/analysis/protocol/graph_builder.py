"""Extracted graph builder calculations; no agent execution."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from protocol_optimization_agent.analysis.protocol._shared import (
    _deduplicate_events,
    _event_identity,
    _event_ref,
    _factor_name,
    _feature_name,
    _has_text,
    _is_accepted_protocol,
    _operation_label,
    _ordered_events,
    _protocol_confidence_score,
    _safe_float,
    _stage_ref,
)


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
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
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

        sorted_stages = sorted(
            record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999
        )
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
                    "action_class": "modify",
                    "operation": stage.get("key_operations"),
                    "evidence_quote": stage.get("evidence_quote"),
                }
                edges.append({"source": stage_id, "target": action_id, "type": "has_action"})
        for previous, current in zip(sorted_stages, sorted_stages[1:], strict=False):
            if previous.get("stage_id") and current.get("stage_id"):
                edges.append({"source": previous["stage_id"], "target": current["stage_id"], "type": "next_stage"})

        ordered_protocol_events = _ordered_events(by_protocol.get(protocol_id, []))
        action_ids_in_order: list[str] = []
        for index, event in enumerate(ordered_protocol_events):
            stage_id = str(event.get("stage_id") or protocol_id)
            action_id = f"action:{event.get('event_id') or protocol_id + ':' + str(index)}"
            action_ids_in_order.append(action_id)
            factor_name = _factor_name(event)
            operation_label = _operation_label(event)
            nodes[action_id] = {
                "id": action_id,
                "type": "action",
                "source_event_identity": _event_identity(event),
                "label": operation_label,
                "action_class": _uwl_action_class(operation_label),
                "factor_type": event.get("factor_type"),
                "operation": event.get("operation"),
                "timing": event.get("timing"),
                "day_start": event.get("day_start"),
                "day_end": event.get("day_end"),
                "evidence_quote": event.get("evidence_quote"),
            }
            edges.append(
                {"source": stage_id if stage_id in nodes else protocol_id, "target": action_id, "type": "has_action"}
            )
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
                        "item_role": _uwl_item_role(event),
                        "day_start": event.get("day_start"),
                        "day_end": event.get("day_end"),
                    }
                )
        for previous, current in zip(action_ids_in_order, action_ids_in_order[1:], strict=False):
            edges.append({"source": previous, "target": current, "type": "next_action"})

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
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        paper_id = str(record.get("paper_id") or protocol.get("paper_id") or "")
        nodes[protocol_id] = {
            "id": protocol_id,
            "type": "protocol",
            "label": protocol.get("protocol_name") or protocol_id,
        }
        if paper_id:
            nodes[paper_id] = {"id": paper_id, "type": "paper", "label": paper_id}
            edges.append({"source": protocol_id, "target": paper_id, "type": "derived_from_paper"})
        sorted_stages = sorted(
            record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999
        )
        for stage in sorted_stages:
            stage_id = str(stage.get("stage_id") or "")
            if not stage_id:
                continue
            nodes[stage_id] = {"id": stage_id, "type": "stage", "label": stage.get("stage_name") or stage_id}
            edges.append({"source": protocol_id, "target": stage_id, "type": "has_stage"})
        for previous, current in zip(sorted_stages, sorted_stages[1:], strict=False):
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
            nodes[source_id] = {
                "id": source_id,
                "type": "source",
                "label": check.get("canonical_filename") or source_id,
            }
            edges.append({"source": protocol_id, "target": source_id, "type": "mapped_to_source"})

    return {"nodes": list(nodes.values()), "edges": edges, "summary": {"n_nodes": len(nodes), "n_edges": len(edges)}}


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
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        stage_representations = []
        for stage in sorted(
            record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999
        ):
            stage_id = str(stage.get("stage_id") or "")
            stage_events = [
                event for event in by_protocol.get(protocol_id, []) if str(event.get("stage_id") or "") == stage_id
            ]
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
                "preparation": record.get("preparation", []),
                "workflow_graph_summary": workflow_graphs.get("protocols", {}).get(protocol_id, {}).get("summary", {}),
            }
        )
    return records


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
        "concentration_standard_value": event.get("concentration_standard_value"),
        "concentration_standard_unit": event.get("concentration_standard_unit"),
        "concentration_comparability_status": event.get("concentration_comparability_status"),
        "parse_status": event.get("parse_status"),
        "validation_status": event.get("validation_status"),
        "evidence": _event_ref(event),
    }


def _uwl_action_class(action_label: str) -> str:
    label = str(action_label or "").lower()
    if label in {"add_reagent", "set_medium", "set_matrix_or_coating", "transfer_or_seed", "aggregate"}:
        return "add"
    if label in {"wash", "dissociate"}:
        return "remove"
    return "modify"


def _uwl_item_role(event: dict[str, Any]) -> str:
    factor_type = str(event.get("factor_type") or "")
    action_label = _operation_label(event)
    if factor_type == "reagent" or action_label == "add_reagent":
        return "mobile_item_added_or_applied"
    if factor_type in {"medium_base", "matrix_or_coating", "culture_format"}:
        return "environment_or_stationary_context"
    if factor_type in {"assay", "qc_marker"}:
        return "measurement_target_or_readout"
    return "associated_item_or_parameter"
