from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from protocol_optimization_agent.llm_client import LLMClient, OfflineLLMClient
from protocol_optimization_agent.schemas import FeatureSpec, FeatureSpecSet

DEFAULT_BASE_FEATURE_SPECS: list[dict[str, Any]] = []


def discover_and_compute_features(
    protocol_records: list[dict[str, Any]],
    analysis_events: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    feature_provenance: dict[str, Any],
    accepted_protocol_ids: set[str],
    quality_filters: dict[str, Any],
    llm_client: LLMClient | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any], dict[str, Any]]:
    base_specs, base_findings = _load_registry(
        Path(str(quality_filters.get("base_feature_registry_path") or "configs/base_feature_registry.json")),
        DEFAULT_BASE_FEATURE_SPECS,
        "base",
    )
    approved_specs, approved_findings = _load_registry(
        Path(str(quality_filters.get("approved_feature_registry_path") or "configs/approved_feature_registry.json")),
        [],
        "approved",
    )
    run_specs, run_findings = (
        _load_registry(Path(str(quality_filters.get("run_specific_feature_registry_path") or "")), [], "run_specific")
        if quality_filters.get("run_specific_feature_registry_path")
        else ([], [])
    )
    formal_registry_findings = [*base_findings, *approved_findings, *run_findings]
    if formal_registry_findings:
        raise FeatureSpecExecutionError(
            f"Formal feature registry contains invalid specs: {formal_registry_findings[:3]}"
        )
    llm_specs, discovery_report = _discover_llm_feature_specs(
        protocol_records,
        analysis_events,
        accepted_protocol_ids,
        llm_client or OfflineLLMClient(),
        int(quality_filters.get("max_llm_feature_discovery_protocols", 0)),
    )
    llm_specs, llm_source_findings = _audit_llm_specs_against_source(
        llm_specs,
        protocol_records,
        analysis_events,
        accepted_protocol_ids,
    )

    approved_registry, approved_dedupe_findings = _dedupe_specs([*base_specs, *approved_specs, *run_specs], "approved")
    if approved_dedupe_findings:
        raise FeatureSpecExecutionError(
            f"Formal feature registry contains duplicate specs: {approved_dedupe_findings[:3]}"
        )
    candidate_registry, candidate_dedupe_findings = _dedupe_specs([*approved_registry, *llm_specs], "candidate")
    final_rows, final_cell_provenance, final_compile_findings = _compute_registry_features(
        feature_rows, feature_provenance, analysis_events, protocol_records, approved_registry, require_executable=True
    )
    candidate_rows, candidate_cell_provenance, candidate_compile_findings = _compute_registry_features(
        feature_rows,
        feature_provenance,
        analysis_events,
        protocol_records,
        candidate_registry,
        require_executable=False,
    )
    cell_provenance = {
        "schema_version": "feature-cell-provenance-v1",
        "final_matrix": final_cell_provenance,
        "candidate_matrix": candidate_cell_provenance,
    }
    all_findings = [
        *discovery_report.get("findings", []),
        *base_findings,
        *approved_findings,
        *run_findings,
        *llm_source_findings,
        *approved_dedupe_findings,
        *candidate_dedupe_findings,
        *final_compile_findings,
        *candidate_compile_findings,
    ]
    summary = dict(discovery_report.get("summary", {}))
    summary["n_findings"] = len(all_findings)
    return (
        final_rows,
        candidate_rows,
        cell_provenance,
        _registry_payload(approved_registry),
        {
            **discovery_report,
            "summary": summary,
            "findings": all_findings,
            "base_feature_registry": _registry_payload(base_specs),
            "approved_feature_registry": _registry_payload(approved_registry),
            "llm_proposed_feature_registry": _registry_payload(llm_specs),
            "candidate_feature_registry": _registry_payload(candidate_registry),
        },
    )


