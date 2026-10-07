from __future__ import annotations

from copy import deepcopy

import pytest
from protocol_optimization_agent.agents.protocol_optimizing import (
    ProtocolOptimizingAgent,
    _optimization_cache_key,
    _scientific_artifact_preview,
)
from protocol_optimization_agent.analysis.optimization.association import analyze_associations, leave_study_out
from protocol_optimization_agent.analysis.optimization.evidence import (
    build_joint_evidence,
    comparison_phenotypes,
    endpoint_preconditions,
    summarize_endpoints,
)
from protocol_optimization_agent.analysis.optimization.experiment_design import design_experiments
from protocol_optimization_agent.analysis.optimization.mechanisms import interpret_mechanisms
from protocol_optimization_agent.runtime import literature
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.runtime.llm_client import OfflineLLMClient, StaticMockLLMClient
from protocol_optimization_agent.science.identity import stable_id
from protocol_optimization_agent.science.models import Hypothesis
from protocol_optimization_agent.science.state import ScientificWorldModel


def _state():
    context = {"cell_line": "line1", "stage": "S6", "sequencing_modality": "scRNA", "reference_version": "v1",
               "expression_scale": "score", "normalization": "fixed", "method_version": "v1",
               "cell_population": "beta", "sampling_condition": "day30"}
    protocols = [{"protocol_id": f"p{i}", "reagent_x": i, "reagent_y": i, **context} for i in range(3)]
    phenotypes = []
    for i, protocol in enumerate(protocols):
        for repeat in range(2):
            phenotypes.append({"id": f"ph{i}_{repeat}", "protocol_id": protocol["protocol_id"], "dataset_id": f"d{i}",
                "sample_id": f"sample{i}_{repeat}", "validation_status": "passed",
                "metadata": {**context, "study_id": "study1", "biological_replicate_id": f"r{repeat}",
                             "protocol_snapshot_id": stable_id("protocol_snapshot", protocol)},
                "metrics": [{"id": f"m{i}_{repeat}", "endpoint": "beta", "family": "maturity", "value": i + repeat,
                             "unit": "score", "method": "fixed", "status": "numeric", "context": {},
                             "evidence_refs": [{"artifact": "maturity_scores.csv", "row_index": i * 2 + repeat}]}]})
    state = {"analysis_dataset": {"id": "ds1", "source_fingerprint": "input-v1", "protocol_rows": protocols,
                                  "phenotypes": phenotypes}, "protocol_feature_rows": protocols,
             "objective": {"comparison": {"endpoints": ["beta"]}}}
    _refresh(state)
    return state


def _refresh(state):
    dataset = state["analysis_dataset"]
    state["endpoint_preconditions"] = endpoint_preconditions(dataset["protocol_rows"], dataset["phenotypes"],
                                                              state["objective"]["comparison"])
    state["joint_evidence"] = build_joint_evidence(state)
    state["endpoint_rows"] = summarize_endpoints(state["joint_evidence"])


def test_joint_evidence_binds_source_values_versions_and_json_paths():
    state = _state()
    before = deepcopy(state["analysis_dataset"])
    package = state["joint_evidence"]
    assert package["identity_versions_verified"]
    observation = package["observations"][1]
    assert observation["raw_value"] == 1
    assert observation["result_ref"] == {"artifact": "analysis_dataset.json", "field_path": "/phenotypes/1/metrics/0/value",
                                         "input_version": "input-v1"}
    assert observation["evidence_refs"]
    assert observation["uncertainty"]["confidence_interval"] == "unknown"
    assert state["analysis_dataset"] == before


def test_new_chat_evidence_previews_do_not_duplicate_full_sample_corpus():
    package = _state()["joint_evidence"]
    package["observations"] = package["observations"] * 100
    before = deepcopy(package)
    preview = _scientific_artifact_preview("joint", package)
    assert preview["n_observations"] == 600
    assert len(preview["observations"]) == 10
    assert preview["complete_evidence_artifact"] == "joint_evidence.json"
    assert preview["preview_only"] is True
    assert package == before


