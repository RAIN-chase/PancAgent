from __future__ import annotations

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from protocol_optimization_agent.agents.orchestrator import build_orchestrator_graph
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine


def test_main_entry_preserves_protocol_comparison_and_evidence(tmp_path: Path) -> None:
    records = [
        {
            "protocol_id": "p1",
            "paper_id": "paper_a_2024",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p1", "protocol_name": "Protocol A", "number_of_stages": 1},
            "stages": [
                {
                    "stage_id": "s1",
                    "stage_index": 1,
                    "stage_name": "DE",
                    "day_start": 0,
                    "day_end": 3,
                    "evidence_quote": "Activin A",
                }
            ],
        },
        {
            "protocol_id": "p2",
            "paper_id": "paper_b_2024",
            "comparison_ready": True,
            "confidence": {"extraction": "high"},
            "protocol": {"protocol_id": "p2", "protocol_name": "Protocol B", "number_of_stages": 1},
            "stages": [
                {
                    "stage_id": "s2",
                    "stage_index": 1,
                    "stage_name": "Maturation",
                    "day_start": 10,
                    "day_end": 20,
                    "evidence_quote": "T3",
                }
            ],
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
    outputs = ProtocolAnalysisAgent(
        output_dir=tmp_path,
        quality_filters={
            "min_protocol_confidence": 0.65,
            "require_comparison_ready": True,
            "allowed_event_validation_statuses": ["evidence_supported"],
        },
    ).run(records, events, [])


    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools):
            assert [tool.name for tool in tools] == ["execute_scientific_action"]
            return self

    model = ToolCallingFakeModel(responses=[
        AIMessage(content="", tool_calls=[{
            "name": "execute_scientific_action",
            "args": {"action_id": "protocol.compare", "arguments": {"protocol_id_a": "p1", "protocol_id_b": "p2"}},
            "id": "compare",
        }], usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
        AIMessage(content="{}", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
    ])
    source = outputs["protocol_feature_matrix"].parent
    expected = ProtocolQueryEngine(source).compare_protocols("p1", "p2", 20)
    result = build_orchestrator_graph(model).invoke({
        "messages": [HumanMessage(content="Compare Protocol A and Protocol B")],
        "protocol_output_dir": str(source), "data_analysis_output_dir": str(tmp_path / "missing_da"),
        "optimization_output_dir": str(tmp_path / "missing_po"),
        "workspace_root": str(tmp_path), "max_features": 20,
    })
    import json
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert [message.name for message in tool_messages] == ["protocol_analysis_agent"]
    actual = json.loads(tool_messages[0].content)
    assert actual["result_preview"]["tool_result"] == expected
    assert actual["evidence_refs"]
    assert result["stop_reason"] == "completed"
    assert "Protocol Analysis Agent" in result["messages"][-1].content
