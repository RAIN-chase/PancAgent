from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from protocol_optimization_agent.runtime.llm_client import LLMClient, OfflineLLMClient
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
    proposed_llm_specs, discovery_report = _discover_llm_feature_specs(
        protocol_records,
        analysis_events,
        accepted_protocol_ids,
        llm_client or OfflineLLMClient(),
        int(quality_filters.get("max_llm_feature_discovery_protocols", 0)),
    )
    llm_specs, llm_source_findings = _audit_llm_specs_against_source(
        proposed_llm_specs,
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
    feature_spec_validation_report = _build_feature_spec_validation_report(
        proposed_llm_specs,
        protocol_records,
        analysis_events,
        accepted_protocol_ids,
        candidate_rows,
    )
    validated_names = {
        str(item.get("feature_name") or "")
        for item in feature_spec_validation_report.get("features", [])
        if item.get("status") == "pass"
    }
    validated_llm_specs = [spec for spec in llm_specs if str(spec.get("feature_name") or "") in validated_names]
    if len(validated_llm_specs) != len(llm_specs):
        candidate_registry, candidate_dedupe_findings = _dedupe_specs(
            [*approved_registry, *validated_llm_specs], "candidate"
        )
        candidate_rows, candidate_cell_provenance, candidate_compile_findings = _compute_registry_features(
            feature_rows,
            feature_provenance,
            analysis_events,
            protocol_records,
            candidate_registry,
            require_executable=False,
        )
    proposed_llm_specs = _attach_validation_statuses(proposed_llm_specs, feature_spec_validation_report)
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
        *_feature_spec_validation_findings(feature_spec_validation_report),
    ]
    summary = dict(discovery_report.get("summary", {}))
    summary["n_findings"] = len(all_findings)
    summary["n_validated_llm_features"] = feature_spec_validation_report["summary"]["n_passed"]
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
            "llm_proposed_feature_registry": _registry_payload(proposed_llm_specs),
            "candidate_feature_registry": _registry_payload(candidate_registry),
            "feature_spec_validation_report": feature_spec_validation_report,
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
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
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
                    "category_set(field), ordered_sequence(field), transition_count, co_occurrence, "
                    "co_occurs_within_stage, conditional_count, or duration_between_events. "
                    "Use stage_filter, before_patterns, after_patterns, and co_occurs_within_stage when a feature "
                    "depends on stage scope, ordering, or same-stage combinations. "
                    "Declare precise source_fields; deterministic validation will execute positive, negative, "
                    "stage-scope, unit, missing-value, and matrix-recomputation checks. "
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
            if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
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


def _build_feature_spec_validation_report(
    specs: list[dict[str, Any]],
    protocol_records: list[dict[str, Any]],
    analysis_events: list[dict[str, Any]],
    accepted_protocol_ids: set[str],
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    records_by_protocol = {
        str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or ""): record
        for record in protocol_records
    }
    events_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in analysis_events:
        events_by_protocol[str(event.get("protocol_id") or "")].append(event)
    matrix_by_protocol = {str(row.get("protocol_id") or ""): row for row in candidate_rows}
    validations = [
        _validate_feature_spec(
            spec,
            records_by_protocol,
            events_by_protocol,
            accepted_protocol_ids,
            matrix_by_protocol,
        )
        for spec in specs
    ]
    n_passed = sum(item["status"] == "pass" for item in validations)
    return {
        "schema_version": "feature-spec-validation-v1",
        "summary": {
            "n_features": len(validations),
            "n_passed": n_passed,
            "n_failed": len(validations) - n_passed,
        },
        "features": validations,
    }


