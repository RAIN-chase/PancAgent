from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.actions import get_action
from protocol_optimization_agent.agents.analysis_flow import AnalysisPlan, ScientificConclusion, resolve_step_inputs
from protocol_optimization_agent.analysis.phenotype.endpoint_comparison import compare_endpoint
from protocol_optimization_agent.runtime.archive import atomic_json, file_hash
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.science.models import Hypothesis
from protocol_optimization_agent.science.reporting import (
    assess_question,
    build_final_report,
    ground_conclusions,
    pointer_value,
    render_report,
)
from protocol_optimization_agent.science.state import ScientificWorldModel


def step(action="protocol.lookup", arguments=None, name="s1", **extra):
    return {"id": name, "action_id": action, "inputs": arguments or {},
            "expected_outputs": get_action(action).outputs, **extra}


def message(value):
    return AIMessage(content=json.dumps(value), usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})


def state(root, question="Protocol A", steps=None, **plan):
    return {"workspace_root": str(root), "user_id": "test", "run_id": "goals",
            "protocol_output_dir": str(root / "pa"), "data_analysis_output_dir": str(root / "da"),
            "optimization_output_dir": str(root / "po"),
            "messages": [HumanMessage(content=question)],
            "request": {"plan": {"question": question, "steps": steps or [step(arguments={"question": "Protocol A"})], **plan}}}


def lookup():
    return {"status": "ok", "evidence_refs": [], "result_preview": {
        "intent": "find_protocols", "results": [{"protocol_id": "p1", "protocol_name": "Protocol A"}]}}


def execute(monkeypatch, results):
    calls = []
    def invoke(action_id, arguments, context, **kwargs):
        calls.append((action_id, arguments))
        return results[len(calls) - 1]
    monkeypatch.setattr(orchestrator, "execute_action", invoke)
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    return orchestrator.build_orchestrator_graph(), calls


def saved(root, value, action="protocol.lookup", name="r1"):
    atomic_json(root / (name + ".json"), value)
    return {"step_id": name, "action_id": action, "tool_name": get_action(action).implementation,
            "executed": True, "status": "ok", "usable": True,
            "result_ref": {"result_id": name, "path": name + ".json", "sha256": file_hash(root / (name + ".json")),
                           "input_version": "inputs", "parameters": {}, "tool_version": "version"}}


def claim(source_id="r1", level="descriptive", **extra):
    return {"claim": "Evidence supports a limited comparison.", "explanation": "Batch effects remain an alternative explanation.",
            "evidence_level": level, "supporting_refs": [{"source_type": "result", "source_id": source_id, "field_path": "/result_preview"}],
            "opposing_refs": [], "limitations": ["Not a causal inference."], "objects": [], "metrics": [], **extra}


def test_successful_lookup_does_not_answer_which_protocol_is_better(tmp_path, monkeypatch):
    graph, calls = execute(monkeypatch, [lookup()])
    result = graph.invoke(state(tmp_path, "两种 protocols 哪个更好", goal_type="lookup"))
    assert len(calls) == 1 and result["step_results"][0]["usable"]
    assert result["stop_reason"] == "evidence_insufficient"
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    assert report["acceptance"]["goal_type"] == "endpoint_comparison"
    assert "evaluation_endpoint_unspecified" in report["evidence_gaps"]
    assert report["clarification_questions"]
    assert report["conclusions"][0]["evidence_level"] == "undetermined"


def test_objects_endpoints_and_unresolved_subquestions_are_acceptance_conditions(tmp_path):
    record = saved(tmp_path, {"status": "ok", "validation": {"task_validity": {"gene_expression": {"valid": True}}},
                            "result_preview": [{"protocol_id": "p1", "gene": "GCG", "mean_expression": "0.75", "status": "ok"}]},
                   "phenotype.gene_expression")
    plan = {"goal_type": "endpoint_analysis", "objects": ["p2"], "target_metrics": ["INS"]}
    acceptance = assess_question(plan, [record], tmp_path)
    assert not acceptance["answered"]
    assert acceptance["missing_objects"] == ["p2"] and acceptance["missing_metrics"] == ["INS"]
    plan.update(objects=["p1"], target_metrics=["mean_expression"])
    assert assess_question(plan, [record], tmp_path)["answered"]
    failed = {"step_id": "s2", "status": "analysis_failed", "usable": False}
    assert assess_question(plan, [record, failed], tmp_path)["incomplete_steps"] == [{"step_id": "s2", "status": "analysis_failed"}]