def test_wrong_version_is_excluded_before_counting_replicates():
    state = _state()
    state["analysis_dataset"]["phenotypes"][0]["metadata"]["protocol_snapshot_id"] = "wrong"
    _refresh(state)
    assert not state["endpoint_preconditions"]["beta"]["scientific_ready"]
    assert state["joint_evidence"]["observations"][0]["identity_link"]["status"] == "version_mismatch"
    assert not any(row["association_eligible"] for row in state["joint_evidence"]["observations"])
    assert not state["endpoint_rows"]


def test_unknown_version_supports_only_exploratory_identity_join():
    state = _state()
    for phenotype in state["analysis_dataset"]["phenotypes"]:
        phenotype["metadata"].pop("protocol_snapshot_id")
    _refresh(state)
    assert not state["joint_evidence"]["identity_versions_verified"]
    assert all(row["association_eligible"] for row in state["joint_evidence"]["observations"])
    assert state["joint_evidence"]["identity_summary"] == {"protocol_id_only_version_unverified": 6}


def test_conflicting_explicit_version_is_not_overridden_by_matching_snapshot():
    state = _state()
    protocol = state["analysis_dataset"]["protocol_rows"][0]
    protocol["protocol_version"] = "v2"
    phenotype = state["analysis_dataset"]["phenotypes"][0]
    phenotype["metadata"].update(protocol_version="v1", protocol_snapshot_id=stable_id("protocol_snapshot", protocol))
    _refresh(state)
    assert state["joint_evidence"]["observations"][0]["identity_link"]["status"] == "version_mismatch"


def test_invalid_endpoint_does_not_block_other_endpoints():
    state = _state()
    state["objective"]["comparison"]["endpoints"].append("stress")
    for phenotype in state["analysis_dataset"]["phenotypes"]:
        metric = deepcopy(phenotype["metrics"][0])
        metric.update(id=metric["id"] + "stress", endpoint="stress", unit="unknown")
        phenotype["metrics"].append(metric)
    _refresh(state)
    assert state["endpoint_preconditions"]["beta"]["scientific_ready"]
    assert not state["endpoint_preconditions"]["stress"]["scientific_ready"]
    assert {row["endpoint"] for row in state["endpoint_rows"]} == {"beta"}


def test_family_qualified_targets_separate_composition_and_maturity_without_changing_ir():
    state = _state()
    state["objective"]["comparison"]["endpoints"] = ["maturity:beta", "cell_identity:beta"]
    for phenotype in state["analysis_dataset"]["phenotypes"]:
        metric = deepcopy(phenotype["metrics"][0])
        metric.update(id=metric["id"] + "composition", family="cell_identity", value=0.5, unit="fraction", method="composition")
        metric["context"].update(expression_scale="fraction", normalization="cell_fraction")
        phenotype["metrics"].append(metric)
    original = deepcopy(state["analysis_dataset"])
    _refresh(state)
    assert all(gate["scientific_ready"] for gate in state["endpoint_preconditions"].values())
    assert {row["endpoint"] for row in state["endpoint_rows"]} == {"maturity:beta", "cell_identity:beta"}
    assert {row["raw_endpoint"] for row in state["joint_evidence"]["observations"]} == {"beta"}
    assert state["analysis_dataset"] == original
    with pytest.raises(ValueError, match="overlapping"):
        comparison_phenotypes(original["phenotypes"], ["beta", "maturity:beta"])


def test_repeat_measurements_do_not_increase_replicate_weight():
    state = _state()
    duplicate = deepcopy(state["analysis_dataset"]["phenotypes"][0])
    duplicate.update(id="repeated_measurement", sample_id="other_sample_of_same_replicate")
    duplicate["metrics"][0].update(id="duplicate_metric", value=2)
    state["analysis_dataset"]["phenotypes"].append(duplicate)
    state["analysis_dataset"]["phenotypes"][1]["metrics"][0]["value"] = 9
    _refresh(state)
    summary = state["endpoint_rows"][0]
    assert summary["raw_mean"] == 5  # replicate means (0+2)/2 and 9, not three independent values
    assert summary["n_biological_replicates"] == 2
    assert summary["n_samples"] == 3


def _points(values, studies):
    return [{"protocol_id": f"p{i}", "x": x, "y": y, "study_ids": [studies[i]]}
            for i, (x, y) in enumerate(values)]


