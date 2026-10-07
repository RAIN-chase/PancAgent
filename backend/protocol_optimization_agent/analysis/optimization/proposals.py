"""Extracted proposals calculations; no agent execution."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from protocol_optimization_agent.analysis.optimization._shared import _to_float
from protocol_optimization_agent.analysis.optimization.association import _candidate_protocol_features
from protocol_optimization_agent.analysis.optimization.contracts import ProtocolOptimizingState
from protocol_optimization_agent.analysis.optimization.mechanisms import interpret_mechanisms
from protocol_optimization_agent.runtime.llm_client import LLMClient, OfflineLLMClient


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
    raw = _feature_provenance_payloads(feature, provenance)
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


def _feature_evidence_refs(
    feature: str,
    provenance: dict[str, Any],
    stage_event_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    refs = _provenance_refs(feature, provenance)
    refs.extend(_stage_event_refs(feature, stage_event_rows))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for ref in refs:
        key = (
            str(ref.get("protocol_id") or ""),
            str(ref.get("stage_id") or ""),
            str(ref.get("event_id") or ""),
            str(ref.get("evidence_quote") or ""),
        )
        if key not in seen:
            deduped.append(ref)
            seen.add(key)
    return deduped[:10]


def _provenance_refs(feature: str, provenance: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for protocol_id, item in _feature_provenance_items(feature, provenance):
        if isinstance(item, dict):
            refs.append(
                {
                    "source": "protocol_feature_provenance",
                    "protocol_id": item.get("protocol_id") or protocol_id,
                    "stage_id": item.get("stage_id") or item.get("stage") or "",
                    "event_id": item.get("event_id", item.get("entity_id", "")),
                    "day_start": item.get("day_start", ""),
                    "day_end": item.get("day_end", ""),
                    "factor_name": item.get("factor_name")
                    or item.get("reagent")
                    or item.get("normalized_family")
                    or "",
                    "operation": item.get("operation", item.get("action_type", "")),
                    "concentration": _concentration_text(item),
                    "evidence_quote": item.get("evidence_quote") or item.get("quote") or item.get("summary") or "",
                }
            )
        elif item:
            refs.append(
                {"source": "protocol_feature_provenance", "protocol_id": protocol_id, "evidence_quote": str(item)}
            )
    return refs


def _feature_provenance_payloads(feature: str, provenance: dict[str, Any]) -> Any:
    items = [item for _, item in _feature_provenance_items(feature, provenance)]
    if not items:
        return None
    return items


def _feature_provenance_items(feature: str, provenance: dict[str, Any]) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    protocols = provenance.get("protocols", {})
    if isinstance(protocols, dict):
        for protocol_id, protocol_features in protocols.items():
            if isinstance(protocol_features, dict) and feature in protocol_features:
                for item in _as_ref_items(protocol_features[feature]):
                    items.append((str(protocol_id), item))
    for item in _as_ref_items(provenance.get(feature)):
        items.append(("", item))
    features = provenance.get("features", {})
    if isinstance(features, dict):
        for item in _as_ref_items(features.get(feature)):
            items.append(("", item))
    return items


def _stage_event_refs(feature: str, stage_event_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    feature_tokens = _feature_tokens(feature)
    for row in stage_event_rows:
        searchable = " ".join(str(row.get(key) or "") for key in row)
        if not _feature_matches_text(feature, feature_tokens, searchable):
            continue
        refs.append(
            {
                "source": "stage_factor_matrix_or_events_summary",
                "protocol_id": row.get("protocol_id", ""),
                "stage_id": row.get("stage_id") or row.get("stage") or "",
                "event_id": row.get("event_id", ""),
                "day_start": row.get("day_start", ""),
                "day_end": row.get("day_end", ""),
                "factor_name": row.get("factor_name") or row.get("standardized_name") or row.get("factor") or "",
                "operation": row.get("operation", row.get("action_type", "")),
                "concentration": _concentration_text(row),
                "evidence_quote": row.get("evidence_quote", ""),
            }
        )
    return refs


def _feature_matches_text(feature: str, feature_tokens: set[str], text: str) -> bool:
    compact_feature = _compact_feature_name(feature)
    compact_text = _compact_feature_name(text)
    if compact_feature and (compact_feature in compact_text or compact_text in compact_feature):
        return True
    for prefix in ("allreagent", "reagent", "semantic", "factor", "motifaction"):
        stripped = compact_feature.removeprefix(prefix)
        if stripped and len(stripped) > 1 and stripped in compact_text:
            return True
    return bool(feature_tokens.intersection(_feature_tokens(text)))


def _compact_feature_name(text: str) -> str:
    return "".join(character for character in text.lower() if character.isalnum())


def _as_ref_items(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("refs", "references", "evidence_refs", "provenance"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return nested
        return [raw]
    return [raw]


def _concentration_text(row: dict[str, Any]) -> str:
    value = row.get("concentration") or row.get("concentration_value") or row.get("dose")
    unit = row.get("concentration_unit") or row.get("unit") or row.get("dose_unit")
    if value in ("", None):
        return ""
    return f"{value} {unit}".strip()


def _feature_tokens(text: str) -> set[str]:
    normalized = text.lower().replace("_", " ").replace("-", " ").replace("/", " ")
    return {token for token in normalized.split() if len(token) > 2 and token not in {"feature", "reagent", "factor"}}


def _evidence_summary(evidence_refs: list[dict[str, Any]]) -> str:
    quotes = [str(ref.get("evidence_quote") or "") for ref in evidence_refs if ref.get("evidence_quote")]
    if quotes:
        return "; ".join(quotes[:3])
    factors = [str(ref.get("factor_name") or "") for ref in evidence_refs if ref.get("factor_name")]
    return "; ".join(factors[:3])


def _feature_topic_hint(feature: str) -> str:
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
    if row.get("confidence") == "low" or row.get("stability_status") == "exploratory_small_sample":
        return "study_variable_only"
    return "candidate_to_add_or_increase" if effect > 0 else "candidate_to_reduce_or_avoid"


def _candidate_confidence(row: dict[str, Any], evidence: str) -> str:
    n_protocols = int(_to_float(row.get("n_protocols")) or 0)
    if n_protocols >= 20 and evidence:
        return "medium"
    return "low"


def _build_protocol_edits(candidates: list[dict[str, Any]], objective: dict[str, Any]) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates[:10], start=1):
        feature = str(candidate.get("feature") or "")
        evidence_refs = _candidate_evidence_refs(candidate)
        role = str(candidate.get("recommendation_role") or "prioritize_for_review")
        representative_ref = _representative_evidence_ref(evidence_refs)
        edits.append(
            {
                "edit_id": f"edit_{index:03d}",
                "edit_type": _edit_type(role),
                "target": objective.get("primary", "increase_beta_like_maturity"),
                "candidate_feature": feature,
                "target_stage": _target_stage(feature, representative_ref),
                "day_start": representative_ref.get("day_start", ""),
                "day_end": representative_ref.get("day_end", ""),
                "reagent_or_operation": representative_ref.get("factor_name") or _feature_label(feature),
                "operation": representative_ref.get("operation") or _operation_from_role(role),
                "dose_or_condition_range": representative_ref.get("concentration", "derive from source protocol range"),
                "source_protocols": _source_protocols(evidence_refs),
                "rationale": _change_text(feature, role),
                "mechanistic_relevance": candidate.get("mechanistic_relevance", ""),
                "mechanism_explanation": candidate.get("mechanism_explanation", "unknown"),
                "mechanism_status": candidate.get("mechanism_status", "mechanism_evidence_missing"),
                "supporting_literature_refs": candidate.get("supporting_literature_refs", []),
                "opposing_evidence_refs": candidate.get("opposing_evidence_refs", []),
                "alternative_explanations": candidate.get("alternative_explanations", []),
                "competing_explanations": candidate.get("competing_explanations", []),
                "falsification_conditions": candidate.get("falsification_conditions", []),
                "expected_direction": candidate.get("expected_direction", "unknown"),
                "endpoint_expectations": candidate.get("endpoint_expectations", {}),
                "endpoint_associations": candidate.get("endpoint_associations", []),
                "evidence_package_id": candidate.get("evidence_package_id", ""),
                "effect_estimate": candidate.get("effect_estimate", ""),
                "confidence": candidate.get("confidence", "low"),
                "confounding_risk": candidate.get("confounding_risk", "high_unknown"),
                "evidence_refs": evidence_refs,
                "negative_controls": _default_negative_controls(),
                "validation_readouts": _validation_readouts(feature),
                "causal_interpretation": "hypothesis_requires_experimental_validation",
            }
        )
    return edits


def _candidate_evidence_refs(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    refs = candidate.get("evidence_refs", [])
    if isinstance(refs, list):
        return [ref for ref in refs if isinstance(ref, dict)]
    return []


def _representative_evidence_ref(evidence_refs: list[dict[str, Any]]) -> dict[str, Any]:
    for ref in evidence_refs:
        if ref.get("stage_id") or ref.get("day_start") or ref.get("factor_name"):
            return ref
    return evidence_refs[0] if evidence_refs else {}


def _edit_type(role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return "add_or_increase"
    if role == "candidate_to_reduce_or_avoid":
        return "reduce_or_remove"
    if role == "study_variable_only":
        return "study_variable_before_intervention"
    return "review_before_edit"


def _target_stage(feature: str, evidence_ref: dict[str, Any]) -> str:
    if evidence_ref.get("stage_id"):
        return str(evidence_ref["stage_id"])
    lowered = feature.lower()
    if "maturation" in lowered or "beta" in lowered:
        return "beta_cell_maturation"
    if "endocrine" in lowered:
        return "endocrine_induction"
    if "progenitor" in lowered:
        return "pancreatic_progenitor_specification"
    return "stage_to_be_determined_from_source_protocol"


def _feature_label(feature: str) -> str:
    return feature.replace("all_reagent_", "").replace("reagent_", "").replace("semantic_", "").replace("_", " ")


def _operation_from_role(role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return "add_or_extend"
    if role == "candidate_to_reduce_or_avoid":
        return "remove_reduce_or_delay"
    if role == "study_variable_only":
        return "do_not_edit_until_validated"
    return "review"


def _source_protocols(evidence_refs: list[dict[str, Any]]) -> list[str]:
    protocols = sorted({str(ref.get("protocol_id") or "") for ref in evidence_refs if ref.get("protocol_id")})
    return protocols


def _validation_readouts(feature: str) -> list[str]:
    lowered = feature.lower()
    readouts = ["objective-weighted phenotype score", "cell-type composition", "QC pass rate"]
    if "beta" in lowered or "maturation" in lowered:
        readouts.extend(["MAFA", "INS", "IAPP", "adult beta gene program score"])
    if any(term in lowered for term in ("grn", "regulatory", "notch", "wnt", "tgf", "fgf", "egf", "ra")):
        readouts.append("GRN/regulon similarity")
    if "stress" in lowered or "off_target" in lowered:
        readouts.extend(["stress program score", "off-target lineage burden"])
    return readouts


def _default_negative_controls() -> list[str]:
    return [
        "source protocol without the proposed edit",
        "vehicle or matched media-change control when applicable",
        "timepoint-matched batch control",
    ]


def _build_recommendations(edits: list[dict[str, Any]], objective: dict[str, Any]) -> list[dict[str, Any]]:
    if not edits:
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
    for index, edit in enumerate(edits, start=1):
        recommendations.append(
            {
                "recommendation_id": f"rec_{index:03d}",
                "target": objective.get("primary", "increase_beta_like_maturity"),
                "candidate_feature": edit.get("candidate_feature", ""),
                "proposed_change": edit.get("rationale", ""),
                "stage_level_edit": edit,
                "source_basis": _evidence_summary(_candidate_evidence_refs(edit))
                or "ranked from protocol feature representation",
                "mechanistic_relevance": edit.get("mechanistic_relevance", ""),
                "effect_estimate": edit.get("effect_estimate", ""),
                "confidence": edit.get("confidence", "low"),
                "risk": "Feature may be confounded with paper, dataset, batch, cell line, assay, or timepoint differences.",
                "evidence_refs": edit.get("evidence_refs", []),
                "negative_controls": edit.get("negative_controls", []),
                "validation_readouts": edit.get("validation_readouts", []),
                "requires_experimental_validation": "true",
            }
        )
    return recommendations


def _change_text(feature: str, role: str) -> str:
    if role == "candidate_to_add_or_increase":
        return f"Consider adding, preserving, or increasing the protocol module represented by `{feature}`."
    if role == "candidate_to_reduce_or_avoid":
        return f"Consider reducing, removing, or delaying the protocol module represented by `{feature}`."
    if role == "study_variable_only":
        return f"Treat `{feature}` as a study variable; current evidence is insufficient for a concrete protocol edit."
    return f"Review `{feature}` as a candidate optimization variable before proposing a concrete protocol edit."


def _render_protocol_markdown(protocol_doc: dict[str, Any]) -> str:
    lines = [
        "# Candidate Pancreatic / Islet Organoid Experiments",
        "",
        "## Policy",
        "",
        "Associations are hypotheses, not causal proof. Candidates require researcher review and experimental validation.",
        "",
        "## Objective",
        "",
        f"- Primary: `{protocol_doc.get('objective', {}).get('primary', 'increase_beta_like_maturity')}`",
        "",
        "## Hypothesis-Based Study Variables",
        "",
    ]
    for rec in protocol_doc.get("recommendations", []):
        lines.extend(
            [
                f"### {rec.get('recommendation_id', 'recommendation')}",
                "",
                f"- Candidate feature: `{rec.get('candidate_feature', '')}`",
                f"- Proposed change: {rec.get('proposed_change', '')}",
                f"- Protocol/observation basis: {rec.get('source_basis', '')}",
                f"- Confidence: `{rec.get('confidence', 'low')}`",
                f"- Risk: {rec.get('risk', '')}",
                f"- Mechanism: {rec.get('stage_level_edit', {}).get('mechanism_explanation', 'unknown')}",
                f"- Mechanism status: `{rec.get('stage_level_edit', {}).get('mechanism_status', 'unknown')}`",
                f"- Competing explanations: {'; '.join(rec.get('stage_level_edit', {}).get('alternative_explanations', []))}",
                f"- Falsification: {'; '.join(rec.get('stage_level_edit', {}).get('falsification_conditions', []))}",
                "",
            ]
        )
    experiment_report = protocol_doc.get("next_experiment_candidates", {})
    if experiment_report:
        lines.extend(
            [
                "## Experiment Selection",
                "",
                "Candidates remain pending researcher review.",
                "",
                f"- Scientific preconditions: `{experiment_report.get('scientific_preconditions', {}).get('status', 'unknown')}`",
                f"- Selected for review: {', '.join(experiment_report.get('selected_for_review', [])) or 'none; resolve missing evidence and assessments'}",
                "",
            ]
        )
        for candidate in experiment_report.get("candidates", []):
            lines.extend(
                [
                    f"### {candidate['id']}",
                    "",
                    f"- Feature: `{candidate['intervention'].get('candidate_feature', '')}`",
                    f"- Ordinal decision utility: `{candidate.get('utility')}`",
                    f"- Critique: {', '.join(candidate.get('critique', [])) or 'no unresolved gate findings'}",
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


def rank_mechanistic_evidence(state: ProtocolOptimizingState, max_candidate_features: int,
                             llm_client: LLMClient | None = None,
                             literature_search: Callable[[str, int], list[dict[str, Any]]] | None = None) -> ProtocolOptimizingState:
    candidates: list[dict[str, Any]] = []
    if state.get("association_rows"):
        source_rows = state["association_rows"]
    else:
        source_rows = _fallback_candidate_rows(state.get("protocol_feature_rows", []), max_candidate_features)
    features = list(dict.fromkeys(str(row.get("feature") or "") for row in source_rows))[:max_candidate_features]
    for index, feature in enumerate(features, start=1):
        rows = [row for row in source_rows if row.get("feature") == feature]
        endpoint_rows = [row for row in rows if row.get("analysis_role") == "primary_raw_endpoint"]
        row = endpoint_rows[0] if endpoint_rows else rows[0]
        feature = str(row.get("feature") or "")
        evidence_refs = _feature_evidence_refs(
            feature, state.get("feature_provenance", {}), state.get("stage_event_rows", [])
        )
        evidence_summary = _evidence_summary(evidence_refs)
        candidates.append(
            {
                "candidate_id": f"candidate_{index:03d}",
                "feature": feature,
                "rank": index,
                "effect_estimate": row.get("effect_estimate", ""),
                "n_protocols": row.get("n_protocols", ""),
                "mechanistic_relevance": "unverified; feature_topic_hint_is_not_mechanism_evidence",
                "feature_topic_hint": _feature_topic_hint(feature),
                "evidence_status": "provenance_available" if evidence_refs else "feature_level_only",
                "evidence_summary": evidence_summary[:500],
                "evidence_refs": evidence_refs,
                "recommendation_role": "study_variable_only" if endpoint_rows else "prioritize_for_review",
                "confidence": "low",
                "endpoint_associations": endpoint_rows,
                "auxiliary_associations": [item for item in rows if item.get("analysis_role") == "cohort_relative_auxiliary"],
                "rank_policy": "screen_order_only_not_intervention_effect_or_success_probability",
                "confounding_risk": row.get("confounding_risk", "high_unknown"),
                "causal_interpretation": "hypothesis_requires_experimental_validation",
            }
        )
    candidates.sort(key=lambda candidate: (not bool(candidate.get("evidence_refs")), int(candidate["rank"])))
    for index, candidate in enumerate(candidates, start=1):
        candidate["rank"] = index
    report = interpret_mechanisms(candidates, state.get("joint_evidence", {"id": "unavailable", "limitations": []}),
        state.get("objective", {}).get("mechanism", {}),
        [*state.get("literature_records", []), *state.get("objective", {}).get("mechanism", {}).get("evidence", [])],
        llm_client or OfflineLLMClient(), literature_search)
    interpretations = {item["feature"]: item for item in report["interpretations"]}
    for candidate in candidates:
        explanation = interpretations[candidate["feature"]]
        candidate.update({key: explanation[key] for key in ("mechanism_explanation", "opposing_evidence_refs",
            "alternative_explanations", "competing_explanations", "falsification_conditions", "expected_direction", "endpoint_expectations")})
        candidate["mechanism_status"] = explanation["status"]
        candidate["supporting_literature_refs"] = explanation["supporting_evidence_refs"]
        candidate["evidence_package_id"] = report["evidence_package_id"]
        metric_ids = {mid for row in candidate["endpoint_associations"] for point in row["points"] for mid in point["metric_ids"]}
        candidate["phenotype_evidence_refs"] = [{"evidence_kind": "phenotype_observation", "protocol_id": row["protocol_id"],
            "dataset_id": row["dataset_id"], "sample_id": row["sample_id"], "metric_id": row["metric_id"],
            "observation_id": row["observation_id"], "path": "analysis_dataset.json", **row["result_ref"]}
            for row in state.get("joint_evidence", {}).get("observations", []) if row["metric_id"] in metric_ids]
        candidate["evidence_refs"] = [*[{**ref, "evidence_kind": "protocol_use"} for ref in candidate["evidence_refs"]],
                                      *candidate["phenotype_evidence_refs"]]
    return {"candidate_feature_rows": candidates, "mechanism_report": report}


def generate_protocol_proposal(state: ProtocolOptimizingState) -> ProtocolOptimizingState:
    edits = _build_protocol_edits(state.get("candidate_feature_rows", []), state.get("objective", {}))
    recommendations = _build_recommendations(edits, state.get("objective", {}))
    proposal = {
        "schema_version": "optimized-protocol-proposal-v1",
        "objective": state.get("objective", {}),
        "recommendation_policy": "associations_are_hypotheses_not_causal_proof",
        "summary": {
            "n_candidate_features": len(state.get("candidate_feature_rows", [])),
            "n_recommendations": len(recommendations),
            "n_stage_level_edits": len(edits),
            "reference_status": state.get("reference_report", {}).get("status", "not_requested"),
        },
        "recommendations": recommendations,
        "protocol_edits": edits,
        "similar_protocol_reference": state.get("reference_report", {}),
        "evidence_package_id": state.get("joint_evidence", {}).get("id"),
        "mechanism_evidence_ref": "mechanism_evidence.json",
        "quantitative_results_ref": "endpoint_associations.json",
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
        "negative_controls": _default_negative_controls(),
    }
    return {"protocol_edits": edits, "protocol_proposal": proposal, "validation_plan": validation_plan}