def test_result_field_binding_resolves_and_archives_dependency(tmp_path, monkeypatch):
    steps = [step(arguments={"question": "Protocol A"}), step("protocol.compare", {"protocol_id_b": "p2"}, "s2",
        input_bindings=[{"source_step": "s1", "field_path": "/result_preview/results/0/protocol_id", "input_path": "/protocol_id_a"}])]
    comparison = {"status": "ok", "evidence_refs": [], "scientific_preconditions": {}, "result_preview": {
        "protocol_id_a": "p1", "protocol_id_b": "p2", "tool_result": {"feature_differences": [], "factor_diff": [],
            "stage_diff": [], "event_step_diff": {"detail_status": "available"}}}}
    graph, calls = execute(monkeypatch, [lookup(), comparison])
    result = graph.invoke(state(tmp_path, "比较两个 protocol 的差异", steps, objects=["p1", "p2"]))
    assert result["stop_reason"] == "completed"
    assert result["plan"]["steps"][1]["depends_on"] == ["s1"]
    assert calls[1][1] == {"protocol_id_a": "p1", "protocol_id_b": "p2"}
    binding = result["step_results"][1]["input_bindings"][0]
    assert binding["result_ref"]["sha256"] == result["step_results"][0]["result_ref"]["sha256"]
    assert result["step_results"][1]["result_ref"]["parameters"] == calls[1][1]


@pytest.mark.parametrize("pointer", ["/result_preview/results/-1/protocol_id", "/result_preview/results/8/protocol_id", "/missing"])
def test_binding_rejects_invalid_fields_and_never_overrides_explicit_parameters(tmp_path, pointer):
    source = saved(tmp_path, lookup())
    source["step_id"] = "s1"
    target = step("protocol.compare", {"protocol_id_b": "p2"}, "s2", input_bindings=[{
        "source_step": "s1", "field_path": pointer, "input_path": "/protocol_id_a"}])
    with pytest.raises((ValueError, KeyError, IndexError)):
        resolve_step_inputs(target, [source], tmp_path)
    target["input_bindings"][0]["field_path"] = "/result_preview/results/0/protocol_id"
    target["inputs"]["protocol_id_a"] = "p3"
    with pytest.raises(ValueError, match="override"):
        resolve_step_inputs(target, [source], tmp_path)


def test_duplicate_and_dependency_failure_do_not_block_independent_steps(tmp_path, monkeypatch):
    steps = [step(arguments={"question": "A"}), step(arguments={"question": "A"}, name="s2"),
             step("protocol.compare", name="s3"), step(arguments={"question": "B"}, name="s4", depends_on=["s3"]),
             step(arguments={"question": "C"}, name="s5")]
    graph, calls = execute(monkeypatch, [lookup(), lookup()])
    result = graph.invoke(state(tmp_path, steps=steps))
    assert len(calls) == 2
    assert [record["status"] for record in result["step_results"]] == ["ok", "skipped_duplicate", "needs_clarification", "blocked_dependency", "ok"]
    assert sum(record["executed"] for record in result["step_results"]) == 2
    assert result["stop_reason"] == "evidence_insufficient"


def test_one_format_repair_is_shared_and_budgeted(tmp_path, monkeypatch):
    graph, calls = execute(monkeypatch, [lookup()])
    model = FakeMessagesListChatModel(responses=[message({"invented": "invalid"}),
        message({"question": "Protocol A", "steps": [step(arguments={"question": "A"})]}), message({})])
    initial = state(tmp_path)
    initial.pop("request")
    result = orchestrator.build_orchestrator_graph(model).invoke(initial)
    assert result["stop_reason"] == "completed" and len(calls) == 1
    assert result["budget"]["format_repairs"] == 1 and result["budget"]["model_calls"] == 3


def test_conclusions_and_historical_counterevidence_are_source_bound(tmp_path):
    world = ScientificWorldModel(RunContext.from_values(workspace_root=tmp_path, user_id="test", run_id="history"))
    world.put([Hypothesis(id="h-ins", statement="INS endpoint depends on stage", feature="INS", objective="maturity",
        opposing_evidence_refs=[{"doi": "source-refuting-stage"}]),
        Hypothesis(id="h-other", statement="Unrelated GCG endpoint", feature="GCG", objective="other")], "S4")
    history = world.reasoning_context("INS", limit=8)
    assert [item["id"] for item in history["hypotheses"]] == ["h-ins"]
    assert history["hypotheses"][0]["version"] == 1
    atomic_json(tmp_path / "history_context.json", history)
    record = saved(tmp_path, {"result_preview": [{"gene": "INS", "mean_expression": 0.5}]}, "phenotype.gene_expression")
    grounded = ground_conclusions([claim(level="exploratory")], [record], history, {"answered": True}, tmp_path)
    assert grounded[0]["opposing_refs"][0]["source_id"] == "h-ins"
    assert grounded[0]["opposing_refs"][0]["version"] == 1
    assert grounded[0]["supporting_refs"][0]["result_ref"]["sha256"] == record["result_ref"]["sha256"]
    assert "unresolved" in " ".join(grounded[0]["limitations"])
    with pytest.raises(ValueError, match="unknown result"):
        ground_conclusions([claim(source_id="invented")], [record], history, {"answered": True}, tmp_path)
    with pytest.raises(ValueError, match="Quantitative"):
        ScientificConclusion.model_validate(claim(claim="Mean expression is 99.5"))