def test_single_study_perfect_correlation_is_not_cross_study_validation():
    points = _points([(i, i) for i in range(6)], ["one"] * 6)
    report = leave_study_out(points)
    assert report["status"].startswith("not_evaluable")
    assert report["n_valid_folds"] == 0


def test_study_driven_correlation_reverses_after_whole_study_exclusion():
    points = _points([(10, 10), (11, 11), (0, 2), (1, 1), (2, 0), (3, -1)], ["a", "a", "b", "b", "c", "c"])
    report = leave_study_out(points)
    assert report["status"] == "direction_unstable"
    assert report["folds"][0]["pearson_r"] == pytest.approx(-1)
    points[2]["study_ids"].append("a")
    fold = leave_study_out(points)["folds"][0]
    assert "p2" in fold["excluded_protocol_ids"] and "p2" not in fold["retained_protocol_ids"]


def test_raw_endpoints_are_primary_and_collinear_features_remain_unresolved():
    state = _state()
    rows = analyze_associations(state, 20)["association_rows"]
    assert all(row["analysis_role"] == "primary_raw_endpoint" for row in rows)
    assert rows[0]["effect_estimate"] == pytest.approx(1)
    assert rows[0]["collinear_features"][0]["feature"] == "reagent_y"
    assert rows[0]["confidence"] == "low"
    assert rows[0]["leave_study_out"]["status"].startswith("not_evaluable")
    assert rows[0]["points"][0]["observation_ids"]


def _mechanism_inputs():
    state = _state()
    candidate = {"feature": "reagent_x", "endpoint_associations": analyze_associations(state, 20)["association_rows"],
                 "evidence_refs": [{"protocol_id": "p0", "evidence_quote": "X was used."}]}
    records = [{"feature": "reagent_x", "pmid": "123", "quote": "An opposing observation in another context.", "stance": "opposes"},
               {"feature": "reagent_x", "pmid": "124", "quote": "A possible mechanism in a limited context.", "stance": "supports"}]
    return candidate, state["joint_evidence"], records


def test_protocol_use_is_not_promoted_to_mechanism_and_default_has_no_network():
    candidate, package, _ = _mechanism_inputs()
    def forbidden(*args):
        raise AssertionError("Unexpected literature request")
    result = interpret_mechanisms([candidate], package, {}, [], OfflineLLMClient(), forbidden)
    item = result["interpretations"][0]
    assert item["status"] == "mechanism_evidence_missing"
    assert item["numeric_predictions"] is None
    assert len(item["competing_explanations"]) >= 3
    assert item["falsification_conditions"]
    assert result["budget"]["llm_calls"] == result["budget"]["literature_queries"] == 0


@pytest.mark.parametrize("invalid", ["citation", "observation", "probability", "reversal", "endpoint"])
def test_model_cannot_invent_sources_probabilities_or_reverse_opposition(invalid):
    candidate, package, records = _mechanism_inputs()
    initial = interpret_mechanisms([candidate], package, {}, records, OfflineLLMClient())
    opposing, supporting = initial["literature"]
    response = {"mechanism_explanation": "A tentative mechanism.", "supporting_literature_ids": [supporting["id"]],
                "observation_ids": [package["observations"][0]["observation_id"]],
                "alternative_explanations": ["Batch", "Co-intervention"], "falsification_conditions": ["No matched effect."]}
    if invalid == "citation":
        response["supporting_literature_ids"] = ["invented"]
    elif invalid == "observation":
        response["observation_ids"] = ["invented"]
    elif invalid == "probability":
        response["success_probability"] = 0.9
    elif invalid == "reversal":
        response["supporting_literature_ids"] = [opposing["id"]]
    else:
        response["endpoint_expectations"] = {"invented": "higher"}
    client = StaticMockLLMClient({"grounded_mechanism_hypotheses": response})
    result = interpret_mechanisms([candidate], package, {"max_llm_calls": 1}, records, client)
    assert result["errors"][0]["phase"] == "mechanism_interpretation"
    assert result["interpretations"][0]["opposing_evidence_refs"][0]["pmid"] == "123"
    assert result["interpretations"][0]["mechanism_explanation"].startswith("unknown;")


