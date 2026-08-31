from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubgraphSpec:
    graph_id: str
    title: str
    nodes: dict[str, str]
    edges: tuple[tuple[str, str, str | None], ...]


PROTOCOL_ANALYSIS_SUBGRAPH = SubgraphSpec(
    graph_id="protocol_analysis_agent",
    title="Protocol Analysis Agent",
    nodes={
        "protocol_input_quality_gate": "Input quality gate",
        "protocol_schema_validator": "Protocol schema validator",
        "stage_timeline_builder": "Stage timeline builder",
        "factor_event_normalizer": "Factor event normalizer",
        "protocol_semantic_interpreter": "Protocol semantic interpreter",
        "cross_protocol_feature_matrix": "Base graph-derived feature matrix",
        "feature_discovery": "LLM FeatureSpec discovery + strict compiler",
        "protocol_relationship_analyzer": "Protocol relationship analyzer",
        "protocol_difference_report": "Factor difference report",
        "quality_and_evidence_auditor": "Quality and evidence auditor",
    },
    edges=(
        ("protocol_input_quality_gate", "protocol_schema_validator", None),
        ("protocol_schema_validator", "stage_timeline_builder", "LLM only for incomplete time"),
        ("stage_timeline_builder", "factor_event_normalizer", "LLM candidate + rule validation"),
        ("factor_event_normalizer", "protocol_semantic_interpreter", "normalized factors + evidence"),
        ("protocol_semantic_interpreter", "cross_protocol_feature_matrix", "semantic modules as features"),
        ("cross_protocol_feature_matrix", "feature_discovery", "LLM proposes columns; code audits and computes"),
        ("feature_discovery", "protocol_relationship_analyzer", "final matrix + candidate matrix"),
        ("protocol_relationship_analyzer", "protocol_difference_report", "computed diffs"),
        ("protocol_difference_report", "quality_and_evidence_auditor", "contradiction and provenance checks"),
    ),
)


H5AD_ANALYSIS_SUBGRAPH = SubgraphSpec(
    graph_id="h5ad_analysis_agent",
    title="H5AD Analysis Agent",
    nodes={
        "h5ad_manifest_gate": "Manifest and file gate",
        "h5ad_loader": "h5ad loader",
        "metadata_resolver": "Metadata resolver",
        "qc_batch_summary": "QC and batch summary",
        "composition_analysis": "Cell type composition",
        "marker_score_analysis": "Marker score analysis",
        "maturity_similarity_scoring": "Maturity and similarity scoring",
        "trajectory_summary": "Trajectory summary",
        "aggregate_by_protocol": "Protocol-level aggregation",
        "h5ad_limitations_report": "Limitations report",
    },
    edges=(
        ("h5ad_manifest_gate", "h5ad_loader", None),
        ("h5ad_loader", "metadata_resolver", None),
        ("metadata_resolver", "qc_batch_summary", None),
        ("metadata_resolver", "composition_analysis", None),
        ("metadata_resolver", "marker_score_analysis", None),
        ("metadata_resolver", "maturity_similarity_scoring", None),
        ("metadata_resolver", "trajectory_summary", "if available"),
        ("qc_batch_summary", "aggregate_by_protocol", None),
        ("composition_analysis", "aggregate_by_protocol", None),
        ("marker_score_analysis", "aggregate_by_protocol", None),
        ("maturity_similarity_scoring", "aggregate_by_protocol", None),
        ("trajectory_summary", "aggregate_by_protocol", None),
        ("h5ad_manifest_gate", "h5ad_limitations_report", None),
        ("aggregate_by_protocol", "h5ad_limitations_report", None),
    ),
)


INTEGRATION_OPTIMIZATION_SUBGRAPH = SubgraphSpec(
    graph_id="integration_optimization_agent",
    title="Integration and Protocol Optimization Agent",
    nodes={
        "load_agent_outputs": "Load agent outputs",
        "align_protocol_h5ad_units": "Align protocol and h5ad units",
        "phenotype_factor_join": "Join phenotype with factors",
        "association_analysis": "Association analysis",
        "recommendation_classifier": "Evidence class assignment",
        "proposed_protocol_builder": "Proposed protocol builder",
        "evidence_risk_qc": "Evidence and risk QC",
        "export_recommendations": "Export recommendations",
    },
    edges=(
        ("load_agent_outputs", "align_protocol_h5ad_units", None),
        ("align_protocol_h5ad_units", "phenotype_factor_join", None),
        ("phenotype_factor_join", "association_analysis", None),
        ("association_analysis", "recommendation_classifier", "correlation, not causality"),
        ("recommendation_classifier", "proposed_protocol_builder", None),
        ("proposed_protocol_builder", "evidence_risk_qc", None),
        ("evidence_risk_qc", "export_recommendations", None),
    ),
)


CORE_AGENT_SUBGRAPHS = (
    PROTOCOL_ANALYSIS_SUBGRAPH,
    H5AD_ANALYSIS_SUBGRAPH,
    INTEGRATION_OPTIMIZATION_SUBGRAPH,
)


def render_core_agent_subgraphs_mermaid() -> str:
    lines = ["flowchart TD"]
    for spec in CORE_AGENT_SUBGRAPHS:
        lines.append(f"  subgraph {spec.graph_id}[{_quote_label(spec.title)}]")
        for node_id, label in spec.nodes.items():
            lines.append(f"    {node_id}[{_quote_label(label)}]")
        for source, target, edge_label in spec.edges:
            if edge_label:
                lines.append(f"    {source} -->|{_quote_label(edge_label)}| {target}")
            else:
                lines.append(f"    {source} --> {target}")
        lines.append("  end")
    lines.extend(
        [
            "  protocol_difference_report --> load_agent_outputs",
            "  quality_and_evidence_auditor --> load_agent_outputs",
            "  aggregate_by_protocol --> load_agent_outputs",
            "  h5ad_limitations_report --> load_agent_outputs",
        ]
    )
    return "\n".join(lines)


def _quote_label(label: str) -> str:
    return '"' + label.replace('"', "'") + '"'