def test_review_conclusions_are_persisted_and_rendered(tmp_path, monkeypatch):
    graph, calls = execute(monkeypatch, [lookup()])
    original = orchestrator._validated_model_proposal
    def proposal(model, current, phase, validator):
        if phase == "review":
            ref = current["step_results"][0]["result_ref"]
            return validator({"conclusions": [claim(source_id=ref["result_id"], level="fact",
                claim="Protocol A is present in the saved source.", objects=["p1"])]}), {}
        return original(model, current, phase, validator)
    monkeypatch.setattr(orchestrator, "_validated_model_proposal", proposal)
    result = orchestrator.build_orchestrator_graph(FakeMessagesListChatModel(responses=[message({})])).invoke(state(tmp_path))
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    assert report["conclusions"] == result["plan"]["conclusions"]
    assert "Protocol A is present" in result["messages"][-1].content
    assert json.loads((tmp_path / "users/test/runs/goals/final_report.json").read_text(encoding="utf-8"))["conclusions"] == report["conclusions"]


def test_requested_gene_is_selected_beyond_first_rows_and_complete_result_is_retained(tmp_path):
    rows = [{"gene": "GCG", "mean_expression": str(index), "status": "ok"} for index in range(30)]
    rows.append({"gene": "INS", "mean_expression": "123.75", "status": "ok"})
    record = saved(tmp_path, {"result_preview": rows}, "phenotype.gene_expression")
    report = build_final_report({"run_id": "test", "trace_id": "trace", "stop_reason": "completed", "budget": {},
        "step_results": [record], "plan": {"question": "INS expression", "goal_type": "endpoint_analysis",
                                           "target_metrics": ["INS"], "objects": []}}, tmp_path)
    selected = report["display_quantitative_bindings"]
    assert selected[0]["field_path"] == "/result_preview/30/mean_expression"
    assert len(report["quantitative_bindings"]) == 31 and len(selected) == 25
    assert pointer_value(json.loads((tmp_path / "r1.json").read_text()), selected[0]["field_path"]) == "123.75"
    assert "123.75" in render_report(report, tmp_path)
    assert "31 numeric references retained" in render_report(report, tmp_path)
    assert report["complete_results"]["report_json"] == str(tmp_path / "final_report.json")


@pytest.mark.parametrize("injected", [{"acceptance": {"answered": True}}, {"conclusions": [{"claim": "invented"}]},
                                     {"conclusion_refs": ["invented"]}])
def test_client_cannot_inject_acceptance_or_conclusions(tmp_path, monkeypatch, injected):
    graph, calls = execute(monkeypatch, [lookup()])
    result = graph.invoke(state(tmp_path, **injected))
    assert result["stop_reason"] == "evidence_insufficient:invalid_plan" and not calls


def test_unknown_dependencies_are_rejected():
    with pytest.raises(ValueError, match="dependencies"):
        AnalysisPlan.model_validate({"question": "query", "steps": [step(input_bindings=[{
            "source_step": "missing", "field_path": "/value", "input_path": "/question"}])]})


def test_bound_input_does_not_hide_invalid_literal_parameter_types():
    with pytest.raises(ValueError, match="must be an object"):
        AnalysisPlan.model_validate({"question": "query", "steps": [step(arguments={"question": "A"}),
            step("phenotype.gene_expression", {"request": "invalid"}, "s2", input_bindings=[{
                "source_step": "s1", "field_path": "/genes", "input_path": "/request/genes"}])]})


