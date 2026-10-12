from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.actions import get_action
from protocol_optimization_agent.runtime.archive import file_hash
from protocol_optimization_agent.runtime.context import RunContext
from protocol_optimization_agent.science.memory import (
    ConversationMemory,
    context_messages,
    prompt_summary,
    remember_report,
    update_summary,
)
from protocol_optimization_agent.science.models import Evidence, Hypothesis, ScientificInterpretation
from protocol_optimization_agent.science.state import ScientificWorldModel


def store(root, user="alice", project="project", run="run"):
    return ScientificWorldModel(RunContext.from_values(workspace_root=root, user_id=user, project_id=project, run_id=run), project)


def initial(root, question="Protocol A", run="first", action="protocol.lookup", arguments=None):
    arguments = arguments if arguments is not None else {"question": "Protocol A"}
    return {"workspace_root": str(root), "user_id": "alice", "project_id": "project", "run_id": run,
        "protocol_output_dir": str(root / "pa"), "data_analysis_output_dir": str(root / "da"),
        "optimization_output_dir": str(root / "po"), "messages": [HumanMessage(content=question)],
        "request": {"plan": {"question": question, "steps": [{"id": "s1", "action_id": action,
            "inputs": arguments, "expected_outputs": get_action(action).outputs}]}}}


def lookup():
    return {"status": "ok", "evidence_refs": [], "result_preview": {"intent": "find_protocols",
        "results": [{"protocol_id": "p1", "protocol_name": "Protocol A"}]}}


def fake_execute(monkeypatch, outputs):
    iterator = iter(outputs)
    monkeypatch.setattr(orchestrator, "execute_action", lambda *args, **kwargs: next(iterator))
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)


def test_workbench_scope_is_bounded_archived_and_survives_thread_followup(tmp_path, monkeypatch):
    fake_execute(monkeypatch, [lookup(), lookup()])
    graph = orchestrator.build_orchestrator_graph(compile_graph=False).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "workbench"}}
    request = initial(tmp_path)
    context = {"dataset_ids": ["atlas"], "dataset_versions": ["atlas=v1"], "cell_types": ["beta"],
               "result_refs": ['{"result_id":"previous","field_path":"/result_preview"}']}
    request["request"]["workbench_context"] = context
    first = graph.invoke(request, config)
    assert first["request"]["workbench_context"] == context
    archived = tmp_path / "users/alice/runs" / first["run_id"] / "workbench_context.json"
    assert json.loads(archived.read_text()) == context
    second = graph.invoke({"messages": [HumanMessage(content="Protocol A again")]}, config)
    assert second["request"]["workbench_context"] == context
    assert not second["request"]["session_summary"]["result_refs"] == context["result_refs"]
    invalid = initial(tmp_path, run="invalid")
    invalid["request"]["workbench_context"] = {"workspace_root": ["/someone-else"]}
    with pytest.raises(ValueError, match="Undeclared memory fields"):
        orchestrator._configure_node(invalid, {})


def test_same_thread_recovers_after_new_graph_and_new_run(tmp_path, monkeypatch):
    comparison = {"status": "ok", "evidence_refs": [], "scientific_preconditions": {}, "result_preview": {
        "protocol_id_a": "p1", "protocol_id_b": "p2", "tool_result": {"feature_differences": [], "factor_diff": [],
        "stage_diff": [], "event_step_diff": {"detail_status": "available"}}}}
    fake_execute(monkeypatch, [lookup(), comparison])
    first = initial(tmp_path, "Protocol A，必须仅比较 Beta，不能作因果推断")
    first["request"]["memory_update"] = {"user_constraints": ["Require independent biological replicates"]}
    config = {"configurable": {"thread_id": "thread1"}}
    result = orchestrator.build_orchestrator_graph().invoke(first, config)
    summary = result["request"]["session_summary"]
    assert summary["protocol_ids"] == ["p1"] and summary["version"] == 1
    old_ref = summary["result_refs"][0]
    second = initial(tmp_path, "再比较另一篇", "second", "protocol.compare", {"protocol_id_a": "p1", "protocol_id_b": "p2"})
    resumed = orchestrator.build_orchestrator_graph().invoke(second, config)
    restored = resumed["request"]["session_summary"]
    assert restored["protocol_ids"] == ["p1", "p2"] and restored["version"] == 2
    assert "Require independent biological replicates" in restored["user_constraints"]
    assert first["messages"][0].content in restored["user_constraints"]
    assert old_ref in restored["result_refs"]
    assert file_hash(tmp_path / "users/alice/runs/first" / old_ref["path"]) == old_ref["sha256"]


