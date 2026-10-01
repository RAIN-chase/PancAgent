from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from protocol_optimization_agent.runtime import RunContext
from protocol_optimization_agent.scientific_models import (
    DecisionAssessment,
    Evidence,
    ExperimentCandidate,
    ExperimentFeedback,
    Hypothesis,
    PhenotypeMetric,
    PhenotypeState,
)
from protocol_optimization_agent.scientific_state import ScientificWorldModel
from pydantic import ValidationError


def _store(tmp_path: Path, user: str = "alice", project: str = "study") -> ScientificWorldModel:
    return ScientificWorldModel(
        RunContext.from_values(user_id=user, run_id="r1", trace_id="t1", workspace_root=tmp_path), project
    )


def _candidate(candidate_id: str) -> ExperimentCandidate:
    return ExperimentCandidate(
        id=candidate_id,
        hypothesis_ids=["h1"],
        intervention={"feature": "factor_x"},
        eligible=True,
        evidence_refs=[{"artifact_id": "synthetic_fixture"}],
        scientific_preconditions={"scientific_ready": True},
        utility=0.5,
        assessment=DecisionAssessment(
            expected_improvement=0.5,
            information_gain=0.2,
            experimental_cost=0.1,
            risk=0.1,
            source="synthetic_fixture",
            rationale="Simulated matched experiment",
        ),
    )


def _feedback(candidate_id: str, outcome: str) -> ExperimentFeedback:
    evidence_id = f"result_{candidate_id}"
    return ExperimentFeedback(
        experiment_id=candidate_id,
        evidence=Evidence(
            id=evidence_id,
            kind="experiment_result",
            source_refs=[{"artifact_id": candidate_id}],
            run_id="result_run",
            trace_id="result_trace",
            quality="passed",
            observations={"experiment_id": candidate_id},
        ),
        outcomes={"h1": outcome},
        interpretation="Matched controlled study reviewed by researcher.",
        researcher="Alice",
        phenotypes=[
            PhenotypeState(
                id=f"phenotype_{candidate_id}",
                protocol_id="p1",
                dataset_id=candidate_id,
                validation_status="passed",
                metrics=[
                    PhenotypeMetric(
                        id=f"metric_{candidate_id}",
                        family="maturity",
                        endpoint="beta_maturity",
                        value=0.8,
                        status="ok",
                        evidence_refs=[{"artifact_id": candidate_id}],
                    )
                ],
            )
        ],
    )


def test_closed_loop_preserves_conflicting_evidence_and_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(
        [Hypothesis(id="h1", statement="Factor X may change maturity", feature="factor_x", objective="maturity")], "S4"
    )
    for candidate_id, outcome in (("exp1", "supports"), ("exp2", "refutes")):
        store.put([_candidate(candidate_id)], "S5")
        store.review(candidate_id, True, "Alice", "Matched design and cost checked")
        store.mark_performed(candidate_id, "Alice")
        feedback = _feedback(candidate_id, outcome)
        store.record_feedback(feedback)
        assert store.record_feedback(feedback)["status"] == "ASSESSED"
    hypothesis = store.get("hypothesis", "h1")
    assert hypothesis["status"] == "UNRESOLVED"
    assert hypothesis["evidence_for"] == ["result_exp1"]
    assert hypothesis["evidence_against"] == ["result_exp2"]
    assert len(hypothesis["tested_experiment_ids"]) == 2
    assert [h["record"]["status"] for h in store.history("hypothesis", "h1")] == [
        "UNRESOLVED",
        "SUPPORTED",
        "UNRESOLVED",
    ]
    assert store.history("experiment", "exp1")[-1]["stage"] == "S9"
    regenerated = Hypothesis(id="h1", statement="Updated rationale", feature="factor_x", objective="maturity")
    store.put([regenerated], "S4")
    assert store.get("hypothesis", "h1")["evidence_against"] == ["result_exp2"]


def test_review_execution_and_feedback_gates_are_enforced(tmp_path: Path) -> None:
    store = _store(tmp_path)
    hypothesis = Hypothesis(id="h1", statement="Test X", feature="x", objective="y")
    store.put([hypothesis, _candidate("exp1")], "S5")
    with pytest.raises(ValueError, match="approved"):
        store.mark_performed("exp1", "Alice")
    with pytest.raises(ValueError, match="performed"):
        store.record_feedback(_feedback("exp1", "supports"))
    store.put([ExperimentCandidate(id="blocked", hypothesis_ids=["h1"], intervention={})], "S5")
    with pytest.raises(ValueError, match="preconditions"):
        store.review("blocked", True, "Alice", "review")
    with pytest.raises(ValueError, match="lifecycle"):
        store.put([_candidate("forged").model_copy(update={"status": "APPROVED"})], "S5")
    with pytest.raises(ValueError, match="Belief"):
        store.put([hypothesis.model_copy(update={"status": "SUPPORTED"})], "S4")
    with pytest.raises(ValidationError, match="experiment"):
        ExperimentFeedback.model_validate({**_feedback("exp1", "supports").model_dump(), "experiment_id": "other"})
    store.review("exp1", True, "Alice", "review")
    store.mark_performed("exp1", "Alice")
    feedback = _feedback("exp1", "supports")
    feedback.phenotypes[0].validation_status = "failed"
    with pytest.raises(ValueError, match="validated"):
        store.record_feedback(feedback)
    assert store.get("experiment", "exp1")["status"] == "PERFORMED"
    assert store.snapshot()["counts"].get("evidence", 0) == 0


def test_project_and_user_scoping_and_concurrent_writes(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def write(index: int) -> None:
        store.put([Hypothesis(id=f"h{index}", statement="Test X", feature="x", objective="y")], "S4")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    assert store.snapshot()["counts"]["hypothesis"] == 12
    assert _store(tmp_path, user="bob").snapshot()["counts"] == {}
    assert _store(tmp_path, project="other").snapshot()["counts"] == {}
    reopened = ScientificWorldModel(
        RunContext.from_values(user_id="alice", run_id="r2", workspace_root=tmp_path), "study"
    )
    assert reopened.snapshot()["counts"]["hypothesis"] == 12
    with pytest.raises(ValueError, match="identifier"):
        _store(tmp_path, project="../escape")


def test_evidence_immutability_and_transaction_rollback(tmp_path: Path) -> None:
    store = _store(tmp_path)
    evidence = Evidence(id="e1", kind="protocol", source_refs=[{"protocol_id": "p1"}], run_id="r1", trace_id="t1")
    store.put([evidence], "S0")
    store.put([evidence], "S0")
    assert len(store.history("evidence", "e1")) == 1
    with pytest.raises(ValueError, match="immutable"):
        store.put([evidence.model_copy(update={"quality": "passed"})], "S0")
    with pytest.raises(ValueError, match="Missing evidence"):
        store.put(
            [
                Evidence(id="e2", kind="protocol", source_refs=[{"protocol_id": "p2"}], run_id="r1", trace_id="t1"),
                Hypothesis(id="h1", statement="Test X", feature="x", objective="y", rationale_evidence=["missing"]),
            ],
            "S4",
        )
    assert store.snapshot()["counts"]["evidence"] == 1
