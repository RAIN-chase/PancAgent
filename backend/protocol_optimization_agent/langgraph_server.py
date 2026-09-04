from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode

from protocol_optimization_agent.query_engine import ProtocolQueryEngine

DEFAULT_OUTPUT_DIR = "runs/protocol_analysis_agent_simplified_autofeatures"

SYSTEM_PROMPT = """You are ProtocolOptimizationAgent, a tool-using scientific protocol analysis assistant.

You answer only from the generated Protocol Analysis Agent outputs exposed by tools. Do not re-read papers, invent protocol facts, or use memory as evidence. For protocol comparison questions, first resolve protocol names with find_protocols, then call compare_protocols, and call evidence tools when a claim needs support.

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


def _output_dir(value: str | None = None) -> str:
    return value or os.getenv("PROTOCOL_ANALYSIS_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR


def _engine(output_dir: str | None = None) -> ProtocolQueryEngine:
    return ProtocolQueryEngine(Path(_output_dir(output_dir)))


@tool
def find_protocols(query: str, output_dir: Annotated[str, InjectedState("output_dir")]) -> list[dict[str, Any]]:
    """Find protocols by natural-language query, author/year, paper id, dataset id, or protocol name."""

    return _engine(output_dir).find_protocols(query)


@tool
def compare_protocols(
    protocol_id_a: str,
    protocol_id_b: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
    max_features: Annotated[int, InjectedState("max_features")],
) -> dict[str, Any]:
    """Compare two protocols using feature matrix, normalized events, semantic annotations, and provenance."""

    return _engine(output_dir).compare_protocols(protocol_id_a, protocol_id_b, max_features=max_features)


@tool
def get_protocol_events(
    protocol_id: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return normalized factor/action events for one protocol."""

    return _engine(output_dir).get_protocol_events(protocol_id, limit=limit)


@tool
def get_feature_evidence(
    protocol_id: str,
    feature: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
) -> list[dict[str, Any]]:
    """Return evidence/provenance records for one protocol feature."""

    return _engine(output_dir).get_evidence_for_feature(protocol_id, feature)


@tool
def query_matrix(
    output_dir: Annotated[str, InjectedState("output_dir")],
    filters: dict[str, Any] | None = None,
    columns: list[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Query rows and columns from protocol_feature_matrix.csv."""

    return _engine(output_dir).query_matrix(filters=filters or {}, columns=columns, limit=limit)


@tool
def explain_cell(
    protocol_id: str,
    feature: str,
    output_dir: Annotated[str, InjectedState("output_dir")],
) -> dict[str, Any]:
    """Explain one matrix cell with value and full provenance."""

    return _engine(output_dir).explain_cell(protocol_id, feature)


@tool
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
def propose_feature_specs_context(
    output_dir: Annotated[str, InjectedState("output_dir")],
    protocol_ids: list[str] | None = None,
    limit_events: int = 200,
) -> dict[str, Any]:
    """Return source-grounded context for proposing FeatureSpec candidates; does not create final matrix values."""

    return _engine(output_dir).propose_feature_specs_context(protocol_ids=protocol_ids or [], limit_events=limit_events)


TOOLS = [
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
    graph.add_node("tools", ToolNode(TOOLS))

    graph.add_edge(START, "configure")
    graph.add_edge("configure", "agent")
    graph.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", "end": END})
    graph.add_edge("tools", "agent")
    return graph.compile() if compile_graph else graph


def _configure_node(state: ProtocolChatState) -> ProtocolChatState:
    return {
        **state,
        "output_dir": _output_dir(state.get("output_dir")),
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
        response = model.invoke([system, *state.get("messages", [])])
        return {**state, "messages": [response]}

    return run


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


def _build_chat_model_from_env() -> Runnable | None:
    provider = os.getenv("PROTOCOL_CHAT_LLM_PROVIDER")
    if not provider and os.getenv("DEEPSEEK_API_KEY"):
        provider = "deepseek"
    if not provider and os.getenv("OPENAI_API_KEY"):
        provider = "openai"
    provider = (provider or "").lower()
    if not provider:
        return None

    if provider not in {"openai", "openai_compatible", "deepseek"}:
        raise ValueError(f"Unsupported PROTOCOL_CHAT_LLM_PROVIDER: {provider}")

    from langchain_openai import ChatOpenAI

    api_key_env = os.getenv("PROTOCOL_CHAT_API_KEY_ENV") or (
        "DEEPSEEK_API_KEY" if provider == "deepseek" else "OPENAI_API_KEY"
    )
    api_key = os.getenv(api_key_env)
    if not api_key:
        raise RuntimeError(f"Missing chat LLM API key environment variable: {api_key_env}")

    model = os.getenv("PROTOCOL_CHAT_MODEL") or ("deepseek-chat" if provider == "deepseek" else "gpt-4o-mini")
    base_url = os.getenv("PROTOCOL_CHAT_BASE_URL") or ("https://api.deepseek.com" if provider == "deepseek" else None)
    temperature = float(os.getenv("PROTOCOL_CHAT_TEMPERATURE") or 0)
    timeout = float(os.getenv("PROTOCOL_CHAT_TIMEOUT_SECONDS") or 60)

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": api_key,
        "temperature": temperature,
        "timeout": timeout,
        "streaming": True,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


graph = build_protocol_chat_graph()
