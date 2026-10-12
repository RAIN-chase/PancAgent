from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.actions import get_action
from protocol_optimization_agent.agents.analysis_flow import (
    AnalysisPlan,
    initialize_budget,
    inspect_result,
    step_signature,
)
from protocol_optimization_agent.science.reporting import build_final_report


class ProposalModel(FakeMessagesListChatModel):
    def bind_tools(self, tools):
        return self


def message(value: dict) -> AIMessage:
    return AIMessage(
        content=json.dumps(value),
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        response_metadata={"cost": 0.0},
    )


def plan_step(action_id="protocol.lookup", inputs=None, step_id="s1", dependencies=None) -> dict:
    return {
        "id": step_id,
        "action_id": action_id,
        "inputs": inputs if inputs is not None else {"question": "Protocol A"},
        "depends_on": dependencies or [],
        "expected_outputs": get_action(action_id).outputs,
    }


def inputs(tmp_path: Path) -> dict:
    pa = tmp_path / "pa"
    pa.mkdir()
    (pa / "protocol_feature_matrix.csv").write_text(
        "protocol_id,paper_id,protocol_name,dose\np1,study1,Protocol A,0.125\n"
    )
    (pa / "normalized_factor_events.jsonl").write_text("")
    return {
        "messages": [HumanMessage(content="Protocol A")],
        "protocol_output_dir": str(pa),
        "data_analysis_output_dir": str(tmp_path / "da"),
        "optimization_output_dir": str(tmp_path / "po"),
        "workspace_root": str(tmp_path),
        "user_id": "test",
        "run_id": "flow",
    }


def graph(monkeypatch):
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    return orchestrator.build_orchestrator_graph()


def test_flow_graph_and_validated_dependency_contract(monkeypatch):
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    assert set(orchestrator.build_orchestrator_graph(compile_graph=False).nodes) == {
        "configure",
        "plan",
        "execute",
        "review",
        "finalize",
    }
    with pytest.raises(ValueError, match="dependencies"):
        AnalysisPlan.model_validate({"question": "test", "steps": [plan_step(dependencies=["s2"])]})
    with pytest.raises(ValueError, match="Undeclared"):
        AnalysisPlan.model_validate(
            {"question": "test", "steps": [{**plan_step(), "expected_outputs": ["invented_output"]}]}
        )
    assert step_signature(plan_step(), "input1", "code1") != step_signature(plan_step(), "input2", "code1")
    with pytest.raises(ValueError):
        initialize_budget({"max_tool_calls": 999})


def test_duplicate_call_reuses_result_without_second_execution(tmp_path, monkeypatch):
    state = inputs(tmp_path)
    state["request"] = {"plan": {"question": "Protocol A", "steps": [plan_step(), plan_step(step_id="s2")]}}
    result = graph(monkeypatch).invoke(state)
    assert result["stop_reason"] == "completed"
    assert len(result["step_results"]) == 2
    assert result["step_results"][1]["status"] == "skipped_duplicate"
    assert not result["step_results"][1]["executed"]
    assert not {"analysis_plan", "iteration_count", "evidence_gaps", "final_report", "active_step",
                "call_signatures", "evidence_signatures", "requested_plan"}.intersection(result)
    assert "plan" not in result["request"]
    assert sum(step["executed"] for step in result["step_results"]) == 1
    assert result["messages"][-1].additional_kwargs["analysis_report"]["stop_reason"] == result["stop_reason"]


@pytest.mark.parametrize("action_id", ["protocol.extract", "phenotype.reference_similarity"])
def test_unimplemented_actions_are_not_executed(tmp_path, monkeypatch, action_id):
    state = inputs(tmp_path)
    required = {key: "dummy" for key in get_action(action_id).required_inputs}
    if "h5ad_manifest_rows" in required:
        required["h5ad_manifest_rows"] = []
    state["request"] = {"plan": {"question": "test", "steps": [plan_step(action_id, required)]}}
    result = graph(monkeypatch).invoke(state)
    assert result["stop_reason"] == "evidence_insufficient:invalid_plan"
    assert result["step_results"] == []
    assert len(result["step_results"]) == 0


@pytest.mark.parametrize(
    "budget,reason",
    [
        ({"max_tool_calls": 0}, "budget_exhausted:tool_calls"),
        ({"max_seconds": 0}, "budget_exhausted:time"),
        ({"max_iterations": 0}, "budget_exhausted:iterations"),
    ],
)
def test_budget_stop_produces_saved_partial_report(tmp_path, monkeypatch, budget, reason):
    state = inputs(tmp_path)
    state["budget"] = budget
    result = graph(monkeypatch).invoke(state)
    assert result["stop_reason"] == reason
    root = tmp_path / "users/test/runs/flow"
    assert json.loads((root / "run_manifest.json").read_text())["status"] == "partial"
    assert (root / "final_report.json").exists()
    assert (root / "final_report.md").exists()


def test_missing_evidence_stops_and_failed_dependencies_do_not_execute(tmp_path, monkeypatch):
    state = inputs(tmp_path)
    state["request"] = {"plan": {
        "question": "INS",
        "steps": [
            plan_step("phenotype.gene_expression", {"question": "INS expression", "request": {"genes": ["INS"]}}),
            plan_step(step_id="s2", dependencies=["s1"]),
        ],
    }}
    result = graph(monkeypatch).invoke(state)
    assert result["stop_reason"].startswith("evidence_insufficient")
    assert result["plan"]["evidence_gaps"]
    assert len(result["step_results"]) == 2
    assert result["step_results"][1]["status"] == "blocked_dependency"
    assert not result["step_results"][1]["executed"]


