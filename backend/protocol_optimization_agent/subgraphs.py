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
        "cross_protocol_feature_matrix": "Organoid-UWL graph and graph-derived feature compiler",
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
    graph_id="data_analysis_agent",
    title="Data Analysis Agent",
    nodes={
        "data_input_gate": "Reference atlas, organoid h5ad, and gene program gate",
        "dataset_inventory_builder": "Dataset inventory builder",
        "metadata_and_qc_summary": "Metadata and QC summary",
        "cell_type_composition_analysis": "Cell type composition analysis",
        "marker_and_gene_program_scoring": "Marker and gene program scoring",
        "maturity_scoring": "Lineage maturity scoring",
        "reference_similarity_analysis": "Organoid-reference similarity analysis",
        "regulatory_and_trajectory_placeholders": "Regulatory and trajectory analysis placeholders",
        "aggregate_quantitative_comparison": "Dataset quantitative comparison aggregation",
        "data_analysis_audit": "Data analysis limitations audit",
    },
    edges=(
        ("data_input_gate", "dataset_inventory_builder", None),
        ("dataset_inventory_builder", "metadata_and_qc_summary", None),
        ("metadata_and_qc_summary", "cell_type_composition_analysis", None),
        ("cell_type_composition_analysis", "marker_and_gene_program_scoring", None),
        ("marker_and_gene_program_scoring", "maturity_scoring", "user gene programs"),
        ("maturity_scoring", "reference_similarity_analysis", "matched lineages and cell types"),
        ("reference_similarity_analysis", "regulatory_and_trajectory_placeholders", "optional external tools"),
        ("regulatory_and_trajectory_placeholders", "aggregate_quantitative_comparison", None),
        ("aggregate_quantitative_comparison", "data_analysis_audit", None),
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
            "  aggregate_quantitative_comparison --> load_agent_outputs",
            "  data_analysis_audit --> load_agent_outputs",
        ]
    )
    return "\n".join(lines)


def _quote_label(label: str) -> str:
    return '"' + label.replace('"', "'") + '"'
