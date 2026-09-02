from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import InjectedState, ToolNode

from protocol_optimization_agent.io import read_csv_dicts, read_json, read_jsonl
from protocol_optimization_agent.langgraph_server import _build_chat_model_from_env
from protocol_optimization_agent.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.tools.h5ad_tools import DATA_ANALYSIS_TOOL_REGISTRY


DEFAULT_PROTOCOL_OUTPUT_DIR = "runs/protocol_analysis_agent_simplified_autofeatures"
DEFAULT_DATA_ANALYSIS_OUTPUT_DIR = "runs/data_analysis"
DEFAULT_OPTIMIZATION_OUTPUT_DIR = "runs/integration_optimization"

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
"""


class OrchestratorState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    protocol_output_dir: str
    data_analysis_output_dir: str
    optimization_output_dir: str
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
        "status": "scaffolded_via_integration_optimization_agent",
        "responsibilities": [
            "join protocol features with h5ad phenotype summaries",
            "classify recommendation evidence strength",
            "generate optimization hypotheses",
            "preserve correlation-vs-causality warnings",
        ],
    },
}


def _protocol_output_dir(value: str | None = None) -> str:
    return value or os.getenv("PROTOCOL_ANALYSIS_OUTPUT_DIR") or DEFAULT_PROTOCOL_OUTPUT_DIR


def _data_analysis_output_dir(value: str | None = None) -> str:
    return value or os.getenv("DATA_ANALYSIS_OUTPUT_DIR") or DEFAULT_DATA_ANALYSIS_OUTPUT_DIR


def _optimization_output_dir(value: str | None = None) -> str:
    return value or os.getenv("PROTOCOL_OPTIMIZATION_OUTPUT_DIR") or DEFAULT_OPTIMIZATION_OUTPUT_DIR


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
) -> dict[str, Any]:
    """Delegate protocol lookup, comparison, matrix, event, evidence, or visualization questions to Protocol Analysis Agent."""

    engine = ProtocolQueryEngine(Path(_protocol_output_dir(protocol_output_dir)))
    result = engine.answer_question(question, max_features=max_features)
    return {
        "subagent": "protocol_analysis_agent",
        "status": result.get("status", "ok"),
        "question": question,
        "result": result,
        "output_dir": _protocol_output_dir(protocol_output_dir),
    }


@tool
def data_analysis_agent(
    question: str,
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
) -> dict[str, Any]:
    """Delegate h5ad, marker, composition, maturity, reference similarity, trajectory, or QC questions to Data Analysis Agent."""

    output_dir = Path(_data_analysis_output_dir(data_analysis_output_dir))
    comparison_report = read_json(output_dir / "data_analysis_comparison_report.json")
    limitations = read_json(output_dir / "h5ad_analysis_limitations.json")
    summaries = read_jsonl(output_dir / "h5ad_summary_by_protocol.jsonl")
    protocol_summary_rows = read_csv_dicts(output_dir / "data_analysis_protocol_summary.csv")
    existing_outputs = sorted(path.name for path in output_dir.glob("*") if path.is_file()) if output_dir.exists() else []
    return {
        "subagent": "data_analysis_agent",
        "status": "ok" if existing_outputs else "no_prior_data_analysis_outputs",
        "question": question,
        "output_dir": str(output_dir),
        "summary": comparison_report.get("summary", {}),
        "protocol_summaries": protocol_summary_rows[:50],
        "dataset_summaries": summaries[:50],
        "limitations": limitations.get("limitations", [])[:50],
        "tool_registry": DATA_ANALYSIS_TOOL_REGISTRY,
        "available_output_files": existing_outputs,
    }


@tool
def protocol_optimizing_agent(
    question: str,
    protocol_output_dir: Annotated[str, InjectedState("protocol_output_dir")],
    data_analysis_output_dir: Annotated[str, InjectedState("data_analysis_output_dir")],
    optimization_output_dir: Annotated[str, InjectedState("optimization_output_dir")],
) -> dict[str, Any]:
    """Delegate integrated protocol-improvement, ranking, recommendation, or optimization questions to Protocol Optimizing Agent."""

    protocol_dir = Path(_protocol_output_dir(protocol_output_dir))
    data_dir = Path(_data_analysis_output_dir(data_analysis_output_dir))
    optimization_dir = Path(_optimization_output_dir(optimization_output_dir))
    recommendation_json = _read_optional_json(optimization_dir / "proposed_improved_protocol.json")
    recommendation_md = _read_optional_text(optimization_dir / "proposed_improved_protocol.md")
    risk_report = _read_optional_json(optimization_dir / "evidence_and_risk_report.json")
    return {
        "subagent": "protocol_optimizing_agent",
        "status": "ok" if recommendation_json or recommendation_md else "no_prior_optimization_outputs",
        "question": question,
        "inputs": {
            "protocol_output_dir": str(protocol_dir),
            "data_analysis_output_dir": str(data_dir),
            "optimization_output_dir": str(optimization_dir),
            "protocol_matrix_exists": (protocol_dir / "protocol_feature_matrix.csv").exists(),
            "data_summary_exists": (data_dir / "data_analysis_protocol_summary.csv").exists(),
        },
        "recommendation_policy": "optimization hypotheses only; experimental validation required",
        "proposed_improved_protocol": recommendation_json,
        "proposed_improved_protocol_markdown": recommendation_md,
        "evidence_and_risk_report": risk_report,
    }


ORCHESTRATOR_TOOLS = [
    describe_subagents,
    protocol_analysis_agent,
    data_analysis_agent,
    protocol_optimizing_agent,
]


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
    return {
        **state,
        "protocol_output_dir": _protocol_output_dir(state.get("protocol_output_dir")),
        "data_analysis_output_dir": _data_analysis_output_dir(state.get("data_analysis_output_dir")),
        "optimization_output_dir": _optimization_output_dir(state.get("optimization_output_dir")),
        "max_features": int(state.get("max_features") or os.getenv("PROTOCOL_QUERY_MAX_FEATURES") or 40),
    }


def _orchestrator_node(model: Runnable | None):
    def run(state: OrchestratorState) -> OrchestratorState:
        if model is None:
            return _offline_orchestrator_response(state)
        system = SystemMessage(
            content=SYSTEM_PROMPT.format(
                protocol_output_dir=_protocol_output_dir(state.get("protocol_output_dir")),
                data_analysis_output_dir=_data_analysis_output_dir(state.get("data_analysis_output_dir")),
                optimization_output_dir=_optimization_output_dir(state.get("optimization_output_dir")),
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
                "protocol_output_dir": _protocol_output_dir(state.get("protocol_output_dir")),
                "max_features": int(state.get("max_features") or 40),
            }
        )
    elif route == "data_analysis_agent":
        result = data_analysis_agent.invoke(
            {
                "question": question,
                "data_analysis_output_dir": _data_analysis_output_dir(state.get("data_analysis_output_dir")),
            }
        )
    else:
        result = protocol_optimizing_agent.invoke(
            {
                "question": question,
                "protocol_output_dir": _protocol_output_dir(state.get("protocol_output_dir")),
                "data_analysis_output_dir": _data_analysis_output_dir(state.get("data_analysis_output_dir")),
                "optimization_output_dir": _optimization_output_dir(state.get("optimization_output_dir")),
            }
        )
    content = (
        f"Offline Orchestrator routed this question to `{route}`.\n\n"
        f"SubAgent result:\n```json\n{json.dumps(result, ensure_ascii=False, indent=2)[:6000]}\n```"
    )
    return {**state, "messages": [AIMessage(content=content)]}


def classify_route(question: str) -> Literal["protocol_analysis_agent", "data_analysis_agent", "protocol_optimizing_agent"]:
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
