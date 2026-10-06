from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode

from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.access_control import authenticated_mode, configure_authenticated_state
from protocol_optimization_agent.runtime.archive import (
    RunArchive,
    audited_operation,
    graph_error_handler,
    record_model_call,
)
from protocol_optimization_agent.runtime.context import RunContext, authorize_output_path, write_run_manifest
from protocol_optimization_agent.runtime.llm_client import _build_chat_model_from_env

DEFAULT_OUTPUT_DIR = "runs/protocol_analysis_agent_simplified_autofeatures"

SYSTEM_PROMPT = """You are ProtocolOptimizationAgent, a tool-using scientific protocol analysis assistant.

You answer only from the generated Protocol Analysis Agent outputs exposed by tools. Do not re-read papers, invent protocol facts, or use memory as evidence. For protocol collection, inventory, count, or "how many protocols" questions, call get_protocol_collection_summary. For protocol comparison questions, first resolve protocol names with find_protocols, then call compare_protocols, and call evidence tools when a claim needs support.

When answering, summarize the tool results in clear scientific language and include the relevant protocol ids, feature names, evidence/provenance availability, confidence or audit caveats when present. Correlation or protocol-design differences must not be presented as causal claims.

The LangGraph UI will display tool calls. You may briefly state what tool you are using, but do not reveal private chain-of-thought. Provide concise evidence-grounded reasoning summaries instead.

Runtime context:
- output_dir: {output_dir}
- max_features: {max_features}
"""


class ProtocolChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    output_dir: str
    max_features: int
    user_id: str
    project_id: str
    workspace_root: str
    run_id: str
    output_artifact_id: str
    trace_id: str
    parent_run_id: str


