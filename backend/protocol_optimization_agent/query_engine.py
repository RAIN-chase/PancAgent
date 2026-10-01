from __future__ import annotations

import csv
import html
import json
import math
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
            haystack_raw = " ".join(
                str(row.get(key) or "") for key in ("protocol_id", "paper_id", "dataset_id", "protocol_name")
            )
            haystack_norm = _normalize_search_text(haystack_raw)
            haystack_tokens = set(_search_tokens(haystack_raw))
            matched_tokens = sorted(
                token for token in query_tokens if token in haystack_tokens or token in haystack_norm
            )
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
            "audit_issue_types": sorted(
                {finding.get("issue_type", "") for finding in audit if finding.get("issue_type")}
            ),
        }

    def get_protocol_collection_summary(self) -> dict[str, Any]:
        rows = self.matrix_rows
        feature_names = [key for key in rows[0] if key not in _metadata_keys()] if rows else []
        comparison_ready_rows = [row for row in rows if _is_truthy(row.get("comparison_ready"))]
        confidence_values = [_to_float(row.get("confidence_score")) for row in rows]
        numeric_confidences = [value for value in confidence_values if value is not None]
        release_gate = _read_json(self.output_dir / "matrix_release_gate_report.json")
        return {
            "status": "ok" if rows else "no_data",
            "output_dir": str(self.output_dir),
            "source_matrix": str(self.output_dir / "protocol_feature_matrix.csv"),
            "n_protocols": len(rows),
            "n_comparison_ready_protocols": len(comparison_ready_rows),
            "n_features": len(feature_names),
            "n_numeric_features": len(_select_numeric_features(rows, [], len(feature_names))) if rows else 0,
            "n_papers": len({row.get("paper_id", "") for row in rows if row.get("paper_id")}),
            "n_datasets": len({row.get("dataset_id", "") for row in rows if row.get("dataset_id")}),
            "mean_confidence_score": round(sum(numeric_confidences) / len(numeric_confidences), 4)
            if numeric_confidences
            else None,
            "release_gate_status": release_gate.get("status") or release_gate.get("release_status") or "",
            "sample_protocols": [
                {
                    "protocol_id": row.get("protocol_id", ""),
                    "paper_id": row.get("paper_id", ""),
                    "dataset_id": row.get("dataset_id", ""),
                    "protocol_name": row.get("protocol_name", ""),
                    "comparison_ready": row.get("comparison_ready", ""),
                    "confidence_score": row.get("confidence_score", ""),
                }
                for row in rows[:10]
            ],
        }

    def compare_protocols(self, protocol_id_a: str, protocol_id_b: str, max_features: int = 40) -> dict[str, Any]:
        matrix_by_protocol = self._matrix_by_protocol()
        left = matrix_by_protocol.get(protocol_id_a)
        right = matrix_by_protocol.get(protocol_id_b)
        if left is None or right is None:
            missing = [
                protocol_id for protocol_id, row in ((protocol_id_a, left), (protocol_id_b, right)) if row is None
            ]
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
        if _is_collection_summary_question(question):
            summary = self.get_protocol_collection_summary()
            return {
                "status": summary["status"],
                "intent": "protocol_collection_summary",
                "answer": _deterministic_collection_summary_answer(summary),
                "tool_result": summary,
            }
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
        return self.feature_provenance.get("protocols", {}).get(protocol_id, {}).get(feature, [])

    def get_protocol_events(self, protocol_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        events = [event for event in self.normalized_events if str(event.get("protocol_id") or "") == protocol_id]
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

    def plot_feature_heatmap(
        self,
        protocol_ids: list[str] | None = None,
        features: list[str] | None = None,
        top_n_features: int = 50,
        normalize: str = "minmax",
        filename: str = "",
        cluster_rows: bool = False,
        cluster_features: bool = False,
    ) -> dict[str, Any]:
        rows = _select_matrix_rows(self.matrix_rows, protocol_ids or [])
        if not rows:
            return {"status": "no_data", "reason": "No matching protocol rows in protocol_feature_matrix.csv."}

        numeric_features = _select_numeric_features(rows, features or [], max(1, top_n_features))
        if not numeric_features:
            return {"status": "no_data", "reason": "No numeric feature columns available for heatmap."}

        matrix = [[_to_float(row.get(feature)) or 0.0 for feature in numeric_features] for row in rows]
        rendered_values = _normalize_matrix_by_column(matrix, normalize)
        protocol_labels = [str(row.get("protocol_id") or f"protocol_{idx + 1}") for idx, row in enumerate(rows)]

        if cluster_rows and len(rendered_values) > 1:
            row_order = _cluster_order(rendered_values)
            protocol_labels = [protocol_labels[idx] for idx in row_order]
            matrix = [matrix[idx] for idx in row_order]
            rendered_values = [rendered_values[idx] for idx in row_order]

        if cluster_features and numeric_features and len(numeric_features) > 1:
            feature_order = _cluster_order(_transpose(rendered_values))
            numeric_features = [numeric_features[idx] for idx in feature_order]
            matrix = [[row[idx] for idx in feature_order] for row in matrix]
            rendered_values = [[row[idx] for idx in feature_order] for row in rendered_values]

        output_path = (
            self.output_dir
            / "visualizations"
            / (filename or _default_heatmap_filename(protocol_labels, numeric_features))
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            _render_heatmap_svg(
                protocol_labels=protocol_labels,
                feature_labels=numeric_features,
                values=rendered_values,
                raw_values=matrix,
                title=f"Protocol feature matrix heatmap ({normalize})",
            ),
            encoding="utf-8",
        )
        return {
            "status": "ok",
            "plot_type": "feature_matrix_heatmap",
            "path": str(output_path),
            "n_protocols": len(protocol_labels),
            "n_features": len(numeric_features),
            "protocol_ids": protocol_labels,
            "features": numeric_features,
            "normalization": normalize,
            "cluster_rows": cluster_rows,
            "cluster_features": cluster_features,
            "source_matrix": str(self.output_dir / "protocol_feature_matrix.csv"),
        }

    def plot_layered_feature_heatmaps(
        self,
        protocol_ids: list[str] | None = None,
        layers: list[str] | None = None,
        top_n_features_per_layer: int = 40,
        normalize: str = "minmax",
        cluster_rows: bool = True,
        cluster_features: bool = True,
        include_sequence_layer: bool = False,
        filename_prefix: str = "protocol_feature_layer",
    ) -> dict[str, Any]:
        rows = _select_matrix_rows(self.matrix_rows, protocol_ids or [])
        if not rows:
            return {"status": "no_data", "reason": "No matching protocol rows in protocol_feature_matrix.csv."}

        available_layers = _feature_layers(rows, include_sequence_layer=include_sequence_layer)
        requested_layers = [layer for layer in layers or available_layers if layer in available_layers]
        layer_results: list[dict[str, Any]] = []
        for layer in requested_layers:
            layer_features = available_layers[layer]
            if not layer_features:
                continue
            result = self.plot_feature_heatmap(
                protocol_ids=protocol_ids or [],
                features=layer_features,
                top_n_features=top_n_features_per_layer,
                normalize=normalize,
                filename=f"{filename_prefix}_{layer}.svg",
                cluster_rows=cluster_rows,
                cluster_features=cluster_features,
            )
            if result.get("status") == "ok":
                result["layer"] = layer
                result["layer_label"] = _feature_layer_label(layer)
                layer_results.append(result)

        if not layer_results:
            return {"status": "no_data", "reason": "No numeric feature layers available for heatmap."}

        index_path = self.output_dir / "visualizations" / f"{filename_prefix}_index.html"
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            _render_layered_heatmap_index(
                title="Layered protocol feature heatmaps",
                layer_results=layer_results,
                source_matrix=str(self.output_dir / "protocol_feature_matrix.csv"),
            ),
            encoding="utf-8",
        )
        return {
            "status": "ok",
            "plot_type": "layered_feature_matrix_heatmaps",
            "index_path": str(index_path),
            "layers": layer_results,
            "n_layers": len(layer_results),
            "n_protocols": len(rows),
            "normalization": normalize,
            "cluster_rows": cluster_rows,
            "cluster_features": cluster_features,
            "source_matrix": str(self.output_dir / "protocol_feature_matrix.csv"),
        }

    def propose_feature_specs_context(
        self, protocol_ids: list[str] | None = None, limit_events: int = 200
    ) -> dict[str, Any]:
        selected = set(protocol_ids or [])
        protocols = [
            self.get_protocol_summary(str(row.get("protocol_id") or ""))
            for row in self.matrix_rows
            if not selected or str(row.get("protocol_id") or "") in selected
        ]
        events = [
            event for event in self.normalized_events if not selected or str(event.get("protocol_id") or "") in selected
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
        return {str(row.get("protocol_id") or ""): row for row in self.strategy_summary.get("protocols", [])}

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
    if (
        feature in {"has_2d_adherent_culture", "has_suspension_culture", "has_shaking", "has_aggregation"}
        or "culture" in feature
    ):
        return "culture_system"
    if feature.startswith("norm_family_") or feature.endswith("_presence"):
        return "core_signaling_family"
    if (
        feature.startswith("num_")
        or feature.startswith("requires_")
        or feature.startswith("transition_")
        or feature in {"ordered_reagent_program", "number_of_stages", "stage_transition_number"}
    ):
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


def _is_collection_summary_question(question: str) -> bool:
    text = question.lower()
    normalized = _normalize_search_text(question)
    count_terms = (
        "多少",
        "几",
        "一共",
        "总共",
        "数量",
        "可供比较",
        "available",
        "how many",
        "count",
        "inventory",
        "collection",
    )
    protocol_terms = ("protocol", "protocols", "协议", "方案")
    has_count_intent = any(term in text or term in normalized for term in count_terms)
    has_protocol_scope = any(term in text or term in normalized for term in protocol_terms)
    return has_count_intent and has_protocol_scope


def _deterministic_collection_summary_answer(summary: dict[str, Any]) -> str:
    if summary.get("status") != "ok":
        return f"No protocol feature matrix was available at {summary.get('source_matrix', '')}."
    return (
        f"The configured Protocol Analysis output contains {summary.get('n_protocols', 0)} protocols, "
        f"including {summary.get('n_comparison_ready_protocols', 0)} comparison-ready protocols. "
        f"The matrix has {summary.get('n_features', 0)} feature columns "
        f"({summary.get('n_numeric_features', 0)} numeric) from {summary.get('n_papers', 0)} papers "
        f"and {summary.get('n_datasets', 0)} datasets. Source: {summary.get('source_matrix', '')}."
    )


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
    value = re.sub(
        r"(protocols?|有什么区别|有何区别|区别|compare|difference|differences|between|what|are|the|和|与)",
        " ",
        value,
        flags=re.IGNORECASE,
    )
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
    for group in (
        "strategy_module",
        "culture_system",
        "core_signaling_family",
        "workflow",
        "timing_or_exposure",
        "concentration",
    ):
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


def _is_truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


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


def _select_matrix_rows(rows: list[dict[str, str]], protocol_ids: list[str]) -> list[dict[str, str]]:
    wanted = {str(protocol_id) for protocol_id in protocol_ids if str(protocol_id)}
    if not wanted:
        return rows
    return [row for row in rows if str(row.get("protocol_id") or "") in wanted]


def _select_numeric_features(rows: list[dict[str, str]], requested: list[str], top_n_features: int) -> list[str]:
    metadata = _metadata_keys()
    candidate_features = requested or sorted(set().union(*(set(row) for row in rows)) - metadata)
    scored: list[tuple[float, str]] = []
    for feature in candidate_features:
        values = [_to_float(row.get(feature)) for row in rows]
        numeric_values = [value for value in values if value is not None and math.isfinite(value)]
        if not numeric_values:
            continue
        variance = _variance(numeric_values)
        nonzero_fraction = sum(1 for value in numeric_values if value != 0) / len(numeric_values)
        score = variance + nonzero_fraction * 0.01
        scored.append((score, feature))
    return [feature for _, feature in sorted(scored, key=lambda item: (-item[0], item[1]))[:top_n_features]]


def _feature_layers(rows: list[dict[str, str]], include_sequence_layer: bool) -> dict[str, list[str]]:
    features = sorted(set().union(*(set(row) for row in rows)) - _metadata_keys())
    layers: dict[str, list[str]] = {
        "semantic": [],
        "workflow": [],
        "action_category": [],
        "factor_type": [],
        "reagent": [],
        "normalized_family": [],
        "temporal_exposure": [],
        "graph_topology": [],
        "motif": [],
    }
    if include_sequence_layer:
        layers["sequence"] = []

    for feature in features:
        layer = _feature_layer(feature)
        if layer in layers:
            layers[layer].append(feature)
    return {layer: layer_features for layer, layer_features in layers.items() if layer_features}


def _feature_layer(feature: str) -> str:
    if feature.startswith("semantic_module_") or feature in {"n_semantic_stage_annotations", "n_strategy_modules"}:
        return "semantic"
    if feature.startswith("action_category_"):
        return "action_category"
    if feature.startswith("factor_type_"):
        return "factor_type"
    if feature.startswith("graph_"):
        return "graph_topology"
    if feature.startswith("motif_"):
        return "motif"
    if feature.startswith("all_reagent_") and feature.endswith(("_start_day", "_end_day", "_total_exposure")):
        return "temporal_exposure"
    if feature.startswith("all_reagent_"):
        return "reagent"
    if feature.startswith("norm_family_"):
        return "normalized_family"
    if feature.startswith(("transition_", "action_bigram_", "factor_type_bigram_", "all_reagent_transition_")):
        return "sequence"
    if feature.startswith(("num_", "has_", "requires_")):
        return "workflow"
    if (
        feature
        in {"total_days", "number_of_stages", "stage_transition_number", "max_stage_duration", "mean_stage_duration"}
        or feature.endswith("_start_day")
        or feature.endswith("_end_day")
        or feature.endswith("_total_exposure")
    ):
        return "temporal_exposure"
    if feature.endswith(("_presence", "_max_concentration", "_n_events")):
        return "reagent"
    return "other"


def _feature_layer_label(layer: str) -> str:
    labels = {
        "semantic": "Semantic strategy modules",
        "workflow": "Workflow and operation features",
        "action_category": "Standardized experimental action categories",
        "factor_type": "Structured factor type coverage",
        "reagent": "Canonical reagent features",
        "normalized_family": "Normalized factor family features",
        "temporal_exposure": "Timing and exposure features",
        "graph_topology": "Graph topology features",
        "motif": "Action-item motif features",
        "sequence": "Reagent transition sequence features",
    }
    return labels.get(layer, layer.replace("_", " ").title())


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / len(values)


def _normalize_matrix_by_column(matrix: list[list[float]], normalize: str) -> list[list[float]]:
    if not matrix:
        return []
    normalize = normalize.lower()
    if normalize in {"none", "raw"}:
        flat = [abs(value) for row in matrix for value in row]
        max_abs = max(flat) if flat else 1.0
        scale = max_abs or 1.0
        return [[max(0.0, min(1.0, abs(value) / scale)) for value in row] for row in matrix]

    n_cols = len(matrix[0])
    output = [[0.0 for _ in range(n_cols)] for _ in matrix]
    for col_idx in range(n_cols):
        column = [row[col_idx] for row in matrix]
        if normalize == "zscore":
            mean = sum(column) / len(column)
            std = math.sqrt(_variance(column)) or 1.0
            scaled = [0.5 + ((value - mean) / std) / 4 for value in column]
        else:
            min_value = min(column)
            max_value = max(column)
            span = max_value - min_value
            scaled = [0.0 if span == 0 else (value - min_value) / span for value in column]
        for row_idx, value in enumerate(scaled):
            output[row_idx][col_idx] = max(0.0, min(1.0, value))
    return output


def _transpose(matrix: list[list[float]]) -> list[list[float]]:
    if not matrix:
        return []
    return [list(column) for column in zip(*matrix, strict=False)]


def _cluster_order(vectors: list[list[float]]) -> list[int]:
    clusters: list[dict[str, Any]] = [{"indices": [idx], "centroid": vector[:]} for idx, vector in enumerate(vectors)]
    if len(clusters) < 2:
        return [idx for idx in range(len(vectors))]

    while len(clusters) > 1:
        best_pair: tuple[int, int] | None = None
        best_distance = float("inf")
        for left_idx in range(len(clusters)):
            for right_idx in range(left_idx + 1, len(clusters)):
                distance = _euclidean_distance(clusters[left_idx]["centroid"], clusters[right_idx]["centroid"])
                if distance < best_distance:
                    best_distance = distance
                    best_pair = (left_idx, right_idx)
        if best_pair is None:
            break
        left_idx, right_idx = best_pair
        left = clusters[left_idx]
        right = clusters[right_idx]
        merged_indices = left["indices"] + right["indices"]
        merged = {
            "indices": merged_indices,
            "centroid": _centroid([vectors[idx] for idx in merged_indices]),
        }
        for idx in sorted(best_pair, reverse=True):
            clusters.pop(idx)
        clusters.append(merged)
    return clusters[0]["indices"] if clusters else []


def _centroid(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    n_cols = len(vectors[0])
    return [sum(vector[col_idx] for vector in vectors) / len(vectors) for col_idx in range(n_cols)]


def _euclidean_distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(sum((left[idx] - right[idx]) ** 2 for idx in range(min(len(left), len(right)))))


def _default_heatmap_filename(protocol_labels: list[str], feature_labels: list[str]) -> str:
    protocol_part = "all_protocols" if len(protocol_labels) > 3 else "_".join(_slug(label) for label in protocol_labels)
    return f"protocol_feature_heatmap_{protocol_part}_{len(feature_labels)}features.svg"


def _slug(value: str) -> str:
    value = _normalize_search_text(value).replace(" ", "_")
    return value[:40] or "protocol"


def _render_heatmap_svg(
    protocol_labels: list[str],
    feature_labels: list[str],
    values: list[list[float]],
    raw_values: list[list[float]],
    title: str,
) -> str:
    cell = 18
    left = 180
    top = 190
    right = 40
    bottom = 120
    width = left + len(feature_labels) * cell + right
    height = top + len(protocol_labels) * cell + bottom
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<style>text{font-family:Arial,sans-serif;font-size:11px;fill:#111827}.title{font-size:16px;font-weight:700}.axis{font-size:10px}.small{font-size:9px;fill:#4b5563}</style>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text class="title" x="20" y="28">{html.escape(title)}</text>',
        '<text class="small" x="20" y="48">Columns are numeric protocol features selected by variance unless explicitly requested. Color intensity is normalized per feature.</text>',
    ]
    for col_idx, feature in enumerate(feature_labels):
        x = left + col_idx * cell + cell * 0.65
        parts.append(
            f'<text class="axis" transform="translate({x},{top - 8}) rotate(-55)" text-anchor="start">{html.escape(feature)}</text>'
        )
    for row_idx, protocol_id in enumerate(protocol_labels):
        y = top + row_idx * cell + cell * 0.68
        parts.append(f'<text class="axis" x="{left - 8}" y="{y}" text-anchor="end">{html.escape(protocol_id)}</text>')
    for row_idx, row in enumerate(values):
        for col_idx, value in enumerate(row):
            raw = raw_values[row_idx][col_idx]
            x = left + col_idx * cell
            y = top + row_idx * cell
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell - 1}" height="{cell - 1}" fill="{_heatmap_color(value)}">'
                f"<title>{html.escape(protocol_labels[row_idx])} | {html.escape(feature_labels[col_idx])}: {raw:g}</title>"
                "</rect>"
            )
    legend_x = 20
    legend_y = height - 54
    for idx in range(80):
        value = idx / 79
        parts.append(
            f'<rect x="{legend_x + idx * 2}" y="{legend_y}" width="2" height="12" fill="{_heatmap_color(value)}"/>'
        )
    parts.extend(
        [
            f'<text class="small" x="{legend_x}" y="{legend_y + 28}">low</text>',
            f'<text class="small" x="{legend_x + 132}" y="{legend_y + 28}">high</text>',
            "</svg>",
        ]
    )
    return "\n".join(parts)


