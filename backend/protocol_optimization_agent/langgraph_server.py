from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from protocol_optimization_agent.query_engine import ProtocolQueryEngine


DEFAULT_OUTPUT_DIR = "runs/protocol_analysis_agent_simplified_autofeatures"


class ProtocolChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    output_dir: str
    max_features: int
    intent: str
    query_parts: list[str]
    compared: bool
    ready_to_answer: bool


def _output_dir(value: str | None = None) -> str:
    return value or os.getenv("PROTOCOL_ANALYSIS_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR


def _engine(output_dir: str | None = None) -> ProtocolQueryEngine:
    return ProtocolQueryEngine(Path(_output_dir(output_dir)))


@tool
def find_protocols(query: str, output_dir: str = "") -> list[dict[str, Any]]:
    """Find protocols by natural-language query, author/year, paper id, dataset id, or protocol name."""

    return _engine(output_dir).find_protocols(query)


@tool
def compare_protocols(protocol_id_a: str, protocol_id_b: str, output_dir: str = "", max_features: int = 40) -> dict[str, Any]:
    """Compare two protocols using the generated feature matrix, normalized events, semantic annotations, and provenance."""

    return _engine(output_dir).compare_protocols(protocol_id_a, protocol_id_b, max_features=max_features)


@tool
def get_protocol_events(protocol_id: str, output_dir: str = "", limit: int = 100) -> list[dict[str, Any]]:
    """Return normalized factor/action events for one protocol."""

    return _engine(output_dir).get_protocol_events(protocol_id, limit=limit)


@tool
def get_feature_evidence(protocol_id: str, feature: str, output_dir: str = "") -> list[dict[str, Any]]:
    """Return evidence/provenance records for one protocol feature."""

    return _engine(output_dir).get_evidence_for_feature(protocol_id, feature)


@tool
def query_matrix(filters: dict[str, Any] | None = None, columns: list[str] | None = None, output_dir: str = "", limit: int = 50) -> list[dict[str, Any]]:
    """Query rows and columns from protocol_feature_matrix.csv."""

    return _engine(output_dir).query_matrix(filters=filters or {}, columns=columns, limit=limit)


@tool
def explain_cell(protocol_id: str, feature: str, output_dir: str = "") -> dict[str, Any]:
    """Explain one matrix cell with value and provenance."""

    return _engine(output_dir).explain_cell(protocol_id, feature)


@tool
def propose_feature_specs_context(protocol_ids: list[str] | None = None, output_dir: str = "", limit_events: int = 200) -> dict[str, Any]:
    """Return source-grounded context for proposing FeatureSpec candidates; this tool does not create final matrix values."""

    return _engine(output_dir).propose_feature_specs_context(protocol_ids=protocol_ids or [], limit_events=limit_events)


TOOLS = [
    find_protocols,
    compare_protocols,
    get_protocol_events,
    get_feature_evidence,
    query_matrix,
    explain_cell,
    propose_feature_specs_context,
]


def build_protocol_chat_graph(compile_graph: bool = True) -> Any:
    graph = StateGraph(ProtocolChatState)
    graph.add_node("configure", _configure_node)
    graph.add_node("planner", _planner_node)
    graph.add_node("tools", ToolNode(TOOLS))
    graph.add_node("answer", _answer_node)

    graph.add_edge(START, "configure")
    graph.add_edge("configure", "planner")
    graph.add_conditional_edges("planner", _route_after_planner, {"tools": "tools", "answer": "answer"})
    graph.add_edge("tools", "planner")
    graph.add_edge("answer", END)
    return graph.compile() if compile_graph else graph


def _configure_node(state: ProtocolChatState) -> ProtocolChatState:
    return {
        **state,
        "output_dir": _output_dir(state.get("output_dir")),
        "max_features": int(state.get("max_features") or os.getenv("PROTOCOL_QUERY_MAX_FEATURES") or 40),
    }


def _planner_node(state: ProtocolChatState) -> ProtocolChatState:
    if state.get("ready_to_answer"):
        return state
    messages = state.get("messages", [])
    question = _last_human_text(messages)
    output_dir = _output_dir(state.get("output_dir"))

    if state.get("intent") == "compare_protocols" and not state.get("compared"):
        protocol_ids = _protocol_ids_from_recent_find_tools(messages)
        if len(protocol_ids) >= 2:
            return {
                **state,
                "compared": True,
                "messages": [
                    AIMessage(
                        content="I found the requested protocols and will compare their structured feature profiles.",
                        tool_calls=[
                            {
                                "name": "compare_protocols",
                                "args": {
                                    "protocol_id_a": protocol_ids[0],
                                    "protocol_id_b": protocol_ids[1],
                                    "output_dir": output_dir,
                                    "max_features": int(state.get("max_features") or 40),
                                },
                                "id": _tool_call_id(),
                            }
                        ],
                    )
                ],
            }
        return {**state, "ready_to_answer": True}

    if state.get("compared"):
        return {**state, "ready_to_answer": True}

    if _looks_like_cell_explanation(question):
        protocol_id, feature = _parse_cell_question(question)
        return {
            **state,
            "intent": "explain_cell",
            "messages": [
                AIMessage(
                    content="I will explain that matrix cell from the generated value and provenance.",
                    tool_calls=[
                        {
                            "name": "explain_cell",
                            "args": {"protocol_id": protocol_id, "feature": feature, "output_dir": output_dir},
                            "id": _tool_call_id(),
                        }
                    ],
                )
            ],
        }

    query_parts = _split_comparison_question(question)
    if len(query_parts) >= 2:
        return {
            **state,
            "intent": "compare_protocols",
            "query_parts": query_parts[:2],
            "messages": [
                AIMessage(
                    content="I will resolve both protocol names against the generated Protocol Analysis outputs.",
                    tool_calls=[
                        {"name": "find_protocols", "args": {"query": query_parts[0], "output_dir": output_dir}, "id": _tool_call_id()},
                        {"name": "find_protocols", "args": {"query": query_parts[1], "output_dir": output_dir}, "id": _tool_call_id()},
                    ],
                )
            ],
        }

    return {
        **state,
        "intent": "find_protocols",
        "ready_to_answer": True,
        "messages": [
            AIMessage(
                content="I will search the generated Protocol Analysis outputs for matching protocols.",
                tool_calls=[{"name": "find_protocols", "args": {"query": question, "output_dir": output_dir}, "id": _tool_call_id()}],
            )
        ],
    }


def _route_after_planner(state: ProtocolChatState) -> Literal["tools", "answer"]:
    messages = state.get("messages", [])
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tools"
    return "answer"


def _answer_node(state: ProtocolChatState) -> ProtocolChatState:
    messages = state.get("messages", [])
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]
    content = _format_answer(state, tool_messages)
    return {**state, "messages": [AIMessage(content=content)]}


