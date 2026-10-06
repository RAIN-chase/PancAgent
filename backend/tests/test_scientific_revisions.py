from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent
from protocol_optimization_agent.analysis.optimization.comparability import scientific_preconditions
from protocol_optimization_agent.analysis.optimization.experiment_design import design_experiments
from protocol_optimization_agent.runtime.archive import RunArchive, file_hash
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.runtime.io import read_csv_dicts, read_json, write_csv_dicts, write_json
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.ingestion import ingest_experiment_proposals
from protocol_optimization_agent.science.models import (
    DecisionAssessment,
    Evidence,
    ExperimentCandidate,
    ExperimentFeedback,
    FeedbackAdmission,
    Hypothesis,
    PhenotypeState,
    ProtocolState,
)
from protocol_optimization_agent.science.state import ScientificWorldModel

METADATA = {"cell_line": "line1", "stage": "S6", "sequencing_modality": "scRNA", "reference_version": "atlas1",
            "expression_scale": "fixed_score", "normalization": "fixed_v1", "method_version": "v1",
            "cell_population": "beta", "sampling_condition": "day30"}


def _phenotypes(pid: str, value: float) -> list[dict]:
    return [{"id": f"{pid}_r{r}", "protocol_id": pid, "dataset_id": f"d_{pid}", "sample_id": f"{pid}_s{r}",
             "validation_status": "passed", "metadata": {**METADATA, "biological_replicate_id": f"{pid}_r{r}"},
             "metrics": [{"id": f"{pid}_m{r}", "family": "maturity", "endpoint": "beta_maturity", "value": value,
                          "unit": "score_0_1", "status": "numeric", "method": "fixed_score",
                          "context": {"source_table": "maturity_scores", "source_column": "maturity_score"},
                          "evidence_refs": [{"artifact_id": f"d_{pid}"}]}]} for r in range(2)]


def _experiment() -> ExperimentCandidate:
    return ExperimentCandidate(id="exp", hypothesis_ids=["h"], intervention={"feature": "reagent_x"},
        eligible=True, utility=0.5, evidence_refs=[{"artifact_id": "fixture"}],
        scientific_preconditions={"scientific_ready": True},
        assessment=DecisionAssessment(expected_improvement=0.5, information_gain=0.2,
            experimental_cost=0.1, risk=0.1, source="fixture", rationale="controlled test"))


def test_candidate_reassessment_and_stale_approval_are_version_bound(tmp_path: Path) -> None:
    store = ScientificWorldModel(RunContext.from_values(workspace_root=tmp_path))
    store.put([Hypothesis(id="h", statement="X", feature="x", objective="y")], "S4")
    blocked = _experiment().model_copy(update={"eligible": False, "critique": ["missing_cell_line"]})
    store.put([blocked], "S5")
    old = store.get("experiment", "exp")
    store.put([_experiment()], "S5")
    current = store.get("experiment", "exp")
    assert current["eligible"] and current["evaluation_version"] == 2
    assert current["evaluation_id"] != old["evaluation_id"]
    with pytest.raises(ValueError, match="Stale"):
        store.review("exp", True, "Alice", "review", evaluation_id=old["evaluation_id"])
    store.review("exp", True, "Alice", "review", evaluation_id=current["evaluation_id"])
    locked = store.get("experiment", "exp")
    store.put([blocked], "S5")
    assert store.get("experiment", "exp") == locked
    assert locked["approved_evaluation_id"] == current["evaluation_id"]
    assert [h["record"]["evaluation_version"] for h in store.history("experiment", "exp")] == [1, 2, 2]
    report = {"hypotheses": [Hypothesis(id="h", statement="X", feature="x", objective="y").model_dump()],
              "candidates": [blocked.model_dump()]}
    write_json(tmp_path / "next_experiment_candidates.json", report)
    ingest_experiment_proposals(store, tmp_path)
    exported = read_json(tmp_path / "next_experiment_candidates.json")
    assert exported["candidates"][0] == locked
    assert exported["selected_for_review"] == []


def test_evaluation_reversion_does_not_revive_a_stale_approval(tmp_path: Path) -> None:
    store = ScientificWorldModel(RunContext.from_values(workspace_root=tmp_path))
    store.put([Hypothesis(id="h", statement="X", feature="x", objective="y"), _experiment()], "S5")
    old = store.get("experiment", "exp")
    store.put([_experiment().model_copy(update={"eligible": False, "critique": ["new_evidence"]})], "S5")
    assert not store.get("experiment", "exp")["eligible"]
    store.put([_experiment()], "S5")
    restored = store.get("experiment", "exp")
    assert restored["eligible"] and restored["evaluation_version"] == 3
    assert restored["evaluation_fingerprint"] == old["evaluation_fingerprint"]
    assert restored["evaluation_id"] != old["evaluation_id"]
    with pytest.raises(ValueError, match="Stale"):
        store.review("exp", True, "Alice", "review", evaluation_id=old["evaluation_id"])


