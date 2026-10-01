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
        "external_tool_inventory": "Downloaded external tool inventory",
        "aggregate_quantitative_comparison": "Dataset quantitative comparison aggregation",
        "data_analysis_audit": "Data analysis limitations audit",
        "phenotype_representation": "Numeric Phenotype IR and optional interpretation consensus",
    },
    edges=(
        ("data_input_gate", "dataset_inventory_builder", None),
        ("dataset_inventory_builder", "metadata_and_qc_summary", None),
        ("metadata_and_qc_summary", "cell_type_composition_analysis", None),
        ("cell_type_composition_analysis", "marker_and_gene_program_scoring", None),
        ("marker_and_gene_program_scoring", "maturity_scoring", "user gene programs"),
        ("maturity_scoring", "reference_similarity_analysis", "matched lineages and cell types"),
        ("reference_similarity_analysis", "regulatory_and_trajectory_placeholders", "optional external tools"),
        ("regulatory_and_trajectory_placeholders", "external_tool_inventory", None),
        ("external_tool_inventory", "aggregate_quantitative_comparison", None),
        ("aggregate_quantitative_comparison", "data_analysis_audit", None),
        ("data_analysis_audit", "phenotype_representation", "one numerical snapshot; independent interpretations"),
    ),
)


PROTOCOL_OPTIMIZING_SUBGRAPH = SubgraphSpec(
    graph_id="protocol_optimizing_agent",
    title="Protocol Optimizing Agent",
    nodes={
        "integration_input_gate": "Load and validate Protocol/Data Agent outputs",
        "phenotype_target_builder": "Build multi-endpoint maturity targets",
        "protocol_phenotype_joiner": "Join protocol features with phenotype summaries",
        "protocol_phenotype_association_analyzer": "Small-sample association screen",
        "mechanistic_evidence_ranker": "Mechanism and provenance ranking",
        "counterfactual_protocol_scorer": "Nearest-neighbor new protocol scorer",
        "optimized_protocol_generator": "Hypothesis-only protocol proposal builder",
        "experiment_design": "Generate, critique, compare, rank, and select experiment candidates",
        "optimization_audit": "Evidence, uncertainty, and risk audit",
        "export_results": "Export optimization contracts",
    },
    edges=(
        ("integration_input_gate", "phenotype_target_builder", None),
        ("phenotype_target_builder", "protocol_phenotype_joiner", None),
        ("protocol_phenotype_joiner", "protocol_phenotype_association_analyzer", None),
        ("protocol_phenotype_association_analyzer", "mechanistic_evidence_ranker", "association, not causality"),
        ("mechanistic_evidence_ranker", "counterfactual_protocol_scorer", None),
        ("counterfactual_protocol_scorer", "optimized_protocol_generator", "relative prediction only"),
        ("optimized_protocol_generator", "experiment_design", "scientific comparability + explicit decision assessments"),
        ("experiment_design", "optimization_audit", "researcher review required"),
        ("optimization_audit", "export_results", None),
    ),
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
            lines.append(f"    {node_id}[{_quote_label(label)}]")
        for source, target, edge_label in spec.edges:
            if edge_label:
                lines.append(f"    {source} -->|{_quote_label(edge_label)}| {target}")
            else:
                lines.append(f"    {source} --> {target}")
        lines.append("  end")
    lines.extend(
        [
            "  protocol_difference_report --> integration_input_gate",
            "  quality_and_evidence_auditor --> integration_input_gate",
            "  aggregate_quantitative_comparison --> integration_input_gate",
            "  data_analysis_audit --> integration_input_gate",
        ]
    )
    return "\n".join(lines)


def _quote_label(label: str) -> str:
    return '"' + label.replace('"', "'") + '"'