def test_grounded_interpretation_preserves_opposition_even_if_model_omits_it():
    candidate, package, records = _mechanism_inputs()
    baseline = interpret_mechanisms([candidate], package, {}, records, OfflineLLMClient())
    response = {"mechanism_explanation": "Possible mechanism, restricted to the supplied context.",
                "supporting_literature_ids": [baseline["literature"][1]["id"]],
                "observation_ids": [package["observations"][0]["observation_id"]],
                "alternative_explanations": ["Batch", "Co-intervention"], "falsification_conditions": ["No matched effect."]}
    client = StaticMockLLMClient({"grounded_mechanism_hypotheses": response})
    result = interpret_mechanisms([candidate], package, {"max_llm_calls": 1}, records, client)
    assert not result["errors"]
    assert result["interpretations"][0]["status"].endswith("requires_review")
    assert result["interpretations"][0]["opposing_evidence_refs"][0]["pmid"] == "123"
    assert len(client.calls) == 1


def test_mechanism_expectations_are_endpoint_specific_not_an_overall_quality_direction():
    candidate, package, records = _mechanism_inputs()
    candidate["endpoint_associations"].append({**candidate["endpoint_associations"][0], "target_endpoint": "stress"})
    baseline = interpret_mechanisms([candidate], package, {}, records, OfflineLLMClient())
    response = {"mechanism_explanation": "Tentative endpoint-specific hypothesis.",
                "supporting_literature_ids": [baseline["literature"][1]["id"]],
                "observation_ids": [package["observations"][0]["observation_id"]],
                "alternative_explanations": ["Batch", "Co-intervention"], "falsification_conditions": ["No matched effect."],
                "expected_direction": "higher", "endpoint_expectations": {"beta": "higher", "stress": "unknown"}}
    client = StaticMockLLMClient({"grounded_mechanism_hypotheses": response})
    result = interpret_mechanisms([candidate], package, {"max_llm_calls": 1}, records, client)
    item = result["interpretations"][0]
    assert item["expected_direction"] == "unknown"
    assert item["endpoint_expectations"] == {"beta": "higher", "stress": "unknown"}


def test_retrieval_is_bounded_logs_challenges_and_blocks_private_identifiers():
    candidate, package, _ = _mechanism_inputs()
    calls = []
    def search(query, limit):
        calls.append(query)
        return [{"pmid": "123", "quote": "Null finding.", "stance": "uncertain"}]
    config = {"max_literature_queries": 2, "queries": [
        {"feature": "reagent_x", "query": "factor X beta cells", "intent": "support"},
        {"feature": "reagent_x", "query": "factor X null findings", "intent": "challenge"},
        {"feature": "reagent_x", "query": "third query", "intent": "neutral"}]}
    result = interpret_mechanisms([candidate], package, config, [], OfflineLLMClient(), search)
    assert len(calls) == 2
    assert result["queries"][1]["intent"] == "challenge"
    config["queries"][0]["query"] = "sample0_0 beta cells"
    calls.clear()
    result = interpret_mechanisms([candidate], package, config, [], OfflineLLMClient(), search)
    assert len(calls) == 1
    assert result["errors"][0]["phase"] == "retrieval"


def test_pubmed_retrieval_parsing_and_failures_are_local(monkeypatch):
    xml = b'<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article><ArticleTitle>Study</ArticleTitle><Abstract><AbstractText>Result.</AbstractText></Abstract></Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'
    def get(endpoint, params):
        return b'{"esearchresult":{"idlist":["123"]}}' if endpoint == "esearch.fcgi" else xml
    monkeypatch.setattr(literature, "_get", get)
    assert literature.search_pubmed("beta cell maturation")[0]["quote"] == "Result."
    with pytest.raises(ValueError, match="Malformed"):
        literature.parse_pubmed_articles(b"<broken")
    with pytest.raises(ValueError, match="entities"):
        literature.parse_pubmed_articles(b'<!ENTITY external SYSTEM "file:///secret">')
    monkeypatch.setattr(literature, "_get", lambda *args: b'{"esearchresult":{"idlist":["not-a-pmid"]}}')
    with pytest.raises(ValueError, match="identifier"):
        literature.search_pubmed("beta cell maturation")