def _discover_llm_feature_specs(
    protocol_records: list[dict[str, Any]],
    analysis_events: list[dict[str, Any]],
    accepted_protocol_ids: set[str],
    client: LLMClient,
    max_protocols: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    proposed: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if max_protocols <= 0:
        return [], {
            "schema_version": "feature-discovery-audit-v1",
            "summary": {"llm_calls": 0, "n_llm_proposed_features": 0},
            "findings": findings,
        }
    events_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in analysis_events:
        events_by_protocol[str(event.get("protocol_id") or "")].append(event)
    llm_calls = 0
    for record in protocol_records:
        protocol_id = str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or "")
        if not protocol_id or (accepted_protocol_ids and protocol_id not in accepted_protocol_ids):
            continue
        if llm_calls >= max_protocols:
            break
        response = client.complete_json(
            task="feature_discovery",
            schema_name="FeatureSpecSet",
            payload={
                "protocol_id": protocol_id,
                "protocol": record.get("protocol") or {},
                "stages": record.get("stages") or [],
                "events": events_by_protocol.get(protocol_id, []),
                "instruction": (
                    "Propose protocol-level feature columns for phenotype-relevant protocol design elements. "
                    "Return strict JSON FeatureSpecSet. value_rule must be one executable DSL rule: "
                    "presence, count, min(field), max(field), first_day, last_day, sum_duration, "
                    "category_set(field), ordered_sequence(field), transition_count, or co_occurrence. "
                    "Do not provide matrix values."
                ),
            },
        )
        llm_calls += 1
        if not response:
            continue
        response.setdefault("protocol_id", protocol_id)
        try:
            spec_set = FeatureSpecSet.model_validate(response)
        except ValidationError as exc:
            findings.append(
                {
                    "severity": "medium",
                    "issue_type": "llm_feature_spec_schema_failed",
                    "protocol_id": protocol_id,
                    "message": exc.errors()[0]["msg"],
                    "check_source": "schema",
                }
            )
            continue
        for spec in spec_set.proposed_features:
            payload = spec.model_dump(mode="json")
            payload["approved_status"] = "candidate"
            payload["proposed_by"] = "llm"
            proposed.append(payload)
    return proposed, {
        "schema_version": "feature-discovery-audit-v1",
        "summary": {"llm_calls": llm_calls, "n_llm_proposed_features": len(proposed), "n_findings": len(findings)},
        "findings": findings,
    }


def _compute_registry_features(
    feature_rows: list[dict[str, Any]],
    feature_provenance: dict[str, Any],
    analysis_events: list[dict[str, Any]],
    protocol_records: list[dict[str, Any]],
    specs: list[dict[str, Any]],
    require_executable: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    rows = deepcopy(feature_rows)
    provenance = deepcopy(feature_provenance)
    findings: list[dict[str, Any]] = []
    records_by_protocol = {
        str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or ""): record
        for record in protocol_records
    }
    events_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in analysis_events:
        events_by_protocol[str(event.get("protocol_id") or "")].append(event)
    cell_provenance: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        protocol_id = str(row.get("protocol_id") or "")
        for spec in specs:
            feature_name = str(spec["feature_name"])
            try:
                value, refs = _compute_feature_value(
                    spec, records_by_protocol.get(protocol_id, {}), events_by_protocol.get(protocol_id, [])
                )
            except FeatureSpecExecutionError as exc:
                findings.append(
                    {
                        "severity": "high" if require_executable else "medium",
                        "issue_type": "feature_spec_not_executable",
                        "feature_name": feature_name,
                        "protocol_id": protocol_id,
                        "message": str(exc),
                        "check_source": "compiler",
                    }
                )
                if require_executable:
                    raise
                continue
            row[feature_name] = value
            provenance.setdefault("protocols", {}).setdefault(protocol_id, {})[feature_name] = refs
            cell_provenance[protocol_id][feature_name] = {
                "protocol_id": protocol_id,
                "feature_name": feature_name,
                "value": value,
                "source_event_uids": [ref.get("event_id", "") for ref in refs if ref.get("event_id")],
                "evidence_quotes": [ref.get("quote", "") for ref in refs if ref.get("quote")],
                "computed_by": "pattern_extractor_v1",
                "feature_spec_source": f"{spec.get('proposed_by')}_{spec.get('approved_status')}",
                "n_provenance_refs": len(refs),
            }
    return rows, {"protocols": dict(cell_provenance), "feature_provenance": provenance}, findings


