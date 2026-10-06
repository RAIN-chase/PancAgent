"""Extracted validation calculations; no agent execution."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from pydantic import ValidationError

from protocol_optimization_agent.analysis.protocol._shared import (
    _action_categories_for_text,
    _event_identity,
    _event_reagent_feature_key,
    _event_text,
    _factor_name,
    _feature_name,
    _has_text,
    _is_accepted_protocol,
    _norm_text,
    _operation_label,
    _reagent_feature_key_map,
    _safe_float,
)
from protocol_optimization_agent.schemas import FactorEvent, ProtocolRecord, StageRecord


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
                findings.append(
                    _schema_finding("stage_record", protocol_id, str(stage.get("stage_id") or ""), stage_index, exc)
                )
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

    return (
        valid_protocol_records,
        valid_factor_events,
        {
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
        },
    )


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
    first_error: dict[str, Any] = (
        dict(exc.errors()[0]) if exc.errors() else {"msg": "schema validation failed", "loc": []}
    )
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


def _build_quantification_coverage_report(
    events: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    feature_provenance: dict[str, Any],
) -> dict[str, Any]:
    matrix_columns = set(feature_rows[0]) if feature_rows else set()
    reagent_feature_keys = _reagent_feature_key_map(events)
    reagent_names = sorted(
        {
            _event_reagent_feature_key(event, reagent_feature_keys)
            for event in events
            if str(event.get("factor_type") or "") == "reagent"
            and _event_reagent_feature_key(event, reagent_feature_keys)
        }
    )
    reagent_labels_by_feature: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if str(event.get("factor_type") or "") != "reagent":
            continue
        label = _factor_name(event)
        feature_key = _event_reagent_feature_key(event, reagent_feature_keys)
        if feature_key:
            reagent_labels_by_feature[feature_key].add(label)
    reagent_name_collisions = {
        feature_key: sorted(labels) for feature_key, labels in reagent_labels_by_feature.items() if len(labels) > 1
    }
    factor_types = sorted(
        {
            _feature_name(str(event.get("factor_type") or "unknown"))
            for event in events
            if _feature_name(str(event.get("factor_type") or "unknown"))
        }
    )
    action_categories = sorted(
        {category for event in events for category in _action_categories_for_text(_event_text(event), event)}
    )
    action_relevant_events = [
        event
        for event in events
        if str(event.get("factor_type") or "") != "reagent" or _has_text(event.get("operation"))
    ]
    uncategorized_action_events = [
        {
            "protocol_id": event.get("protocol_id") or "",
            "stage_id": event.get("stage_id") or "",
            "factor_type": event.get("factor_type") or "",
            "factor_name": _factor_name(event),
            "operation": event.get("operation") or "",
            "quote": event.get("evidence_quote") or "",
        }
        for event in action_relevant_events
        if not _action_categories_for_text(_event_text(event), event)
    ]
    missing_reagents = [name for name in reagent_names if f"all_reagent_{name}_presence" not in matrix_columns]
    missing_factor_types = [
        factor_type for factor_type in factor_types if f"factor_type_{factor_type}_presence" not in matrix_columns
    ]
    missing_action_categories = [
        category for category in action_categories if f"action_category_{category}_presence" not in matrix_columns
    ]
    provenance_protocols = feature_provenance.get("protocols", {}) if isinstance(feature_provenance, dict) else {}
    domain_coverage = _build_protocol_detail_domain_coverage(events, provenance_protocols)
    missing_provenance_columns = sorted(
        column
        for column in matrix_columns
        if column.startswith(("all_reagent_", "factor_type_", "action_category_"))
        and not any(column in protocol_provenance for protocol_provenance in provenance_protocols.values())
    )
    status = (
        "pass"
        if not (
            missing_reagents
            or missing_factor_types
            or missing_action_categories
            or missing_provenance_columns
            or uncategorized_action_events
        )
        else "fail"
    )
    return {
        "status": status,
        "scope": "accepted_filtered_analysis_events_only",
        "n_analysis_events": len(events),
        "n_feature_rows": len(feature_rows),
        "n_matrix_columns": len(matrix_columns),
        "reagent_coverage": {
            "n_unique_reagents": len(reagent_names),
            "n_presence_columns": sum(
                1 for column in matrix_columns if column.startswith("all_reagent_") and column.endswith("_presence")
            ),
            "missing_reagents": missing_reagents,
            "reagent_name_collisions": reagent_name_collisions,
        },
        "factor_type_coverage": {
            "n_unique_factor_types": len(factor_types),
            "n_presence_columns": sum(
                1 for column in matrix_columns if column.startswith("factor_type_") and column.endswith("_presence")
            ),
            "missing_factor_types": missing_factor_types,
        },
        "action_category_coverage": {
            "n_unique_action_categories": len(action_categories),
            "n_presence_columns": sum(
                1 for column in matrix_columns if column.startswith("action_category_") and column.endswith("_presence")
            ),
            "n_action_relevant_events": len(action_relevant_events),
            "n_uncategorized_action_events": len(uncategorized_action_events),
            "missing_action_categories": missing_action_categories,
            "uncategorized_action_events": uncategorized_action_events[:100],
        },
        "protocol_detail_domain_coverage": domain_coverage,
        "missing_provenance_columns": missing_provenance_columns,
    }


def _build_protocol_detail_domain_coverage(
    events: list[dict[str, Any]],
    provenance_protocols: dict[str, Any],
) -> dict[str, Any]:
    event_ids_with_provenance = _provenance_event_ids(provenance_protocols)
    domains: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        event_domains = _protocol_detail_domains(event)
        for domain in event_domains:
            domains[domain].append(event)
    report: dict[str, Any] = {}
    for domain, domain_events in sorted(domains.items()):
        event_ids = {_event_identity(event) for event in domain_events}
        missing_ids = sorted(event_ids - event_ids_with_provenance)
        report[domain] = {
            "n_events": len(domain_events),
            "n_unique_event_ids": len(event_ids),
            "n_events_without_feature_provenance": len(missing_ids),
            "missing_event_ids": missing_ids[:200],
            "status": "pass" if not missing_ids else "fail",
        }
    for domain in (
        "culture_parameters",
        "physical_conditions",
        "cell_handling",
        "qc_or_readout",
        "transplantation",
    ):
        report.setdefault(
            domain,
            {
                "n_events": 0,
                "n_unique_event_ids": 0,
                "n_events_without_feature_provenance": 0,
                "missing_event_ids": [],
                "status": "not_reported_in_agent1_structured_input",
            },
        )
    return report


def _protocol_detail_domains(event: dict[str, Any]) -> set[str]:
    factor_type = str(event.get("factor_type") or "")
    action_type = _operation_label(event)
    text = _event_text(event).lower()
    domains: set[str] = set()
    if factor_type in {"medium_base", "matrix_or_coating", "culture_format"}:
        domains.add("culture_parameters")
    if factor_type in {"mechanical_condition", "oxygen_condition"} or any(
        token in text for token in ("oxygen", "hypoxia", "normoxia", "shak", "spinner", "bioreactor", "air-liquid")
    ):
        domains.add("physical_conditions")
    if factor_type in {"aggregation", "sorting", "cell_state_transition"} or action_type in {
        "dissociate",
        "transfer_or_seed",
        "aggregate",
    }:
        domains.add("cell_handling")
    if factor_type in {"qc_marker", "assay"} or "assay_or_qc" in _action_categories_for_text(text, event):
        domains.add("qc_or_readout")
    if factor_type == "transplantation" or "transplantation" in _action_categories_for_text(text, event):
        domains.add("transplantation")
    return domains


def _build_lossless_protocol_representation_audit(
    protocol_records: list[dict[str, Any]],
    input_events: list[dict[str, Any]],
    analysis_events: list[dict[str, Any]],
    event_filter_report: dict[str, Any],
    feature_provenance: dict[str, Any],
    workflow_graphs: dict[str, Any],
    accepted_protocol_ids: set[str],
) -> dict[str, Any]:
    graph_protocols = workflow_graphs.get("protocols", {}) if isinstance(workflow_graphs, dict) else {}
    provenance_protocols = feature_provenance.get("protocols", {}) if isinstance(feature_provenance, dict) else {}
    analysis_event_ids = {_event_identity(event) for event in analysis_events}
    accepted_stage_ids = _accepted_stage_ids(protocol_records, accepted_protocol_ids)
    graph_stage_ids = _graph_node_ids_by_type(graph_protocols, "stage")
    graph_action_event_ids = _graph_action_event_ids(graph_protocols)
    provenance_event_ids = _provenance_event_ids(provenance_protocols)
    accepted_event_ids = {_event_identity(event) for event in analysis_events}
    input_event_ids = {_event_identity(event) for event in input_events}
    filtered_out_event_ids = sorted(input_event_ids - analysis_event_ids)
    missing_stage_graph_ids = sorted(accepted_stage_ids - graph_stage_ids)
    missing_event_graph_ids = sorted(accepted_event_ids - graph_action_event_ids)
    missing_event_provenance_ids = sorted(accepted_event_ids - provenance_event_ids)
    status = (
        "pass" if not (missing_stage_graph_ids or missing_event_graph_ids or missing_event_provenance_ids) else "fail"
    )
    return {
        "schema_version": "lossless-protocol-representation-audit-v1",
        "status": status,
        "scope": "accepted_filtered_analysis_events_only",
        "absence_semantics": {
            "covered": "The accepted Agent1 entity is represented in graph and/or feature provenance.",
            "not_reported_in_agent1_structured_input": (
                "No accepted Agent1 stage/event exists for this concept; with Agent1 assumed complete, treat as not given "
                "by the source protocol, not as a PA recovery target."
            ),
            "excluded_by_quality_or_schema_filter": (
                "Agent1 emitted an event, but PA excluded it from quantitative analysis because quality, schema, parse, "
                "validation, or evidence filters rejected it."
            ),
            "possible_agent1_or_filter_gap": (
                "Use only when source evidence coverage is low or filtered-out event counts are high; PA cannot infer this "
                "from final matrix absence alone."
            ),
        },
        "summary": {
            "n_accepted_stages": len(accepted_stage_ids),
            "n_accepted_analysis_events": len(accepted_event_ids),
            "n_input_events": len(input_event_ids),
            "n_filtered_out_events": len(filtered_out_event_ids),
            "n_missing_stage_graph_nodes": len(missing_stage_graph_ids),
            "n_missing_event_graph_actions": len(missing_event_graph_ids),
            "n_missing_event_feature_provenance": len(missing_event_provenance_ids),
            "event_filter_report": event_filter_report,
        },
        "missing_stage_graph_ids": missing_stage_graph_ids[:200],
        "missing_event_graph_ids": missing_event_graph_ids[:200],
        "missing_event_provenance_ids": missing_event_provenance_ids[:200],
        "filtered_out_event_ids": filtered_out_event_ids[:500],
    }


def _build_field_quantification_map(
    protocol_records: list[dict[str, Any]],
    analysis_events: list[dict[str, Any]],
    feature_provenance: dict[str, Any],
    workflow_graphs: dict[str, Any],
    accepted_protocol_ids: set[str],
) -> dict[str, Any]:
    provenance_protocols = feature_provenance.get("protocols", {}) if isinstance(feature_provenance, dict) else {}
    graph_protocols = workflow_graphs.get("protocols", {}) if isinstance(workflow_graphs, dict) else {}
    graph_event_ids = _graph_action_event_ids(graph_protocols)
    provenance_index = _feature_targets_by_event_identity(provenance_protocols)
    field_rows = []
    status_counts: Counter[str] = Counter()
    critical_failures = []
    for event in analysis_events:
        protocol_id = str(event.get("protocol_id") or "")
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        event_identity = _event_identity(event)
        mapped_features = sorted(provenance_index.get(event_identity, set()))
        field_mappings = _event_field_mappings(event, event_identity in graph_event_ids, mapped_features)
        row = {
            "protocol_id": protocol_id,
            "stage_id": event.get("stage_id") or "",
            "event_identity": event_identity,
            "field_mappings": field_mappings,
        }
        row_statuses = {item["conversion_status"] for item in field_mappings}
        row["status"] = "blocked" if "not_quantified" in row_statuses else "pass"
        status_counts[str(row["status"])] += 1
        if row["status"] == "blocked":
            critical_failures.append(row)
        field_rows.append(row)
    accepted_protocol_count = len(
        {
            str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or "")
            for record in protocol_records
            if _is_accepted_protocol(
                str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or ""),
                accepted_protocol_ids,
            )
        }
    )
    return {
        "schema_version": "field-quantification-map-v1",
        "scope": "accepted_filtered_analysis_events_only",
        "status": "pass" if not critical_failures else "fail",
        "summary": {
            "n_accepted_protocols": accepted_protocol_count,
            "n_events_checked": len(field_rows),
            "status_counts": dict(status_counts),
            "n_critical_failures": len(critical_failures),
        },
        "field_rows": field_rows,
        "critical_failures": critical_failures[:200],
    }


def _feature_targets_by_event_identity(provenance_protocols: dict[str, Any]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = defaultdict(set)
    for protocol_provenance in provenance_protocols.values():
        if not isinstance(protocol_provenance, dict):
            continue
        for feature, refs in protocol_provenance.items():
            if not isinstance(refs, list):
                continue
            for ref in refs:
                event_identity = str(ref.get("event_identity") or ref.get("event_id") or "")
                if event_identity:
                    targets[event_identity].add(str(feature))
    return targets


def _event_field_mappings(
    event: dict[str, Any],
    in_graph: bool,
    mapped_features: list[str],
) -> list[dict[str, Any]]:
    has_feature_mapping = bool(mapped_features)
    mappings = [
        _field_mapping("event", event, "factor_type", ["factor_type_*"], has_feature_mapping),
        _field_mapping("event", event, "factor_name", ["all_reagent_*", "motif_action_*"], has_feature_mapping),
        _field_mapping("event", event, "operation", ["action_category_*", "action_bigram_*"], has_feature_mapping),
        _field_mapping("event", event, "day_start", ["*_start_day", "graph_sequence"], has_feature_mapping),
        _field_mapping("event", event, "day_end", ["*_end_day", "graph_sequence"], has_feature_mapping),
        _field_mapping("event", event, "duration_days", ["*_total_exposure"], has_feature_mapping),
        _field_mapping("event", event, "concentration_value", ["*_max_concentration"], has_feature_mapping),
    ]
    mappings.append(
        {
            "source_path": "workflow_graph.action_node",
            "source_value": _event_identity(event),
            "target_columns": ["protocol_workflow_graphs.nodes"],
            "conversion_rule": "event_to_action_node",
            "conversion_status": "mapped" if in_graph else "not_quantified",
        }
    )
    for mapping in mappings:
        mapping["evidence_quote"] = event.get("evidence_quote") or ""
        mapping["target_feature_candidates"] = mapped_features[:50]
    return mappings


def _field_mapping(
    entity: str,
    event: dict[str, Any],
    field: str,
    target_columns: list[str],
    has_feature_mapping: bool,
) -> dict[str, Any]:
    value = event.get(field)
    status = "not_reported_in_agent1_structured_input" if value in (None, "") else "mapped"
    if field == "concentration_value" and value not in (None, ""):
        status = (
            "standardized" if event.get("concentration_comparability_status") == "standardized" else "not_comparable"
        )
    if value not in (None, "") and not has_feature_mapping:
        status = "not_quantified"
    return {
        "source_path": f"{entity}.{field}",
        "source_value": value if value is not None else "",
        "target_columns": target_columns,
        "conversion_rule": _field_conversion_rule(field),
        "conversion_status": status,
    }


def _field_conversion_rule(field: str) -> str:
    rules = {
        "factor_type": "factor_type_presence_count_timing",
        "factor_name": "canonical_reagent_identity_features",
        "operation": "action_category_and_sequence_features",
        "day_start": "temporal_min_and_sequence_sort",
        "day_end": "temporal_max_and_exposure_window",
        "duration_days": "exposure_sum_when_interval_available",
        "concentration_value": "standardize_unit_then_compare_within_standard_unit",
    }
    return rules.get(field, "direct_projection")


def _build_matrix_release_gate_report(
    quantification_coverage_report: dict[str, Any],
    lossless_audit: dict[str, Any],
    field_quantification_map: dict[str, Any],
    quality_filters: dict[str, Any],
) -> dict[str, Any]:
    enforce = bool(quality_filters.get("enforce_matrix_release_gate", True))
    failures = []
    if quantification_coverage_report.get("status") != "pass":
        failures.append("quantification_coverage_report_failed")
    if lossless_audit.get("status") != "pass":
        failures.append("lossless_protocol_representation_audit_failed")
    if field_quantification_map.get("status") != "pass":
        failures.append("field_quantification_map_failed")
    return {
        "schema_version": "matrix-release-gate-v1",
        "status": "blocked" if enforce and failures else "pass",
        "enforced": enforce,
        "failures": failures,
        "release_ready": not failures,
    }


def _accepted_stage_ids(protocol_records: list[dict[str, Any]], accepted_protocol_ids: set[str]) -> set[str]:
    stage_ids: set[str] = set()
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        stage_ids.update(
            str(stage.get("stage_id") or "") for stage in record.get("stages") or [] if stage.get("stage_id")
        )
    return stage_ids


def _graph_node_ids_by_type(graph_protocols: dict[str, Any], node_type: str) -> set[str]:
    ids: set[str] = set()
    for graph in graph_protocols.values():
        ids.update(str(node.get("id") or "") for node in graph.get("nodes", []) if node.get("type") == node_type)
    return ids


def _graph_action_event_ids(graph_protocols: dict[str, Any]) -> set[str]:
    event_ids: set[str] = set()
    for graph in graph_protocols.values():
        for node in graph.get("nodes", []):
            if node.get("type") != "action":
                continue
            if node.get("source_event_identity"):
                event_ids.add(str(node.get("source_event_identity")))
                continue
            action_id = str(node.get("id") or "")
            if action_id.startswith("action:"):
                event_ids.add(action_id.removeprefix("action:"))
    return event_ids


def _provenance_event_ids(provenance_protocols: dict[str, Any]) -> set[str]:
    event_ids: set[str] = set()
    for protocol_provenance in provenance_protocols.values():
        if not isinstance(protocol_provenance, dict):
            continue
        for refs in protocol_provenance.values():
            if isinstance(refs, list):
                event_ids.update(
                    str(ref.get("event_identity") or ref.get("event_id") or "")
                    for ref in refs
                    if ref.get("event_identity") or ref.get("event_id")
                )
    return event_ids


def _build_source_identity_report(
    protocol_records: list[dict[str, Any]],
    source_identity_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    by_paper: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_identity_rows:
        by_paper[str(row.get("canonical_paper_id") or row.get("guessed_paper_key") or "")].append(row)

    checks: list[dict[str, Any]] = []
    status_counts: Counter[str] = Counter()
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        paper_id = str(record.get("paper_id") or protocol.get("paper_id") or "")
        source_file = str(record.get("source_doc_id") or protocol.get("source_file") or "")
        candidates = by_paper.get(paper_id, [])
        matched = _best_source_match(source_file, candidates)
        status = (
            "matched" if matched else ("paper_id_not_in_inventory" if not candidates else "source_file_not_matched")
        )
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
