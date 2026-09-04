from __future__ import annotations

from protocol_optimization_agent.subgraphs import CORE_AGENT_SUBGRAPHS, render_core_agent_subgraphs_mermaid
from protocol_optimization_agent.visualization.mermaid import mermaid_markdown


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