def _output_dir(value: str | None = None) -> str:
    return value or os.getenv("PROTOCOL_ANALYSIS_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR


def _engine(output_dir: str | None = None) -> ProtocolQueryEngine:
    return ProtocolQueryEngine(Path(_output_dir(output_dir)))


@tool
@audited_operation
def get_protocol_collection_summary(output_dir: Annotated[str, InjectedState("output_dir")]) -> dict[str, Any]:
    """Summarize the configured protocol analysis collection: protocol count, comparable count, features, papers, and datasets."""

    return _engine(output_dir).get_protocol_collection_summary()


@tool
@audited_operation
def find_protocols(query: str, output_dir: Annotated[str, InjectedState("output_dir")]) -> list[dict[str, Any]]:
    """Find protocols by natural-language query, author/year, paper id, dataset id, or protocol name."""

    return _engine(output_dir).find_protocols(query)


@tool
@audited_operation
def compare_protocols(
    protocol_id_a: str,
    protocol_id_b: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
    max_features: Annotated[int, InjectedState("max_features")],
) -> dict[str, Any]:
    """Compare two protocols using feature matrix, normalized events, semantic annotations, and provenance."""

    return _engine(output_dir).compare_protocols(protocol_id_a, protocol_id_b, max_features=max_features)


@tool
@audited_operation
def get_protocol_events(
    protocol_id: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return normalized factor/action events for one protocol."""

    return _engine(output_dir).get_protocol_events(protocol_id, limit=limit)


@tool
@audited_operation
def get_feature_evidence(
    protocol_id: str,
    feature: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
) -> list[dict[str, Any]]:
    """Return evidence/provenance records for one protocol feature."""

    return _engine(output_dir).get_evidence_for_feature(protocol_id, feature)


@tool
@audited_operation
def query_matrix(
    output_dir: Annotated[str, InjectedState("output_dir")],
    filters: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query rows and columns from protocol_feature_matrix.csv."""

    return _engine(output_dir).query_matrix(filters=filters or {}, columns=columns, limit=limit)


@tool
@audited_operation
def explain_cell(
    protocol_id: str,
    feature: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
) -> dict[str, Any]:
    """Explain one matrix cell with value and full provenance."""

    return _engine(output_dir).explain_cell(protocol_id, feature)


@tool
@audited_operation
def plot_protocol_feature_heatmap(
    output_dir: Annotated[str, InjectedState("output_dir")],
    protocol_ids: list[str] | None = None,
    features: list[str] | None = None,
    top_n_features: int = 50,
    normalize: str = "minmax",
    filename: str = "",
    cluster_rows: bool = False,
    cluster_features: bool = False,
) -> dict[str, Any]:
    """Create an SVG heatmap from protocol_feature_matrix.csv, optionally clustered by protocol and feature similarity."""

    return _engine(output_dir).plot_feature_heatmap(
        protocol_ids=protocol_ids or [],
        features=features or [],
        top_n_features=top_n_features,
        normalize=normalize,
        filename=filename,
        cluster_rows=cluster_rows,
        cluster_features=cluster_features,
    )


@tool
@audited_operation
def plot_layered_protocol_feature_heatmaps(
    output_dir: Annotated[str, InjectedState("output_dir")],
    protocol_ids: list[str] | None = None,
    layers: list[str] | None = None,
    top_n_features_per_layer: int = 40,
    normalize: str = "minmax",
    cluster_rows: bool = True,
    cluster_features: bool = True,
    include_sequence_layer: bool = False,
    filename_prefix: str = "protocol_feature_layer",
) -> dict[str, Any]:
    """Create grouped SVG heatmaps for semantic, workflow, reagent, normalized-family, and timing/exposure features."""

    return _engine(output_dir).plot_layered_feature_heatmaps(
        protocol_ids=protocol_ids or [],
        layers=layers or [],
        top_n_features_per_layer=top_n_features_per_layer,
        normalize=normalize,
        cluster_rows=cluster_rows,
        cluster_features=cluster_features,
        include_sequence_layer=include_sequence_layer,
        filename_prefix=filename_prefix,
    )


@tool
@audited_operation
def propose_feature_specs_context(
    output_dir: Annotated[str, InjectedState("output_dir")],
    protocol_ids: list[str] | None = None,
    limit_events: int = 200,
) -> dict[str, Any]:
    """Return source-grounded context for proposing FeatureSpec candidates; does not create final matrix values."""

    return _engine(output_dir).propose_feature_specs_context(protocol_ids=protocol_ids or [], limit_events=limit_events)


TOOLS = [
    get_protocol_collection_summary,
    find_protocols,
    compare_protocols,
    get_protocol_events,
    get_feature_evidence,
    query_matrix,
    explain_cell,
    plot_protocol_feature_heatmap,
    plot_layered_protocol_feature_heatmaps,
    propose_feature_specs_context,
]


def build_protocol_chat_graph(chat_model: Runnable | None = None, compile_graph: bool = True) -> Any:
    bound_model = _bind_tools(chat_model or _build_chat_model_from_env())
    graph = StateGraph(ProtocolChatState)
    graph.add_node("configure", _configure_node)
    graph.add_node("agent", _agent_node(bound_model))
    graph.add_node("tools", _tools_node)
    graph.add_node("finalize", _finalize_node)

    graph.add_edge(START, "configure")
    graph.add_edge("configure", "agent")
    graph.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", "end": "finalize"})
    graph.add_edge("tools", "agent")
    graph.add_edge("finalize", END)
    return graph.compile().with_config(callbacks=[graph_error_handler()]) if compile_graph else graph


def _configure_node(state: ProtocolChatState, config: RunnableConfig) -> ProtocolChatState:
    state = configure_authenticated_state(state, config)
    workspace = state.get("workspace_root")
    if not workspace and not authenticated_mode() and state.get("output_dir"):
        workspace = str(Path(state["output_dir"]).resolve().parent)
    context = RunContext.from_values(user_id=state.get("user_id"), workspace_root=workspace,
                                     run_id=state.get("run_id"), project_id=state.get("project_id", "default"))
    parent = state.get("parent_run_id", "")
    if context.manifest_path().exists():
        parent = context.run_id
        context = RunContext.from_values(user_id=context.user_id, workspace_root=context.workspace_root,
                                         run_id=f"run_{uuid4().hex[:16]}", project_id=context.project_id)
    archive = RunArchive(context)
    archive.start({"entrypoint": "protocol_chat", "max_features": int(state.get("max_features") or 40)},
                  parent_run_id=parent, thread_id=str(config.get("configurable", {}).get("thread_id", "")))
    try:
        source = authorize_output_path(Path(_output_dir(state.get("output_dir"))), context)
        output_dir = str(archive.seed(source, "protocol_analysis"))
        write_run_manifest(context, output_dirs={"protocol_analysis": output_dir})
    except BaseException as exc:
        archive.record("error", {"type": type(exc).__name__, "message": str(exc)})
        archive.finish({"error": str(exc)}, status="failed")
        raise
    return {
        **state,
        "output_dir": output_dir,
        "run_id": context.run_id,
        "trace_id": context.trace_id,
        "user_id": context.user_id,
        "project_id": context.project_id,
        "workspace_root": str(context.workspace_root),
        "parent_run_id": parent,
        "max_features": int(state.get("max_features") or os.getenv("PROTOCOL_QUERY_MAX_FEATURES") or 40),
    }


def _agent_node(model: Runnable | None):
    def run(state: ProtocolChatState) -> ProtocolChatState:
        if model is None:
            return {
                **state,
                "messages": [
                    AIMessage(
                        content=(
                            "Chat LLM is not configured. Set PROTOCOL_CHAT_LLM_PROVIDER, "
                            "PROTOCOL_CHAT_MODEL, PROTOCOL_CHAT_API_KEY_ENV, and the referenced API key environment variable."
                        )
                    )
                ],
            }
        system = SystemMessage(
            content=SYSTEM_PROMPT.format(
                output_dir=_output_dir(state.get("output_dir")),
                max_features=int(state.get("max_features") or 40),
            )
        )
        archive = _archive(state)
        messages = [system, *state.get("messages", [])]
        started = time.perf_counter()
        try:
            with archive.scope(), archive.operation("protocol_chat.model", {}, kind="model"):
                response = model.invoke(messages)
                record_model_call(model, messages, response, (time.perf_counter() - started) * 1000)
        except BaseException as exc:
            with archive.scope():
                record_model_call(model, messages, None, (time.perf_counter() - started) * 1000, str(exc))
            archive.finish({"error": str(exc)}, status="failed")
            raise
        return {**state, "messages": [response]}

    return run


def _archive(state: ProtocolChatState) -> RunArchive:
    return RunArchive(RunContext.from_values(**{key: state.get(key) for key in
                    ("user_id", "run_id", "trace_id", "workspace_root", "project_id")}))


def _tools_node(state: ProtocolChatState, config: RunnableConfig) -> dict[str, Any]:
    archive = _archive(state)
    try:
        with archive.scope(), archive.operation("protocol_chat.dispatch", {"tool_calls": state["messages"][-1].tool_calls}) as call_id:
            result = ToolNode(TOOLS).invoke(state, config)
            archive.result(call_id, result)
            for message in result.get("messages", []):
                if getattr(message, "status", "") == "error":
                    archive.record("error", {"type": "ToolDispatchError", "tool": message.name,
                                              "message": message.content, "call_id": call_id})
            return result
    except BaseException as exc:
        archive.record("error", {"type": type(exc).__name__, "message": str(exc)})
        archive.finish({"error": str(exc)}, status="failed")
        raise


def _finalize_node(state: ProtocolChatState) -> dict[str, Any]:
    result = state["messages"][-1].model_dump(mode="json")
    result.update({"run_id": state["run_id"], "trace_id": state["trace_id"]})
    _archive(state).finish(result)
    return {}


def _route_after_agent(state: ProtocolChatState) -> Literal["tools", "end"]:
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "end"


def _bind_tools(model: Runnable | None) -> Runnable | None:
    if model is None:
        return None
    bind_tools = getattr(model, "bind_tools", None)
    if callable(bind_tools):
        return bind_tools(TOOLS)
    return model




graph = build_protocol_chat_graph()
