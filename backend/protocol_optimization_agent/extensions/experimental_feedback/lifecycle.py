"""Reviewed experiment operations; core analyses do not execute this extension."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from protocol_optimization_agent.runtime.access_control import require_researcher
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import (
    ExperimentCandidate,
    ExperimentFeedback,
    FeedbackAdmission,
    Hypothesis,
    PhenotypeState,
    ProtocolState,
)

if TYPE_CHECKING:
    from protocol_optimization_agent.science.state import ScientificWorldModel


def review(
    store: ScientificWorldModel, experiment_id: str, approve: bool, researcher: str, reason: str, *, evaluation_id: str
) -> dict[str, Any]:
    require_researcher(store.context, store.project_id, researcher)
    if not researcher.strip() or not reason.strip():
        raise ValueError("Researcher identity and review reason are required.")
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        candidate = ExperimentCandidate.model_validate(store._required(db, "experiment", experiment_id))
        if candidate.status != "PROPOSED":
            raise ValueError("Only proposed experiments may be reviewed.")
        if not evaluation_id or candidate.evaluation_id != evaluation_id:
            raise ValueError("Stale candidate evaluation; reload the current evaluation before review.")
        if approve and (
            not candidate.eligible
            or candidate.critique
            or candidate.assessment is None
            or (candidate.utility is None)
            or (not candidate.evidence_refs)
            or (not candidate.scientific_preconditions.get("scientific_ready"))
        ):
            raise ValueError("Resolve scientific preconditions and decision assessments before approval.")
        candidate.status = "APPROVED" if approve else "REJECTED"
        candidate.approved_evaluation_id = evaluation_id
        candidate.reviewer, candidate.review_reason = (researcher, reason)
        store._save(db, "experiment", candidate, "S6")
    return store.get("experiment", experiment_id)


def mark_performed(store: ScientificWorldModel, experiment_id: str, researcher: str) -> dict[str, Any]:
    require_researcher(store.context, store.project_id, researcher)
    if not researcher.strip():
        raise ValueError("Researcher identity is required.")
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        candidate = ExperimentCandidate.model_validate(store._required(db, "experiment", experiment_id))
        if candidate.status != "APPROVED":
            raise ValueError("Only approved experiments may be marked performed.")
        if not candidate.approved_evaluation_id:
            raise ValueError("Legacy approval has no evaluation binding; submit a versioned candidate for review.")
        candidate.status = "PERFORMED"
        store._save(db, "experiment", candidate, "S7", {"researcher": researcher})
    return store.get("experiment", experiment_id)


def record_feedback(store: ScientificWorldModel, feedback: ExperimentFeedback) -> dict[str, Any]:
    require_researcher(store.context, store.project_id, feedback.researcher)
    digest = stable_id("feedback", feedback.model_dump(mode="json"))
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        candidate = ExperimentCandidate.model_validate(store._required(db, "experiment", feedback.experiment_id))
        if candidate.status == "ASSESSED" and candidate.feedback_digest == digest:
            return store._required(db, "experiment", candidate.id)
        if candidate.status != "PERFORMED":
            raise ValueError("Experimental feedback requires a performed experiment.")
        if set(feedback.outcomes) != set(candidate.hypothesis_ids):
            raise ValueError("Feedback must assess every hypothesis linked to this experiment.")
        for hypothesis_id in candidate.hypothesis_ids:
            spec = store._required(db, "hypothesis", hypothesis_id).get("test_spec", {})
            if spec and (
                feedback.evidence.observations.get("test_specs", {}).get(hypothesis_id) != spec
                or feedback.evidence.observations.get("evaluation_id") != candidate.approved_evaluation_id
            ):
                raise ValueError(
                    "Feedback must bind the approved evaluation and exact tested hypothesis specification."
                )
        if any(
            not any(
                m.value is not None and m.evidence_refs and (m.status in {"ok", "numeric", "computed", "passed"})
                for m in p.metrics
            )
            or p.validation_status != "passed"
            for p in feedback.phenotypes
        ):
            raise ValueError("Feedback requires validated numeric phenotype observations.")
        store._save(db, "evidence", feedback.evidence, "S8")
        for phenotype in feedback.phenotypes:
            if store._get(db, "phenotype", phenotype.id):
                raise ValueError("Experimental feedback must use new phenotype observation ids.")
            if phenotype.evidence_ids and phenotype.evidence_ids != [feedback.evidence.id]:
                raise ValueError("Feedback phenotypes must refer to the supplied experimental evidence.")
            phenotype = phenotype.model_copy(update={"evidence_ids": [feedback.evidence.id]})
            store._save(db, "phenotype", phenotype, "S8")
        candidate.status = "OBSERVED"
        candidate.result_evidence_ids.append(feedback.evidence.id)
        candidate.result_phenotype_ids = [p.id for p in feedback.phenotypes]
        store._save(db, "experiment", candidate, "S8")
        for hypothesis_id, outcome in feedback.outcomes.items():
            hypothesis = Hypothesis.model_validate(store._required(db, "hypothesis", hypothesis_id))
            if outcome == "supports":
                hypothesis.evidence_for.append(feedback.evidence.id)
            elif outcome == "refutes":
                hypothesis.evidence_against.append(feedback.evidence.id)
            hypothesis.tested_experiment_ids.append(candidate.id)
            hypothesis.status = (
                "UNRESOLVED"
                if bool(hypothesis.evidence_for) == bool(hypothesis.evidence_against)
                else "SUPPORTED"
                if hypothesis.evidence_for
                else "REFUTED"
            )
            hypothesis.confidence = "moderate" if hypothesis.status != "UNRESOLVED" else "low"
            store._save(
                db,
                "hypothesis",
                hypothesis,
                "S9",
                {
                    "researcher": feedback.researcher,
                    "interpretation": feedback.interpretation,
                    "outcome": outcome,
                    "evidence_id": feedback.evidence.id,
                },
            )
        candidate.status, candidate.feedback_digest = ("ASSESSED", digest)
        store._save(db, "experiment", candidate, "S9")
    return store.get("experiment", candidate.id)


def admit_feedback(store: ScientificWorldModel, admission: FeedbackAdmission) -> dict[str, Any]:
    require_researcher(store.context, store.project_id, admission.researcher)
    if not admission.researcher.strip() or not admission.reason.strip():
        raise ValueError("Admission requires a researcher identity and review reason.")
    with store._connect() as db:
        db.execute("BEGIN IMMEDIATE")
        candidate = store._required(db, "experiment", admission.experiment_id)
        if candidate["status"] != "ASSESSED" or candidate["feedback_digest"] != admission.feedback_digest:
            raise ValueError("Admission must bind the assessed feedback digest.")
        admission.id = stable_id("admission", admission.experiment_id)
        existing = store._get(db, "feedback_admission", admission.id)
        if existing:
            generated = {"id", "version", "phenotypes", "control_protocol"}
            requested = admission.model_dump(exclude=generated)
            if {k: v for k, v in existing.items() if k not in generated} != requested:
                raise ValueError("Feedback admission is immutable; do not silently rebind an experiment.")
            return existing
        treatment = set(admission.treatment_sample_ids)
        controls = set(admission.control_sample_ids)
        if treatment & controls or not admission.executed_protocol.features:
            raise ValueError("Distinct treatment/control samples and actual protocol features are required.")
        phenotypes = [
            PhenotypeState.model_validate(store._required(db, "phenotype", pid))
            for pid in candidate.get("result_phenotype_ids", [])
        ]
        if {p.sample_id for p in phenotypes if p.protocol_id == admission.executed_protocol.protocol_id} != treatment:
            raise ValueError("Feedback must bind every treatment sample to the executed protocol.")
        control_rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM records WHERE kind='phenotype'")]
        control_rows = [
            PhenotypeState.model_validate(p)
            for p in control_rows
            if p["protocol_id"] == admission.control_protocol_id and p.get("sample_id") in controls
        ]
        if {p.sample_id for p in control_rows} != controls or any(
            p.validation_status != "passed" for p in control_rows
        ):
            raise ValueError("Validated matched control samples must exist before admission.")
        control_protocol = ProtocolState.model_validate(
            store._required(db, "protocol", stable_id("protocol", admission.control_protocol_id))
        )
        protocol = admission.executed_protocol
        if protocol.id != stable_id("protocol", protocol.protocol_id):
            raise ValueError("Executed protocol id must identify the actual protocol version.")
        canonical = ProtocolState.model_validate(store._required(db, "protocol", protocol.id))
        if protocol != canonical or not canonical.evidence_ids:
            raise ValueError("Executed protocol must match a persisted, versioned PA feature snapshot.")
        for eid in canonical.evidence_ids:
            if store._required(db, "evidence", eid)["kind"] != "protocol":
                raise ValueError("Executed features must originate from protocol evidence, not phenotype feedback.")
        if protocol.protocol_id == admission.control_protocol_id:
            raise ValueError("Executed and control protocol versions must have distinct identities.")
        if {p.sample_id for p in phenotypes} - treatment - controls:
            raise ValueError("Unbound feedback samples cannot enter the analysis dataset.")
        admission = admission.model_copy(
            update={"phenotypes": [*phenotypes, *control_rows], "control_protocol": control_protocol}
        )
        store._save(db, "feedback_admission", admission, "S8")
    return store.get("feedback_admission", admission.id)