def test_ingesting_sealed_proposals_creates_persisted_child_without_mutating_history(tmp_path: Path) -> None:
    context = RunContext.from_values(workspace_root=tmp_path, run_id="source")
    archive = RunArchive(context)
    archive.start({"operation": "optimization"})
    output = archive.root / "protocol_optimization"
    report = {"hypotheses": [Hypothesis(id="h", statement="X", feature="x", objective="y").model_dump()],
              "candidates": [_experiment().model_dump()]}
    write_json(output / "next_experiment_candidates.json", report)
    archive.finish({"output_dir": str(output)})
    original_bundle = read_json(archive.root / "bundle_manifest.json")
    store = ScientificWorldModel(context)
    result = ingest_experiment_proposals(store, output)
    target = Path(result["output_dir"])
    assert target != output
    assert read_json(output / "next_experiment_candidates.json") == report
    assert read_json(target / "next_experiment_candidates.json")["candidates"][0] == store.get("experiment", "exp")
    assert read_json(target.parent / "run_manifest.json")["parent_run_id"] == "source"
    assert read_json(target.parent / "final_response.json")["archive_provenance"]["candidate_versions"]
    assert store.history("experiment", "exp")[0]["run_id"] == result["run_id"]
    for root, bundle in ((archive.root, original_bundle), (target.parent, read_json(target.parent / "bundle_manifest.json"))):
        assert all(file_hash(root / name) == checksum for name, checksum in bundle["files"].items())


def test_hypotheses_separate_stage_direction_dose_and_context() -> None:
    edit = {"candidate_feature": "x", "baseline_protocol_id": "p1", "target_stage": "A",
            "day_start": 0, "day_end": 10, "operation": "increase", "dose_or_condition_range": "10 ng/ml",
            "applicable_context": {"cell_line": "line1"}}
    variations = [edit, {**edit, "target_stage": "B"}, {**edit, "operation": "reduce"},
                  {**edit, "dose_or_condition_range": "1 ng/ml"}, {**edit, "applicable_context": {"cell_line": "line2"}}]
    reports = [design_experiments([e], {"primary": "maturity"}, {"scientific_ready": True}) for e in variations]
    assert len({r["hypotheses"][0]["id"] for r in reports}) == 5
    assert len({r["hypotheses"][0]["topic_id"] for r in reports}) == 1
    prior = [{**reports[0]["hypotheses"][0], "tested_experiment_ids": ["old_exp"]}]
    revised = design_experiments([variations[1]], {"primary": "maturity"}, {"scientific_ready": True}, prior)
    assert "hypothesis_already_tested_requires_retest_reason" not in revised["candidates"][0]["critique"]


def test_target_cohort_rejects_unknown_scales_pending_values_and_pseudoreplicates() -> None:
    protocols = [{"protocol_id": f"p{i}", **METADATA} for i in range(5)]
    phenotypes = [p for i in range(5) for p in _phenotypes(f"p{i}", i / 10)]
    comparison = {"endpoints": ["beta_maturity"]}
    assert scientific_preconditions(protocols, phenotypes, comparison=comparison)["scientific_ready"]
    pending = deepcopy(phenotypes)
    for p in pending:
        if p["protocol_id"] == "p4":
            p["metrics"][0]["value"] = None
    report = scientific_preconditions(protocols, pending, comparison={**comparison, "minimum_protocols": 5})
    assert not report["scientific_ready"]
    assert report["cohort"]["counts"]["beta_maturity"]["protocols"] == 4
    for p in phenotypes:
        p["metadata"]["biological_replicate_id"] = "one_replicate"
    assert not scientific_preconditions(protocols, phenotypes, comparison=comparison)["scientific_ready"]
    mixed = [p for i in range(5) for p in _phenotypes(f"p{i}", i / 10)]
    mixed[0]["metadata"]["expression_scale"] = "raw_counts"
    assert not scientific_preconditions(protocols, mixed, comparison=comparison)["scientific_ready"]
    for p in mixed:
        p["metrics"][0]["unit"] = "source_scale"
    assert not scientific_preconditions(protocols, mixed, comparison=comparison)["scientific_ready"]
    stratified = [p for i in range(6) for p in _phenotypes(f"p{i}", i / 10)]
    for p in stratified:
        if p["protocol_id"] in {"p3", "p4", "p5"}:
            p["metadata"]["cell_line"] = "line2"
    assert scientific_preconditions(protocols, stratified,
        comparison={**comparison, "stratum": {"cell_line": "line1"}})["scientific_ready"]