def _format_answer(state: ProtocolChatState, tool_messages: list[ToolMessage]) -> str:
    if not tool_messages:
        return "没有可用的工具结果。请先运行 Protocol Analysis Agent，并确认 output_dir 指向对应 runs 目录。"
    last_payload = _tool_payload(tool_messages[-1])
    intent = state.get("intent", "")
    if intent == "compare_protocols" and isinstance(last_payload, dict) and last_payload.get("status") == "ok":
        return _format_comparison_answer(last_payload)
    if intent == "explain_cell" and isinstance(last_payload, dict):
        return _format_cell_answer(last_payload)
    if intent == "find_protocols":
        payload = _tool_payload(tool_messages[-1])
        count = len(payload) if isinstance(payload, list) else 0
        lines = [f"找到 {count} 个候选 protocol。"]
        if isinstance(payload, list):
            for item in payload[:10]:
                lines.append(f"- `{item.get('protocol_id', '')}` | {item.get('paper_id', '')} | {item.get('protocol_name', '')}")
        return "\n".join(lines)
    return "工具结果已返回：\n\n```json\n" + json.dumps(last_payload, ensure_ascii=False, indent=2)[:6000] + "\n```"


def _format_comparison_answer(comparison: dict[str, Any]) -> str:
    a = comparison.get("protocol_a", {}).get("metadata", {})
    b = comparison.get("protocol_b", {}).get("metadata", {})
    lines = [
        "两者的结构化比较已基于本地 `protocol_feature_matrix.csv`、normalized events 和 provenance 生成。",
        f"- Protocol A: `{comparison.get('protocol_a', {}).get('protocol_id', '')}` | {a.get('paper_id', '')} | {a.get('protocol_name', '')}",
        f"- Protocol B: `{comparison.get('protocol_b', {}).get('protocol_id', '')}` | {b.get('paper_id', '')} | {b.get('protocol_name', '')}",
    ]
    grouped = comparison.get("grouped_feature_differences", {})
    for group, title in (
        ("strategy_module", "分化策略模块"),
        ("culture_system", "培养体系"),
        ("core_signaling_family", "核心 signaling / reagent"),
        ("workflow", "流程操作"),
        ("timing_or_exposure", "时间与暴露窗口"),
        ("concentration", "浓度"),
    ):
        items = grouped.get(group, [])
        if not items:
            continue
        lines.append(f"\n{title}:")
        for item in items[:6]:
            lines.append(f"- `{item.get('feature')}`: A=`{item.get('protocol_a_value')}`; B=`{item.get('protocol_b_value')}`")
    relationship = comparison.get("relationship", {})
    if relationship:
        lines.append(f"\nBinary feature Jaccard similarity: `{relationship.get('binary_feature_jaccard_similarity', '')}`")
    lines.append("\n注意：以上是结构化 protocol 特征差异，不直接解释为因果结论。")
    return "\n".join(lines)


