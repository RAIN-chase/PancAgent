from __future__ import annotations

import json
import os
import re
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
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent
from protocol_optimization_agent.analysis.optimization.comparability import scientific_preconditions
from protocol_optimization_agent.analysis.optimization.experiment_design import design_experiments
from protocol_optimization_agent.analysis.optimization.readiness import build_optimization_orchestration_plan
from protocol_optimization_agent.analysis.phenotype.endpoint_comparison import compare_endpoint
from protocol_optimization_agent.analysis.phenotype.external_tools import build_external_tool_inventory
from protocol_optimization_agent.analysis.phenotype.h5ad_tools import DATA_ANALYSIS_TOOL_REGISTRY
from protocol_optimization_agent.analysis.protocol.assessment import assess_protocols
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.access_control import (
    configure_authenticated_state,
    forbid_client_paths,
    resolve_manifest_artifacts,
)
from protocol_optimization_agent.runtime.archive import (
    RunArchive,
    atomic_json,
    audited_tool,
    current_archive,
    file_hash,
    graph_error_handler,
    record_model_call,
)
from protocol_optimization_agent.runtime.context import (
    RunContext,
    append_tool_call_log,
    content_fingerprint,
    evidence_response_envelope,
    resolve_agent_output_dir,
    write_run_manifest,
)
from protocol_optimization_agent.runtime.io import read_csv_dicts, read_json, read_jsonl
from protocol_optimization_agent.runtime.llm_client import _build_chat_model_from_env
from protocol_optimization_agent.runtime.paths import optimization_code_paths
from protocol_optimization_agent.science.ingestion import (
    ingest_experiment_proposals,
    ingest_phenotype_outputs,
    ingest_protocol_outputs,
    record_comparison_claim,
)
from protocol_optimization_agent.science.reporting import build_final_report, render_report
from protocol_optimization_agent.science.state import ScientificWorldModel

DEFAULT_PROTOCOL_OUTPUT_DIR = "runs/protocol_analysis_agent_simplified_autofeatures"
DEFAULT_DATA_ANALYSIS_OUTPUT_DIR = "runs/data_analysis"
DEFAULT_OPTIMIZATION_OUTPUT_DIR = "runs/protocol_optimization"


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
@audited_tool
def protocol_analysis_agent(
    question: str,
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    max_features: Annotated[int, InjectedState("max_features")],
    request: dict[str, Any] | None = None,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Delegate protocol lookup, comparison, matrix, event, evidence, or visualization questions to Protocol Analysis Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root, project_id=project_id)
    forbid_client_paths(request or {})
    output_dir = _protocol_output_dir(protocol_output_dir, context)
    engine = ProtocolQueryEngine(Path(output_dir))
    if request and request.get("protocol_id_a") and request.get("protocol_id_b"):
        comparison = engine.compare_protocols(str(request["protocol_id_a"]), str(request["protocol_id_b"]), max_features)
        result = {"status": comparison.get("status", "not_found"), "intent": "compare_protocols", "tool_result": comparison}
    else:
        result = engine.answer_question(question, max_features=max_features)
    store = ScientificWorldModel(context, project_id)
    comparison = result.get("tool_result", {})
    protocol_ids = {str(comparison.get(key, {}).get("protocol_id") or "") for key in ("protocol_a", "protocol_b")} - {""}
    protocol_rows = [row for protocol_id in sorted(protocol_ids) for row in engine.query_matrix(filters={"protocol_id": protocol_id})]
    preconditions = scientific_preconditions(protocol_rows, [], purpose="descriptive_comparison") if protocol_ids else {}
    world_update = ingest_protocol_outputs(store, Path(output_dir), protocol_ids or None)
    record_comparison_claim(store, result, preconditions)
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
        confidence_caveats=[*_protocol_confidence_caveats(result), *(
            ["This is a descriptive comparison. Metadata are insufficient for causal or matched-phenotype inference."]
            if preconditions and not preconditions.get("scientific_ready") else []
        )],
        result_preview=result,
        output_files=output_files,
        provenance={"output_dir": output_dir},
        validation={"scientific_preconditions": preconditions},
        extra={"output_dir": output_dir, "scientific_preconditions": preconditions,
               "scientific_state": world_update},
    )


