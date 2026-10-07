"""Extracted experiment design calculations; no agent execution."""

from __future__ import annotations

import math
from typing import Any

from protocol_optimization_agent.analysis.optimization._values import _unknown_spec
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import (
    DecisionAssessment,
    ExperimentCandidate,
    Hypothesis,
)


def design_experiments(
    edits: list[dict[str, Any]],
    objective: dict[str, Any],
    preconditions: dict[str, Any],
    prior_hypotheses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    design = objective.get("experiment_design", {})
    assessments = design.get("assessments", {})
    weights = {
        key: float(design.get("weights", {}).get(key, 1.0)) for key in ("improvement", "information", "cost", "risk")
    }
    if any(not math.isfinite(v) or v < 0 for v in weights.values()):
        raise ValueError("Utility weights must be finite and nonnegative.")
    limit = int(design.get("max_candidates", 10))
    if not 1 <= limit <= 50:
        raise ValueError("max_candidates must be between 1 and 50.")
    prior = {h["id"]: h for h in prior_hypotheses or []}
    hypotheses: list[Hypothesis] = []
    candidates: list[ExperimentCandidate] = []
    for edit in edits[:limit]:
        feature = edit["candidate_feature"]
        target = str(objective.get("primary") or "increase_beta_like_maturity")
        topic_id = stable_id("topic", [feature, target])
        test_spec: dict[str, Any] = {
            "objective": target,
            "baseline_protocol_id": edit.get("baseline_protocol_id")
            or objective.get("baseline_protocol_id")
            or "unknown",
            "location": {
                key: edit.get(key) if edit.get(key) not in (None, "") else "unknown"
                for key in ("target_stage", "day_start", "day_end")
            },
            "intervention": {
                key: edit.get(key) if edit.get(key) not in (None, "") else "unknown"
                for key in ("edit_type", "operation", "reagent_or_operation", "dose_or_condition_range")
            },
            "context": edit.get("applicable_context") or objective.get("applicable_context") or {"status": "unknown"},
            "endpoints": objective.get("comparison", {}).get("endpoints") or [target],
        }
        test_spec["baseline_protocol_version"] = preconditions.get("protocol_fingerprints", {}).get(
            test_spec["baseline_protocol_id"],
            edit.get("baseline_protocol_version") or objective.get("baseline_protocol_version") or "unknown",
        )
        hypothesis_id = stable_id("hypothesis_v2", [feature, test_spec])
        if any(h.id == hypothesis_id for h in hypotheses):
            raise ValueError(
                "Duplicate testable hypothesis specifications; merge source evidence before experiment design."
            )
        previous = prior.get(hypothesis_id, {})
        hypotheses.append(
            Hypothesis(
                id=hypothesis_id,
                feature=feature,
                objective=target,
                topic_id=topic_id,
                test_spec=test_spec,
                scope=test_spec,
                statement=f"Changing {feature} may alter {target}; test against matched controls.",
                protocol_ids=edit.get("source_protocols", []),
                caveats=["Observational associations do not establish intervention effects."],
                mechanism_explanation=str(edit.get("mechanism_explanation") or edit.get("rationale") or "unknown"),
                supporting_evidence_refs=[*edit.get("evidence_refs", []), *edit.get("supporting_literature_refs", [])],
                opposing_evidence_refs=[*edit.get("opposing_evidence_refs", []),
                                        *previous.get("opposing_evidence_refs", [])],
                evidence_for=previous.get("evidence_for", []),
                evidence_against=previous.get("evidence_against", []),
                status=previous.get("status", "UNRESOLVED"),
                confidence=previous.get("confidence", "low"),
                tested_experiment_ids=previous.get("tested_experiment_ids", []),
                alternative_explanations=edit.get("alternative_explanations") or [
                    "cell_line_or_batch_confounding", "stage_or_sampling_difference", "unmeasured_cointervention"],
                applicable_conditions=test_spec["context"],
                expected_change={"endpoint": test_spec["endpoints"],
                                 "direction": edit.get("expected_direction") or "unknown",
                                 "by_endpoint": edit.get("endpoint_expectations", {}),
                                 "magnitude": "unknown; not an estimated success probability"},
                falsification_conditions=edit.get("falsification_conditions") or [
                    "Matched independent replicates show no predicted endpoint change or the opposite direction.",
                    "The proposed mechanism does not reproduce under the declared intervention and context."],
                competing_explanations=edit.get("competing_explanations", []),
            )
        )
        critique = []
        if _unknown_spec(test_spec):
            critique.append("testable_intervention_unresolved")
        if not edit.get("evidence_refs"):
            critique.append("source_evidence_missing")
        if not preconditions["scientific_ready"]:
            critique.append("scientific_preconditions_unresolved")
        if previous.get("tested_experiment_ids") and not design.get("retest_reason"):
            critique.append("hypothesis_already_tested_requires_retest_reason")
        raw = assessments.get(feature)
        assessment = DecisionAssessment.model_validate(raw) if raw is not None else None
        if assessment is None:
            critique.append("decision_assessment_missing")
        utility = (
            None
            if assessment is None
            else (
                weights["improvement"] * assessment.expected_improvement
                + weights["information"] * assessment.information_gain
                - weights["cost"] * assessment.experimental_cost
                - weights["risk"] * assessment.risk
            )
        )
        candidate_id = stable_id(
            "experiment_v2", [hypothesis_id, previous.get("tested_experiment_ids", []), design.get("retest_reason", "")]
        )
        candidates.append(
            ExperimentCandidate(
                id=candidate_id,
                hypothesis_ids=[hypothesis_id],
                intervention=edit,
                evidence_refs=edit.get("evidence_refs", []),
                critique=critique,
                assessment=assessment,
                utility=utility,
                scientific_preconditions=preconditions,
                input_snapshot={
                    "objective": objective,
                    "prior_hypothesis": previous,
                    "cohort_fingerprint": preconditions.get("input_fingerprint", "unknown"),
                    "evidence_package_id": edit.get("evidence_package_id", ""),
                },
                eligible=not critique,
            )
        )
    candidates.sort(key=lambda c: (not c.eligible, -(c.utility or 0) if c.eligible else 0, c.id))
    rankable = [candidate for candidate in candidates if candidate.eligible and candidate.utility is not None]
    for index, candidate in enumerate(rankable, 1):
        candidate.rank = index
    pairwise = [
        {"a": a.id, "b": b.id, "utility_difference": a.utility - b.utility}
        for i, a in enumerate(candidates)
        for b in candidates[i + 1 :]
        if a in rankable and b in rankable and a.utility is not None and b.utility is not None
    ]
    select_count = int(design.get("select_count", 3))
    if not 1 <= select_count <= limit:
        raise ValueError("select_count must be positive and no greater than max_candidates.")
    return {
        "schema_version": "next-experiments-v1",
        "status": "pending_researcher_review",
        "ranking_status": "ranked_eligible_candidates" if rankable else "unranked_hypothesis_set",
        "reasoning_steps": ["generate", "critique", "compare", "rank", "select"],
        "evolution_policy": "regenerate_after_versioned_experimental_feedback",
        "utility_policy": "explicit_ordinal_assessments; not predicted probabilities or measured_information_gain",
        "weights": weights,
        "scientific_preconditions": preconditions,
        "hypotheses": [h.model_dump(mode="json") for h in hypotheses],
        "candidates": [c.model_dump(mode="json") for c in candidates],
        "pairwise_comparisons": pairwise,
        "selected_for_review": [c.id for c in candidates if c.eligible][:select_count],
        "automatic_execution_allowed": False,
        "selection_limit": select_count,
        "authority": "generation_draft; persist_before_review",
    }