def _validate_feature_spec(
    spec: dict[str, Any],
    records_by_protocol: dict[str, dict[str, Any]],
    events_by_protocol: dict[str, list[dict[str, Any]]],
    accepted_protocol_ids: set[str],
    matrix_by_protocol: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    feature_name = str(spec.get("feature_name") or "")
    observed_cases = []
    detail_checks: list[bool] = []
    recomputation_checks: list[bool] = []
    execution_errors: list[str] = []
    for protocol_id in sorted(accepted_protocol_ids):
        value, refs, execution_error = _safe_compute_feature_value(
            spec,
            records_by_protocol.get(protocol_id, {}),
            events_by_protocol.get(protocol_id, []),
        )
        if execution_error:
            execution_errors.append(execution_error)
        case_type = "positive" if _has_feature_value(spec, value) else "negative"
        observed_cases.append(
            {
                "case_id": f"observed:{protocol_id}",
                "case_type": case_type,
                "protocol_id": protocol_id,
                "expected_value": value,
                "source_refs": refs,
                "execution_error": execution_error,
            }
        )
        if refs:
            detail_checks.extend(_ref_preserves_required_detail(spec, ref) for ref in refs)
        matrix_row = matrix_by_protocol.get(protocol_id, {})
        recomputation_checks.append(
            not execution_error and feature_name in matrix_row and matrix_row.get(feature_name) == value
        )
    missing_value, missing_refs, missing_error = _safe_compute_feature_value(spec, {}, [])
    if missing_error:
        execution_errors.append(missing_error)
    missing_expected = _missing_feature_value(spec)
    relation_cases = _relation_validation_cases(spec)
    source_evidence_found = any(case["source_refs"] for case in observed_cases)
    detail_status = "pass" if source_evidence_found and all(detail_checks or [True]) else "fail"
    quantification_status = (
        "pass" if matrix_by_protocol and all(feature_name in row for row in matrix_by_protocol.values()) else "fail"
    )
    recomputation_status = "pass" if recomputation_checks and all(recomputation_checks) else "fail"
    unit_status = _unit_validation_status(spec, observed_cases)
    stage_status = _stage_validation_status(spec, observed_cases)
    missing_status = "pass" if not missing_error and missing_value == missing_expected and not missing_refs else "fail"
    relation_status = "pass" if all(case["passed"] for case in relation_cases) else "fail"
    dimension_statuses = {
        "source_detail_preservation": detail_status,
        "quantification_coverage": quantification_status,
        "matrix_recomputation_consistency": recomputation_status,
    }
    auxiliary_statuses = {
        "source_evidence": "pass" if source_evidence_found else "fail",
        "unit_validation": unit_status,
        "stage_scope_validation": stage_status,
        "missing_value_semantics": missing_status,
        "relation_case_validation": relation_status,
    }
    checked_statuses = [*dimension_statuses.values(), *auxiliary_statuses.values()]
    return {
        "feature_name": feature_name,
        "status": "pass" if all(status in {"pass", "not_applicable"} for status in checked_statuses) else "fail",
        "source_fields": spec.get("source_fields", []),
        "dimensions": dimension_statuses,
        "checks": auxiliary_statuses,
        "missing_value_policy": _missing_value_policy(spec),
        "execution_errors": sorted(set(execution_errors)),
        "observed_positive_cases": [case for case in observed_cases if case["case_type"] == "positive"],
        "observed_negative_cases": [case for case in observed_cases if case["case_type"] == "negative"],
        "controlled_negative_case": {
            "case_id": "controlled:empty_source",
            "expected_value": missing_expected,
            "actual_value": missing_value,
            "passed": missing_status == "pass",
        },
        "relation_cases": relation_cases,
    }


def _safe_compute_feature_value(
    spec: dict[str, Any], record: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[Any, list[dict[str, Any]], str]:
    try:
        value, refs = _compute_feature_value(spec, record, events)
        return value, refs, ""
    except FeatureSpecExecutionError as exc:
        return _missing_feature_value(spec), _matching_refs(spec, record, events), str(exc)


def _attach_validation_statuses(specs: list[dict[str, Any]], report: dict[str, Any]) -> list[dict[str, Any]]:
    validations = {str(item.get("feature_name") or ""): item for item in report.get("features", [])}
    enriched = []
    for spec in specs:
        payload = dict(spec)
        validation = validations.get(str(spec.get("feature_name") or ""), {})
        payload["validation_status"] = validation.get("status", "fail")
        payload["validation_report_ref"] = f"feature_spec_validation_report.json#{spec.get('feature_name', '')}"
        enriched.append(payload)
    return enriched


def _feature_spec_validation_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "severity": "high",
            "issue_type": "llm_feature_spec_validation_failed",
            "feature_name": item.get("feature_name", ""),
            "message": "LLM-proposed FeatureSpec failed deterministic validation and was excluded from candidates.",
            "check_source": "feature_spec_validator",
        }
        for item in report.get("features", [])
        if item.get("status") != "pass"
    ]