def _audit_llm_specs_against_source(
    specs: list[dict[str, Any]],
    protocol_records: list[dict[str, Any]],
    analysis_events: list[dict[str, Any]],
    accepted_protocol_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    records_by_protocol = {
        str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or ""): record
        for record in protocol_records
    }
    events_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in analysis_events:
        events_by_protocol[str(event.get("protocol_id") or "")].append(event)
    for spec in specs:
        matched_refs: list[dict[str, Any]] = []
        for protocol_id, record in records_by_protocol.items():
            if accepted_protocol_ids and protocol_id not in accepted_protocol_ids:
                continue
            matched_refs.extend(_matching_refs(spec, record, events_by_protocol.get(protocol_id, [])))
        if matched_refs:
            payload = dict(spec)
            payload["source_existence_status"] = "source_evidence_found"
            payload["source_evidence_ref_count"] = len(matched_refs)
            accepted.append(payload)
            continue
        rejected = dict(spec)
        rejected["approved_status"] = "rejected"
        rejected["source_existence_status"] = "no_source_evidence_found"
        findings.append(
            {
                "severity": "high",
                "issue_type": "llm_feature_spec_has_no_source_evidence",
                "feature_name": spec.get("feature_name", ""),
                "message": "LLM-proposed feature did not match any accepted protocol record, stage, or canonical event.",
                "check_source": "source_existence_auditor",
            }
        )
    return accepted, findings


