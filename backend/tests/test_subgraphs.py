from __future__ import annotations

from pathlib import Path

from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent
from protocol_optimization_agent.analysis.visualization.mermaid import mermaid_markdown
from protocol_optimization_agent.subgraphs import CORE_AGENT_SUBGRAPHS, render_core_agent_subgraphs_mermaid


def test_core_agent_subgraphs_are_defined() -> None:
    assert [spec.graph_id for spec in CORE_AGENT_SUBGRAPHS] == [
        "protocol_analysis_agent",
        "data_analysis_agent",
        "protocol_optimizing_agent",
    ]


def test_core_agent_subgraphs_render_mermaid() -> None:
    source = render_core_agent_subgraphs_mermaid()
    assert source.startswith("flowchart TD")
    assert "subgraph protocol_analysis_agent" in source
    assert "protocol_schema_validator" in source
    assert "protocol_semantic_interpreter" in source
    assert "quality_and_evidence_auditor" in source
    assert "subgraph data_analysis_agent" in source
    assert "subgraph protocol_optimizing_agent" in source
    assert "association, not causality" in source


def test_ipython_mermaid_markdown_wrapper() -> None:
    markdown = mermaid_markdown("flowchart TD\n  a --> b")
    assert "```mermaid" in markdown.data


def test_subgraph_topology_matches_executable_graphs() -> None:
    agents = (ProtocolAnalysisAgent(Path("."), {}), DataAnalysisAgent(Path("."), {}), ProtocolOptimizingAgent(Path(".")))
    for agent, spec in zip(agents, CORE_AGENT_SUBGRAPHS, strict=True):
        graph = agent.build_langgraph(compile_graph=False)
        assert list(spec.nodes) == list(graph.nodes)
        assert {(source, target) for source, target, _ in spec.edges} == {
            (source, target) for source, target in graph.edges if source in graph.nodes and target in graph.nodes
        }
        assert "export_results" in spec.nodes
    assert "resolve_tasks" in CORE_AGENT_SUBGRAPHS[1].nodes
    assert "execute_tasks" in CORE_AGENT_SUBGRAPHS[1].nodes


def test_rendered_graph_has_distinct_exports_and_artifact_handoffs() -> None:
    source = render_core_agent_subgraphs_mermaid()
    for spec in CORE_AGENT_SUBGRAPHS:
        assert f"{spec.graph_id}__export_results[" in source
    assert "    export_results[" not in source
    for graph_id in ("protocol_analysis_agent", "data_analysis_agent"):
        assert f"{graph_id}__export_results --> protocol_optimizing_agent__integration_input_gate" in source
    assert "LLM only for incomplete time" not in source
