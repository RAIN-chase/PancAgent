from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.langgraph_server import build_protocol_chat_graph


def test_langgraph_server_chat_graph_runs_tools(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a_2024",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [{"stage_id": "s1", "stage_index": 1, "stage_name": "DE", "day_start": 0, "day_end": 3, "evidence_quote": "Activin A"}],
        },
        {
            "protocol_id": "p2",
            "paper_id": "paper_b_2024",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p2", "protocol_name": "Protocol B", "number_of_stages": 1},
            "stages": [{"stage_id": "s2", "stage_index": 1, "stage_name": "Maturation", "day_start": 10, "day_end": 20, "evidence_quote": "T3"}],
        },
    ]
    events = [
        {
            "protocol_id": "p1",
            "stage_id": "s1",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "Activin A",
            "standardized_name": "Activin A",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "Activin A was added.",
        },
        {
            "protocol_id": "p2",
            "stage_id": "s2",
            "stage_index": "1",
            "factor_type": "reagent",
            "factor_name": "T3",
            "standardized_name": "T3",
            "validation_status": "evidence_supported",
            "parse_status": "categorical",
            "concentration_parse_status": "not_applicable",
            "evidence_quote": "T3 was added.",
        },
    ]
    ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools):
            return self

    fake_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "find_protocols", "args": {"query": "Protocol A"}, "id": "call_find_a"},
                    {"name": "find_protocols", "args": {"query": "Protocol B"}, "id": "call_find_b"},
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "compare_protocols",
                        "args": {"protocol_id_a": "p1", "protocol_id_b": "p2"},
                        "id": "call_compare",
                    }
                ],
            ),
            AIMessage(content="结构化比较：Protocol A 和 Protocol B 的差异已基于工具结果总结。"),
        ]
    )

    graph = build_protocol_chat_graph(chat_model=fake_model)
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="Protocol A 和 Protocol B protocols 有什么区别？")],
            "output_dir": str(tmp_path),
            "max_features": 20,
        }
    )

    tool_names = [message.name for message in result["messages"] if isinstance(message, ToolMessage)]
    assert tool_names.count("find_protocols") == 2
    assert "compare_protocols" in tool_names
    assert "结构化比较" in result["messages"][-1].content