def test_model_revision_and_repeated_evidence_are_bounded(tmp_path):
    state = inputs(tmp_path)
    # Different question text is not new evidence when the same protocol result returns.
    model = ProposalModel(
        responses=[
            message({"question": "Protocol A", "steps": [plan_step()]}),
            message(
                {
                    "alternative_explanations": ["unknown_mechanism"],
                    "next_steps": [plan_step(inputs={"question": "find Protocol A"}, step_id="s2")],
                }
            ),
        ]
    )
    result = orchestrator.build_orchestrator_graph(model).invoke(state)
    assert result["stop_reason"] == "completed"
    assert len(result["step_results"]) == 2
    assert not result["step_results"][1]["new_evidence"]
    assert result["plan"]["alternative_explanations"] == ["unknown_mechanism"]


def test_report_numbers_are_bound_to_archived_json_and_single_study_marked(tmp_path, monkeypatch):
    state = inputs(tmp_path)
    result = graph(monkeypatch).invoke(state)
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    root = tmp_path / "users/test/runs/flow"
    assert report == json.loads((root / "final_report.json").read_text(encoding="utf-8"))
    assert "single_study_driven:study1" in report["confidence_caveats"]
    assert report["quantitative_bindings"]
    for binding in report["quantitative_bindings"]:
        value = json.loads((root / binding["path"]).read_text(encoding="utf-8"))
        for key in binding["field_path"].split("/")[1:]:
            key = key.replace("~1", "/").replace("~0", "~")
            value = value[int(key)] if isinstance(value, list) else value[key]
        assert "value" not in binding
        if binding in report["display_quantitative_bindings"]:
            assert str(value) in result["messages"][-1].content
        assert binding["input_version"] and binding["parameters"] and binding["tool_version"]
    assert all("saved_result_view" not in item for section in report["sections"].values() for item in section)


def test_removed_tool_proposal_is_rejected_without_execution(tmp_path):
    model = ProposalModel(responses=[AIMessage(content="", tool_calls=[{
        "name": "protocol_analysis_agent", "args": {"question": "Protocol A"}, "id": "legacy"}],
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})])
    result = orchestrator.build_orchestrator_graph(model).invoke(inputs(tmp_path))
    assert result["stop_reason"] == "evidence_insufficient:invalid_plan"
    assert result["step_results"] == []


def test_model_budget_and_unknown_usage_stop_without_recursion_error(tmp_path):
    state = inputs(tmp_path)
    state["budget"] = {"max_model_calls": 0}
    result = orchestrator.build_orchestrator_graph(ProposalModel(responses=[message({})])).invoke(state)
    assert result["stop_reason"] == "budget_exhausted:model_calls"
    assert result["step_results"] == []


def test_model_fabricated_numbers_do_not_enter_final_report(tmp_path):
    state = inputs(tmp_path)
    model = ProposalModel(
        responses=[
            message({"question": "Protocol A", "steps": [plan_step()]}),
            message({"invented_measurement": 123456789}),
        ]
    )
    result = orchestrator.build_orchestrator_graph(model).invoke(state)
    assert result["stop_reason"] == "analysis_error:invalid_review"
    assert "123456789" not in json.dumps(result["messages"][-1].additional_kwargs["analysis_report"])


@pytest.mark.parametrize(
    "budget,reason", [({"max_model_tokens": 0}, "model_tokens"), ({"max_model_cost": 0}, "model_cost")]
)
def test_model_token_and_cost_zero_budgets_prevent_invocation(tmp_path, budget, reason):
    state = inputs(tmp_path)
    state["budget"] = budget
    result = orchestrator.build_orchestrator_graph(ProposalModel(responses=[message({})])).invoke(state)
    assert result["stop_reason"] == "budget_exhausted:" + reason
    assert result["budget"]["model_calls"] == 0


def test_missing_usage_stops_review_and_preserves_tool_result(tmp_path):
    state = inputs(tmp_path)
    proposal = AIMessage(content=json.dumps({"question": "Protocol A", "steps": [plan_step()]}))
    result = orchestrator.build_orchestrator_graph(ProposalModel(responses=[proposal])).invoke(state)
    assert result["stop_reason"] == "budget_exhausted:model_usage_unknown"
    assert len(result["step_results"]) == 1
    assert result["messages"][-1].additional_kwargs["analysis_report"]["quantitative_bindings"]


def test_success_label_cannot_replace_missing_expected_outputs():
    usable, gaps, _ = inspect_result({"status": "ok"}, "protocol.compare", get_action("protocol.compare").outputs)
    assert not usable and "expected_output_missing:feature_diff" in gaps


def test_report_rejects_tampered_saved_json(tmp_path, monkeypatch):
    result = graph(monkeypatch).invoke(inputs(tmp_path))
    root = tmp_path / "users/test/runs/flow"
    saved = root / result["step_results"][0]["result_ref"]["path"]
    saved.write_text('{"status":"ok","invented_number":123456789}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        build_final_report(result, root)
