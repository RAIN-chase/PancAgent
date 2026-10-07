from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState

from protocol_optimization_agent.agents.actions import (
    action_registry,
    execute_action,
    get_action,
    select_action,
    validate_action_arguments,
)
from protocol_optimization_agent.agents.analysis_flow import (
    AnalysisPlan,
    ReviewProposal,
    budget_stop,
    initialize_budget,
    inspect_result,
    parse_object,
    step_signature,
)
from protocol_optimization_agent.runtime.access_control import (
    configure_authenticated_state,
)
from protocol_optimization_agent.runtime.archive import (
    RunArchive,
    atomic_json,
    audited_tool,
    file_hash,
    graph_error_handler,
    record_model_call,
)
from protocol_optimization_agent.runtime.context import (
    RunContext,
    content_fingerprint,
    evidence_response_envelope,
    resolve_domain_output_dir,
    write_run_manifest,
)
from protocol_optimization_agent.runtime.io import read_json
from protocol_optimization_agent.runtime.llm_client import _build_chat_model_from_env
from protocol_optimization_agent.science.reporting import build_final_report, render_report
from protocol_optimization_agent.science.state import ScientificWorldModel


class OrchestratorState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    protocol_output_dir: str
    data_analysis_output_dir: str
    optimization_output_dir: str
    user_id: str
    run_id: str
    trace_id: str
    workspace_root: str
    max_features: int
    project_id: str
    protocol_output_artifact_id: str
    data_analysis_output_artifact_id: str
    optimization_output_artifact_id: str
    parent_run_id: str
    thread_id: str
    request: dict[str, Any]
    plan: dict[str, Any]
    step_results: list[dict[str, Any]]
    budget: dict[str, Any]
    stop_reason: str