def _render_layered_heatmap_index(title: str, layer_results: list[dict[str, Any]], source_matrix: str) -> str:
    cards = []
    for result in layer_results:
        path = Path(str(result["path"]))
        label = html.escape(str(result.get("layer_label") or result.get("layer") or path.stem))
        cards.append(
            "\n".join(
                [
                    '<section class="layer">',
                    f"<h2>{label}</h2>",
                    f"<p>{int(result.get('n_features', 0))} features, {int(result.get('n_protocols', 0))} protocols</p>",
                    f'<object data="{html.escape(path.name)}" type="image/svg+xml"></object>',
                    "</section>",
                ]
            )
        )
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8"/>',
            f"<title>{html.escape(title)}</title>",
            "<style>",
            "body{margin:0;background:#f8fafc;color:#111827;font-family:Arial,sans-serif}",
            "main{max-width:1280px;margin:0 auto;padding:24px}",
            "h1{font-size:22px;margin:0 0 8px}",
            "h2{font-size:16px;margin:0 0 4px}",
            "p{font-size:12px;color:#4b5563;margin:0 0 12px}",
            ".layer{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:16px;margin:16px 0}",
            "object{width:100%;min-height:520px;border:0}",
            "</style>",
            "</head>",
            "<body>",
            "<main>",
            f"<h1>{html.escape(title)}</h1>",
            f"<p>Source matrix: {html.escape(source_matrix)}</p>",
            *cards,
            "</main>",
            "</body>",
            "</html>",
        ]
    )


def _heatmap_color(value: float) -> str:
    stops = ((247, 250, 252), (253, 186, 116), (220, 38, 38))
    value = max(0.0, min(1.0, value))
    if value <= 0.5:
        local = value / 0.5
        start, end = stops[0], stops[1]
    else:
        local = (value - 0.5) / 0.5
        start, end = stops[1], stops[2]
    rgb = tuple(round(start[idx] + (end[idx] - start[idx]) * local) for idx in range(3))
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
