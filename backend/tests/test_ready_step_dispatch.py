import json
from threading import Barrier

import pytest
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.orchestrator import _ready_steps


def test_ready_steps_skip_blocked_dependencies_and_keep_plan_order():
    plan = {"steps": [
        {"id": "failed", "depends_on": []},
        {"id": "blocked", "depends_on": ["failed"]},
        {"id": "independent_a", "depends_on": []},
        {"id": "independent_b", "depends_on": []},
        {"id": "downstream", "depends_on": ["independent_a"]},
    ]}
    results = [{"step_id": "failed", "usable": False}]
    assert [step["id"] for step in _ready_steps(plan, results)] == ["independent_a", "independent_b"]
    results.append({"step_id": "independent_a", "usable": True})
    assert [step["id"] for step in _ready_steps(plan, results)] == ["independent_b", "downstream"]


def test_parallel_eligibility_is_declared_by_action_registry():
    from protocol_optimization_agent.agents.actions import action_registry

    actions = {row["id"]: row for row in action_registry()["actions"]}
    assert {key for key, row in actions.items() if row["parallel_read_only"]} == {
        "data.catalog", "protocol.steps", "protocol.assess", "protocol.phenotype_compare", "scientific.state"}


def test_batch_collects_three_ready_steps_before_review(tmp_path, monkeypatch):
    calls = []
    def execute_one(state):
        step = _ready_steps(state["plan"], state["step_results"])[0]
        calls.append(step["id"])
        return {"step_results": [*state["step_results"], {"step_id": step["id"], "usable": True}],
                "messages": []}
    monkeypatch.setattr(orchestrator, "_execute_node", execute_one)
    state = {"workspace_root": str(tmp_path), "user_id": "test", "run_id": "batch",
             "plan": {"steps": [{"id": str(i), "action_id": "protocol.lookup", "depends_on": []} for i in range(4)]},
             "step_results": [], "stop_reason": "", "budget": {"max_tool_calls": 6, "max_iterations": 6}}
    result = orchestrator._execute_batch_node(state)
    assert calls == ["0", "1", "2"]
    assert result["batch_start"] == 0
    assert [row["step_id"] for row in result["step_results"]] == calls


