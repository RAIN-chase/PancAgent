import importlib.util
import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.optimization.mechanisms import audit_literature_scope, interpret_mechanisms
from protocol_optimization_agent.analysis.protocol.coverage_ledger import information_units
from protocol_optimization_agent.analysis.protocol.input_loader import load_protocol_analysis_inputs
from protocol_optimization_agent.runtime.archive import file_hash
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.runtime.llm_client import OfflineLLMClient
from protocol_optimization_agent.science.memory import method_design
from protocol_optimization_agent.science.models import MethodExperience
from protocol_optimization_agent.science.reporting import result_dimensions
from protocol_optimization_agent.science.state import ScientificWorldModel


def evaluation_module():
    path = Path(__file__).parents[2] / "evals/research_tasks/evaluate.py"
    spec = importlib.util.spec_from_file_location("research_eval", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_input_ledger_accounts_for_filtered_and_unparsed_original_fields(tmp_path):
    record = {"protocol_id": "p", "paper_id": "paper", "comparison_ready": True, "confidence_level": "high",
        "invented_domain_input": "Retain this unknown field without claiming parsing", "stages": [{"stage_id": "s", "stage_index": 1,
            "day_start": 0, "day_end": 2, "duration_days": 2, "evidence_quote": "Reported stage",
            "key_operations": "Change medium only when morphology is ready", "trigger_condition": "morphology ready",
            "reagents": [{"original_name": "Activin A", "concentration": "10-20", "parse_status": "unparsed", "unit": "ng/ml", "evidence_quote": "10-20 ng/ml Activin A"}]}]}
    (tmp_path / "protocols.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    loaded = load_protocol_analysis_inputs(tmp_path)
    outputs = ProtocolAnalysisAgent(tmp_path / "out", {}).run(loaded["protocol_records"], loaded["factor_events"],
        loaded["source_identity_rows"], raw_protocol_records=loaded["raw_protocol_records"])
    raw = json.loads(outputs["raw_protocol_input"].read_text(encoding="utf-8"))
    assert raw == [record]
    ledger = json.loads(outputs["input_coverage_ledger"].read_text(encoding="utf-8"))
    assert ledger["n_units"] == len(list(information_units(record)))
    by_path = {row["source_pointer"]: row for row in ledger["units"]}
    assert by_path["/invented_domain_input"]["disposition"] == "pending_parse"
    assert by_path["/stages/0/reagents/0/concentration"]["disposition"] == "filtered"
    assert "unparsed_numeric_field" in by_path["/stages/0/reagents/0/concentration"]["reason"]
    assert not ledger["complete_understanding"]
    assert ledger["paper_fulltext_completeness"] == "not_evaluated"
    assert by_path["/stages/0/trigger_condition"]["semantic_validation"] == "not_adjudicated"


def test_execution_success_is_not_numerical_or_scientific_validation():
    step = {"step_id": "s", "action_id": "phenotype.gene_expression", "executed": True, "status": "ok"}
    value = result_dimensions(step, {})
    assert value["execution"] == "succeeded"
    assert value["numerical_correctness"] == "not_independently_checked"
    assert value["scientific_applicability"] == "requires_review"


def test_method_experience_is_scoped_versioned_and_revalidated(tmp_path):
    context = RunContext.from_values(workspace_root=tmp_path, user_id="alice", project_id="p", run_id="r")
    root = context.run_root()
    root.mkdir(parents=True)
    source = root / "result.json"
    source.write_text("{}")
    store = ScientificWorldModel(context, "p")
    store.put([MethodExperience(id="method", action_id="data.exploratory_sensitivity", tool_version="v1", code_version="c1",
        outcome="failed", prerequisites={"scale": "module_score", "replicate_column": "rep"}, validation={},
        failure_reasons=["missing_replicates"], source_refs=[{"path": str(source), "sha256": file_hash(source)}],
        originating_run_id="r")], "S0")
    requirements = {"actions": {"data.exploratory_sensitivity": {"tool_version": "v1", "design": {"scale": "module_score"}}}, "code_version": "c1"}
    record = store.reasoning_context(method_requirements=requirements)["method_experiences"][0]
    assert record["outcome"] == "failed" and not record["reuse_assessment"]["biological_fact"]
    assert record["reuse_assessment"]["unverified_prerequisites"] == ["replicate_column"]
    assert ScientificWorldModel(RunContext.from_values(workspace_root=tmp_path, user_id="bob", project_id="p"), "p").reasoning_context(method_requirements=requirements)["method_experiences"] == []
    requirements["actions"]["data.exploratory_sensitivity"]["design"]["scale"] = "counts"
    assert store.reasoning_context(method_requirements=requirements)["method_experiences"] == []
    requirements["actions"]["data.exploratory_sensitivity"]["design"]["scale"] = "module_score"
    source.write_text('{"changed":true}')
    assert not store.reasoning_context(method_requirements=requirements)["method_experiences"][0]["reuse_assessment"]["source_snapshot_intact"]
    assert "candidate_code" not in method_design({"candidate_code": "secret", "scale": "counts"})


def test_scope_mismatch_is_not_fulltext_mechanism_validation():
    source = {"endpoint": "maturity", "applicability": {"cell_type": "alpha", "stage": "late"}}
    value = audit_literature_scope(source, [{"endpoint": "maturity", "context": {"cell_type": "beta", "stage": "early"}}])
    assert value["status"] == "mismatched"
    assert value["checks"]["dose"] == "unknown"
    assert value["quote_entailment"] == "not_independently_verified"


def test_scope_mismatched_counterevidence_is_retained_without_direct_refutation():
    result = interpret_mechanisms(
        [{"feature": "X", "endpoint_associations": [{"target_endpoint": "maturity", "points": [{"protocol_id": "p"}]}]}],
        {"id": "package", "limitations": [], "observations": [{"protocol_id": "p", "endpoint": "maturity", "context": {"cell_type": "beta"}}]},
        {}, [{"feature": "X", "endpoint": "maturity", "pmid": "123", "quote": "Observed no effect in alpha cells.",
              "stance": "opposes", "applicability": {"cell_type": "alpha"}}], OfflineLLMClient())
    item = result["interpretations"][0]
    assert not item["opposing_evidence_refs"]
    assert item["scope_mismatched_refs"][0]["stance"] == "opposes"
    assert item["scope_mismatched_refs"][0]["source_level"] == "provided_excerpt"
    assert item["distinguishability_status"] == "not_established"


def test_pilot_splits_and_no_data_controls_do_not_leak_answers():
    module = evaluation_module()
    suite = json.loads((Path(__file__).parents[2] / "evals/research_tasks/cases.json").read_text(encoding="utf-8"))
    assert module.validate_cases(suite)["n_cases"] == 24
    suite["cases"][-1]["split"] = "test"
    suite["cases"][-1]["study_ids"] = ["nair_2019"]
    with pytest.raises(ValueError, match="leakage"):
        module.validate_cases(suite)


def test_evaluation_preserves_unknown_cost_and_checks_saved_numbers(tmp_path):
    module = evaluation_module()
    suite = {"cases": [{"id": "q", "split": "test", "study_ids": ["heldout"], "input_mode": "versioned_data", "question": "Mean?"}]}
    source = tmp_path / "result.json"
    source.write_text('{"mean":4}')
    prediction = {"case_id": "q", "baseline": "full_agent", "repetition": 0, "source_study_ids": ["heldout"],
        "input_sha256": "a" * 64, "config_sha256": "b" * 64, "prompt_sha256": "c" * 64,
        "execution_status": "succeeded", "decision": "compute", "cost_usd": None,
        "results": [{"path": "result.json", "sha256": file_hash(source)}],
        "quantities": [{"key": "mean", "result_index": 0, "field_path": "/mean"}]}
    report = module.evaluate(suite, [prediction], {"q": {"expected_numbers": {"mean": 4}, "expected_decision": "compute"}}, tmp_path)
    assert report["results"][0]["numerical_correctness"]
    assert report["results"][0]["cost_usd"] is None
    assert report["results"][0]["source_entailment"] is None
    assert not report["comparisons"][0]["all_baselines_present"]
    assert not report["comparisons"][0]["same_frozen_inputs_and_budget"]
    source.write_text('{"mean":5}')
    assert not module.evaluate(suite, [prediction], {"q": {"expected_numbers": {"mean": 4}}}, tmp_path)["results"][0]["numerical_correctness"]
    suite["cases"][0]["input_mode"] = "no_data"
    with pytest.raises(ValueError, match="No-data"):
        module.evaluate(suite, [prediction], {}, tmp_path)


def test_module_score_independent_analytic_fixture():
    import anndata
    import numpy as np
    import scanpy

    data = anndata.AnnData(np.array([[2., 4., 1., 1.], [5., 7., 2., 2.]]))
    data.var_names = ["target1", "target2", "control1", "control2"]
    scanpy.tl.score_genes(data, ["target1", "target2"], ctrl_size=4, n_bins=2, random_state=0, use_raw=False, ctrl_as_ref=True)
    # Independent analytical expectation: target mean minus two identical control profiles.
    np.testing.assert_allclose(data.obs["score"].to_numpy(), [2., 4.], rtol=0, atol=1e-7)