def test_checkpoint_chat_followup_creates_new_archive(tmp_path, monkeypatch):
    fake_execute(monkeypatch, [lookup(), lookup()])
    graph = orchestrator.build_orchestrator_graph(compile_graph=False).compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "checkpoint-chat"}}
    first = graph.invoke(initial(tmp_path), config)
    first_root = tmp_path / "users/alice/runs" / first["run_id"]
    original = file_hash(first_root / "bundle_manifest.json")
    second = graph.invoke({"messages": [HumanMessage(content="Protocol A again")]}, config)
    assert second["run_id"] != first["run_id"]
    assert second["parent_run_id"] == first["run_id"]
    assert file_hash(first_root / "bundle_manifest.json") == original
    assert second["request"]["session_summary"]["version"] == 2


def test_explicit_endpoint_switch_cannot_succeed_using_old_gene_result(tmp_path, monkeypatch):
    memory = ConversationMemory(store(tmp_path))
    memory.save("thread1", {**memory.restore("thread1"), "endpoints": ["composition"]}, 0)
    expression = {"status": "ok", "phenotype_ir": {}, "evidence_refs": [],
        "validation": {"task_validity": {"gene_expression": {"valid": True}}},
        "result_preview": [{"protocol_id": "p1", "gene": "INS", "mean_expression": 0.5, "status": "ok"}]}
    fake_execute(monkeypatch, [expression])
    result = orchestrator.build_orchestrator_graph().invoke(initial(tmp_path, "换成成熟度指标", action="phenotype.gene_expression",
        arguments={"request": {"genes": ["INS"]}}), {"configurable": {"thread_id": "thread1"}})
    assert result["plan"]["target_metrics"] == ["maturity"]
    assert result["stop_reason"] == "evidence_insufficient"
    assert result["request"]["session_summary"]["endpoints"] == ["maturity"]


def test_summary_and_raw_messages_are_scoped_by_user_project_and_thread(tmp_path):
    alice = ConversationMemory(store(tmp_path))
    summary = {**alice.restore("same"), "protocol_ids": ["private"], "user_constraints": ["Only Beta"]}
    alice.capture("same", [{"type": "human", "content": "Private source"}])
    alice.save("same", summary, 0)
    for user, project, thread in [("bob", "project", "same"), ("alice", "other", "same"), ("alice", "project", "different")]:
        memory = ConversationMemory(store(tmp_path, user, project))
        assert memory.restore(thread)["protocol_ids"] == []
        with memory.store._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM conversation_messages WHERE thread_id=?", (thread,)).fetchone()[0] == 0


def test_reusing_graph_state_in_another_project_does_not_copy_constraints(tmp_path, monkeypatch):
    fake_execute(monkeypatch, [lookup(), lookup()])
    old = orchestrator.build_orchestrator_graph().invoke(initial(tmp_path, "Only Beta cells"), {"configurable": {"thread_id": "same"}})
    new = {**old, "project_id": "other", "messages": [*old["messages"], HumanMessage(content="Protocol A")],
           "request": {**old["request"], "plan": initial(tmp_path)["request"]["plan"]}}
    result = orchestrator.build_orchestrator_graph().invoke(new, {"configurable": {"thread_id": "same"}})
    assert result["request"]["context_reset"]
    assert result["request"]["session_summary"]["user_constraints"] == []
    with store(tmp_path, project="other")._connect() as db:
        assert all("Only Beta cells" not in row[0] for row in db.execute("SELECT payload FROM conversation_messages"))


