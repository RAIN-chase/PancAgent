from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode

from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent
from protocol_optimization_agent.io import read_csv_dicts, read_json, read_jsonl
from protocol_optimization_agent.langgraph_server import _build_chat_model_from_env
from protocol_optimization_agent.optimization_orchestration import build_optimization_orchestration_plan
from protocol_optimization_agent.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime import (
    RunContext,
    append_tool_call_log,
    content_fingerprint,
    evidence_response_envelope,
    resolve_agent_output_dir,
    write_run_manifest,
)
from protocol_optimization_agent.tools.external_data_tools import build_external_tool_inventory
from protocol_optimization_agent.tools.h5ad_tools import DATA_ANALYSIS_TOOL_REGISTRY

DEFAULT_PROTOCOL_OUTPUT_DIR = "runs/protocol_analysis_agent_simplified_autofeatures"
DEFAULT_DATA_ANALYSIS_OUTPUT_DIR = "runs/data_analysis"
DEFAULT_OPTIMIZATION_OUTPUT_DIR = "runs/protocol_optimization"

SYSTEM_PROMPT = """You are the Orchestrator Agent for ProtocolOptimizationAgent.

Your job is to understand the user's scientific question, choose the correct callable SubAgent, call that SubAgent tool, and synthesize only from returned tool results.

Available SubAgents:
- Protocol Analysis Agent: protocol evidence, protocol lookup, protocol comparison, factor events, feature matrix, provenance, heatmaps.
- Data Analysis Agent: h5ad dataset inventory, cell-type composition, marker or gene-program scoring, maturity scoring, reference similarity, QC limitations.
- Protocol Optimizing Agent: integrated requests that need both protocol evidence and h5ad phenotype summaries to produce evidence-labeled optimization hypotheses.

Routing policy:
- Use Protocol Analysis Agent for questions about protocols, papers, factors, stages, comparisons, features, evidence, or protocol visualizations.
- Use Data Analysis Agent for questions about h5ad, AnnData, single-cell expression, marker expression, cell composition, maturity, similarity, trajectories, or reference atlases.
- Use Protocol Optimizing Agent for requests to improve, optimize, rank, recommend, or propose protocol changes. If needed, call protocol and data agents first for context.
- Never turn associations into causal claims. Every optimization answer must state that recommendations are hypotheses requiring experimental validation.

Runtime context:
- protocol_output_dir: {protocol_output_dir}
- data_analysis_output_dir: {data_analysis_output_dir}
- optimization_output_dir: {optimization_output_dir}
- max_features: {max_features}

Mandatory response schema:
- used_tools: names of SubAgent tools and deterministic tools used.
- used_features: protocol matrix features or data metrics used for the answer.
- evidence_refs: protocol/data evidence references returned by tools.
- confidence_caveats: limitations that affect interpretation.
- trace_id: runtime trace id linking answer, tool-call log, and run manifest.
"""


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


SUBAGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "protocol_analysis_agent": {
        "title": "Protocol Analysis Agent",
        "status": "implemented",
        "responsibilities": [
            "protocol lookup and comparison",
            "factor/stage event retrieval",
            "feature matrix querying",
            "evidence and provenance lookup",
            "protocol feature heatmap generation",
        ],
    },
    "data_analysis_agent": {
        "title": "Data Analysis Agent",
        "status": "framework_ready_numeric_tools_pending",
        "responsibilities": [
            "h5ad manifest and QC inspection",
            "cell-type composition output contracts",
            "marker and gene-program scoring output contracts",
            "maturity and reference similarity output contracts",
        ],
    },
    "protocol_optimizing_agent": {
        "title": "Protocol Optimizing Agent",
        "status": "framework_implemented",
        "responsibilities": [
            "join protocol features with h5ad phenotype summaries",
            "classify recommendation evidence strength",
            "generate optimization hypotheses",
            "preserve correlation-vs-causality warnings",
        ],
    },
}


def _protocol_output_dir(value: str | None = None, context: RunContext | None = None) -> str:
    context = context or RunContext.from_values()
    return resolve_agent_output_dir(
        "protocol_analysis",
        value,
        "PROTOCOL_ANALYSIS_OUTPUT_DIR",
        DEFAULT_PROTOCOL_OUTPUT_DIR,
        context,
    )


def _data_analysis_output_dir(value: str | None = None, context: RunContext | None = None) -> str:
    context = context or RunContext.from_values()
    return resolve_agent_output_dir(
        "data_analysis",
        value,
        "DATA_ANALYSIS_OUTPUT_DIR",
        DEFAULT_DATA_ANALYSIS_OUTPUT_DIR,
        context,
    )


