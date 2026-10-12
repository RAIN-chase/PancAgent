from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.analysis_flow import AnalysisPlan
from protocol_optimization_agent.runtime.archive import atomic_json, file_hash
from protocol_optimization_agent.science.reporting import build_final_report, render_report


def inputs(root, text):
    return {"messages": [HumanMessage(content=text)], "workspace_root": str(root), "run_id": "chat",
            "user_id": "test", "protocol_output_dir": str(root / "pa"),
            "data_analysis_output_dir": str(root / "da"), "optimization_output_dir": str(root / "po")}


@pytest.mark.parametrize("text", ["你好", "您好！", "Hello!", "谢谢", "你能做什么？", "帮我分析一下"])
def test_social_and_unspecified_turns_need_no_model_or_tools(tmp_path, monkeypatch, text):
    monkeypatch.setattr(orchestrator, "execute_action", lambda *a, **k: pytest.fail("No scientific action needed"))
    model = RunnableLambda(lambda messages: pytest.fail("No model needed for this turn"))
    # A greeting must not touch large data or reject it while seeding an analysis workspace.
    (tmp_path / "da").mkdir()
    (tmp_path / "da/large.h5ad").touch()
    result = orchestrator.build_orchestrator_graph(model).invoke(inputs(tmp_path, text))
    assert result["step_results"] == [] and result["budget"]["model_calls"] == 0
    response = result["messages"][-1]
    assert response.content and "analysis_report" not in response.additional_kwargs
    assert "Scientific Analysis" not in response.content and "证据不足" not in response.content
    root = tmp_path / "users/test/runs/chat"
    assert (root / "conversation_messages.json").exists()
    assert json.loads((root / "run_manifest.json").read_text())["status"] == "succeeded"


def test_planner_can_ask_clarification_without_filler_action(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "execute_action", lambda *a, **k: pytest.fail("Do not query state to fill a plan"))
    def clarify(messages):
        return AIMessage(content=json.dumps({"question": "比较这些方案", "steps": [],
            "clarification_questions": ["请提供要比较的 protocol 名称或 ID。"]}))
    result = orchestrator.build_orchestrator_graph(RunnableLambda(clarify)).invoke(inputs(tmp_path, "比较这些方案"))
    assert result["stop_reason"] == "needs_clarification" and result["step_results"] == []
    assert result["messages"][-1].content == "请提供要比较的 protocol 名称或 ID。"
    assert result["budget"]["model_calls"] == 1
    assert "analysis_report" not in result["messages"][-1].additional_kwargs


def test_empty_plan_without_clarification_is_invalid():
    with pytest.raises(ValueError):
        AnalysisPlan.model_validate({"question": "INS expression", "steps": []})


def test_state_inspection_cannot_be_used_as_a_filler_for_missing_protocols(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "execute_action", lambda *a, **k: pytest.fail("No filler action"))
    responses = iter([
        {"question": "比较这些方案", "steps": [{"id": "filler", "action_id": "scientific.state",
            "inputs": {"limit": 8}, "expected_outputs": ["versioned_world_model"]}]},
        {"question": "比较这些方案", "steps": [], "clarification_questions": ["请提供比较对象。"]},
    ])
    model = RunnableLambda(lambda messages: AIMessage(content=json.dumps(next(responses)),
        usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}))
    result = orchestrator.build_orchestrator_graph(model).invoke(inputs(tmp_path, "比较这些方案"))
    assert result["stop_reason"] == "needs_clarification" and not result["step_results"]
    assert result["budget"]["format_repairs"] == 1


def test_greeting_then_science_in_same_checkpoint_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    calls = []
    def execute(*args, **kwargs):
        calls.append(args[0])
        return {"status": "ok", "evidence_refs": [], "result_preview": {"intent": "find_protocols", "results": [{"protocol_id": "p1"}]}}
    monkeypatch.setattr(orchestrator, "execute_action", execute)
    graph = orchestrator.build_orchestrator_graph(compile_graph=False).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "same"}}
    first = graph.invoke(inputs(tmp_path, "你好"), config)
    assert not calls
    second = graph.invoke({"messages": [HumanMessage(content="查找 Protocol A")]}, config)
    assert calls == ["protocol.lookup"] and second["stop_reason"] == "completed"
    assert second["request"]["session_summary"]["version"] == 2
    assert second["run_id"] != first["run_id"]


def test_scientific_state_metadata_is_not_a_measurement_or_download_url(tmp_path):
    value = {"status": "ok", "result_preview": {"limit_per_kind": 8, "counts": {"hypothesis": 3}}}
    atomic_json(tmp_path / "result.json", value)
    record = {"step_id": "state", "action_id": "scientific.state", "tool_name": "inspect_scientific_state",
              "executed": True, "status": "ok", "usable": True,
              "result_ref": {"result_id": "state", "path": "result.json", "sha256": file_hash(tmp_path / "result.json")}}
    report = build_final_report({"run_id": "chat", "trace_id": "trace", "stop_reason": "completed", "budget": {},
        "step_results": [record], "plan": {"question": "科研状态", "goal_type": "state", "steps": []}}, tmp_path)
    assert report["quantitative_bindings"] == []
    rendered = render_report(report, tmp_path)
    assert "limit_per_kind" not in rendered and "[Complete result JSON]" not in rendered
    assert str(tmp_path) not in rendered
