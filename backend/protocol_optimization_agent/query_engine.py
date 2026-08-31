from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any


class ProtocolQueryEngine:
    """Query structured Protocol Analysis Agent outputs.

    This layer is intentionally deterministic. A chat LLM can call these methods
    later, but the evidence retrieval and protocol diff calculation stay local.
    """

    def __init__(self, output_dir: Path | str) -> None:
        self.output_dir = Path(output_dir)
        self._matrix_rows: list[dict[str, str]] | None = None
        self._normalized_events: list[dict[str, Any]] | None = None
        self._semantic_annotations: list[dict[str, Any]] | None = None
        self._strategy_summary: dict[str, Any] | None = None
        self._relationship_report: dict[str, Any] | None = None
        self._feature_provenance: dict[str, Any] | None = None
        self._audit_report: dict[str, Any] | None = None

    def find_protocols(self, query: str) -> list[dict[str, Any]]:
        query_tokens = _search_tokens(query)
        query_norm = _normalize_search_text(query)
        hits: list[dict[str, Any]] = []
        for row in self.matrix_rows:
            haystack_raw = " ".join(str(row.get(key) or "") for key in ("protocol_id", "paper_id", "dataset_id", "protocol_name"))
            haystack_norm = _normalize_search_text(haystack_raw)
            haystack_tokens = set(_search_tokens(haystack_raw))
            matched_tokens = sorted(token for token in query_tokens if token in haystack_tokens or token in haystack_norm)
            score = _search_score(query_norm, query_tokens, haystack_norm, haystack_tokens)
            if not query_norm or score > 0:
                hits.append(
                    {
                        "protocol_id": row.get("protocol_id", ""),
                        "paper_id": row.get("paper_id", ""),
                        "dataset_id": row.get("dataset_id", ""),
                        "protocol_name": row.get("protocol_name", ""),
                        "confidence_score": row.get("confidence_score", ""),
                        "comparison_ready": row.get("comparison_ready", ""),
                        "match_score": round(score, 3),
                        "matched_tokens": matched_tokens,
                    }
                )
        return sorted(hits, key=lambda item: (-float(item["match_score"]), item["protocol_id"]))

    def get_protocol_summary(self, protocol_id: str) -> dict[str, Any]:
        matrix = self._matrix_by_protocol().get(protocol_id, {})
        strategy = self._strategy_by_protocol().get(protocol_id, {})
        audit = self.get_audit_findings(protocol_id)
        return {
            "protocol_id": protocol_id,
            "metadata": {key: matrix.get(key, "") for key in _metadata_keys()},
            "strategy_summary": strategy,
            "n_audit_findings": len(audit),
            "audit_issue_types": sorted({finding.get("issue_type", "") for finding in audit if finding.get("issue_type")}),
        }

    def compare_protocols(self, protocol_id_a: str, protocol_id_b: str, max_features: int = 40) -> dict[str, Any]:
        matrix_by_protocol = self._matrix_by_protocol()
        left = matrix_by_protocol.get(protocol_id_a)
        right = matrix_by_protocol.get(protocol_id_b)
        if left is None or right is None:
            missing = [protocol_id for protocol_id, row in ((protocol_id_a, left), (protocol_id_b, right)) if row is None]
            return {"status": "not_found", "missing_protocol_ids": missing}

        feature_differences = _feature_differences(left, right, max_features=max_features)
        grouped_feature_differences = _group_feature_differences(feature_differences)
        factor_diff = self.get_factor_diff(protocol_id_a, protocol_id_b)
        stage_diff = self.get_stage_diff(protocol_id_a, protocol_id_b)
        relationship = self._find_pair_relationship(protocol_id_a, protocol_id_b)
        return {
            "status": "ok",
            "protocol_a": self.get_protocol_summary(protocol_id_a),
            "protocol_b": self.get_protocol_summary(protocol_id_b),
            "relationship": relationship,
            "feature_differences": feature_differences,
            "grouped_feature_differences": grouped_feature_differences,
            "factor_diff": factor_diff,
            "stage_diff": stage_diff,
            "audit_warnings": {
                protocol_id_a: self.get_audit_findings(protocol_id_a)[:20],
                protocol_id_b: self.get_audit_findings(protocol_id_b)[:20],
            },
        }

    def answer_question(self, question: str, max_features: int = 30) -> dict[str, Any]:
        candidates = _split_comparison_question(question)
        if len(candidates) >= 2:
            left_hits = self.find_protocols(candidates[0])
            right_hits = self.find_protocols(candidates[1])
            if left_hits and right_hits:
                comparison = self.compare_protocols(
                    left_hits[0]["protocol_id"],
                    right_hits[0]["protocol_id"],
                    max_features=max_features,
                )
                return {
                    "status": "ok",
                    "intent": "compare_protocols",
                    "resolved_protocols": [left_hits[0], right_hits[0]],
                    "answer": _deterministic_comparison_answer(comparison),
                    "tool_result": comparison,
                }
            return {
                "status": "not_found",
                "intent": "compare_protocols",
                "query_parts": candidates,
                "left_hits": left_hits,
                "right_hits": right_hits,
            }
        hits = self.find_protocols(question)
        return {
            "status": "ok",
            "intent": "find_protocols",
            "answer": f"Found {len(hits)} protocol candidate(s).",
            "results": hits,
        }

    def get_stage_diff(self, protocol_id_a: str, protocol_id_b: str) -> dict[str, Any]:
        by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for annotation in self.semantic_annotations:
            by_protocol[str(annotation.get("protocol_id") or "")].append(annotation)
        modules_a = _semantic_modules(by_protocol.get(protocol_id_a, []))
        modules_b = _semantic_modules(by_protocol.get(protocol_id_b, []))
        return {
            "protocol_a_n_annotated_stages": len(by_protocol.get(protocol_id_a, [])),
            "protocol_b_n_annotated_stages": len(by_protocol.get(protocol_id_b, [])),
            "shared_strategy_modules": sorted(modules_a & modules_b),
            "protocol_a_specific_strategy_modules": sorted(modules_a - modules_b),
            "protocol_b_specific_strategy_modules": sorted(modules_b - modules_a),
            "protocol_a_annotations": by_protocol.get(protocol_id_a, [])[:20],
            "protocol_b_annotations": by_protocol.get(protocol_id_b, [])[:20],
        }

    def get_factor_diff(self, protocol_id_a: str, protocol_id_b: str) -> dict[str, Any]:
        families_by_protocol: dict[str, set[str]] = defaultdict(set)
        events_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in self.normalized_events:
            protocol_id = str(event.get("protocol_id") or "")
            family = str(event.get("normalized_family") or "")
            if family:
                families_by_protocol[protocol_id].add(family)
            events_by_protocol[protocol_id].append(event)
        left = families_by_protocol.get(protocol_id_a, set())
        right = families_by_protocol.get(protocol_id_b, set())
        return {
            "shared_normalized_families": sorted(left & right),
            "protocol_a_specific_normalized_families": sorted(left - right),
            "protocol_b_specific_normalized_families": sorted(right - left),
            "protocol_a_representative_events": _representative_events(events_by_protocol.get(protocol_id_a, [])),
            "protocol_b_representative_events": _representative_events(events_by_protocol.get(protocol_id_b, [])),
        }

    def get_evidence_for_feature(self, protocol_id: str, feature: str) -> list[dict[str, Any]]:
        return (
            self.feature_provenance.get("protocols", {})
            .get(protocol_id, {})
            .get(feature, [])
        )

    def get_protocol_events(self, protocol_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        events = [
            event
            for event in self.normalized_events
            if str(event.get("protocol_id") or "") == protocol_id
        ]
        return events[:limit] if limit else events

    def query_matrix(
        self,
        filters: dict[str, Any] | None = None,
        columns: list[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        filters = filters or {}
        rows = []
        for row in self.matrix_rows:
            if not all(str(row.get(key, "")) == str(value) for key, value in filters.items()):
                continue
            rows.append({column: row.get(column, "") for column in columns} if columns else dict(row))
        return rows[:limit] if limit else rows

    def explain_cell(self, protocol_id: str, feature: str) -> dict[str, Any]:
        row = self._matrix_by_protocol().get(protocol_id, {})
        evidence = self.get_evidence_for_feature(protocol_id, feature)
        return {
            "protocol_id": protocol_id,
            "feature": feature,
            "value": row.get(feature, ""),
            "evidence": evidence,
            "n_evidence_items": len(evidence),
            "audit_findings": [
                finding
                for finding in self.get_audit_findings(protocol_id)
                if str(finding.get("feature") or "") == feature
            ],
        }

    def propose_feature_specs_context(self, protocol_ids: list[str] | None = None, limit_events: int = 200) -> dict[str, Any]:
        selected = set(protocol_ids or [])
        protocols = [
            self.get_protocol_summary(str(row.get("protocol_id") or ""))
            for row in self.matrix_rows
            if not selected or str(row.get("protocol_id") or "") in selected
        ]
        events = [
            event
            for event in self.normalized_events
            if not selected or str(event.get("protocol_id") or "") in selected
        ]
        return {
            "instruction": "Use this context to propose FeatureSpec candidates only. Do not compute matrix values.",
            "protocols": protocols,
            "events": events[:limit_events],
            "allowed_value_rules": [
                "presence",
                "count",
                "min(day_start)",
                "max(day_start)",
                "min(day_end)",
                "max(day_end)",
                "min(duration_days)",
                "max(duration_days)",
                "first_day",
                "last_day",
                "sum_duration",
                "min(concentration_value)",
                "max(concentration_value)",
                "category_set(factor_type)",
                "category_set(action_type)",
                "category_set(normalized_family)",
                "category_set(concentration_unit)",
                "ordered_sequence(normalized_family)",
                "ordered_sequence(action_type)",
                "transition_count",
                "co_occurrence",
            ],
        }

    def get_audit_findings(self, protocol_id: str) -> list[dict[str, Any]]:
        return [
            finding
            for finding in self.audit_report.get("findings", [])
            if str(finding.get("protocol_id") or "") == protocol_id
        ]

    @property
    def matrix_rows(self) -> list[dict[str, str]]:
        if self._matrix_rows is None:
            self._matrix_rows = _read_csv(self.output_dir / "protocol_feature_matrix.csv")
        return self._matrix_rows

    @property
    def normalized_events(self) -> list[dict[str, Any]]:
        if self._normalized_events is None:
            self._normalized_events = _read_jsonl(self.output_dir / "normalized_factor_events.jsonl")
        return self._normalized_events

    @property
    def semantic_annotations(self) -> list[dict[str, Any]]:
        if self._semantic_annotations is None:
            self._semantic_annotations = _read_jsonl(self.output_dir / "semantic_stage_annotations.jsonl")
        return self._semantic_annotations

    @property
    def strategy_summary(self) -> dict[str, Any]:
        if self._strategy_summary is None:
            self._strategy_summary = _read_json(self.output_dir / "protocol_strategy_summary.json")
        return self._strategy_summary

    @property
    def relationship_report(self) -> dict[str, Any]:
        if self._relationship_report is None:
            self._relationship_report = _read_json(self.output_dir / "protocol_relationship_report.json")
        return self._relationship_report

    @property
    def feature_provenance(self) -> dict[str, Any]:
        if self._feature_provenance is None:
            self._feature_provenance = _read_json(self.output_dir / "protocol_feature_provenance.json")
        return self._feature_provenance

    @property
    def audit_report(self) -> dict[str, Any]:
        if self._audit_report is None:
            self._audit_report = _read_json(self.output_dir / "llm_audit_report.json")
        return self._audit_report

    def _matrix_by_protocol(self) -> dict[str, dict[str, str]]:
        return {str(row.get("protocol_id") or ""): row for row in self.matrix_rows}

    def _strategy_by_protocol(self) -> dict[str, dict[str, Any]]:
        return {
            str(row.get("protocol_id") or ""): row
            for row in self.strategy_summary.get("protocols", [])
        }

    def _find_pair_relationship(self, protocol_id_a: str, protocol_id_b: str) -> dict[str, Any]:
        wanted = {protocol_id_a, protocol_id_b}
        for relationship in self.relationship_report.get("pairwise_relationships", []):
            if {relationship.get("protocol_id_a"), relationship.get("protocol_id_b")} == wanted:
                return relationship
        return {}


def _feature_differences(left: dict[str, str], right: dict[str, str], max_features: int) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for key in sorted(set(left).union(right)):
        if key in _metadata_keys():
            continue
        left_value = left.get(key, "")
        right_value = right.get(key, "")
        if left_value == right_value:
            continue
        differences.append(
            {
                "feature": key,
                "protocol_a_value": left_value,
                "protocol_b_value": right_value,
                "difference_type": _difference_type(left_value, right_value),
                "importance_group": _feature_importance_group(key),
                "importance_rank": _feature_importance_rank(key),
            }
        )
    return sorted(differences, key=lambda item: (item["importance_rank"], item["feature"]))[:max_features]


def _group_feature_differences(differences: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for difference in differences:
        payload = {key: value for key, value in difference.items() if key != "importance_rank"}
        grouped[str(difference["importance_group"])].append(payload)
    preferred_order = (
        "strategy_module",
        "culture_system",
        "core_signaling_family",
        "workflow",
        "timing_or_exposure",
        "concentration",
        "audit_or_confidence",
        "other",
    )
    return {group: grouped[group] for group in preferred_order if grouped.get(group)}


def _feature_importance_group(feature: str) -> str:
    if feature.startswith("semantic_module_") or feature == "n_strategy_modules":
        return "strategy_module"
    if feature in {"has_2d_adherent_culture", "has_suspension_culture", "has_shaking", "has_aggregation"} or "culture" in feature:
        return "culture_system"
    if feature.startswith("norm_family_") or feature.endswith("_presence"):
        return "core_signaling_family"
    if feature.startswith("num_") or feature.startswith("requires_") or feature.startswith("transition_") or feature in {"ordered_reagent_program", "number_of_stages", "stage_transition_number"}:
        return "workflow"
    if any(token in feature for token in ("day", "duration", "exposure")):
        return "timing_or_exposure"
    if "concentration" in feature:
        return "concentration"
    if any(token in feature for token in ("confidence", "fallback", "schema", "audit")):
        return "audit_or_confidence"
    return "other"


def _feature_importance_rank(feature: str) -> int:
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
    return order[_feature_importance_group(feature)]


def _difference_type(left_value: str, right_value: str) -> str:
    left_num = _to_float(left_value)
    right_num = _to_float(right_value)
    if left_num is not None and right_num is not None:
        if left_num == 0 and right_num != 0:
            return "protocol_b_only_or_higher"
        if right_num == 0 and left_num != 0:
            return "protocol_a_only_or_higher"
        return "numeric_difference"
    return "categorical_difference"


def _representative_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, dict[str, Any]] = {}
    for event in events:
        family = str(event.get("normalized_family") or "")
        if family and family not in by_family:
            by_family[family] = {
                "normalized_family": family,
                "normalized_name": event.get("normalized_name") or "",
                "factor_type": event.get("factor_type") or "",
                "day_start": event.get("day_start"),
                "day_end": event.get("day_end"),
                "confidence": event.get("confidence") or "",
                "normalization_source": event.get("normalization_source") or "",
                "evidence_quote": event.get("evidence_quote") or "",
            }
    return list(by_family.values())[:30]


def _semantic_modules(annotations: list[dict[str, Any]]) -> set[str]:
    return {
        str(module)
        for annotation in annotations
        for module in annotation.get("strategy_modules", [])
        if module and str(module) != "unknown"
    }


def _metadata_keys() -> set[str]:
    return {
        "protocol_id",
        "paper_id",
        "dataset_id",
        "protocol_name",
        "source_identity_status",
        "confidence_score",
        "comparison_ready",
        "ordered_reagent_program",
    }


def _split_comparison_question(question: str) -> list[str]:
    text = question.strip()
    for separator in (" vs ", " versus ", " 和 ", " 与 ", " and "):
        if separator in text.lower() or separator in text:
            if separator.strip() in {"和", "与"}:
                parts = text.split(separator.strip())
            else:
                parts = re.split(re.escape(separator), text, flags=re.IGNORECASE)
            cleaned = [_clean_query_part(part) for part in parts if _clean_query_part(part)]
            if len(cleaned) >= 2:
                return cleaned[:2]
    return []


def _clean_query_part(value: str) -> str:
    value = re.sub(r"(protocols?|有什么区别|有何区别|区别|compare|difference|differences|between|what|are|the|和|与)", " ", value, flags=re.IGNORECASE)
    return " ".join(value.replace("？", " ").replace("?", " ").split())


def _deterministic_comparison_answer(comparison: dict[str, Any]) -> str:
    if comparison.get("status") != "ok":
        return "No complete comparison is available for the requested protocols."
    a = comparison.get("protocol_a", {}).get("metadata", {})
    b = comparison.get("protocol_b", {}).get("metadata", {})
    grouped = comparison.get("grouped_feature_differences", {})
    lines = [
        f"Protocol A: {a.get('protocol_name') or comparison.get('protocol_a', {}).get('protocol_id')}",
        f"Protocol B: {b.get('protocol_name') or comparison.get('protocol_b', {}).get('protocol_id')}",
    ]
    for group in ("strategy_module", "culture_system", "core_signaling_family", "workflow", "timing_or_exposure", "concentration"):
        items = grouped.get(group, [])
        if items:
            features = ", ".join(item["feature"] for item in items[:5])
            lines.append(f"{group}: {features}")
    return "\n".join(lines)


def _normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    ascii_text = "".join(char for char in normalized if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text).strip()


def _search_tokens(value: str) -> list[str]:
    tokens = _normalize_search_text(value).split()
    return [token for token in tokens if len(token) >= 2]


def _search_score(query_norm: str, query_tokens: list[str], haystack_norm: str, haystack_tokens: set[str]) -> float:
    if not query_norm:
        return 1.0
    if query_norm and query_norm in haystack_norm:
        return 1.0 + len(query_tokens) * 0.1
    if not query_tokens:
        return 0.0
    matched = [token for token in query_tokens if token in haystack_tokens or token in haystack_norm]
    if not matched:
        return 0.0
    score = len(matched) / len(query_tokens)
    has_year_query = any(token.isdigit() and len(token) == 4 for token in query_tokens)
    if has_year_query:
        year_tokens = {token for token in query_tokens if token.isdigit() and len(token) == 4}
        if not year_tokens.intersection(haystack_tokens):
            score *= 0.5
    return score


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _to_float(value: Any) -> float | None:
    try:
        if value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