def _optimization_output_dir(value: str | None = None, context: RunContext | None = None) -> str:
    context = context or RunContext.from_values()
    return resolve_agent_output_dir(
        "protocol_optimization",
        value,
        "PROTOCOL_OPTIMIZATION_OUTPUT_DIR",
        DEFAULT_OPTIMIZATION_OUTPUT_DIR,
        context,
    )


@tool
def describe_subagents() -> dict[str, Any]:
    """Return the Orchestrator's callable SubAgent registry and routing responsibilities."""

    return {
        "schema_version": "orchestrator-subagent-registry-v1",
        "subagents": SUBAGENT_REGISTRY,
    }


@tool
def protocol_analysis_agent(
    question: str,
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    max_features: Annotated[int, InjectedState("max_features")],
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
) -> dict[str, Any]:
    """Delegate protocol lookup, comparison, matrix, event, evidence, or visualization questions to Protocol Analysis Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root)
    output_dir = _protocol_output_dir(protocol_output_dir, context)
    engine = ProtocolQueryEngine(Path(output_dir))
    result = engine.answer_question(question, max_features=max_features)
    selected_operation = str(result.get("intent") or "protocol_query")
    output_files = {
        "protocol_feature_matrix": str(Path(output_dir) / "protocol_feature_matrix.csv"),
        "normalized_events": str(Path(output_dir) / "normalized_factor_events.jsonl"),
        "feature_provenance": str(Path(output_dir) / "protocol_feature_provenance.json"),
    }
    append_tool_call_log(
        context,
        subagent="protocol_analysis_agent",
        operation=selected_operation,
        status=str(result.get("status", "ok")),
        question=question,
        output_files=output_files,
    )
    write_run_manifest(
        context,
        output_dirs={"protocol_analysis": output_dir},
        inputs={"protocol_analysis_output_dir": output_dir},
        extra={"entrypoint": "orchestrator", "last_subagent": "protocol_analysis_agent"},
    )
    return evidence_response_envelope(
        context=context,
        subagent="protocol_analysis_agent",
        status=str(result.get("status", "ok")),
        question=question,
        selected_operation=selected_operation,
        used_tools=_protocol_used_tools(result),
        used_features=_protocol_used_features(result),
        evidence_refs=_protocol_evidence_refs(result),
        confidence_caveats=_protocol_confidence_caveats(result),
        result_preview=result,
        output_files=output_files,
        provenance={"output_dir": output_dir},
        extra={"result": result, "output_dir": output_dir},
    )


@tool
def data_analysis_agent(
    question: str,
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    request: dict[str, Any] | None = None,
    h5ad_manifest_rows: list[dict[str, Any]] | None = None,
    h5ad_config: dict[str, Any] | None = None,
    force_rerun: bool = False,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
) -> dict[str, Any]:
    """Delegate h5ad, marker, composition, maturity, reference similarity, trajectory, or QC questions to Data Analysis Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root)
    output_dir = Path(_data_analysis_output_dir(data_analysis_output_dir, context))
    structured_request = _build_data_analysis_request(question, request or {})
    selected_operation = str(structured_request["operation"])
    effective_h5ad_config = _merge_h5ad_query_config(h5ad_config or {}, structured_request)
    if force_rerun or (h5ad_manifest_rows and _data_query_requires_execution(selected_operation)):
        DataAnalysisAgent(output_dir=output_dir, h5ad_config=effective_h5ad_config).run(h5ad_manifest_rows or [])
    comparison_report = read_json(output_dir / "data_analysis_comparison_report.json")
    limitations = read_json(output_dir / "h5ad_analysis_limitations.json")
    tool_registry = read_json(output_dir / "data_analysis_tool_registry.json")
    summaries = read_jsonl(output_dir / "h5ad_summary_by_protocol.jsonl")
    protocol_summary_rows = read_csv_dicts(output_dir / "data_analysis_protocol_summary.csv")
    existing_outputs = (
        sorted(path.name for path in output_dir.glob("*") if path.is_file()) if output_dir.exists() else []
    )
    selected_output = _data_operation_output_file(selected_operation)
    cached_rows = read_csv_dicts(output_dir / selected_output)
    result_preview = _filter_data_result_rows(cached_rows, structured_request)[:50]
    status = _data_tool_status(existing_outputs, selected_output, cached_rows, result_preview, structured_request)
    output_files = {
        "selected": str(output_dir / selected_output),
        "summary": str(output_dir / "data_analysis_comparison_report.json"),
        "limitations": str(output_dir / "h5ad_analysis_limitations.json"),
    }
    append_tool_call_log(
        context,
        subagent="data_analysis_agent",
        operation=selected_operation,
        status=status,
        question=question,
        output_files=output_files,
    )
    write_run_manifest(
        context,
        output_dirs={"data_analysis": str(output_dir)},
        inputs={"data_analysis_output_dir": str(output_dir)},
        extra={
            "entrypoint": "orchestrator",
            "last_subagent": "data_analysis_agent",
            "request": structured_request,
            "force_rerun": force_rerun,
        },
    )
    return evidence_response_envelope(
        context=context,
        subagent="data_analysis_agent",
        status=status,
        question=question,
        selected_operation=selected_operation,
        used_tools=[_data_operation_tool_name(selected_operation)],
        used_features=_data_used_features(selected_operation, result_preview),
        evidence_refs=_data_evidence_refs(summaries[:50]),
        confidence_caveats=_data_confidence_caveats(status, limitations.get("limitations", [])),
        limitations=limitations.get("limitations", [])[:50],
        provenance=_data_analysis_provenance(summaries[:50]),
        result_preview=result_preview,
        output_files=output_files,
        extra={
            "structured_request": structured_request,
            "output_dir": str(output_dir),
            "summary": comparison_report.get("summary", {}),
            "protocol_summaries": protocol_summary_rows[:50],
            "dataset_summaries": summaries[:50],
            "tool_registry": tool_registry.get("tools", DATA_ANALYSIS_TOOL_REGISTRY),
            "external_tool_inventory": tool_registry.get(
                "external_tool_inventory", build_external_tool_inventory(Path.cwd())
            ),
            "callable_tools": tool_registry.get("callable_tools", []),
            "available_output_files": existing_outputs,
        },
    )


