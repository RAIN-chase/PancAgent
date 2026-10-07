from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent


@dataclass(frozen=True)
class SubgraphSpec:
    graph_id: str
    title: str
    nodes: dict[str, str]
    edges: tuple[tuple[str, str, str | None], ...]


def _describe_graph(
    graph: Any,
    graph_id: str,
    title: str,
    labels: dict[str, str],
    edge_notes: dict[tuple[str, str], str],
) -> SubgraphSpec:
    """Derive executable topology; keep only scientific annotations by hand."""
    if graph.branches:
        raise ValueError("Conditional graphs require explicit branch rendering, not an incomplete diagram.")
    if set(labels) - set(graph.nodes) or set(edge_notes) - graph.edges:
        raise ValueError(f"Stale graph annotations: {graph_id}")
    positions = {node: index for index, node in enumerate(graph.nodes)}
    edges = sorted(
        ((source, target) for source, target in graph.edges if source in positions and target in positions),
        key=lambda pair: (positions[pair[0]], positions[pair[1]]),
    )
    return SubgraphSpec(
        graph_id=graph_id, title=title,
        nodes={node: labels.get(node, node) for node in graph.nodes},
        edges=tuple((source, target, edge_notes.get((source, target))) for source, target in edges),
    )


PROTOCOL_ANALYSIS_SUBGRAPH = _describe_graph(
    ProtocolAnalysisAgent(Path("."), {}).build_langgraph(compile_graph=False),
    graph_id="protocol_analysis_agent",
    title="Protocol Analysis Agent",
    labels={
        "protocol_input_quality_gate": "Input quality gate",
        "protocol_schema_validator": "Protocol schema validator",
        "stage_timeline_builder": "Stage timeline builder",
        "factor_event_normalizer": "Factor event normalizer",
        "protocol_semantic_interpreter": "Protocol semantic interpreter",
        "cross_protocol_feature_matrix": "Organoid-UWL graph and graph-derived feature compiler",
        "feature_discovery": "LLM FeatureSpec discovery + strict compiler",
        "protocol_relationship_analyzer": "Protocol relationship analyzer",
        "protocol_difference_report": "Factor difference report",
        "quality_and_evidence_auditor": "Quality and evidence auditor",
        "export_results": "Export audited protocol artifacts",
    },
    edge_notes={
        ("protocol_schema_validator", "stage_timeline_builder"): "structured timing; unknown stays unknown",
        ("stage_timeline_builder", "factor_event_normalizer"): "LLM candidate + rule validation",
        ("factor_event_normalizer", "protocol_semantic_interpreter"): "normalized factors + evidence",
        ("protocol_semantic_interpreter", "cross_protocol_feature_matrix"): "semantic modules as features",
        ("cross_protocol_feature_matrix", "feature_discovery"): "LLM proposes columns; code audits and computes",
        ("feature_discovery", "protocol_relationship_analyzer"): "final matrix + candidate matrix",
        ("protocol_relationship_analyzer", "protocol_difference_report"): "computed diffs",
        ("protocol_difference_report", "quality_and_evidence_auditor"): "contradiction and provenance checks",
    },
)


H5AD_ANALYSIS_SUBGRAPH = _describe_graph(
    DataAnalysisAgent(Path("."), {}).build_langgraph(compile_graph=False),
    graph_id="data_analysis_agent",
    title="Data Analysis Agent",
    labels={
        "data_input_gate": "Reference atlas, organoid h5ad, and gene program gate",
        "resolve_tasks": "Resolve explicit tasks, parameters and prerequisites",
        "dataset_inventory_builder": "Dataset inventory builder",
        "execute_tasks": "Execute selected operations only; no autonomous planning loop",
        "data_analysis_audit": "Data analysis limitations audit",
        "phenotype_representation": "Numeric Phenotype IR and optional interpretation consensus",
        "export_results": "Export audited data analysis artifacts",
    },
    edge_notes={
        ("data_analysis_audit", "phenotype_representation"): "one numerical snapshot; independent interpretations",
    },
)


PROTOCOL_OPTIMIZING_SUBGRAPH = _describe_graph(
    ProtocolOptimizingAgent(Path(".")).build_langgraph(compile_graph=False),
    graph_id="protocol_optimizing_agent",
    title="Protocol Optimizing Agent",
    labels={
        "integration_input_gate": "Load and validate Protocol/Data Agent outputs",
        "phenotype_target_builder": "Build multi-endpoint maturity targets",
        "protocol_phenotype_joiner": "Join protocol features with phenotype summaries",
        "protocol_phenotype_association_analyzer": "Small-sample association screen",
        "mechanistic_evidence_ranker": "Grounded mechanisms and competing explanations",
        "similar_protocol_reference": "Similar-protocol reference assessment",
        "optimized_protocol_generator": "Hypothesis-only protocol proposal builder",
        "experiment_design": "Generate, critique, compare, rank, and select experiment candidates",
        "optimization_audit": "Evidence, uncertainty, and risk audit",
        "export_results": "Export optimization contracts",
    },
    edge_notes={
        ("protocol_phenotype_association_analyzer", "mechanistic_evidence_ranker"): "association, not causality",
        ("similar_protocol_reference", "optimized_protocol_generator"): "observed reference, not an intervention prediction",
        ("optimized_protocol_generator", "experiment_design"): "scientific comparability + explicit decision assessments",
        ("experiment_design", "optimization_audit"): "researcher review required",
    },
)


CORE_AGENT_SUBGRAPHS = (
    PROTOCOL_ANALYSIS_SUBGRAPH,
    H5AD_ANALYSIS_SUBGRAPH,
    PROTOCOL_OPTIMIZING_SUBGRAPH,
)


def render_core_agent_subgraphs_mermaid() -> str:
    lines = ["flowchart TD"]
    for spec in CORE_AGENT_SUBGRAPHS:
        lines.append(f"  subgraph {spec.graph_id}[{_quote_label(spec.title)}]")
        for node_id, label in spec.nodes.items():
            lines.append(f"    {spec.graph_id}__{node_id}[{_quote_label(label)}]")
        for source, target, edge_label in spec.edges:
            source, target = f"{spec.graph_id}__{source}", f"{spec.graph_id}__{target}"
            if edge_label:
                lines.append(f"    {source} -->|{_quote_label(edge_label)}| {target}")
            else:
                lines.append(f"    {source} --> {target}")
        lines.append("  end")
    lines.extend(
        [
            "  protocol_analysis_agent__export_results --> protocol_optimizing_agent__integration_input_gate",
            "  data_analysis_agent__export_results --> protocol_optimizing_agent__integration_input_gate",
        ]
    )
    return "\n".join(lines)


def _quote_label(label: str) -> str:
    return '"' + label.replace('"', "'") + '"'
