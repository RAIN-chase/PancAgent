from __future__ import annotations

import json
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from protocol_optimization_agent.orchestrator_agent import (
    ORCHESTRATOR_TOOLS,
    SUBAGENT_REGISTRY,
    build_orchestrator_graph,
    classify_route,
)


def test_orchestrator_subagents_are_registered() -> None:
    assert list(SUBAGENT_REGISTRY) == [
        "protocol_analysis_agent",
        "data_analysis_agent",
        "protocol_optimizing_agent",
    ]
    assert [tool.name for tool in ORCHESTRATOR_TOOLS] == [
        "describe_subagents",
        "protocol_analysis_agent",
        "data_analysis_agent",
        "protocol_optimizing_agent",
    ]


def test_orchestrator_route_classifier() -> None:
    assert classify_route("比较 protocol A 和 protocol B 的差异") == "protocol_analysis_agent"
    assert classify_route("这个 h5ad 的 beta cell marker expression 怎么样") == "data_analysis_agent"
    assert classify_route("基于 protocol 和 h5ad 结果推荐一个优化方案") == "protocol_optimizing_agent"


def test_orchestrator_graph_runs_subagent_tool(tmp_path: Path) -> None:
    protocol_dir = tmp_path / "protocol_outputs"
    protocol_dir.mkdir()
    (protocol_dir / "protocol_feature_matrix.csv").write_text(
        "protocol_id,paper_id,dataset_id,protocol_name,confidence_score,comparison_ready\n"
        "p1,paper_a_2024,,Protocol A,0.9,true\n",
        encoding="utf-8",
    )
    (protocol_dir / "normalized_factor_events.jsonl").write_text("", encoding="utf-8")
    (protocol_dir / "semantic_stage_annotations.jsonl").write_text("", encoding="utf-8")
    (protocol_dir / "protocol_strategy_summary.json").write_text(json.dumps({"protocols": []}), encoding="utf-8")
    (protocol_dir / "protocol_relationship_report.json").write_text(json.dumps({}), encoding="utf-8")
    (protocol_dir / "protocol_feature_provenance.json").write_text(json.dumps({}), encoding="utf-8")
    (protocol_dir / "llm_audit_report.json").write_text(json.dumps({"findings": []}), encoding="utf-8")

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools):
            return self

    fake_model = ToolCallingFakeModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "protocol_analysis_agent",
                        "args": {"question": "find Protocol A"},
                        "id": "call_protocol_agent",
                    }
                ],
            ),
            AIMessage(content="已由 Protocol Analysis Agent 完成路由和查询。"),
        ]
    )

    graph = build_orchestrator_graph(chat_model=fake_model)
    result = graph.invoke(
        {
            "messages": [HumanMessage(content="find Protocol A")],
            "protocol_output_dir": str(protocol_dir),
            "max_features": 10,
        }
    )

    tool_names = [message.name for message in result["messages"] if isinstance(message, ToolMessage)]
    assert tool_names == ["protocol_analysis_agent"]
    assert "Protocol Analysis Agent" in result["messages"][-1].content