@tool
def protocol_optimizing_agent(
    question: str,
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    optimization_output_dir: Annotated[str, InjectedState("optimization_output_dir")],
    objective: dict[str, Any] | None = None,
    new_protocol_features: dict[str, Any] | None = None,
    protocol_records: list[dict[str, Any]] | None = None,
    factor_events: list[dict[str, Any]] | None = None,
    source_identity_rows: list[dict[str, Any]] | None = None,
    quality_filters: dict[str, Any] | None = None,
    h5ad_manifest_rows: list[dict[str, Any]] | None = None,
    h5ad_config: dict[str, Any] | None = None,
    force_protocol_rerun: bool = False,
    force_data_rerun: bool = False,
    force_optimization_rerun: bool = False,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
) -> dict[str, Any]:
    """Delegate integrated protocol-improvement, ranking, recommendation, or optimization questions to Protocol Optimizing Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root)
    protocol_dir = Path(_protocol_output_dir(protocol_output_dir, context))
    data_dir = Path(_data_analysis_output_dir(data_analysis_output_dir, context))
    optimization_dir = Path(_optimization_output_dir(optimization_output_dir, context))
    orchestration_plan = build_optimization_orchestration_plan(
        protocol_dir,
        data_dir,
        objective=objective or {},
        new_protocol_features=new_protocol_features or {},
        force_protocol_rerun=force_protocol_rerun,
        force_data_rerun=force_data_rerun,
        force_optimization_rerun=force_optimization_rerun,
    )
    upstream_tools: list[str] = []
    if "protocol_analysis_agent" in orchestration_plan["required_upstream_reruns"] and protocol_records and factor_events:
        ProtocolAnalysisAgent(
            output_dir=protocol_dir,
            quality_filters=quality_filters or {},
        ).run(protocol_records, factor_events, source_identity_rows or [])
        upstream_tools.append("ProtocolAnalysisAgent.run")
    if "data_analysis_agent" in orchestration_plan["required_upstream_reruns"] and h5ad_manifest_rows:
        DataAnalysisAgent(output_dir=data_dir, h5ad_config=h5ad_config or {}).run(h5ad_manifest_rows)
        upstream_tools.append("DataAnalysisAgent.run")
    if upstream_tools:
        orchestration_plan = build_optimization_orchestration_plan(
            protocol_dir,
            data_dir,
            objective=objective or {},
            new_protocol_features=new_protocol_features or {},
            force_protocol_rerun=False,
            force_data_rerun=False,
            force_optimization_rerun=force_optimization_rerun,
        )
    cache_key = _optimization_cache_key(protocol_dir, data_dir, objective or {}, new_protocol_features or {})
    cache_meta = _read_optional_json(optimization_dir / "optimization_cache_metadata.json")
    has_cached_optimization = (
        (optimization_dir / "proposed_improved_protocol.json").exists()
        and cache_meta.get("cache_key") == cache_key
    )
    should_run = orchestration_plan["can_run_protocol_optimizing_agent"] and (
        new_protocol_features or force_optimization_rerun or not has_cached_optimization
    )
    if should_run:
        ProtocolOptimizingAgent(output_dir=optimization_dir, objective=objective).run(
            protocol_dir,
            data_dir,
            objective=objective,
            new_protocol_features=new_protocol_features,
        )
        _write_optimization_cache_metadata(optimization_dir, cache_key, objective or {}, new_protocol_features or {})
    recommendation_json = _read_optional_json(optimization_dir / "proposed_improved_protocol.json")
    recommendation_md = _read_optional_text(optimization_dir / "proposed_improved_protocol.md")
    risk_report = _read_optional_json(optimization_dir / "evidence_and_risk_report.json")
    candidates = read_csv_dicts(optimization_dir / "optimization_candidate_features.csv")
    prediction = _read_optional_json(optimization_dir / "counterfactual_protocol_predictions.json")
    status = _optimization_tool_status(
        bool(orchestration_plan["can_run_protocol_optimizing_agent"]),
        bool(recommendation_json or recommendation_md),
        bool(should_run),
    )
    output_files = {
        "proposed_protocol_json": str(optimization_dir / "proposed_improved_protocol.json"),
        "proposed_protocol_md": str(optimization_dir / "proposed_improved_protocol.md"),
        "evidence_and_risk_report": str(optimization_dir / "evidence_and_risk_report.json"),
        "candidate_features": str(optimization_dir / "optimization_candidate_features.csv"),
    }
    append_tool_call_log(
        context,
        subagent="protocol_optimizing_agent",
        operation="protocol_optimization",
        status=status,
        question=question,
        output_files=output_files,
    )
    write_run_manifest(
        context,
        output_dirs={
            "protocol_analysis": str(protocol_dir),
            "data_analysis": str(data_dir),
            "protocol_optimization": str(optimization_dir),
        },
        inputs={
            "protocol_analysis_output_dir": str(protocol_dir),
            "data_analysis_output_dir": str(data_dir),
        },
        extra={"entrypoint": "orchestrator", "last_subagent": "protocol_optimizing_agent"},
    )
    inputs = {
            "protocol_output_dir": str(protocol_dir),
            "data_analysis_output_dir": str(data_dir),
            "optimization_output_dir": str(optimization_dir),
            "protocol_matrix_exists": (protocol_dir / "protocol_feature_matrix.csv").exists(),
            "data_summary_exists": (data_dir / "data_analysis_protocol_summary.csv").exists(),
    }
    return evidence_response_envelope(
        context=context,
        subagent="protocol_optimizing_agent",
        status=status,
        question=question,
        selected_operation="protocol_optimization",
        used_tools=[*upstream_tools, *_optimization_used_tools(bool(should_run), bool(has_cached_optimization))],
        used_features=[str(row.get("feature") or "") for row in candidates[:20] if row.get("feature")],
        evidence_refs=_optimization_evidence_refs(candidates[:20], risk_report),
        confidence_caveats=_optimization_confidence_caveats(status, candidates[:20], risk_report),
        limitations=["Optimization recommendations are hypotheses and require experimental validation."],
        provenance={"inputs": inputs, "orchestration_plan": orchestration_plan},
        result_preview={
            "proposed_improved_protocol": recommendation_json,
            "top_candidate_features": candidates[:20],
            "counterfactual_prediction": prediction,
            "cache_key": cache_key,
            "cache_status": "hit" if has_cached_optimization and not should_run else "miss_or_rerun",
        },
        output_files=output_files,
        extra={
            "orchestration_plan": orchestration_plan,
            "inputs": inputs,
            "recommendation_policy": "optimization hypotheses only; experimental validation required",
            "proposed_improved_protocol": recommendation_json,
            "proposed_improved_protocol_markdown": recommendation_md,
            "evidence_and_risk_report": risk_report,
            "top_candidate_features": candidates[:20],
            "counterfactual_prediction": prediction,
        },
    )


ORCHESTRATOR_TOOLS = [
    describe_subagents,
    protocol_analysis_agent,
    data_analysis_agent,
    protocol_optimizing_agent,
]


def _optimization_tool_status(can_run: bool, has_outputs: bool, ran_now: bool) -> str:
    if not can_run:
        return "upstream_rerun_required"
    if ran_now:
        return "optimization_ran"
    if has_outputs:
        return "ok_cached_optimization_outputs"
    return "no_prior_optimization_outputs"


def _optimization_cache_key(
    protocol_dir: Path,
    data_dir: Path,
    objective: dict[str, Any],
    new_protocol_features: dict[str, Any],
) -> str:
    return content_fingerprint(
        {
            "protocol_feature_matrix": str(protocol_dir / "protocol_feature_matrix.csv"),
            "protocol_feature_provenance": str(protocol_dir / "protocol_feature_provenance.json"),
            "stage_factor_summary": str(protocol_dir / "stage_factor_matrix_or_events_summary.csv"),
            "data_protocol_summary": str(data_dir / "data_analysis_protocol_summary.csv"),
            "marker_scores": str(data_dir / "marker_scores.csv"),
            "gene_expression_summary": str(data_dir / "gene_expression_summary.csv"),
            "differential_expression": str(data_dir / "differential_expression.csv"),
            "maturity_scores": str(data_dir / "maturity_scores.csv"),
            "similarity_scores": str(data_dir / "similarity_scores.csv"),
        },
        extra={
            "objective": objective,
            "new_protocol_features": new_protocol_features,
            "output_contract": "protocol-optimization-v1",
        },
    )


def _write_optimization_cache_metadata(
    optimization_dir: Path,
    cache_key: str,
    objective: dict[str, Any],
    new_protocol_features: dict[str, Any],
) -> None:
    optimization_dir.mkdir(parents=True, exist_ok=True)
    (optimization_dir / "optimization_cache_metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "optimization-cache-metadata-v1",
                "cache_key": cache_key,
                "objective": objective,
                "new_protocol_features": new_protocol_features,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _optimization_used_tools(ran_now: bool, cache_hit: bool) -> list[str]:
    tools = ["build_optimization_orchestration_plan"]
    if ran_now:
        tools.append("ProtocolOptimizingAgent.run")
    elif cache_hit:
        tools.append("read_cached_protocol_optimization_outputs")
    return tools


def build_orchestrator_graph(chat_model: Runnable | None = None, compile_graph: bool = True) -> Any:
    bound_model = _bind_tools(chat_model or _build_chat_model_from_env())
    graph = StateGraph(OrchestratorState)
    graph.add_node("configure", _configure_node)
    graph.add_node("orchestrator", _orchestrator_node(bound_model))
    graph.add_node("subagents", ToolNode(ORCHESTRATOR_TOOLS))

    graph.add_edge(START, "configure")
    graph.add_edge("configure", "orchestrator")
    graph.add_conditional_edges("orchestrator", _route_after_orchestrator, {"subagents": "subagents", "end": END})
    graph.add_edge("subagents", "orchestrator")
    return graph.compile() if compile_graph else graph


def _configure_node(state: OrchestratorState) -> OrchestratorState:
    context = RunContext.from_values(
        user_id=state.get("user_id"),
        run_id=state.get("run_id"),
        trace_id=state.get("trace_id"),
        workspace_root=state.get("workspace_root"),
    )
    protocol_output_dir = resolve_agent_output_dir(
        "protocol_analysis",
        state.get("protocol_output_dir"),
        "PROTOCOL_ANALYSIS_OUTPUT_DIR",
        DEFAULT_PROTOCOL_OUTPUT_DIR,
        context,
    )
    data_analysis_output_dir = resolve_agent_output_dir(
        "data_analysis",
        state.get("data_analysis_output_dir"),
        "DATA_ANALYSIS_OUTPUT_DIR",
        DEFAULT_DATA_ANALYSIS_OUTPUT_DIR,
        context,
    )
    optimization_output_dir = resolve_agent_output_dir(
        "protocol_optimization",
        state.get("optimization_output_dir"),
        "PROTOCOL_OPTIMIZATION_OUTPUT_DIR",
        DEFAULT_OPTIMIZATION_OUTPUT_DIR,
        context,
    )
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
        **state,
        "protocol_output_dir": protocol_output_dir,
        "data_analysis_output_dir": data_analysis_output_dir,
        "optimization_output_dir": optimization_output_dir,
        "user_id": context.user_id,
        "run_id": context.run_id,
        "trace_id": context.trace_id,
        "workspace_root": str(context.workspace_root),
        "max_features": int(state.get("max_features") or os.getenv("PROTOCOL_QUERY_MAX_FEATURES") or 40),
    }


def _orchestrator_node(model: Runnable | None):
    def run(state: OrchestratorState) -> OrchestratorState:
        if model is None:
            return _offline_orchestrator_response(state)
        system = SystemMessage(
            content=SYSTEM_PROMPT.format(
                protocol_output_dir=state.get("protocol_output_dir", DEFAULT_PROTOCOL_OUTPUT_DIR),
                data_analysis_output_dir=state.get("data_analysis_output_dir", DEFAULT_DATA_ANALYSIS_OUTPUT_DIR),
                optimization_output_dir=state.get("optimization_output_dir", DEFAULT_OPTIMIZATION_OUTPUT_DIR),
                max_features=int(state.get("max_features") or 40),
            )
        )
        response = model.invoke([system, *state.get("messages", [])])
        return {**state, "messages": [response]}

    return run


def _offline_orchestrator_response(state: OrchestratorState) -> OrchestratorState:
    question = _last_human_content(state.get("messages", []))
    route = classify_route(question)
    if route == "protocol_analysis_agent":
        result = protocol_analysis_agent.invoke(
            {
                "question": question,
                "protocol_output_dir": state.get("protocol_output_dir", ""),
                "max_features": int(state.get("max_features") or 40),
                "user_id": state.get("user_id", ""),
                "run_id": state.get("run_id", ""),
                "trace_id": state.get("trace_id", ""),
                "workspace_root": state.get("workspace_root", ""),
            }
        )
    elif route == "data_analysis_agent":
        result = data_analysis_agent.invoke(
            {
                "question": question,
                "data_analysis_output_dir": state.get("data_analysis_output_dir", ""),
                "user_id": state.get("user_id", ""),
                "run_id": state.get("run_id", ""),
                "trace_id": state.get("trace_id", ""),
                "workspace_root": state.get("workspace_root", ""),
            }
        )
    else:
        result = protocol_optimizing_agent.invoke(
            {
                "question": question,
                "protocol_output_dir": state.get("protocol_output_dir", ""),
                "data_analysis_output_dir": state.get("data_analysis_output_dir", ""),
                "optimization_output_dir": state.get("optimization_output_dir", ""),
                "user_id": state.get("user_id", ""),
                "run_id": state.get("run_id", ""),
                "trace_id": state.get("trace_id", ""),
                "workspace_root": state.get("workspace_root", ""),
            }
        )
    content = (
        f"Offline Orchestrator routed this question to `{route}`.\n\n"
        f"SubAgent result:\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)[:6000]}\n```"
    )
    return {**state, "messages": [AIMessage(content=content)]}


def classify_route(
    question: str,
) -> Literal["protocol_analysis_agent", "data_analysis_agent", "protocol_optimizing_agent"]:
    normalized = question.lower()
    optimization_terms = (
        "优化",
        "改进",
        "推荐",
        "建议",
        "proposal",
        "propose",
        "improve",
        "optimize",
        "optimization",
        "recommend",
        "best protocol",
    )
    data_terms = (
        "h5ad",
        "anndata",
        "scanpy",
        "single-cell",
        "single cell",
        "scrna",
        "snrna",
        "marker",
        "expression",
        "cell type",
        "composition",
        "maturity",
        "similarity",
        "reference atlas",
        "trajectory",
        "pseudotime",
        "基因",
        "表达",
        "细胞类型",
        "成熟度",
        "参考图谱",
        "轨迹",
    )
    if any(term in normalized for term in optimization_terms):
        return "protocol_optimizing_agent"
    if any(term in normalized for term in data_terms):
        return "data_analysis_agent"
    return "protocol_analysis_agent"


def _select_data_analysis_operation(question: str) -> str:
    normalized = question.lower()
    if any(term in normalized for term in ("de", "differential", "差异表达", "差异基因")):
        return "differential_expression"
    if any(term in normalized for term in ("gene expression", "expression", "表达量", "基因表达")):
        return "gene_expression"
    if any(term in normalized for term in ("marker", "标记基因")):
        return "marker_score"
    if any(term in normalized for term in ("composition", "cell type", "proportion", "细胞组成", "细胞类型", "比例")):
        return "cell_type_composition"
    if any(term in normalized for term in ("maturity", "成熟度")):
        return "maturity"
    if any(term in normalized for term in ("similarity", "reference", "相似度", "参考图谱")):
        return "reference_similarity"
    return "summary"


def _build_data_analysis_request(question: str, request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request.get("operation") or _select_data_analysis_operation(question))
    genes = _string_list(request.get("genes") or request.get("target_genes") or _extract_gene_symbols(question))
    return {
        "operation": operation,
        "genes": genes,
        "protocol_id": str(request.get("protocol_id") or ""),
        "dataset_id": str(request.get("dataset_id") or ""),
        "sample_id": str(request.get("sample_id") or ""),
        "condition_id": str(request.get("condition_id") or ""),
        "groupby": str(request.get("groupby") or request.get("obs_column") or ""),
        "group_a": str(request.get("group_a") or request.get("case_group") or ""),
        "group_b": str(request.get("group_b") or request.get("control_group") or ""),
        "marker_set": str(request.get("marker_set") or ""),
        "cell_type": str(request.get("cell_type") or ""),
    }


def _merge_h5ad_query_config(config: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    merged = dict(config)
    if request["genes"]:
        merged["query_genes"] = request["genes"]
    if request["groupby"]:
        merged["expression_groupby"] = request["groupby"]
        merged["groupby"] = request["groupby"]
    if request["operation"] == "differential_expression":
        merged["differential_expression"] = {
            **dict(merged.get("differential_expression", {})),
            "groupby": request["groupby"],
            "group_a": request["group_a"],
            "group_b": request["group_b"],
            "genes": request["genes"],
        }
    return merged


def _data_query_requires_execution(operation: str) -> bool:
    return operation in {"gene_expression", "differential_expression", "marker_score", "cell_type_composition"}


def _filter_data_result_rows(rows: list[dict[str, str]], request: dict[str, Any]) -> list[dict[str, str]]:
    filtered = rows
    for key in ("protocol_id", "dataset_id", "sample_id", "condition_id"):
        value = request.get(key)
        if value:
            filtered = [row for row in filtered if str(row.get(key) or "") == str(value)]
    genes = {str(gene).upper() for gene in request.get("genes", [])}
    if genes and any("gene" in row for row in filtered):
        filtered = [row for row in filtered if str(row.get("gene") or "").upper() in genes]
    for request_key, row_key in (("groupby", "groupby"), ("group_a", "group_a"), ("group_b", "group_b")):
        value = request.get(request_key)
        if value and any(row.get(row_key) for row in filtered):
            filtered = [row for row in filtered if str(row.get(row_key) or "") == str(value)]
    if request.get("marker_set") and any("marker_set" in row for row in filtered):
        filtered = [row for row in filtered if str(row.get("marker_set") or "") == str(request["marker_set"])]
    if request.get("cell_type") and any("cell_type" in row for row in filtered):
        filtered = [row for row in filtered if str(row.get("cell_type") or "") == str(request["cell_type"])]
    return filtered


def _data_tool_status(
    existing_outputs: list[str],
    selected_output: str,
    cached_rows: list[dict[str, str]],
    result_preview: list[dict[str, str]],
    request: dict[str, Any],
) -> str:
    if not existing_outputs:
        return "no_prior_data_analysis_outputs"
    if selected_output not in existing_outputs:
        return "selected_analysis_output_missing"
    if cached_rows and not result_preview and _request_has_filters(request):
        return "no_matching_cached_result"
    if not cached_rows and _data_query_requires_execution(str(request.get("operation") or "")):
        return "selected_analysis_not_computed"
    return "ok_cached_result"


def _request_has_filters(request: dict[str, Any]) -> bool:
    return any(
        bool(request.get(key))
        for key in (
            "genes",
            "protocol_id",
            "dataset_id",
            "sample_id",
            "condition_id",
            "groupby",
            "group_a",
            "group_b",
            "marker_set",
            "cell_type",
        )
    )


def _extract_gene_symbols(question: str) -> list[str]:
    candidates = re.findall(r"\b[A-Z][A-Z0-9-]{1,12}\b", question)
    stopwords = {"DE", "RNA", "H5AD", "QC", "UMAP", "PCA"}
    return [candidate for candidate in candidates if candidate not in stopwords][:50]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[,;|\s]+", value) if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _data_operation_output_file(operation: str) -> str:
    return {
        "summary": "data_analysis_protocol_summary.csv",
        "cell_type_composition": "cell_type_composition.csv",
        "marker_score": "marker_scores.csv",
        "gene_expression": "gene_expression_summary.csv",
        "differential_expression": "differential_expression.csv",
        "maturity": "maturity_scores.csv",
        "reference_similarity": "similarity_scores.csv",
    }.get(operation, "data_analysis_protocol_summary.csv")


def _data_analysis_provenance(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "protocol_id": row.get("protocol_id", ""),
            "dataset_id": row.get("dataset_id", ""),
            "sample_id": row.get("sample_id", ""),
            "condition_id": row.get("condition_id", ""),
            "h5ad_path": row.get("h5ad_path", ""),
            "cell_type_column": row.get("cell_type_column", ""),
            "batch_column": row.get("batch_column", ""),
            "lineage_column": row.get("lineage_column", ""),
        }
        for row in summaries
    ]


def _protocol_used_tools(result: dict[str, Any]) -> list[str]:
    intent = str(result.get("intent") or "")
    if intent == "compare_protocols":
        return ["find_protocols", "compare_protocols"]
    if intent:
        return [intent]
    return ["ProtocolQueryEngine.answer_question"]


def _protocol_used_features(result: dict[str, Any]) -> list[str]:
    tool_result = result.get("tool_result", {})
    differences = tool_result.get("feature_differences", []) if isinstance(tool_result, dict) else []
    return [str(row.get("feature") or "") for row in differences[:50] if row.get("feature")]


def _protocol_evidence_refs(result: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    tool_result = result.get("tool_result", {})
    if not isinstance(tool_result, dict):
        return refs
    for side in ("protocol_a", "protocol_b"):
        summary = tool_result.get(side, {})
        if isinstance(summary, dict):
            metadata = summary.get("metadata", {})
            refs.append(
                {
                    "source": side,
                    "protocol_id": summary.get("protocol_id") or metadata.get("protocol_id", ""),
                    "paper_id": metadata.get("paper_id", ""),
                    "evidence_type": "protocol_summary",
                }
            )
    for event_group in ("protocol_a_representative_events", "protocol_b_representative_events"):
        for event in tool_result.get("factor_diff", {}).get(event_group, [])[:20]:
            refs.append(
                {
                    "source": event_group,
                    "factor_type": event.get("factor_type", ""),
                    "normalized_family": event.get("normalized_family", ""),
                    "evidence_quote": event.get("evidence_quote", ""),
                }
            )
    return refs


def _protocol_confidence_caveats(result: dict[str, Any]) -> list[str]:
    caveats: list[str] = []
    if result.get("status") != "ok":
        caveats.append(f"Protocol Analysis Agent returned status={result.get('status')}.")
    tool_result = result.get("tool_result", {})
    if isinstance(tool_result, dict) and tool_result.get("audit_warnings"):
        caveats.append("Protocol-level audit warnings are available and should be considered.")
    if not _protocol_evidence_refs(result):
        caveats.append("No detailed evidence references were returned for this query path.")
    return caveats


def _data_operation_tool_name(operation: str) -> str:
    return {
        "summary": "data_analysis_summary",
        "cell_type_composition": "cell_type_composition",
        "marker_score": "marker_score_analysis",
        "gene_expression": "query_gene_expression",
        "differential_expression": "differential_expression",
        "maturity": "gene_program_maturity_score",
        "reference_similarity": "reference_similarity",
    }.get(operation, operation)


def _data_used_features(operation: str, rows: list[dict[str, str]]) -> list[str]:
    if operation in {"gene_expression", "differential_expression"}:
        return sorted({row.get("gene", "") for row in rows if row.get("gene")})[:50]
    if operation == "marker_score":
        return sorted({row.get("marker_set", "") for row in rows if row.get("marker_set")})[:50]
    if operation == "cell_type_composition":
        return sorted({row.get("cell_type", "") for row in rows if row.get("cell_type")})[:50]
    if operation == "maturity":
        return sorted({row.get("lineage", "") for row in rows if row.get("lineage")})[:50]
    return []


def _data_evidence_refs(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidence_type": "h5ad_dataset_summary",
            "protocol_id": row.get("protocol_id", ""),
            "dataset_id": row.get("dataset_id", ""),
            "sample_id": row.get("sample_id", ""),
            "condition_id": row.get("condition_id", ""),
            "h5ad_path": row.get("h5ad_path", ""),
        }
        for row in summaries
    ]


def _data_confidence_caveats(status: str, limitations: list[dict[str, Any]]) -> list[str]:
    caveats: list[str] = []
    if status != "ok":
        caveats.append(f"Data Analysis Agent returned status={status}.")
    if limitations:
        caveats.append("Data Analysis Agent reported limitations; inspect h5ad_analysis_limitations.json.")
    return caveats


def _optimization_evidence_refs(candidates: list[dict[str, str]], risk_report: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for row in candidates[:20]:
        refs.append(
            {
                "evidence_type": "optimization_candidate_feature",
                "feature": row.get("feature", ""),
                "evidence_status": row.get("evidence_status", ""),
                "confidence": row.get("confidence", ""),
            }
        )
    for rec in risk_report.get("recommendations", [])[:20] if isinstance(risk_report, dict) else []:
        refs.append(
            {
                "evidence_type": "optimization_recommendation",
                "recommendation_id": rec.get("recommendation_id", ""),
                "confidence": rec.get("confidence", ""),
                "requires_experimental_validation": rec.get("requires_experimental_validation", ""),
            }
        )
    return refs


def _optimization_confidence_caveats(
    status: str,
    candidates: list[dict[str, str]],
    risk_report: dict[str, Any],
) -> list[str]:
    caveats = ["Optimization outputs are hypotheses, not causal protocol-change conclusions."]
    if status != "optimization_ran" and status != "ok_cached_optimization_outputs":
        caveats.append(f"Protocol Optimizing Agent returned status={status}.")
    if not candidates:
        caveats.append("No optimization candidate feature table was available.")
    policy = risk_report.get("recommendation_policy") or risk_report.get("risk_policy") if isinstance(risk_report, dict) else ""
    if policy:
        caveats.append(str(policy))
    return caveats


def _route_after_orchestrator(state: OrchestratorState) -> Literal["subagents", "end"]:
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "subagents"
    return "end"


def _bind_tools(model: Runnable | None) -> Runnable | None:
    if model is None:
        return None
    bind_tools = getattr(model, "bind_tools", None)
    if callable(bind_tools):
        return bind_tools(ORCHESTRATOR_TOOLS)
    return model


def _last_human_content(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", "") == "human":
            return str(message.content)
    return ""


def _read_optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


graph = build_orchestrator_graph()