def test_admitted_feedback_changes_actual_dataset_scores_and_associations(tmp_path: Path) -> None:
    pa, da = tmp_path / "pa", tmp_path / "da"
    pa.mkdir()
    da.mkdir()
    protocols = [{"protocol_id": f"p{i}", "reagent_x": i, **METADATA} for i in range(3)]
    phenotypes = [p for i in range(3) for p in _phenotypes(f"p{i}", (i + 1) / 10)]
    write_csv_dicts(pa / "protocol_feature_matrix.csv", protocols)
    write_json(da / "phenotype_ir.json", {"phenotypes": phenotypes})
    objective = {"primary": "maturity", "comparison": {"endpoints": ["beta_maturity"]}}
    store = ScientificWorldModel(RunContext.from_values(workspace_root=tmp_path), "study")
    store.put([ProtocolState(id=stable_id("protocol", p["protocol_id"]), protocol_id=p["protocol_id"],
                             metadata=METADATA, features={"reagent_x": p["reagent_x"]}) for p in protocols], "S1")
    store.put([PhenotypeState.model_validate(p) for p in phenotypes], "S2")
    store.put([Hypothesis(id="h", statement="X", feature="x", objective="maturity"), _experiment()], "S5")
    candidate = store.get("experiment", "exp")
    store.review("exp", True, "Alice", "review", evaluation_id=candidate["evaluation_id"])
    store.mark_performed("exp", "Alice")
    evidence = Evidence(id="result", kind="experiment_result", quality="passed", run_id="r", trace_id="t",
                        source_refs=[{"artifact_id": "result"}], observations={"experiment_id": "exp"})
    store.record_feedback(ExperimentFeedback(experiment_id="exp", evidence=evidence, outcomes={"h": "supports"},
        interpretation="controlled result", researcher="Alice",
        phenotypes=[PhenotypeState.model_validate(p) for p in _phenotypes("new_p3", 0.05)]))
    before = ProtocolOptimizingAgent(tmp_path / "before", objective).run(pa, da, scientific_context=store.reasoning_context())
    dataset_before = read_json(before["analysis_dataset"])
    assert len(dataset_before["phenotypes"]) == 6
    ingest_experiment_proposals(store, before["analysis_result"].parent)
    fingerprint_before = store.feedback_fingerprint()
    pa_evidence = Evidence(id="actual_pa", kind="protocol", quality="passed", run_id="pa_run", trace_id="pa_trace",
                           source_refs=[{"artifact_id": "actual_protocol_compiler_output"}])
    actual_protocol = ProtocolState(id=stable_id("protocol", "new_p3"), protocol_id="new_p3", metadata=METADATA,
                                    features={"reagent_x": 3}, evidence_ids=["actual_pa"])
    store.put([pa_evidence, actual_protocol], "S1")
    admission = FeedbackAdmission(id="pending", experiment_id="exp", feedback_digest=store.get("experiment", "exp")["feedback_digest"],
        executed_protocol=actual_protocol,
        treatment_sample_ids=["new_p3_s0", "new_p3_s1"], control_protocol_id="p0", control_sample_ids=["p0_s0", "p0_s1"],
        researcher="Alice", reason="Actual executed features and matched controls verified")
    saved = store.admit_feedback(admission)
    assert store.admit_feedback(admission) == saved
    assert fingerprint_before != store.feedback_fingerprint()
    forged = admission.model_copy(update={"executed_protocol": actual_protocol.model_copy(update={"features": {"reagent_x": 1000}})})
    with pytest.raises(ValueError, match="immutable"):
        store.admit_feedback(forged)
    after = ProtocolOptimizingAgent(tmp_path / "after", objective).run(pa, da, scientific_context=store.reasoning_context())
    dataset_after = read_json(after["analysis_dataset"])
    assert len(dataset_after["phenotypes"]) == 8
    assert dataset_after["id"] != dataset_before["id"]
    assert dataset_after["admitted_experiment_ids"] == ["exp"]
    result = read_json(after["analysis_result"])
    assert result["previous_result_id"] == read_json(before["analysis_result"])["id"]
    assert result["change_summary"]["admitted_experiments_added"] == ["exp"]
    assert not result["change_summary"]["base_source_changed"]
    old_association = read_csv_dicts(before["protocol_phenotype_associations"])[0]
    new_association = read_csv_dicts(after["protocol_phenotype_associations"])[0]
    assert float(old_association["effect_estimate"]) != float(new_association["effect_estimate"])
    assert old_association["n_protocols"] == "3" and new_association["n_protocols"] == "4"
    assert any(row["protocol_id"] == "new_p3" and float(row["raw_score"]) == 0.05
               for row in read_csv_dicts(after["phenotype_targets"]))
    ingest_experiment_proposals(store, after["analysis_result"].parent)
    assert store.get("analysis_dataset", dataset_after["id"])["admitted_experiment_ids"] == ["exp"]
    invalid = admission.model_copy(update={"control_sample_ids": ["nonexistent"]})
    with pytest.raises(ValueError, match="immutable"):
        store.admit_feedback(invalid)
    assert json.loads(after["analysis_dataset"].read_text())["policy"].endswith("not_causal_inference")
