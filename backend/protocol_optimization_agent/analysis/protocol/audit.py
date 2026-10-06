from __future__ import annotations

from collections import Counter
from typing import Any

from protocol_optimization_agent.schemas import AuditFinding


def audit_protocol_analysis_outputs(
    protocol_records: list[dict[str, Any]],
    filtered_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    semantic_annotations: list[dict[str, Any]],
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    findings.extend(_audit_event_duplicates(filtered_events))
    findings.extend(_audit_quote_field_consistency(filtered_events))
    findings.extend(_audit_normalization_fallbacks(normalized_events))
    findings.extend(_audit_semantic_annotations(semantic_annotations))
    findings.extend(_audit_protocol_records(protocol_records))
    severity_counts = Counter(finding["severity"] for finding in findings)
    issue_counts = Counter(finding["issue_type"] for finding in findings)
    return {
        "summary": {
            "n_findings": len(findings),
            "severity_counts": dict(severity_counts),
            "issue_counts": dict(issue_counts),
        },
        "findings": findings,
    }


def _audit_event_duplicates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter(
        (
            event.get("protocol_id"),
            event.get("stage_id"),
            event.get("factor_type"),
            event.get("standardized_name") or event.get("factor_name"),
            event.get("day_start"),
            event.get("day_end"),
            event.get("concentration_value"),
        )
        for event in events
    )
    findings: list[dict[str, Any]] = []
    for key, count in counter.items():
        if count <= 3:
            continue
        protocol_id, stage_id, factor_type, factor_name, day_start, day_end, concentration = key
        findings.append(
            _finding(
                severity="low",
                issue_type="duplicate_factor_event_candidate",
                protocol_id=str(protocol_id or ""),
                stage_id=str(stage_id or ""),
                factor_name=str(factor_name or ""),
                message=f"{count} repeated {factor_type} event(s) with same stage, timing, and concentration.",
                evidence_quote="",
                check_source="rule",
            )
        )
    return findings


def _audit_quote_field_consistency(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for event in events:
        quote = str(event.get("evidence_quote") or "").lower()
        factor = str(event.get("standardized_name") or event.get("factor_name") or "").lower()
        unit = str(event.get("concentration_unit") or event.get("unit") or "").lower()
        if factor and len(factor) > 2 and factor not in quote:
            findings.append(
                _finding(
                    severity="medium",
                    issue_type="factor_name_not_visible_in_quote",
                    protocol_id=str(event.get("protocol_id") or ""),
                    stage_id=str(event.get("stage_id") or ""),
                    factor_name=str(event.get("standardized_name") or event.get("factor_name") or ""),
                    message="Factor name is not directly visible in the evidence quote; may be inherited from nearby context.",
                    evidence_quote=str(event.get("evidence_quote") or ""),
                    check_source="rule",
                )
            )
        if unit and unit not in quote and str(event.get("factor_type") or "") == "reagent":
            findings.append(
                _finding(
                    severity="low",
                    issue_type="concentration_unit_not_visible_in_quote",
                    protocol_id=str(event.get("protocol_id") or ""),
                    stage_id=str(event.get("stage_id") or ""),
                    factor_name=str(event.get("standardized_name") or event.get("factor_name") or ""),
                    message="Parsed concentration unit is not directly visible in the evidence quote.",
                    evidence_quote=str(event.get("evidence_quote") or ""),
                    check_source="rule",
                )
            )
    return findings[:1000]


def _audit_normalization_fallbacks(normalized_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for event in normalized_events:
        if event.get("normalization_source") != "fallback":
            continue
        findings.append(
            _finding(
                severity="low",
                issue_type="factor_normalization_fallback",
                protocol_id=str(event.get("protocol_id") or ""),
                stage_id=str(event.get("stage_id") or ""),
                factor_name=str(event.get("original_name") or ""),
                message="No ontology or LLM normalization matched this factor; original structured value was retained.",
                evidence_quote=str(event.get("evidence_quote") or ""),
                check_source="rule",
            )
        )
    return findings[:1000]


def _audit_semantic_annotations(annotations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for annotation in annotations:
        if "unknown" not in annotation.get("strategy_modules", []):
            continue
        findings.append(
            _finding(
                severity="low",
                issue_type="unknown_stage_semantic_role",
                protocol_id=str(annotation.get("protocol_id") or ""),
                stage_id=str(annotation.get("stage_id") or ""),
                factor_name=None,
                message="Stage biological role could not be inferred from structured fields and evidence.",
                evidence_quote=str(annotation.get("evidence_quote") or ""),
                check_source="rule",
            )
        )
    return findings


def _audit_protocol_records(protocol_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not record.get("comparison_ready", True):
            findings.append(
                _finding(
                    severity="medium",
                    issue_type="protocol_not_comparison_ready",
                    protocol_id=protocol_id,
                    stage_id=None,
                    factor_name=None,
                    message="Protocol is excluded from matrix/graph generation by quality gate.",
                    evidence_quote=str(protocol.get("evidence_quote") or ""),
                    check_source="rule",
                )
            )
    return findings


def _finding(
    *,
    severity: str,
    issue_type: str,
    protocol_id: str,
    stage_id: str | None,
    factor_name: str | None,
    message: str,
    evidence_quote: str,
    check_source: str,
) -> dict[str, Any]:
    return AuditFinding.model_validate(
        {
            "severity": severity,
            "issue_type": issue_type,
            "protocol_id": protocol_id,
            "stage_id": stage_id,
            "factor_name": factor_name,
            "message": message,
            "evidence_quote": evidence_quote,
            "check_source": check_source,
        }
    ).model_dump(mode="json")