@tool
@audited_tool
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
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Delegate h5ad, marker, composition, maturity, reference similarity, trajectory, or QC questions to Data Analysis Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root, project_id=project_id)
    forbid_client_paths(request or {})
    forbid_client_paths(h5ad_config or {})
    h5ad_manifest_rows = resolve_manifest_artifacts(h5ad_manifest_rows or [], context.user_id, project_id)
    output_dir = Path(_data_analysis_output_dir(data_analysis_output_dir, context))
    structured_request = _build_data_analysis_request(question, request or {})
    selected_operation = str(structured_request["operation"])
    effective_h5ad_config = _merge_h5ad_query_config(h5ad_config or {}, structured_request)
    archive = current_archive()
    if archive:
        archive.record("effective_config", {"request": structured_request, "h5ad": effective_h5ad_config,
                       "manifest_rows": h5ad_manifest_rows})
        archive.inputs({f"h5ad_{index}": str(row["h5ad_path"]) for index, row in enumerate(h5ad_manifest_rows)
                        if row.get("h5ad_path")}, {"manifest_rows": h5ad_manifest_rows})
    ran_now = bool(force_rerun or (h5ad_manifest_rows and _data_query_requires_execution(selected_operation)))
    if ran_now:
        DataAnalysisAgent(output_dir=output_dir, h5ad_config=effective_h5ad_config).run(h5ad_manifest_rows or [])
    comparison_report = read_json(output_dir / "data_analysis_comparison_report.json")
    limitations = read_json(output_dir / "h5ad_analysis_limitations.json")
    validation = read_json(output_dir / "data_analysis_validation.json")
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
    world_update = ingest_phenotype_outputs(ScientificWorldModel(context, project_id), output_dir)
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
        confidence_caveats=_data_confidence_caveats(
            status, limitations.get("limitations", []), validation, selected_operation
        ),
        limitations=limitations.get("limitations", [])[:50],
        provenance=_data_analysis_provenance(summaries[:50]),
        result_preview=result_preview,
        output_files=output_files,
        extra={
            "structured_request": structured_request,
            "execution_mode": "executed" if ran_now else "cached_result_read" if result_preview else "unavailable",
            "output_dir": str(output_dir),
            "summary": comparison_report.get("summary", {}),
            "validation": validation,
            "statistical_applicability": validation.get("statistical_applicability", {}),
            "protocol_summaries": protocol_summary_rows[:50],
            "dataset_summaries": summaries[:50],
            "tool_registry": tool_registry.get("tools", DATA_ANALYSIS_TOOL_REGISTRY),
            "external_tool_inventory": tool_registry.get(
                "external_tool_inventory", build_external_tool_inventory(Path.cwd())
            ),
            "callable_tools": tool_registry.get("callable_tools", []),
            "available_output_files": existing_outputs,
            "scientific_state": world_update,
            "phenotype_ir": read_json(output_dir / "phenotype_ir.json"),
            "phenotype_interpretation": read_json(output_dir / "phenotype_interpretation.json"),
        },
    )


