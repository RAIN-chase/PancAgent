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
    data_analysis_agent,
    protocol_optimizing_agent,
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
            "workspace_root": str(tmp_path / "workspace"),
            "user_id": "test_user",
            "run_id": "test_run",
            "trace_id": "trace_test",
            "max_features": 10,
        }
    )

    tool_names = [message.name for message in result["messages"] if isinstance(message, ToolMessage)]
    assert tool_names == ["protocol_analysis_agent"]
    assert "Protocol Analysis Agent" in result["messages"][-1].content
    assert (tmp_path / "workspace" / "users" / "test_user" / "runs" / "test_run" / "run_manifest.json").exists()
    assert (tmp_path / "workspace" / "users" / "test_user" / "runs" / "test_run" / "_logs" / "tool_calls.jsonl").exists()


def test_protocol_optimizing_tool_reports_required_upstream_reruns(tmp_path: Path) -> None:
    result = protocol_optimizing_agent.invoke(
        {
            "question": "优化 protocol",
            "protocol_output_dir": str(tmp_path / "missing_protocol_outputs"),
            "data_analysis_output_dir": str(tmp_path / "missing_data_outputs"),
            "optimization_output_dir": str(tmp_path / "optimization_outputs"),
            "workspace_root": str(tmp_path / "workspace"),
            "user_id": "test_user",
            "run_id": "test_run",
            "trace_id": "trace_test",
        }
    )

    assert result["schema_version"] == "orchestrator-evidence-envelope-v1"
    assert result["used_tools"] == ["build_optimization_orchestration_plan", "ProtocolOptimizingAgent.run"]
    assert result["status"] == "upstream_rerun_required"
    assert result["orchestration_plan"]["required_upstream_reruns"] == [
        "protocol_analysis_agent",
        "data_analysis_agent",
    ]
    assert result["orchestration_plan"]["can_run_protocol_optimizing_agent"] is False


def test_data_analysis_tool_dispatches_to_selected_output(tmp_path: Path) -> None:
    data_dir = tmp_path / "data_outputs"
    data_dir.mkdir()
    (data_dir / "data_analysis_comparison_report.json").write_text(
        json.dumps({"summary": {"n_datasets": 1}}),
        encoding="utf-8",
    )
    (data_dir / "h5ad_analysis_limitations.json").write_text(json.dumps({"limitations": []}), encoding="utf-8")
    (data_dir / "data_analysis_tool_registry.json").write_text(
        json.dumps({"tools": [], "external_tool_inventory": [], "callable_tools": ["scanpy_h5ad_profile_tool"]}),
        encoding="utf-8",
    )
    (data_dir / "h5ad_summary_by_protocol.jsonl").write_text(
        json.dumps(
            {
                "protocol_id": "p1",
                "dataset_id": "d1",
                "sample_id": "s1",
                "condition_id": "baseline",
                "h5ad_path": "/data/d1.h5ad",
                "cell_type_column": "cell_type",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (data_dir / "data_analysis_protocol_summary.csv").write_text(
        "protocol_id,n_datasets\np1,1\n",
        encoding="utf-8",
    )
    (data_dir / "differential_expression.csv").write_text(
        "protocol_id,dataset_id,gene,mean_diff,status\np1,d1,INS,2.0,ok\n",
        encoding="utf-8",
    )

    result = data_analysis_agent.invoke(
        {
            "question": "INS 的差异表达怎么样",
            "data_analysis_output_dir": str(data_dir),
            "workspace_root": str(tmp_path / "workspace"),
            "user_id": "test_user",
            "run_id": "test_run",
            "trace_id": "trace_test",
        }
    )

    assert result["schema_version"] == "orchestrator-evidence-envelope-v1"
    assert result["selected_operation"] == "differential_expression"
    assert result["used_tools"] == ["differential_expression"]
    assert result["used_features"] == ["INS"]
    assert result["evidence_refs"][0]["dataset_id"] == "d1"
    assert result["result_preview"][0]["gene"] == "INS"
    assert result["provenance"][0]["dataset_id"] == "d1"