def _format_cell_answer(payload: dict[str, Any]) -> str:
    lines = [
        f"`{payload.get('protocol_id', '')}` 的 `{payload.get('feature', '')}` = `{payload.get('value', '')}`",
        f"provenance items: {payload.get('n_evidence_items', 0)}",
    ]
    for item in payload.get("evidence", [])[:5]:
        lines.append(f"- stage/event: `{item.get('entity_id') or item.get('event_id') or item.get('stage_id', '')}`; quote: {item.get('quote', '')[:300]}")
    return "\n".join(lines)


def _last_human_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def _tool_payload(message: ToolMessage) -> Any:
    content = message.content
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def _protocol_ids_from_recent_find_tools(messages: list[BaseMessage]) -> list[str]:
    protocol_ids: list[str] = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if not isinstance(message, ToolMessage) or message.name != "find_protocols":
            continue
        payload = _tool_payload(message)
        if isinstance(payload, list) and payload:
            protocol_id = str(payload[0].get("protocol_id") or "")
            if protocol_id:
                protocol_ids.append(protocol_id)
    return list(reversed(protocol_ids))[:2]


def _split_comparison_question(question: str) -> list[str]:
    text = question.strip()
    for separator in (" vs ", " versus ", " 和 ", " 与 ", " and "):
        if separator in text.lower() or separator in text:
            parts = text.split(separator.strip()) if separator.strip() in {"和", "与"} else re.split(re.escape(separator), text, flags=re.IGNORECASE)
            cleaned = [_clean_query_part(part) for part in parts if _clean_query_part(part)]
            if len(cleaned) >= 2:
                return cleaned[:2]
    return []


def _clean_query_part(value: str) -> str:
    value = re.sub(r"(protocols?|有什么区别|有何区别|区别|compare|difference|differences|between|what|are|the|和|与)", " ", value, flags=re.IGNORECASE)
    return " ".join(value.replace("？", " ").replace("?", " ").split())


def _looks_like_cell_explanation(question: str) -> bool:
    return "explain_cell" in question or ("feature" in question.lower() and "protocol" in question.lower())


def _parse_cell_question(question: str) -> tuple[str, str]:
    protocol_match = re.search(r"(protocol_[a-zA-Z0-9_]+)", question)
    feature_match = re.search(r"feature[:= ]+([a-zA-Z0-9_]+)", question)
    return (protocol_match.group(1) if protocol_match else "", feature_match.group(1) if feature_match else "")


def _tool_call_id() -> str:
    return "call_" + uuid.uuid4().hex[:12]


graph = build_protocol_chat_graph()