@tool
@audited_tool
def protocol_optimizing_agent(
    question: str,
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    optimization_output_dir: Annotated[str, InjectedState("optimization_output_dir")],
    objective: dict[str, Any] | None = None,
    new_protocol_features: dict[str, Any] | None = None,
    force_optimization_rerun: bool = False,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Delegate integrated protocol-improvement, ranking, recommendation, or optimization questions to Protocol Optimizing Agent."""

    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root, project_id=project_id)
    forbid_client_paths(objective or {})
    protocol_dir = Path(_protocol_output_dir(protocol_output_dir, context))
    data_dir = Path(_data_analysis_output_dir(data_analysis_output_dir, context))
    optimization_dir = Path(_optimization_output_dir(optimization_output_dir, context))
    archive = current_archive()
    if archive:
        archive.record("effective_config", {"objective": objective or {}, "new_protocol_features": new_protocol_features or {}})
    store = ScientificWorldModel(context, project_id)
    orchestration_plan = build_optimization_orchestration_plan(
        protocol_dir,
        data_dir,
        objective=objective or {},
        new_protocol_features=new_protocol_features or {},
        force_optimization_rerun=force_optimization_rerun,
    )
    ingest_protocol_outputs(store, protocol_dir)
    ingest_phenotype_outputs(store, data_dir)
    scientific_context = store.reasoning_context()
    cache_key = _optimization_cache_key(protocol_dir, data_dir, objective or {}, new_protocol_features or {}, store.feedback_fingerprint())
    cache_meta = _read_optional_json(optimization_dir / "optimization_cache_metadata.json")
    has_cached_optimization = (
        (optimization_dir / "proposed_improved_protocol.json").exists()
        and (optimization_dir / "next_experiment_candidates.json").exists()
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
            scientific_context=scientific_context,
        )
        _write_optimization_cache_metadata(optimization_dir, cache_key, objective or {}, new_protocol_features or {})
    current_outputs = bool(should_run or (has_cached_optimization and orchestration_plan["can_run_protocol_optimizing_agent"]))
    recommendation_json = _read_optional_json(optimization_dir / "proposed_improved_protocol.json") if current_outputs else {}
    recommendation_md = _read_optional_text(optimization_dir / "proposed_improved_protocol.md") if current_outputs else ""
    risk_report = _read_optional_json(optimization_dir / "evidence_and_risk_report.json") if current_outputs else {}
    candidates = read_csv_dicts(optimization_dir / "optimization_candidate_features.csv") if current_outputs else []
    prediction = _read_optional_json(optimization_dir / "counterfactual_protocol_predictions.json") if current_outputs else {}
    next_experiments = _read_optional_json(optimization_dir / "next_experiment_candidates.json") if current_outputs else {}
    world_update = ingest_experiment_proposals(store, optimization_dir) if next_experiments else {"path": str(store.path), "status": "not_generated"}
    if next_experiments:
        next_experiments = read_json(optimization_dir / "next_experiment_candidates.json")
        recommendation_json = read_json(optimization_dir / "proposed_improved_protocol.json")
        recommendation_md = _read_optional_text(optimization_dir / "proposed_improved_protocol.md")
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
        "next_experiment_candidates": str(optimization_dir / "next_experiment_candidates.json"),
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
        used_tools=_optimization_used_tools(bool(should_run), bool(has_cached_optimization and current_outputs)),
        used_features=[str(row.get("feature") or "") for row in candidates[:20] if row.get("feature")],
        evidence_refs=_optimization_evidence_refs(candidates[:20], risk_report),
        confidence_caveats=_optimization_confidence_caveats(status, candidates[:20], risk_report),
        limitations=["Optimization recommendations are hypotheses and require experimental validation."],
        validation={"upstream_status": orchestration_plan["upstream_status"]},
        provenance={"inputs": inputs, "orchestration_plan": orchestration_plan},
        result_preview={
            "proposed_improved_protocol": recommendation_json,
            "proposed_improved_protocol_markdown": recommendation_md,
            "evidence_and_risk_report": risk_report,
            "top_candidate_features": candidates[:20],
            "counterfactual_prediction": prediction,
            "cache_key": cache_key,
            "cache_status": "hit" if has_cached_optimization and not should_run else "miss_or_rerun",
            "next_experiment_candidates": next_experiments,
            "analysis_result": read_json(optimization_dir / "analysis_result.json") if next_experiments else {},
        },
        output_files=output_files,
        extra={
            "orchestration_plan": orchestration_plan,
            "inputs": inputs,
            "recommendation_policy": "optimization hypotheses only; experimental validation required",
            "scientific_state": world_update,
        },
    )


@tool
@audited_tool
def inspect_scientific_state(
    limit: int = 20,
    user_id: Annotated[str, InjectedState("user_id")] = "",
    run_id: Annotated[str, InjectedState("run_id")] = "",
    trace_id: Annotated[str, InjectedState("trace_id")] = "",
    workspace_root: Annotated[str, InjectedState("workspace_root")] = "",
    project_id: Annotated[str, InjectedState("project_id")] = "default",
) -> dict[str, Any]:
    """Read versioned scientific evidence, hypotheses, experimental outcomes, and conflicts."""
    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id, workspace_root=workspace_root, project_id=project_id)
    store = ScientificWorldModel(context, project_id)
    result = store.snapshot(limit)
    append_tool_call_log(context, subagent="orchestrator", operation="inspect_scientific_state", status="ok",
                        question="Inspect scientific state", output_files={"scientific_state": str(store.path)})
    return result


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
    action = get_action(action_id)
    context = RunContext.from_values(user_id=user_id, run_id=run_id, trace_id=trace_id,
                                    workspace_root=workspace_root, project_id=project_id)
    if action.availability != "available":
        return evidence_response_envelope(context=context, subagent=action.agent, status=action.availability,
            question=str(arguments.get("question", "")), selected_operation=action_id, result_preview={},
            limitations=["This registered capability has no available execution implementation."],
            validation={"available": False}, extra={"action_id": action_id, "executed": False})
    validate_action_arguments(action_id, arguments)
    injected = {"user_id": user_id, "run_id": run_id, "trace_id": trace_id,
                "workspace_root": str(workspace_root), "project_id": project_id}
    context = RunContext.from_values(**injected)
    if action_id in {"protocol.assess", "protocol.phenotype_compare", "hypothesis.generate"}:
        if action_id == "protocol.assess":
            result = assess_protocols(ProtocolQueryEngine(Path(_protocol_output_dir(protocol_output_dir, context))),
                                      arguments.get("protocol_ids", []))
        elif action_id == "protocol.phenotype_compare":
            result = compare_endpoint(read_json(Path(_data_analysis_output_dir(data_analysis_output_dir, context)) / "phenotype_ir.json"),
                                      arguments["protocol_ids"], arguments["endpoint"])
        else:
            prior = ScientificWorldModel(context, project_id).reasoning_context()["hypotheses"]
            result = design_experiments(arguments["edits"], arguments["objective"],
                                        {"scientific_ready": False, "status": "hypothesis_generation_only"}, prior)
            result["status"] = "ok" if result["hypotheses"] else "evidence_insufficient"
        evidence = ([{"protocol_id": row["protocol_id"], "paper_id": (row.get("matrix") or {}).get("paper_id"),
                      "events": row["events"], "structured_steps": row["structured_steps"]}
                     for row in result.get("protocol_assessments", [])]
                    + [ref for row in result.get("observations", []) for ref in row["metric"].get("evidence_refs", [])]
                    + [ref for edit in arguments.get("edits", []) for ref in edit.get("evidence_refs", [])])
        return evidence_response_envelope(context=context, subagent=action.agent, status=result["status"],
            question=str(arguments.get("question", "")), selected_operation=action_id, result_preview=result,
            used_tools=[action.implementation], evidence_refs=evidence,
            validation={"evidence_gaps": result.get("evidence_gaps", [])},
            confidence_caveats=["Descriptive assessment or unvalidated hypotheses, not causal effects."],
            extra={"action_id": action_id})
    if action_id == "scientific.state":
        snapshot = inspect_scientific_state.invoke({**arguments, **injected})
        snapshot["conflicts"] = [record for kind in ("claim", "hypothesis") for record in snapshot["records"].get(kind, [])
                                 if record.get("evidence_against") or record.get("opposing_evidence_refs")]
        return evidence_response_envelope(context=context, subagent=action.agent, status="ok", question="Scientific state",
            selected_operation=action_id, result_preview=snapshot, used_tools=[action.implementation])
    if action.agent == "PA":
        return protocol_analysis_agent.invoke({"question": arguments.get("question", "Compare protocols"),
            "request": arguments if action_id == "protocol.compare" else None,
            "protocol_output_dir": protocol_output_dir, "max_features": max_features, **injected})
    if action.agent == "DA":
        return data_analysis_agent.invoke({**arguments, "question": arguments.get("question", action.id),
            "request": {**arguments.get("request", {}), "operation": action.operation},
            "force_rerun": bool(action_id == "data.assess" and arguments.get("h5ad_manifest_rows")),
            "data_analysis_output_dir": data_analysis_output_dir, **injected})
    return protocol_optimizing_agent.invoke({**arguments, "question": arguments.get("question", "Propose next experiments"),
        "protocol_output_dir": protocol_output_dir, "data_analysis_output_dir": data_analysis_output_dir,
        "optimization_output_dir": optimization_output_dir, **injected})


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
    feedback_fingerprint: str = "",
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
            "phenotype_ir": str(data_dir / "phenotype_ir.json"),
            "data_validation": str(data_dir / "data_analysis_validation.json"),
            "phenotype_interpretation": str(data_dir / "phenotype_interpretation.json"),
            "cell_type_composition": str(data_dir / "cell_type_composition.csv"),
            "gene_program_scores": str(data_dir / "gene_program_scores.csv"),
            "regulatory_program_scores": str(data_dir / "regulatory_program_scores.csv"),
            "trajectory_scores": str(data_dir / "trajectory_scores.csv"),
            "h5ad_dataset_metadata": str(data_dir / "h5ad_summary_by_protocol.jsonl"),
            "normalized_events": str(protocol_dir / "normalized_factor_events.jsonl"),
            "filtered_events": str(protocol_dir / "filtered_protocol_factor_events.csv"),
            **optimization_code_paths(),
        },
        extra={
            "objective": objective,
            "new_protocol_features": new_protocol_features,
            "output_contract": "protocol-optimization-scientific-v3",
            "experimental_feedback": feedback_fingerprint,
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
    protocol_output_dir = resolve_agent_output_dir(
        "protocol_analysis",
        configured.get("protocol_output_dir"),
        "PROTOCOL_ANALYSIS_OUTPUT_DIR",
        DEFAULT_PROTOCOL_OUTPUT_DIR,
        context,
    )
    data_analysis_output_dir = resolve_agent_output_dir(
        "data_analysis",
        configured.get("data_analysis_output_dir"),
        "DATA_ANALYSIS_OUTPUT_DIR",
        DEFAULT_DATA_ANALYSIS_OUTPUT_DIR,
        context,
    )
    optimization_output_dir = resolve_agent_output_dir(
        "protocol_optimization",
        configured.get("optimization_output_dir"),
        "PROTOCOL_OPTIMIZATION_OUTPUT_DIR",
        DEFAULT_OPTIMIZATION_OUTPUT_DIR,
        context,
    )
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


def _build_data_analysis_request(question: str, request: dict[str, Any]) -> dict[str, Any]:
    operation = str(request.get("operation") or select_action(question).operation or "summary")
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
    merged["requested_tasks"] = [request["operation"]] if request["operation"] != "summary" else ["cell_type_composition"]
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
    if result_preview and any(row.get("status") and row["status"] != "ok" for row in result_preview):
        return "partial" if any(row.get("status") == "ok" for row in result_preview) else "selected_analysis_not_computed"
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
    refs: list[dict[str, Any]] = [{"protocol_id": row.get("protocol_id"), "paper_id": row.get("paper_id"),
                                  "evidence_type": "protocol_lookup"} for row in result.get("results", [])]
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
    refs.extend(tool_result.get("event_step_diff", {}).get("source_refs", []))
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
            "study_id": study_id,
            "protocol_id": row.get("protocol_id", ""),
            "dataset_id": row.get("dataset_id", ""),
            "sample_id": row.get("sample_id", ""),
            "condition_id": row.get("condition_id", ""),
            "h5ad_path": row.get("h5ad_path", ""),
        }
        for row in summaries
        for study_id in ([row["study_id"]] if row.get("study_id") else
                         row.get("design_metadata", {}).get("fields", {}).get("study", {}).get("values", []) or [""])
    ]


def _data_confidence_caveats(
    status: str, limitations: list[dict[str, Any]], validation: dict[str, Any] | None = None, operation: str = "summary"
) -> list[str]:
    caveats: list[str] = []
    if status != "ok":
        caveats.append(f"Data Analysis Agent returned status={status}.")
    if limitations:
        caveats.append("Data Analysis Agent reported limitations; inspect h5ad_analysis_limitations.json.")
    if validation and validation.get("task_validity", {}).get(operation, {}).get("valid") is not True:
        caveats.append("The selected task has no valid numeric result; unrelated metrics do not determine its validity.")
    if validation:
        caveats.append("Any LLM-authored quality score is qualitative commentary, not a statistical confidence probability.")
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


def _read_optional_json(path: Path) -> dict[str, Any]:
    return read_json(path) if path.exists() else {}


def _read_optional_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


graph = build_orchestrator_graph()