def test_step_annotations_and_mechanism_sources_invalidate_po_cache(tmp_path):
    pa, da = tmp_path / "pa", tmp_path / "da"
    pa.mkdir()
    da.mkdir()
    baseline = _optimization_cache_key(pa, da, {}, {})
    (pa / "semantic_step_annotations.jsonl").write_text('{"protocol_id":"p1"}\n', encoding="utf-8")
    changed = _optimization_cache_key(pa, da, {}, {})
    assert changed != baseline
    (da / "mechanism_literature_evidence.json").write_text('{"records":[]}', encoding="utf-8")
    assert _optimization_cache_key(pa, da, {}, {}) != changed


def test_host_model_configuration_changes_mechanism_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOCOL_CHAT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("PROTOCOL_CHAT_MODEL", "model-a")
    objective = {"mechanism": {"max_llm_calls": 1}}
    first = _optimization_cache_key(tmp_path, tmp_path, objective, {})
    monkeypatch.setenv("PROTOCOL_CHAT_MODEL", "model-b")
    assert _optimization_cache_key(tmp_path, tmp_path, objective, {}) != first


def test_integration_gate_blocks_approval_for_unverified_sample_versions(tmp_path, monkeypatch):
    from protocol_optimization_agent.agents import protocol_optimizing as po
    state = _state()
    dataset = state["analysis_dataset"]
    for phenotype in dataset["phenotypes"]:
        phenotype["metadata"].pop("protocol_snapshot_id")
    monkeypatch.setattr(po, "read_csv_dicts", lambda path: dataset["protocol_rows"] if path.name == "protocol_feature_matrix.csv" else [])
    monkeypatch.setattr(po, "read_jsonl", lambda path: [])
    monkeypatch.setattr(po, "read_json", lambda path: {"phenotypes": dataset["phenotypes"]} if path.name == "phenotype_ir.json" else {})
    result = ProtocolOptimizingAgent(tmp_path / "output")._node_integration_input_gate({
        "protocol_output_dir": str(tmp_path), "data_analysis_output_dir": str(tmp_path), "objective": state["objective"]})
    assert result["endpoint_rows"]
    assert not result["scientific_preconditions"]["scientific_ready"]
    assert result["scientific_preconditions"]["checks"][-1]["name"] == "sample_protocol_version_identity"


def test_retrieval_failure_returns_partial_hypotheses_and_audit():
    candidate, package, _ = _mechanism_inputs()
    config = {"max_literature_queries": 1, "queries": [
        {"feature": "reagent_x", "query": "factor X null findings", "intent": "challenge"}]}
    def failed(query, limit):
        raise OSError("Synthetic network failure")
    result = interpret_mechanisms([candidate], package, config, [], OfflineLLMClient(), failed)
    assert result["budget"]["literature_queries"] == 1
    assert result["errors"][0]["phase"] == "retrieval"
    assert not result["challenge_search_performed"]
    assert result["interpretations"][0]["falsification_conditions"]


def test_model_query_plan_requires_challenge_instead_of_support_only():
    candidate, package, _ = _mechanism_inputs()
    client = StaticMockLLMClient({"mechanism_literature_queries": {"queries": [
        {"query": "factor X benefits", "intent": "support"}]}})
    def forbidden(*args):
        raise AssertionError("Support-only model search must not run")
    result = interpret_mechanisms([candidate], package, {"max_llm_calls": 1, "max_literature_queries": 2}, [], client, forbidden)
    assert result["errors"][0]["phase"] == "query_plan"
    assert result["budget"]["literature_queries"] == 0


def test_historical_opposition_survives_persistence_and_regeneration(tmp_path):
    store = ScientificWorldModel(RunContext.from_values(user_id="tester", run_id="r1", workspace_root=tmp_path), "study")
    edit = {"candidate_feature": "reagent_x", "evidence_refs": [{"source": "synthetic"}],
            "opposing_evidence_refs": [{"source": "earlier_null_result"}]}
    first = design_experiments([edit], {}, {"scientific_ready": False})
    store.put([Hypothesis.model_validate(first["hypotheses"][0])], "S4")
    edit.pop("opposing_evidence_refs")
    second = design_experiments([edit], {}, {"scientific_ready": False}, store.reasoning_context()["hypotheses"])
    assert second["hypotheses"][0]["opposing_evidence_refs"] == [{"source": "earlier_null_result"}]
    assert second["ranking_status"] == "unranked_hypothesis_set"