def test_parallel_dispatch_can_be_disabled_for_evaluation(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOCOL_PARALLEL_READ_ONLY", "0")
    calls = []
    def execute_one(state):
        step = _ready_steps(state["plan"], state["step_results"])[0]
        calls.append(step["id"])
        return {"step_results": [*state["step_results"], {"step_id": step["id"], "usable": True}],
                "messages": []}
    monkeypatch.setattr(orchestrator, "_execute_node", execute_one)
    state = {"workspace_root": str(tmp_path), "user_id": "test", "run_id": "serial",
             "plan": {"steps": [{"id": "a", "action_id": "protocol.steps", "depends_on": []},
                                {"id": "b", "action_id": "protocol.assess", "depends_on": []}]},
             "step_results": [], "stop_reason": "", "budget": {"max_tool_calls": 6, "max_iterations": 6}}
    orchestrator._execute_batch_node(state)
    assert calls == ["a", "b"]


def test_read_only_steps_run_concurrently_and_commit_in_plan_order(tmp_path, monkeypatch):
    started = Barrier(2, timeout=5)
    def execute_one(state, *, step, commit):
        assert not commit
        started.wait()
        record = {"step_id": step["id"], "usable": True, "evidence_gaps": []}
        return {"step_results": [*state["step_results"], record], "messages": []}
    monkeypatch.setattr(orchestrator, "_execute_node", execute_one)
    state = {"workspace_root": str(tmp_path), "user_id": "test", "run_id": "parallel",
             "project_id": "default", "stop_reason": "", "step_results": [],
             "budget": {"max_tool_calls": 6, "max_iterations": 6},
             "plan": {"evidence_gaps": [], "steps": [
                 {"id": "steps", "action_id": "protocol.steps", "depends_on": []},
                 {"id": "assess", "action_id": "protocol.assess", "depends_on": []}]}}
    result = orchestrator._execute_batch_node(state)
    assert [row["step_id"] for row in result["step_results"]] == ["steps", "assess"]
    archive = orchestrator._state_archive(state)
    saved = json.loads((archive.root / "step_results.json").read_text(encoding="utf-8"))
    assert saved == result["step_results"]


def test_graph_archives_concurrent_action_results(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage
    from protocol_optimization_agent.agents.actions import get_action

    started = Barrier(2, timeout=10)
    def execute(action_id, arguments, context, **kwargs):
        started.wait()
        return {"status": "not_found", "result_preview": {}, "evidence_gaps": [action_id]}
    monkeypatch.setattr(orchestrator, "execute_action", execute)
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    steps = [{"id": name, "action_id": action, "inputs": inputs,
              "expected_outputs": get_action(action).outputs}
             for name, action, inputs in (("a", "protocol.steps", {"protocol_id": "missing"}),
                                          ("b", "protocol.assess", {}))]
    result = orchestrator.build_orchestrator_graph().invoke({
        "workspace_root": str(tmp_path), "user_id": "test", "run_id": "concurrent_graph",
        "messages": [HumanMessage(content="Check protocol evidence")],
        "request": {"plan": {"question": "Check protocol evidence", "steps": steps}}})
    assert [row["step_id"] for row in result["step_results"]] == ["a", "b"]
    assert all(row["result_ref"] for row in result["step_results"])


@pytest.mark.parametrize("limit", ["max_tool_calls", "max_iterations"])
def test_parallel_batch_respects_remaining_action_budget(tmp_path, monkeypatch, limit):
    from langchain_core.messages import HumanMessage
    from protocol_optimization_agent.agents.actions import get_action

    calls = []
    def execute(action_id, arguments, context, **kwargs):
        calls.append(action_id)
        return {"status": "not_found", "result_preview": {}}
    monkeypatch.setattr(orchestrator, "execute_action", execute)
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    steps = [{"id": name, "action_id": action, "inputs": inputs,
              "expected_outputs": get_action(action).outputs}
             for name, action, inputs in (("a", "protocol.steps", {"protocol_id": "missing"}),
                                          ("b", "protocol.assess", {}))]
    result = orchestrator.build_orchestrator_graph().invoke({
        "workspace_root": str(tmp_path), "user_id": "test", "run_id": "budget_" + limit,
        "messages": [HumanMessage(content="Check protocol evidence")],
        "budget": {limit: 1},
        "request": {"plan": {"question": "Check protocol evidence", "steps": steps}}})
    assert calls == ["protocol.steps"]
    assert result["stop_reason"] == "budget_exhausted:" + ("tool_calls" if limit == "max_tool_calls" else "iterations")


def test_batch_recovers_persisted_step_without_reexecuting(tmp_path, monkeypatch):
    state = {"workspace_root": str(tmp_path), "user_id": "test", "run_id": "recover",
             "plan": {"evidence_gaps": [], "steps": [{"id": "a", "action_id": "protocol.steps", "depends_on": []}]},
             "step_results": [], "stop_reason": "", "budget": {"max_tool_calls": 1, "max_iterations": 1}}
    orchestrator._state_archive(state).record("analysis_step", {
        "step_id": "a", "action_id": "protocol.steps", "executed": True, "usable": False,
        "evidence_gaps": ["not_found"], "new_evidence": False})
    monkeypatch.setattr(orchestrator, "_execute_node", lambda *args, **kwargs: pytest.fail("reexecuted"))
    result = orchestrator._execute_batch_node(state)
    assert result["step_results"][0]["step_id"] == "a"
    assert result["plan"]["evidence_gaps"] == ["not_found"]


def test_recovered_missing_inputs_preserve_clarification_question(tmp_path, monkeypatch):
    state = {"workspace_root": str(tmp_path), "user_id": "test", "run_id": "recover_missing",
             "plan": {"evidence_gaps": [], "clarification_questions": [],
                      "steps": [{"id": "a", "action_id": "protocol.steps", "depends_on": []}]},
             "step_results": [], "stop_reason": "", "budget": {"max_tool_calls": 1, "max_iterations": 1}}
    orchestrator._state_archive(state).record("analysis_step", {
        "step_id": "a", "action_id": "protocol.steps", "executed": False, "usable": False,
        "status": "needs_clarification", "reason": "protocol_id missing",
        "evidence_gaps": ["needs_clarification:a"], "new_evidence": False})
    monkeypatch.setattr(orchestrator, "_execute_node", lambda *args, **kwargs: pytest.fail("reexecuted"))
    result = orchestrator._execute_batch_node(state)
    assert result["plan"]["clarification_questions"] == [
        "请补充步骤 a 的科研参数或修正结果绑定：protocol_id missing"]


def test_scientific_dispatch_passes_only_the_agents_required_directories(tmp_path, monkeypatch):
    received = []
    def execute(action_id, arguments, context, **kwargs):
        received.append((action_id, kwargs))
        return {"status": "ok"}
    monkeypatch.setattr(orchestrator, "execute_action", execute)
    runtime = {"protocol_output_dir": str(tmp_path / "pa"),
               "data_analysis_output_dir": str(tmp_path / "da"),
               "optimization_output_dir": str(tmp_path / "po"),
               "max_features": 10, "workspace_root": str(tmp_path)}
    for action in ("protocol.steps", "protocol.phenotype_compare", "hypothesis.generate", "data.catalog"):
        orchestrator.execute_scientific_action.invoke({"action_id": action, "arguments": {}, **runtime})
    assert set(received[0][1]) == {"protocol_output_dir", "max_features"}
    assert set(received[1][1]) == {"data_analysis_output_dir"}
    assert set(received[2][1]) == {"protocol_output_dir", "data_analysis_output_dir", "optimization_output_dir"}
    assert received[3][1] == {}


@pytest.mark.parametrize(("action", "arguments", "paths"), [
    ("protocol.steps", {"protocol_id": "p1"}, {}),
    ("protocol.phenotype_compare", {"protocol_ids": ["p1", "p2"], "endpoint": "beta"}, {}),
    ("hypothesis.generate", {"edits": [{"candidate_feature": "x"}], "objective": {"endpoint": "beta"}},
     {"protocol_output_dir": "pa", "data_analysis_output_dir": "da"}),
])
def test_domain_action_rejects_missing_directory(tmp_path, action, arguments, paths):
    from protocol_optimization_agent.agents.actions import execute_action
    from protocol_optimization_agent.runtime.context import RunContext

    context = RunContext.from_values(workspace_root=str(tmp_path))
    with pytest.raises(ValueError, match="requires"):
        execute_action(action, arguments, context, **paths)