def test_named_endpoint_acceptance_uses_real_comparability_checks(tmp_path):
    phenotypes = [{"id": "ph-" + protocol, "protocol_id": protocol, "sample_id": "sample-" + protocol,
        "metrics": [{"id": "metric-" + protocol, "endpoint": "beta", "value": value, "status": "ok", "unit": "fraction",
            "method": "annotated_fraction", "context": {"method_version": "v1", "cell_population": "all",
                "sampling_condition": "baseline", "cell_line": "shared", "stage": "terminal"}}]}
        for protocol, value in [("p1", 0.4), ("p2", 0.5)]]
    comparison = compare_endpoint({"phenotypes": phenotypes}, ["p1", "p2"], "beta")
    record = saved(tmp_path, {"status": "ok", "result_preview": comparison}, "protocol.phenotype_compare")
    plan = {"goal_type": "endpoint_comparison", "objects": ["p1", "p2"], "target_metrics": ["beta"]}
    acceptance = assess_question(plan, [record], tmp_path)
    assert acceptance["answered"] and comparison["ranking"] is None
    assert acceptance["comparability"] == "descriptive_endpoint_checks_passed"
    phenotypes[1]["metrics"][0]["context"].pop("stage")
    rejected = compare_endpoint({"phenotypes": phenotypes}, ["p1", "p2"], "beta")
    assert rejected["status"] == "partial" and rejected["cohorts"] == []
    failed = saved(tmp_path, {"result_preview": rejected}, "protocol.phenotype_compare", "r2")
    failed["usable"] = False
    assert not assess_question(plan, [failed], tmp_path)["answered"]


def test_planning_automatically_receives_versioned_relevant_history(tmp_path, monkeypatch):
    world = ScientificWorldModel(RunContext.from_values(workspace_root=tmp_path, user_id="test", run_id="history"))
    world.put([Hypothesis(id="h-current", statement="Protocol A has a competing mechanism", feature="Protocol A", objective="beta",
                         opposing_evidence_refs=[{"doi": "counterevidence"}])], "S4")
    graph, _ = execute(monkeypatch, [lookup()])
    result = graph.invoke(state(tmp_path))
    assert result["request"]["history_context"]["hypotheses"][0]["id"] == "h-current"
    assert result["request"]["history_context"]["hypotheses"][0]["opposing_evidence_refs"]
    snapshot = json.loads((tmp_path / "users/test/runs/goals/history_context.json").read_text(encoding="utf-8"))
    assert snapshot == result["request"]["history_context"] and snapshot["hypotheses"][0]["version"] == 1


def test_exploratory_evidence_cannot_be_promoted_to_facts(tmp_path):
    record = saved(tmp_path, {"result_preview": [{"gene": "INS", "mean_diff": 0.5}]}, "phenotype.differential_expression")
    with pytest.raises(ValueError, match="promoted to facts"):
        ground_conclusions([claim(level="fact")], [record], {}, {"answered": True}, tmp_path)


def test_claim_scope_must_match_cited_field_not_unrelated_rows(tmp_path):
    record = saved(tmp_path, {"result_preview": [{"gene": "GCG", "mean_expression": 0.5},
                                               {"gene": "INS", "mean_expression": 0.8}]}, "phenotype.gene_expression")
    proposal = claim(objects=["INS"], supporting_refs=[{
        "source_type": "result", "source_id": "r1", "field_path": "/result_preview/0/mean_expression"}])
    with pytest.raises(ValueError, match="objects are not supported"):
        ground_conclusions([proposal], [record], {}, {"answered": True}, tmp_path)


def test_repair_respects_model_budget_without_executing_invalid_plan(tmp_path, monkeypatch):
    graph, calls = execute(monkeypatch, [lookup()])
    initial = state(tmp_path)
    initial.pop("request")
    initial["budget"] = {"max_model_calls": 1}
    model = FakeMessagesListChatModel(responses=[message({"invented": "bad"})])
    result = orchestrator.build_orchestrator_graph(model).invoke(initial)
    assert result["stop_reason"] == "budget_exhausted:model_calls" and not calls
    assert result["messages"][-1].additional_kwargs["analysis_report"]["acceptance"]["status"] == "undetermined"


def test_budget_stop_marks_unexecuted_subquestion_unresolved(tmp_path, monkeypatch):
    graph, calls = execute(monkeypatch, [lookup()])
    initial = state(tmp_path, steps=[step(arguments={"question": "A"}), step(arguments={"question": "B"}, name="s2")])
    initial["budget"] = {"max_tool_calls": 1}
    result = graph.invoke(initial)
    assert len(calls) == 1 and result["stop_reason"] == "budget_exhausted:tool_calls"
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    assert not report["acceptance"]["answered"]
    assert {"step_id": "s2", "status": "not_executed"} in report["acceptance"]["incomplete_steps"]