@tool
@audited_tool
def execute_scientific_action(
    action_id: str,
    arguments: dict[str, Any],
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    optimization_output_dir: Annotated[str, InjectedState("optimization_output_dir")],
    max_features: Annotated[int, InjectedState("max_features")],
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Execute one available registered action using server-injected runtime context."""
    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id,
                                    workspace_root=workspace_root, project_id=project_id)
    return execute_action(action_id, arguments, context, protocol_output_dir=protocol_output_dir,
                          data_analysis_output_dir=data_analysis_output_dir,
                          optimization_output_dir=optimization_output_dir, max_features=max_features)


def build_orchestrator_graph(chat_model: Runnable | None = None, compile_graph: bool = True) -> Any:
    bound_model = _bind_tools(chat_model or _build_chat_model_from_env())
    graph = StateGraph(OrchestratorState)
    graph.add_node("configure", _configure_node)
    graph.add_node("plan", _plan_node(bound_model))
    graph.add_node("execute", _execute_node)
    graph.add_node("review", _review_node(bound_model))
    graph.add_node("finalize", _finalize_node)

    graph.add_edge(START, "configure")
    graph.add_edge("configure", "plan")
    graph.add_conditional_edges("plan", lambda state: "finalize" if state.get("stop_reason") else "execute")
    graph.add_edge("execute", "review")
    graph.add_conditional_edges("review", lambda state: "finalize" if state.get("stop_reason") else "execute")
    graph.add_edge("finalize", END)
    return graph.compile().with_config(callbacks=[graph_error_handler()], recursion_limit=100) if compile_graph else graph


def _configure_node(state: OrchestratorState, config: RunnableConfig) -> dict[str, Any]:
    configured = configure_authenticated_state(dict(state), dict(config))
    project_id = configured.get("project_id") or os.getenv("PROTOCOL_RESEARCH_PROJECT_ID") or "default"
    context = RunContext.from_values(
        user_id=configured.get("user_id"),
        run_id=configured.get("run_id"),
        trace_id=configured.get("trace_id"),
        workspace_root=configured.get("workspace_root"),
        project_id=project_id,
    )
    parent_run_id = configured.get("parent_run_id", "")
    if (context.run_root() / "run_journal.sqlite").exists() or context.manifest_path().exists():
        parent_run_id = context.run_id
        context = RunContext.from_values(user_id=context.user_id, project_id=project_id,
                    workspace_root=context.workspace_root, run_id=f"run_{uuid4().hex[:16]}")
    ScientificWorldModel(context, project_id)
    protocol_output_dir = resolve_domain_output_dir("protocol_analysis", configured.get("protocol_output_dir"), context)
    data_analysis_output_dir = resolve_domain_output_dir("data_analysis", configured.get("data_analysis_output_dir"), context)
    optimization_output_dir = resolve_domain_output_dir("protocol_optimization", configured.get("optimization_output_dir"), context)
    archive = RunArchive(context)
    budget = initialize_budget(configured.get("budget", {}))
    archive.start({"entrypoint": "orchestrator", "max_features": int(configured.get("max_features") or 40),
                   "model": {"model": os.getenv("PROTOCOL_CHAT_MODEL"),
                             "temperature": os.getenv("PROTOCOL_CHAT_TEMPERATURE", "0")},
                   "question": _last_human_content(configured.get("messages", [])), "analysis_budget": budget,
                   "flow_version": "bounded-analysis-v2"},
                  parent_run_id=parent_run_id,
                  thread_id=str(config.get("configurable", {}).get("thread_id", "")))
    try:
        protocol_output_dir = str(archive.seed(Path(protocol_output_dir), "protocol_analysis"))
        data_analysis_output_dir = str(archive.seed(Path(data_analysis_output_dir), "data_analysis"))
        optimization_output_dir = str(archive.seed(Path(optimization_output_dir), "protocol_optimization"))
    except BaseException as exc:
        archive.record("error", {"type": type(exc).__name__, "message": str(exc)})
        archive.finish({"error": str(exc)}, status="failed")
        raise
    write_run_manifest(
        context,
        output_dirs={
            "protocol_analysis": protocol_output_dir,
            "data_analysis": data_analysis_output_dir,
            "protocol_optimization": optimization_output_dir,
        },
        extra={"entrypoint": "orchestrator", "event": "configure"},
    )
    return {
        **configured,
        "protocol_output_dir": protocol_output_dir,
        "data_analysis_output_dir": data_analysis_output_dir,
        "optimization_output_dir": optimization_output_dir,
        "user_id": context.user_id,
        "run_id": context.run_id,
        "trace_id": context.trace_id,
        "workspace_root": str(context.workspace_root),
        "max_features": int(configured.get("max_features") or os.getenv("PROTOCOL_QUERY_MAX_FEATURES") or 40),
        "project_id": project_id,
        "parent_run_id": parent_run_id,
        "thread_id": str(config.get("configurable", {}).get("thread_id", "")),
        "request": {"question": _last_human_content(configured.get("messages", [])),
                    "plan": configured.get("request", {}).get("plan"),
                    "input_version": content_fingerprint({}, extra=read_json(archive.root / "input_manifest.json")),
                    "code_version": content_fingerprint({}, extra=read_json(archive.root / "environment.json").get("source_sha256", {}))},
        "plan": {"question": _last_human_content(configured.get("messages", [])), "steps": [], "evidence_gaps": [], "alternative_explanations": []},
        "step_results": [], "budget": budget, "stop_reason": "",

    }


def _model_proposal(model: Runnable, state: OrchestratorState, phase: str) -> tuple[Any, dict[str, Any]]:
    budget = dict(state["budget"])
    reason = budget_stop(budget, model=True)
    if reason:
        return None, {"stop_reason": reason}
    schema = AnalysisPlan.model_json_schema() if phase == "plan" else ReviewProposal.model_json_schema()
    system = SystemMessage(content=(
        "Return only a JSON object matching the schema. Propose registered available actions, never execute or approve "
        "experiments. Tools are proposal syntax only and all proposals are validated by code. Do not author numeric "
        "analysis results. Unknown scientific prerequisites remain unknown. Review valid results, retain opposing evidence, "
        "identify alternative explanations, and propose independent next steps only if they add evidence.\n"
        + json.dumps({"phase": phase, "schema": schema, "registry": action_registry(),
                      "question": _last_human_content(state.get("messages", [])),
                      "plan": state["plan"], "step_results": state["step_results"],
                      "budget": budget}, ensure_ascii=True)))
    messages = [system, *state.get("messages", [])]
    archive = _state_archive(state)
    started = time.perf_counter()
    budget["model_calls"] += 1
    try:
        with archive.scope(), archive.operation("orchestrator." + phase, {"phase": phase}, kind="model"):
            response = model.invoke(messages)
            record_model_call(model, messages, response, (time.perf_counter() - started) * 1000)
        usage = getattr(response, "usage_metadata", None) or {}
        tokens = usage.get("total_tokens")
        budget["model_usage_known"] = isinstance(tokens, int) and tokens >= 0
        budget["model_tokens"] += tokens if budget["model_usage_known"] else 0
        cost = getattr(response, "response_metadata", {}).get("cost")
        budget["model_cost_known"] = isinstance(cost, (int, float)) and cost >= 0
        budget["model_cost"] += cost if budget["model_cost_known"] else 0
        return response, {"budget": budget}
    except Exception as exc:
        budget["model_usage_known"] = False
        budget["model_cost_known"] = False
        with archive.scope():
            record_model_call(model, messages, None, (time.perf_counter() - started) * 1000, str(exc))
        archive.record("error", {"type": type(exc).__name__, "phase": phase, "message": str(exc)})
        return None, {"budget": budget, "stop_reason": "evidence_insufficient:model_error",
                      "plan": {**state["plan"], "evidence_gaps": [*state["plan"]["evidence_gaps"], "model_proposal_failed"]}}


def _steps_from_tool_calls(response: AIMessage, state: OrchestratorState) -> list[dict[str, Any]]:
    steps = []
    seen = {step["id"] for step in state["plan"]["steps"]}
    for index, call in enumerate(response.tool_calls):
        arguments = dict(call["args"])
        name = call["name"]
        if name == "execute_scientific_action":
            action_id, inputs = arguments["action_id"], arguments["arguments"]
        else:
            raise ValueError(f"Unregistered execution proposal: {name}")
        action = get_action(action_id)
        suffix = len(state["plan"]["steps"]) + index + 1
        while f"s{suffix}" in seen:
            suffix += 1
        seen.add(f"s{suffix}")
        steps.append({"id": f"s{suffix}", "action_id": action_id, "inputs": inputs,
                      "depends_on": [], "expected_outputs": action.outputs})
    return steps


def _plan_node(model: Runnable | None):
    def run(state: OrchestratorState) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        try:
            if state["request"].get("plan"):
                proposal = state["request"]["plan"]
            elif model is not None:
                response, updates = _model_proposal(model, state, "plan")
                if response is None:
                    return updates
                proposal = ({"question": state["plan"]["question"], "steps": _steps_from_tool_calls(response, state)}
                            if getattr(response, "tool_calls", []) else parse_object(response.content))
            else:
                question = state["plan"]["question"]
                action = select_action(question)
                action_id = action.id
                inputs = {"question": question} if "question" in action.inputs else {}
                proposal = {"question": question, "steps": [{"id": "s1", "action_id": action_id, "inputs": inputs,
                                                              "expected_outputs": get_action(action_id).outputs}]}
            plan = AnalysisPlan.model_validate(proposal).model_dump()
            plan["question"] = _last_human_content(state.get("messages", []))
            archive = _state_archive(state)
            atomic_json(archive.root / "analysis_plan.json", plan)
            archive.record("analysis_plan", {"plan": plan, "phase": "initial"})
            request = {key: value for key, value in state["request"].items() if key != "plan"}
            return {**updates, "request": request, "plan": plan, **_execution_gate({**state, **updates, "plan": plan})}
        except (ValueError, TypeError, KeyError) as exc:
            _state_archive(state).record("plan_validation_error", {"message": str(exc)})
            return {**updates, "stop_reason": "evidence_insufficient:invalid_plan",
                    "request": {key: value for key, value in state["request"].items() if key != "plan"},
                    "plan": {**state["plan"], "evidence_gaps": ["invalid_plan_schema"]}}
    return run


def _execution_gate(state: Mapping[str, Any]) -> dict[str, Any]:
    if state["stop_reason"]:
        return {}
    reason = budget_stop(state["budget"], execute=True, iterations=len(state["step_results"]))
    if reason:
        return {"stop_reason": reason}
    completed = {step["step_id"]: step for step in state["step_results"]}
    for step in state["plan"]["steps"]:
        if step["id"] in completed:
            continue
        if any(not completed.get(dep, {}).get("usable") for dep in step["depends_on"]):
            continue
        try:
            validate_action_arguments(step["action_id"], step["inputs"])
        except ValueError as exc:
            _state_archive(state).record("action_validation_error", {"message": str(exc)})
            return {"stop_reason": "evidence_insufficient:invalid_action", "plan": {**state["plan"], "evidence_gaps": [*state["plan"]["evidence_gaps"], "invalid_action_arguments"]}}
        signature = step_signature(step, state["request"]["input_version"], state["request"]["code_version"])
        if signature in {record["call_signature"] for record in state["step_results"]}:
            return {"stop_reason": "no_new_evidence:duplicate_call"}
        return {}
    return {"stop_reason": "completed" if completed and all(step["usable"] for step in completed.values()) and
            len(completed) == len(state["plan"]["steps"]) else "evidence_insufficient:unresolved_dependencies"}


def _execute_node(state: OrchestratorState) -> dict[str, Any]:
    archive = _state_archive(state)
    gate = _execution_gate(state)
    if gate:
        return gate
    completed = {record["step_id"]: record for record in state["step_results"]}
    step = next(step for step in state["plan"]["steps"] if step["id"] not in completed
                and all(completed.get(dep, {}).get("usable") for dep in step["depends_on"]))
    signature = step_signature(step, state["request"]["input_version"], state["request"]["code_version"])
    evidence_signatures = {record["evidence_signature"] for record in state["step_results"]}
    action = get_action(step["action_id"])
    inputs = {key: state[key] for key in ("protocol_output_dir", "data_analysis_output_dir", "optimization_output_dir",
              "max_features", "user_id", "run_id", "trace_id", "workspace_root", "project_id")}
    tool_name = action.implementation
    tool_call_id = "plan_" + step["id"]
    try:
        with archive.scope(), archive.operation("analysis.action", {"action_id": action.id, "arguments": step["inputs"],
                                                                   "tool_version": action.version}) as result_id:
            result = execute_scientific_action.invoke({"action_id": action.id, "arguments": step["inputs"], **inputs})
            archive.result(result_id, result)
    except Exception as exc:
        result = evidence_response_envelope(context=archive.context, subagent=action.agent, status="analysis_failed",
            question=state["request"]["question"], selected_operation=action.id, result_preview={},
            confidence_caveats=["Action failed; no inferred replacement values."], extra={"error": str(exc)})
        result_id = "failed_" + uuid4().hex
        archive.result(result_id, result)
    path = archive.root / "intermediate" / (result_id + ".json")
    saved = read_json(path)
    usable, gaps, evidence_signature = inspect_result(saved, action.id, step["expected_outputs"])
    if evidence_signature in evidence_signatures:
        gaps.append("no_new_evidence")
        usable = False
    ref = {"result_id": result_id, "path": str(path.relative_to(archive.root)), "sha256": file_hash(path),
           "input_version": content_fingerprint({}, extra=read_json(archive.root / "input_manifest.json")),
           "parameters": step["inputs"], "tool_version": action.version, "code_version": state["request"]["code_version"]}
    record = {"step_id": step["id"], "action_id": action.id, "tool_name": tool_name, "executed": True,
              "status": str(saved.get("status", "unknown")), "usable": usable, "evidence_gaps": gaps, "result_ref": ref,
              "call_signature": signature, "evidence_signature": evidence_signature, "new_evidence": evidence_signature not in evidence_signatures}
    results = [*state["step_results"], record]
    atomic_json(archive.root / "step_results.json", results)
    archive.record("analysis_step", record)
    return {"step_results": results,
            "plan": {**state["plan"], "evidence_gaps": sorted(set([*state["plan"]["evidence_gaps"], *gaps]))},
            "messages": [AIMessage(content="", tool_calls=[{"name": tool_name, "args": step["inputs"], "id": tool_call_id}]),
                         ToolMessage(name=tool_name, tool_call_id=tool_call_id,
                                     content=json.dumps({**saved, "result_refs": [ref]}, ensure_ascii=False),
                                     status="success" if usable else "error")]}


def _review_node(model: Runnable | None):
    def run(state: OrchestratorState) -> dict[str, Any]:
        if state["stop_reason"]:
            return {}
        last = state["step_results"][-1]
        updates: dict[str, Any] = {}
        if not last["new_evidence"]:
            return {"stop_reason": "no_new_evidence"}
        reason = budget_stop(state["budget"])
        if reason:
            return {"stop_reason": reason}
        if model is not None:
            response, updates = _model_proposal(model, state, "review")
            if response is None:
                return updates
            try:
                proposal = (ReviewProposal.model_validate({"next_steps": _steps_from_tool_calls(response, state)})
                            if getattr(response, "tool_calls", []) else ReviewProposal.model_validate(parse_object(response.content)))
                valid_refs = {step["result_ref"]["result_id"] for step in state["step_results"]}
                if set(proposal.conclusion_refs) - valid_refs:
                    raise ValueError("Review cites an unknown result")
                if proposal.next_steps:
                    plan = AnalysisPlan.model_validate({**state["plan"], "steps": [*state["plan"]["steps"],
                                                        *[step.model_dump() for step in proposal.next_steps]]}).model_dump()
                    updates["plan"] = plan
                updates["plan"] = {**updates.get("plan", state["plan"]), "alternative_explanations": sorted(set([*state["plan"]["alternative_explanations"], *proposal.alternative_explanations]))}
            except (ValueError, TypeError, KeyError) as exc:
                _state_archive(state).record("review_validation_error", {"message": str(exc)})
                return {**updates, "stop_reason": "evidence_insufficient:invalid_review",
                        "plan": {**state["plan"], "evidence_gaps": [*state["plan"]["evidence_gaps"], "invalid_review_schema"]}}
        plan = updates.get("plan", state["plan"])
        completed = {step["step_id"] for step in state["step_results"]}
        if all(step["id"] in completed for step in plan["steps"]):
            updates["stop_reason"] = "completed" if all(step["usable"] for step in state["step_results"]) else "evidence_insufficient"
        archive = _state_archive(state)
        atomic_json(archive.root / "analysis_plan.json", plan)
        archive.record("analysis_plan", {"plan": plan, "phase": "reviewed"})
        return {**updates, **_execution_gate({**state, **updates})}
    return run


def _state_archive(state: Mapping[str, Any]) -> RunArchive:
    return RunArchive(RunContext.from_values(user_id=state.get("user_id"), run_id=state.get("run_id"),
        trace_id=state.get("trace_id"), workspace_root=state.get("workspace_root"),
        project_id=state.get("project_id") or "default"))


def _finalize_node(state: OrchestratorState) -> dict[str, Any]:
    archive = _state_archive(state)
    budget = {**state["budget"], "elapsed_seconds": time.time() - state["budget"]["started_at"]}
    report = build_final_report({**state, "budget": budget}, archive.root)
    message = AIMessage(content=render_report(report, archive.root), additional_kwargs={"analysis_report": report})
    final = message.model_dump(mode="json")
    final.update(run_id=state["run_id"], trace_id=state["trace_id"], stop_reason=state["stop_reason"])
    # Historical conversation messages are context, not executions in this run.
    with sqlite3.connect(archive.database) as con:
        failed = con.execute("SELECT 1 FROM events WHERE kind='error' LIMIT 1").fetchone()
        partial = con.execute("SELECT 1 FROM events WHERE kind='tool_summary' AND json_extract(payload,'$.status') NOT IN "
                              "('ok','optimization_ran','ok_cached_optimization_outputs') LIMIT 1").fetchone()
    archive.finish(final, status="partial" if failed or partial or state.get("stop_reason") != "completed" else "succeeded")
    return {"budget": budget, "messages": [message]}


def _bind_tools(model: Runnable | None) -> Runnable | None:
    if model is None:
        return None
    bind_tools = getattr(model, "bind_tools", None)
    if callable(bind_tools):
        return bind_tools([execute_scientific_action])
    return model


def _last_human_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            return str(message.content)
    return ""


graph = build_orchestrator_graph()
