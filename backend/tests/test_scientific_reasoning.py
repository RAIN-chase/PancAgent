from __future__ import annotations

from copy import deepcopy

import pytest
from protocol_optimization_agent.scientific_reasoning import (
    build_phenotype_ir,
    design_experiments,
    interpret_phenotypes,
    scientific_preconditions,
)


def _ir() -> dict:
    return build_phenotype_ir(
        {
            "maturity_scores": [
                {"protocol_id": "p1", "dataset_id": "d1", "maturity_score": 0.7, "status": "ok", "lineage": "beta"}
            ]
        },
        [],
        {"validation_status": "passed"},
        "/analysis",
    )


def test_phenotype_ir_preserves_numeric_basis_and_pending_values() -> None:
    ir = build_phenotype_ir(
        {
            "marker_scores": [
                {"protocol_id": "p1", "dataset_id": "d1", "score": 0.2, "marker_set": "stress", "status": "ok"},
                {"protocol_id": "p1", "dataset_id": "d1", "score": 0.9, "marker_set": "beta", "status": "pending"},
            ]
        },
        [],
        {},
        "/analysis",
    )
    metrics = ir["phenotypes"][0]["metrics"]
    assert metrics[0]["value"] == 0.2
    assert metrics[0]["family"] == "quality"
    assert metrics[0]["evidence_refs"][0]["row_index"] == 0
    assert metrics[1]["value"] is None
    assert ir["phenotypes"][0]["validation_status"] == "unknown"


def test_interpretation_is_independent_and_conflicts_require_review() -> None:
    ir = _ir()
    original = deepcopy(ir)

    def interpreter(payload: dict, index: int) -> list:
        metric_id = payload["phenotypes"][0]["metrics"][0]["id"]
        payload["phenotypes"][0]["metrics"][0]["value"] = 999
        return [
            {
                "claim_id": "c1",
                "statement": "Maturity may increase",
                "metric_ids": [metric_id],
                "conclusion": "refutes" if index == 2 else "supports",
            }
        ]

    result = interpret_phenotypes(ir, interpreter, 3)
    assert ir == original
    assert result["status"] == "human_review_required"
    assert result["consensus"][0]["conclusions"] == ["refutes", "supports"]
    assert interpret_phenotypes(ir, None)["status"] == "not_requested"
    invalid = interpret_phenotypes(
        ir, lambda *_: [{"claim_id": "c1", "statement": "X", "metric_ids": ["invented"], "conclusion": "supports"}], 2
    )
    assert len(invalid["errors"]) == 2
    agreed = interpret_phenotypes(
        ir,
        lambda p, _: [
            {
                "claim_id": "c1",
                "statement": "Maturity may increase",
                "metric_ids": [p["phenotypes"][0]["metrics"][0]["id"]],
                "conclusion": "supports",
            }
        ],
        2,
    )
    assert agreed["status"] == "agreement"


def test_scientific_comparability_distinguishes_unknown_and_mismatch() -> None:
    metadata = {"cell_line": "line1", "stage": "S6", "sequencing_modality": "scRNA", "reference_version": "atlas-v1"}
    protocols = [{"protocol_id": f"p{i}", **metadata} for i in range(5)]
    phenotypes = [{**_ir()["phenotypes"][0], "protocol_id": f"p{i}", "metadata": metadata} for i in range(5)]
    assert scientific_preconditions(protocols, phenotypes)["scientific_ready"] is True
    protocols[0].pop("cell_line")
    report = scientific_preconditions(protocols, phenotypes)
    assert report["scientific_ready"] is False
    assert report["checks"][0]["status"] == "unknown"
    protocols[0]["cell_line"] = "line2"
    assert scientific_preconditions(protocols, phenotypes)["checks"][0]["status"] == "mismatch"
    assert scientific_preconditions(protocols, [], "descriptive_comparison")["status"] == "descriptive_only"


def test_experiment_selection_uses_explicit_information_value_and_prior_tests() -> None:
    edits = [
        {"candidate_feature": f"factor_{letter}", "evidence_refs": [{"protocol_id": "p1"}], "rationale": "test"}
        for letter in ("a", "b")
    ]
    objective = {
        "primary": "maturity",
        "experiment_design": {
            "assessments": {
                "factor_a": {
                    "expected_improvement": 0.9,
                    "information_gain": 0.1,
                    "experimental_cost": 0.1,
                    "risk": 0.1,
                    "source": "researcher",
                    "rationale": "comparison A",
                },
                "factor_b": {
                    "expected_improvement": 0.8,
                    "information_gain": 0.7,
                    "experimental_cost": 0.1,
                    "risk": 0.1,
                    "source": "researcher",
                    "rationale": "distinguishes mechanisms",
                },
            }
        },
    }
    report = design_experiments(edits, objective, {"scientific_ready": True})
    assert report["candidates"][0]["intervention"]["candidate_feature"] == "factor_b"
    assert report["candidates"][0]["utility"] == pytest.approx(1.3)
    assert report["selected_for_review"][0] == report["candidates"][0]["id"]
    assert report["automatic_execution_allowed"] is False
    prior = [{**report["hypotheses"][0], "tested_experiment_ids": ["exp1"]}]
    next_report = design_experiments(edits, objective, {"scientific_ready": True}, prior)
    retested = next(c for c in next_report["candidates"] if c["intervention"]["candidate_feature"] == "factor_a")
    assert not retested["eligible"]
    assert "hypothesis_already_tested_requires_retest_reason" in retested["critique"]
    missing = design_experiments(edits, {"primary": "maturity"}, {"scientific_ready": False})
    assert missing["selected_for_review"] == []
    assert all(c["utility"] is None for c in missing["candidates"])