def test_long_context_keeps_old_constraints_and_opposition_without_deleting_sources(tmp_path, monkeypatch):
    world = store(tmp_path)
    world.put([Hypothesis(id="h", statement="Protocol A has competing mechanisms", feature="Protocol A", objective="maturity",
        protocol_ids=["p1"], opposing_evidence_refs=[{"doi": "contradiction"}])], "S4")
    fake_execute(monkeypatch, [lookup()])
    prompts = []
    def model(messages):
        prompts.append(messages)
        return AIMessage(content="{}", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    state = initial(tmp_path)
    rule = HumanMessage(id="rule", content="Only Beta cells; never infer causality.")
    state["messages"] = [rule, *[HumanMessage(content="Earlier discussion " + str(index)) for index in range(20)], HumanMessage(content="Protocol A")]
    result = orchestrator.build_orchestrator_graph(RunnableLambda(model)).invoke(state, {"configurable": {"thread_id": "long"}})
    assert rule.content in prompts[0][0].content
    assert "contradiction" in prompts[0][0].content
    assert all(item.id != "rule" for item in prompts[0][1:])
    assert result["request"]["session_summary"]["opposing_refs"]
    with world._connect() as db:
        rows = [json.loads(row[0]) for row in db.execute("SELECT payload FROM conversation_messages WHERE thread_id='long'")]
    assert any(row.get("id") == "rule" for row in rows)
    assert world.get("hypothesis", "h")["opposing_evidence_refs"] == [{"doi": "contradiction"}]


def test_forgetting_constraint_is_explicit_revision_not_evidence_deletion(tmp_path):
    memory = ConversationMemory(store(tmp_path))
    message = {"type": "human", "id": "rule", "content": "Only Beta cells"}
    first = update_summary(memory.restore("t"), "", {}, [message])
    memory.capture("t", [message])
    saved = memory.save("t", first, 0)
    forgotten = update_summary(saved, "", {"remove_constraints": ["Only Beta cells"]}, [message])
    assert forgotten["user_constraints"] == []
    memory.save("t", forgotten, 1)
    replay = update_summary(memory.restore("t"), "new question", {}, [message])
    assert replay["user_constraints"] == []
    with memory.store._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM conversation_revisions").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM conversation_messages").fetchone()[0] == 1


def test_stale_writer_cannot_erase_new_constraints(tmp_path):
    memory = ConversationMemory(store(tmp_path))
    snapshot = memory.restore("t")
    memory.save("t", {**snapshot, "user_constraints": ["Keep opposition"]}, 0)
    with pytest.raises(ValueError, match="concurrently"):
        memory.save("t", snapshot, 0)
    assert memory.restore("t")["user_constraints"] == ["Keep opposition"]


def test_structured_retrieval_filters_before_keywords_and_expands_both_directions(tmp_path):
    world = store(tmp_path)
    support = Evidence(id="for", kind="phenotype", source_refs=[{"dataset_id": "d1"}], run_id="a", trace_id="a")
    opposition = Evidence(id="against", kind="literature", source_refs=[{"doi": "against-doi"}], run_id="a", trace_id="a")
    hypotheses = [Hypothesis(id="h1", statement="A stage-dependent hypothesis", feature="INS", objective="maturity", protocol_ids=["p1"],
        test_spec={"endpoints": ["maturity"], "baseline_protocol_version": "v1", "location": {"target_stage": "S6"}},
        rationale_evidence=["for"], opposing_evidence_refs=[{"evidence_id": "against"}]),
        Hypothesis(id="h2", statement="p2 keyword", feature="GCG", objective="maturity", protocol_ids=["p2"],
            test_spec={"endpoints": ["maturity"], "baseline_protocol_version": "v2", "location": {"target_stage": "S5"}})]
    world.put([support, opposition, *hypotheses], "S4")
    filters = {"protocol_ids": ["p1"], "endpoints": ["成熟度"], "stages": ["S6"], "protocol_versions": ["v1"]}
    history = world.reasoning_context("p2 keyword", filters=filters)
    assert [row["id"] for row in history["hypotheses"]] == ["h1"]
    row = history["hypotheses"][0]
    assert row["supporting_evidence"][0]["id"] == "for"
    assert row["opposing_evidence"][0]["source_refs"] == [{"doi": "against-doi"}]
    assert row["opposing_evidence"][0]["version"] == 1
    assert world.reasoning_context("INS", filters={**filters, "protocol_versions": ["v2"]})["hypotheses"] == []
    assert world.reasoning_context("INS", filters={**filters, "protocol_ids": ["P1"]})["hypotheses"] == []
    assert world.reasoning_context("成熟度", limit=1)["hypotheses"]
    with pytest.raises(ValueError):
        world.reasoning_context("INS", filters={"sql": ["DROP TABLE records"]})