def _compute_feature_value(
    spec: dict[str, Any], record: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[Any, list[dict[str, Any]]]:
    refs = _matching_refs(spec, record, events)
    feature_type = str(spec.get("feature_type") or "binary")
    value_rule = str(spec.get("value_rule") or "").strip()
    _validate_feature_rule(feature_type, value_rule)
    if feature_type == "binary":
        if value_rule == "co_occurrence":
            return int(_has_co_occurrence(spec, refs)), refs
        return int(bool(refs)), refs
    if feature_type == "count":
        if value_rule == "transition_count":
            return _transition_count(refs), refs
        return len(refs), refs
    if feature_type == "numeric_max":
        return _numeric_aggregate(value_rule, refs, max), refs
    if feature_type == "numeric_min":
        return _numeric_aggregate(value_rule, refs, min), refs
    if feature_type == "numeric_sum":
        return _numeric_sum(value_rule, refs), refs
    if feature_type == "categorical":
        if value_rule.startswith("ordered_sequence("):
            return _ordered_sequence(value_rule, refs), refs
        return _category_set(value_rule, refs), refs
    if feature_type == "text_flag":
        return "|".join(
            sorted({str(ref.get("matched_pattern") or "") for ref in refs if ref.get("matched_pattern")})
        ), refs
    raise FeatureSpecExecutionError(f"Unsupported feature_type: {feature_type}")


def _matching_refs(spec: dict[str, Any], record: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns = [str(pattern).lower() for pattern in spec.get("trigger_patterns", []) if str(pattern).strip()]
    required_evidence = bool(spec.get("required_evidence", True))
    refs = []
    for event in events:
        text = _event_text(event, spec.get("source_fields", []))
        matched = _matched_pattern(text, patterns)
        if matched and (not required_evidence or event.get("evidence_quote")):
            refs.append(_event_ref(event, matched))
    for stage in record.get("stages") or []:
        text = _stage_text(stage, spec.get("source_fields", []))
        matched = _matched_pattern(text, patterns)
        if matched and (not required_evidence or stage.get("evidence_quote")):
            refs.append(_stage_ref(stage, matched))
    return refs


def _matched_pattern(text: str, patterns: list[str]) -> str:
    normalized_text = _normalize(text)
    for pattern in patterns:
        if _normalize(pattern) in normalized_text:
            return pattern
    return ""


def _event_text(event: dict[str, Any], source_fields: list[str] | None = None) -> str:
    fields = _source_fields_for("event", source_fields)
    if source_fields and not fields:
        return ""
    fields = fields or (
        "factor_type",
        "action_type",
        "factor_name",
        "standardized_name",
        "normalized_name",
        "normalized_family",
        "pathway",
        "operation",
        "timing",
        "value_raw",
        "evidence_quote",
    )
    return " ".join(str(event.get(key) or "") for key in fields).lower()


def _stage_text(stage: dict[str, Any], source_fields: list[str] | None = None) -> str:
    fields = _source_fields_for("stage", source_fields)
    if source_fields and not fields:
        return ""
    fields = fields or ("stage_name", "key_operations", "evidence_quote")
    return " ".join(str(stage.get(key) or "") for key in fields).lower()


def _event_ref(event: dict[str, Any], matched_pattern: str) -> dict[str, Any]:
    return {
        "entity_type": "protocol_factor_event",
        "event_id": event.get("event_id") or "",
        "stage_id": event.get("stage_id") or "",
        "factor_name": event.get("normalized_name") or event.get("standardized_name") or event.get("factor_name") or "",
        "matched_pattern": matched_pattern,
        "quote": event.get("evidence_quote") or "",
        "source_location": event.get("source_location") or "",
        "day_start": event.get("day_start"),
        "day_end": event.get("day_end"),
        "duration_days": event.get("duration_days"),
        "concentration_value": event.get("concentration_value"),
        "concentration_unit": event.get("concentration_unit"),
        "factor_type": event.get("factor_type") or "",
        "action_type": event.get("action_type") or "",
        "normalized_family": event.get("normalized_family") or "",
    }


def _stage_ref(stage: dict[str, Any], matched_pattern: str) -> dict[str, Any]:
    return {
        "entity_type": "stage",
        "stage_id": stage.get("stage_id") or "",
        "stage_index": stage.get("stage_index"),
        "matched_pattern": matched_pattern,
        "quote": stage.get("evidence_quote") or "",
        "source_location": stage.get("source_location") or "",
        "day_start": stage.get("day_start"),
        "day_end": stage.get("day_end"),
        "duration_days": stage.get("duration_days"),
    }


def _load_registry(
    path: Path, default: list[dict[str, Any]], registry_name: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw = default
    findings: list[dict[str, Any]] = []
    if str(path) and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("features", data) if isinstance(data, dict) else data
    specs = []
    for index, item in enumerate(raw):
        try:
            spec = FeatureSpec.model_validate(item).model_dump(mode="json")
            _validate_feature_rule(str(spec.get("feature_type") or ""), str(spec.get("value_rule") or ""))
            specs.append(spec)
        except (ValidationError, FeatureSpecExecutionError) as exc:
            findings.append(
                {
                    "severity": "high",
                    "issue_type": "invalid_feature_spec",
                    "registry": registry_name,
                    "feature_name": item.get("feature_name", "") if isinstance(item, dict) else "",
                    "index": index,
                    "message": str(exc),
                    "check_source": "schema",
                }
            )
            continue
    return specs, findings


def _dedupe_specs(specs: list[dict[str, Any]], registry_name: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_name: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    for spec in specs:
        name = str(spec["feature_name"])
        if name in by_name:
            findings.append(
                {
                    "severity": "medium",
                    "issue_type": "duplicate_feature_spec",
                    "registry": registry_name,
                    "feature_name": name,
                    "message": "Duplicate feature name encountered; later spec takes precedence.",
                    "check_source": "registry",
                }
            )
        by_name[name] = spec
    return [by_name[name] for name in sorted(by_name)], findings


def _registry_payload(specs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"schema_version": "feature-registry-v1", "features": specs, "summary": {"n_features": len(specs)}}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


class FeatureSpecExecutionError(ValueError):
    pass


def _validate_feature_rule(feature_type: str, value_rule: str) -> None:
    rule = value_rule.strip()
    allowed = {
        "binary": {"presence", "co_occurrence"},
        "count": {"count", "transition_count"},
        "numeric_max": {"max(concentration_value)", "max(day_start)", "max(day_end)", "max(duration_days)", "last_day"},
        "numeric_min": {
            "min(concentration_value)",
            "min(day_start)",
            "min(day_end)",
            "min(duration_days)",
            "first_day",
        },
        "numeric_sum": {"sum_duration"},
        "categorical": {
            "category_set(factor_type)",
            "category_set(action_type)",
            "category_set(normalized_family)",
            "category_set(concentration_unit)",
            "ordered_sequence(normalized_family)",
            "ordered_sequence(action_type)",
        },
        "text_flag": {"category_set(matched_pattern)"},
    }
    if rule not in allowed.get(feature_type, set()):
        raise FeatureSpecExecutionError(
            f"FeatureSpec value_rule '{rule}' is not executable for feature_type '{feature_type}'."
        )


def _numeric_aggregate(value_rule: str, refs: list[dict[str, Any]], aggregate: Any) -> Any:
    field = _rule_field(value_rule)
    if value_rule == "first_day":
        field = "day_start"
    if value_rule == "last_day":
        field = "day_end"
    values = [_to_float(ref.get(field)) for ref in refs]
    present_values = [value for value in values if value is not None]
    if not present_values:
        return ""
    return aggregate(present_values)


def _numeric_sum(value_rule: str, refs: list[dict[str, Any]]) -> Any:
    if value_rule != "sum_duration":
        raise FeatureSpecExecutionError(f"Unsupported numeric_sum value_rule: {value_rule}")
    values = [_to_float(ref.get("duration_days")) for ref in refs]
    present_values = [value for value in values if value is not None]
    if not present_values:
        return ""
    return sum(present_values)


def _category_set(value_rule: str, refs: list[dict[str, Any]]) -> str:
    field = _rule_field(value_rule)
    values = [str(ref.get(field) or "") for ref in refs if str(ref.get(field) or "")]
    return "|".join(sorted(set(values)))


def _ordered_sequence(value_rule: str, refs: list[dict[str, Any]]) -> str:
    field = _rule_field(value_rule)
    ordered = sorted(
        refs,
        key=lambda ref: (
            _to_float(ref.get("day_start")) if _to_float(ref.get("day_start")) is not None else 10**9,
            _to_float(ref.get("stage_index")) if _to_float(ref.get("stage_index")) is not None else 10**9,
            str(ref.get("stage_id") or ""),
        ),
    )
    values = [str(ref.get(field) or "") for ref in ordered if str(ref.get(field) or "")]
    deduped: list[str] = []
    for value in values:
        if not deduped or deduped[-1] != value:
            deduped.append(value)
    return ">".join(deduped)


def _transition_count(refs: list[dict[str, Any]]) -> int:
    ordered = _ordered_matched_patterns(refs)
    if len(ordered) < 2:
        return 0
    return sum(1 for left, right in zip(ordered, ordered[1:], strict=False) if left != right)


def _has_co_occurrence(spec: dict[str, Any], refs: list[dict[str, Any]]) -> bool:
    expected = {_normalize(str(pattern)) for pattern in spec.get("trigger_patterns", []) if str(pattern).strip()}
    observed = {_normalize(str(ref.get("matched_pattern") or "")) for ref in refs if ref.get("matched_pattern")}
    return bool(expected) and expected.issubset(observed)


def _ordered_matched_patterns(refs: list[dict[str, Any]]) -> list[str]:
    ordered = sorted(
        refs,
        key=lambda ref: (
            _to_float(ref.get("day_start")) if _to_float(ref.get("day_start")) is not None else 10**9,
            _to_float(ref.get("stage_index")) if _to_float(ref.get("stage_index")) is not None else 10**9,
            str(ref.get("stage_id") or ""),
            str(ref.get("matched_pattern") or ""),
        ),
    )
    return [str(ref.get("matched_pattern") or "") for ref in ordered if ref.get("matched_pattern")]


def _rule_field(value_rule: str) -> str:
    match = re.match(r"^[a-z_]+\(([^)]+)\)$", value_rule)
    if match:
        return match.group(1)
    if value_rule == "category_set(matched_pattern)":
        return "matched_pattern"
    return ""


def _source_fields_for(entity_type: str, source_fields: list[str] | None) -> tuple[str, ...]:
    if not source_fields:
        return ()
    prefix = "stage." if entity_type == "stage" else "event."
    fields = []
    for field in source_fields:
        text = str(field)
        if "." not in text:
            fields.append(text)
        elif text.startswith(prefix):
            fields.append(text.split(".", 1)[1])
    return tuple(fields)


def _to_float(value: Any) -> float | None:
    try:
        if value in {"", None}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