def _has_feature_value(spec: dict[str, Any], value: Any) -> bool:
    if str(spec.get("feature_type") or "") in {"binary", "count"}:
        return bool(_to_float(value))
    return value not in {"", None}


def _missing_feature_value(spec: dict[str, Any]) -> Any:
    return 0 if str(spec.get("feature_type") or "") in {"binary", "count"} else ""


def _missing_value_policy(spec: dict[str, Any]) -> str:
    if str(spec.get("feature_type") or "") in {"binary", "count"}:
        return "0 means not observed in accepted structured input; it does not prove biological absence"
    return "empty means not reported or not safely comparable; it must not be imputed as zero"


def _ref_preserves_required_detail(spec: dict[str, Any], ref: dict[str, Any]) -> bool:
    if not ref.get("matched_pattern") or not ref.get("stage_id"):
        return False
    if spec.get("required_evidence", True) and not ref.get("quote"):
        return False
    if "concentration_value" in str(spec.get("value_rule") or ""):
        return all(
            ref.get(field) not in {None, ""}
            for field in ("concentration_value", "concentration_unit", "concentration_comparability_status")
        )
    return True


def _unit_validation_status(spec: dict[str, Any], observed_cases: list[dict[str, Any]]) -> str:
    if "concentration_value" not in str(spec.get("value_rule") or ""):
        return "not_applicable"
    positive_refs = [ref for case in observed_cases for ref in case["source_refs"]]
    if not positive_refs:
        return "fail"
    return "pass" if _comparable_concentration_field(positive_refs) else "fail"


def _stage_validation_status(spec: dict[str, Any], observed_cases: list[dict[str, Any]]) -> str:
    stage_filter = spec.get("stage_filter") or {}
    if not stage_filter:
        return "not_applicable"
    refs = [ref for case in observed_cases for ref in case["source_refs"]]
    return "pass" if refs and all(_matches_stage_filter(ref, stage_filter) for ref in refs) else "fail"