def test_repeated_model_interpretation_never_promotes_to_fact(tmp_path, monkeypatch):
    fake_execute(monkeypatch, [lookup()])
    result = orchestrator.build_orchestrator_graph().invoke(initial(tmp_path))
    world = store(tmp_path, run="first")
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    root = tmp_path / "users/alice/runs/first"
    before = world.snapshot()
    remember_report(world, report, root)
    after = world.snapshot()
    assert before["counts"] == after["counts"]
    assert after["records"]["interpretation"][0]["status"] == "UNREVIEWED"
    assert after["records"]["evidence"][0]["observations"]["classification"] == "tool_observation"
    assert "claim" not in after["counts"]
    retrieved = world.reasoning_context("unrelated keyword", filters={"protocol_ids": ["p1"]})
    assert retrieved["interpretations"]
    assert retrieved["interpretations"][0]["supporting_evidence"]
    for record in after["records"]["interpretation"]:
        bad = ScientificInterpretation.model_validate(record).model_copy(update={"status": "SUPPORTED"})
        with pytest.raises(ValueError):
            world.put([bad], "S0")


def test_compressed_tool_view_keeps_original_and_counterevidence():
    payload = {"result_preview": {"rows": [{"gene": "INS", "value": index} for index in range(100)]},
               "opposing_refs": [{"doi": "opposition"}], "confidence_caveats": ["Confounded"], "result_refs": [{"result_id": "saved"}]}
    original = ToolMessage(content=json.dumps(payload), tool_call_id="tool")
    selected = context_messages([HumanMessage(content="INS"), AIMessage(content="", tool_calls=[{
        "name": "tool", "args": {}, "id": "tool"}]), original], 10000)
    preview = json.loads(selected[-1].content)
    assert len(preview["result_preview"]["rows"]) == 6
    assert preview["opposing_refs"] == payload["opposing_refs"]
    assert preview["result_refs"] == payload["result_refs"]
    assert "not complete evidence" in preview["context_view"]
    assert json.loads(original.content) == payload


def test_summary_view_deduplicates_constraint_sources_without_forgetting_opposition():
    summary = {"user_constraints": ["Only Beta"], "constraint_sources": [
        {"constraint": "Only Beta", "message_key": str(index)} for index in range(100)],
        "opposing_refs": [{"source_id": "opposition"}], "result_refs": [{"result_id": str(index)} for index in range(12)]}
    view = prompt_summary(summary)
    assert len(view["constraint_sources"]) == 1
    assert view["opposing_refs"] == summary["opposing_refs"]
    assert len(view["result_refs"]) == 8 and view["result_ref_count"] == 12
    assert len(summary["constraint_sources"]) == 100 and len(summary["result_refs"]) == 12


def test_oversized_critical_constraints_stop_instead_of_silent_forgetting(tmp_path, monkeypatch):
    fake_execute(monkeypatch, [])
    state = initial(tmp_path)
    state.pop("request")
    state["budget"] = {"max_context_chars": 1}
    model = RunnableLambda(lambda messages: pytest.fail("Model should not receive a truncated critical context"))
    result = orchestrator.build_orchestrator_graph(model).invoke(state, {"configurable": {"thread_id": "t"}})
    assert result["stop_reason"] == "budget_exhausted:context"
    assert (tmp_path / "users/alice/runs/first/conversation_messages.json").exists()
    assert result["budget"]["model_calls"] == 0


def test_memory_writes_require_authenticated_project_permission(tmp_path, monkeypatch):
    config = {"workspace_root": str(tmp_path), "users": {"reader": {"projects": {"project": ["read"]}}}}
    path = tmp_path / "auth.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "authenticated")
    monkeypatch.setenv("PROTOCOL_AUTH_CONFIG", str(path))
    memory = ConversationMemory(store(tmp_path, user="reader"))
    with pytest.raises(PermissionError):
        memory.capture("t", [{"type": "human", "content": "Do not change evidence"}])
    with pytest.raises(PermissionError):
        memory.save("t", memory.restore("t"), 0)


def test_cannot_register_another_users_run_as_own_memory(tmp_path, monkeypatch):
    fake_execute(monkeypatch, [lookup()])
    result = orchestrator.build_orchestrator_graph().invoke(initial(tmp_path))
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    with pytest.raises(PermissionError):
        remember_report(store(tmp_path, user="bob", run="first"), report, tmp_path / "users/alice/runs/first")