def _relation_validation_cases(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rule = str(spec.get("value_rule") or "")
    patterns = [str(pattern) for pattern in spec.get("trigger_patterns", []) if str(pattern).strip()]
    if rule not in {"co_occurrence", "co_occurs_within_stage"} or len(patterns) < 2:
        return []
    first, second = patterns[:2]
    stage_filter = spec.get("stage_filter") or {}
    cross_stage_expected = int(rule == "co_occurrence" and "stage_id" not in stage_filter)
    definitions = [
        ("only_a", [("s1", first)], 0),
        ("only_b", [("s1", second)], 0),
        ("same_stage_a_b", [("s1", first), ("s1", second)], 1),
        ("cross_stage_a_b", [("s1", first), ("s2", second)], cross_stage_expected),
        ("neither_reported", [], 0),
    ]
    cases = []
    for case_name, items, expected in definitions:
        record, events = _synthetic_relation_input(spec, items)
        actual, _ = _compute_feature_value(spec, record, events)
        cases.append(
            {
                "case_id": f"controlled:{case_name}",
                "expected_value": expected,
                "actual_value": actual,
                "passed": actual == expected,
            }
        )
    return cases


def _synthetic_relation_input(
    spec: dict[str, Any], items: list[tuple[str, str]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stage_filter = spec.get("stage_filter") or {}
    stages_by_id: dict[str, dict[str, Any]] = {}
    events = []
    for index, (default_stage_id, pattern) in enumerate(items, start=1):
        configured_stage_id = str(stage_filter.get("stage_id") or "")
        stage_id = configured_stage_id if default_stage_id == "s1" and configured_stage_id else default_stage_id
        configured_stage_index = stage_filter.get("stage_index")
        stage_index = configured_stage_index if default_stage_id == "s1" and configured_stage_index else index
        stage = stages_by_id.setdefault(
            stage_id,
            {
                "stage_id": stage_id,
                "stage_index": stage_index,
                "stage_name": pattern,
                "key_operations": pattern,
                "evidence_quote": pattern,
            },
        )
        stage["stage_name"] = f"{stage['stage_name']} {pattern}"
        stage["evidence_quote"] = f"{stage['evidence_quote']} {pattern}"
        events.append(
            {
                "event_id": f"validation-{index}",
                "protocol_id": "validation",
                "stage_id": stage_id,
                "stage_index": stage_index,
                "factor_type": str(stage_filter.get("factor_type") or pattern),
                "action_type": str(stage_filter.get("action_type") or pattern),
                "factor_name": pattern,
                "standardized_name": pattern,
                "normalized_name": pattern,
                "normalized_family": pattern,
                "operation": pattern,
                "evidence_quote": pattern,
            }
        )
    return {"stages": list(stages_by_id.values())}, events


def _compute_feature_value(
    spec: dict[str, Any], record: dict[str, Any], events: list[dict[str, Any]]
) -> tuple[Any, list[dict[str, Any]]]:
    refs = _matching_refs(spec, record, events)
    feature_type = str(spec.get("feature_type") or "binary")
    value_rule = str(spec.get("value_rule") or "").strip()
    _validate_feature_rule(feature_type, value_rule)
    if feature_type == "binary":
        if value_rule == "co_occurrence":
            return int(_has_all_trigger_patterns(spec, refs)), refs
        if value_rule == "co_occurs_within_stage":
            return int(bool(refs)), refs
        return int(bool(refs)), refs
    if feature_type == "count":
        if value_rule == "transition_count":
            return _transition_count(refs), refs
        if value_rule == "conditional_count":
            return len(refs), refs
        return len(refs), refs
    if feature_type == "numeric_max":
        return _numeric_aggregate(value_rule, refs, max), refs
    if feature_type == "numeric_min":
        return _numeric_aggregate(value_rule, refs, min), refs
    if feature_type == "numeric_sum":
        if value_rule == "duration_between_events":
            return _duration_between_events(spec, record, events), refs
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
    events = [event for event in events if _matches_stage_filter(event, spec.get("stage_filter", {}))]
    refs = []
    for event in events:
        text = _event_text(event, spec.get("source_fields", []))
        matched = _matched_pattern(text, patterns)
        if matched and (not required_evidence or event.get("evidence_quote")):
            refs.append(_event_ref(event, matched))
    for stage in _filtered_stages(record.get("stages") or [], spec.get("stage_filter", {})):
        text = _stage_text(stage, spec.get("source_fields", []))
        matched = _matched_pattern(text, patterns)
        if matched and (not required_evidence or stage.get("evidence_quote")):
            refs.append(_stage_ref(stage, matched))
    return _apply_relation_constraints(spec, refs, record, events)


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
        "event_identity": _event_identity(event),
        "event_id": event.get("event_id") or "",
        "stage_id": event.get("stage_id") or "",
        "stage_index": event.get("stage_index"),
        "factor_name": event.get("normalized_name") or event.get("standardized_name") or event.get("factor_name") or "",
        "matched_pattern": matched_pattern,
        "quote": event.get("evidence_quote") or "",
        "source_location": event.get("source_location") or "",
        "day_start": event.get("day_start"),
        "day_end": event.get("day_end"),
        "duration_days": event.get("duration_days"),
        "concentration_value": event.get("concentration_value"),
        "concentration_unit": event.get("concentration_unit"),
        "concentration_standard_value": event.get("concentration_standard_value"),
        "concentration_standard_unit": event.get("concentration_standard_unit"),
        "concentration_comparability_status": event.get("concentration_comparability_status"),
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


def _is_accepted_protocol(protocol_id: str, accepted_protocol_ids: set[str]) -> bool:
    return bool(protocol_id) and protocol_id in accepted_protocol_ids


class FeatureSpecExecutionError(ValueError):
    pass


def _validate_feature_rule(feature_type: str, value_rule: str) -> None:
    rule = value_rule.strip()
    allowed = {
        "binary": {"presence", "co_occurrence", "co_occurs_within_stage"},
        "count": {"count", "transition_count", "conditional_count"},
        "numeric_max": {"max(concentration_value)", "max(day_start)", "max(day_end)", "max(duration_days)", "last_day"},
        "numeric_min": {
            "min(concentration_value)",
            "min(day_start)",
            "min(day_end)",
            "min(duration_days)",
            "first_day",
        },
        "numeric_sum": {"sum_duration", "duration_between_events"},
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
    if field == "concentration_value":
        field = _comparable_concentration_field(refs)
        if not field:
            return ""
    if value_rule == "first_day":
        field = "day_start"
    if value_rule == "last_day":
        field = "day_end"
    values = [_to_float(ref.get(field)) for ref in refs]
    present_values = [value for value in values if value is not None]
    if not present_values:
        return ""
    return aggregate(present_values)


def _comparable_concentration_field(refs: list[dict[str, Any]]) -> str:
    standardized_refs = [
        ref for ref in refs if str(ref.get("concentration_comparability_status") or "") == "standardized"
    ]
    if not standardized_refs:
        return ""
    units = {str(ref.get("concentration_standard_unit") or "") for ref in standardized_refs}
    return "concentration_standard_value" if len(units) == 1 else ""


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


def _has_all_trigger_patterns(spec: dict[str, Any], refs: list[dict[str, Any]]) -> bool:
    expected = {_normalize(str(pattern)) for pattern in spec.get("trigger_patterns", []) if str(pattern).strip()}
    observed = {_normalize(str(ref.get("matched_pattern") or "")) for ref in refs if ref.get("matched_pattern")}
    return bool(expected) and expected.issubset(observed)


def _matches_stage_filter(event: dict[str, Any], stage_filter: dict[str, Any] | None) -> bool:
    if not stage_filter:
        return True
    return _matches_exact_filter(event, stage_filter) and _matches_numeric_filter(event, stage_filter)


def _filtered_stages(stages: list[dict[str, Any]], stage_filter: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not stage_filter:
        return stages
    return [
        stage
        for stage in stages
        if _matches_exact_filter(stage, stage_filter) and _matches_numeric_filter(stage, stage_filter)
    ]


def _matches_exact_filter(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key in ("stage_id", "stage_index", "factor_type", "action_type"):
        if key not in filters:
            continue
        expected = str(filters.get(key) or "")
        if expected and str(row.get(key) or "") != expected:
            return False
    return True


def _matches_numeric_filter(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    numeric_rules = {
        "day_start_min": ("day_start", lambda actual, expected: actual >= expected),
        "day_start_max": ("day_start", lambda actual, expected: actual <= expected),
        "day_end_min": ("day_end", lambda actual, expected: actual >= expected),
        "day_end_max": ("day_end", lambda actual, expected: actual <= expected),
        "stage_index_min": ("stage_index", lambda actual, expected: actual >= expected),
        "stage_index_max": ("stage_index", lambda actual, expected: actual <= expected),
    }
    for filter_key, (field, predicate) in numeric_rules.items():
        if filter_key not in filters:
            continue
        actual = _to_float(row.get(field))
        expected = _to_float(filters.get(filter_key))
        if actual is None or expected is None or not predicate(actual, expected):
            return False
    return True


def _apply_relation_constraints(
    spec: dict[str, Any],
    refs: list[dict[str, Any]],
    record: dict[str, Any],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = _filter_refs_by_relative_patterns(refs, spec.get("before_patterns", []), events, before=True)
    refs = _filter_refs_by_relative_patterns(refs, spec.get("after_patterns", []), events, before=False)
    co_patterns = [str(pattern) for pattern in spec.get("co_occurs_within_stage", []) if str(pattern).strip()]
    if co_patterns or str(spec.get("value_rule") or "") == "co_occurs_within_stage":
        refs = _filter_refs_by_stage_co_occurrence(
            refs, co_patterns or spec.get("trigger_patterns", []), record, events
        )
    return refs


def _filter_refs_by_relative_patterns(
    refs: list[dict[str, Any]], patterns: list[str], events: list[dict[str, Any]], before: bool
) -> list[dict[str, Any]]:
    relation_patterns = [str(pattern) for pattern in patterns if str(pattern).strip()]
    if not relation_patterns:
        return refs
    anchors = [
        _event_ref(event, _matched_pattern(_event_text(event), relation_patterns))
        for event in events
        if _matched_pattern(_event_text(event), relation_patterns)
    ]
    if not anchors:
        return []
    filtered = []
    for ref in refs:
        ref_time = _ref_time(ref)
        for anchor in anchors:
            anchor_time = _ref_time(anchor)
            if ref_time is None or anchor_time is None:
                continue
            if (before and ref_time < anchor_time) or (not before and ref_time > anchor_time):
                filtered.append(ref)
                break
    return filtered


def _filter_refs_by_stage_co_occurrence(
    refs: list[dict[str, Any]], patterns: list[str], record: dict[str, Any], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = [_normalize(str(pattern)) for pattern in patterns if str(pattern).strip()]
    if not expected:
        return refs
    stage_texts: dict[str, str] = defaultdict(str)
    for event in events:
        stage_id = str(event.get("stage_id") or "")
        if stage_id:
            stage_texts[stage_id] += " " + _event_text(event)
    for stage in record.get("stages") or []:
        stage_id = str(stage.get("stage_id") or "")
        if stage_id:
            stage_texts[stage_id] += " " + _stage_text(stage)
    valid_stage_ids = {
        stage_id for stage_id, text in stage_texts.items() if all(pattern in _normalize(text) for pattern in expected)
    }
    return [ref for ref in refs if str(ref.get("stage_id") or "") in valid_stage_ids]


def _duration_between_events(spec: dict[str, Any], record: dict[str, Any], events: list[dict[str, Any]]) -> Any:
    start_patterns = spec.get("after_patterns") or spec.get("trigger_patterns") or []
    end_patterns = spec.get("before_patterns") or []
    if not start_patterns or not end_patterns:
        raise FeatureSpecExecutionError("duration_between_events requires trigger/after patterns and before_patterns.")
    source_fields = _anchor_source_fields(spec.get("source_fields", []))
    starts = _pattern_times(start_patterns, record, events, source_fields)
    ends = _pattern_times(end_patterns, record, events, source_fields)
    if not starts or not ends:
        return ""
    deltas = [end - start for start in starts for end in ends if end >= start]
    return min(deltas) if deltas else ""


def _pattern_times(
    patterns: list[str],
    record: dict[str, Any],
    events: list[dict[str, Any]],
    source_fields: list[str] | None,
) -> list[float]:
    times = []
    for event in events:
        if _matched_pattern(_event_text(event, source_fields), patterns):
            time = _ref_time(_event_ref(event, ""))
            if time is not None:
                times.append(time)
    for stage in record.get("stages") or []:
        if _matched_pattern(_stage_text(stage, source_fields), patterns):
            time = _ref_time(_stage_ref(stage, ""))
            if time is not None:
                times.append(time)
    return times


def _anchor_source_fields(source_fields: list[str] | None) -> list[str]:
    fields = [
        field
        for field in source_fields or []
        if str(field).split(".")[-1] not in {"evidence_quote", "quote", "source_location"}
    ]
    return fields or [
        "event.factor_name",
        "event.standardized_name",
        "event.normalized_name",
        "event.normalized_family",
        "event.action_type",
        "event.factor_type",
        "stage.stage_name",
    ]


def _ref_time(ref: dict[str, Any]) -> float | None:
    for field in ("day_start", "day_end", "stage_index"):
        value = _to_float(ref.get(field))
        if value is not None:
            return value
    return None


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


def _event_identity(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "")
    if event_id:
        return event_id
    return "|".join(
        str(event.get(key) or "")
        for key in ("protocol_id", "stage_id", "factor_type", "factor_name", "day_start", "day_end", "operation")
    )
