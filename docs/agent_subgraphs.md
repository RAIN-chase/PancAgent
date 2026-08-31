# Core Agent Subgraphs

This is the simplified ProtocolOptimizationAgent subgraph design. Protocol Analysis now keeps query/chat outside the LangGraph run and uses one FeatureSpec discovery/compiler node for LLM-proposed columns, strict validation, deterministic computation, and provenance.

```mermaid
flowchart TD
  subgraph protocol_analysis_agent["Protocol Analysis Agent"]
    protocol_input_quality_gate["Input quality gate"]
    protocol_schema_validator["Protocol schema validator"]
    stage_timeline_builder["Stage timeline builder"]
    factor_event_normalizer["Factor event normalizer"]
    protocol_semantic_interpreter["Protocol semantic interpreter"]
    cross_protocol_feature_matrix["Base graph-derived feature matrix"]
    feature_discovery["LLM FeatureSpec discovery + strict compiler"]
    protocol_relationship_analyzer["Protocol relationship analyzer"]
    protocol_difference_report["Factor difference report"]
    quality_and_evidence_auditor["Quality and evidence auditor"]
    protocol_input_quality_gate --> protocol_schema_validator
    protocol_schema_validator -->|"LLM only for incomplete time"| stage_timeline_builder
    stage_timeline_builder -->|"LLM candidate + rule validation"| factor_event_normalizer
    factor_event_normalizer -->|"normalized factors + evidence"| protocol_semantic_interpreter
    protocol_semantic_interpreter -->|"semantic modules as features"| cross_protocol_feature_matrix
    cross_protocol_feature_matrix -->|"LLM proposes columns; code audits and computes"| feature_discovery
    feature_discovery -->|"final matrix + candidate matrix"| protocol_relationship_analyzer
    protocol_relationship_analyzer -->|"computed diffs"| protocol_difference_report
    protocol_difference_report -->|"contradiction and provenance checks"| quality_and_evidence_auditor
  end
  subgraph h5ad_analysis_agent["H5AD Analysis Agent"]
    h5ad_manifest_gate["Manifest and file gate"]
    h5ad_loader["h5ad loader"]
    metadata_resolver["Metadata resolver"]
    qc_batch_summary["QC and batch summary"]
    composition_analysis["Cell type composition"]
    marker_score_analysis["Marker score analysis"]
    maturity_similarity_scoring["Maturity and similarity scoring"]
    trajectory_summary["Trajectory summary"]
    aggregate_by_protocol["Protocol-level aggregation"]
    h5ad_limitations_report["Limitations report"]
    h5ad_manifest_gate --> h5ad_loader
    h5ad_loader --> metadata_resolver
    metadata_resolver --> qc_batch_summary
    metadata_resolver --> composition_analysis
    metadata_resolver --> marker_score_analysis
    metadata_resolver --> maturity_similarity_scoring
    metadata_resolver -->|"if available"| trajectory_summary
    qc_batch_summary --> aggregate_by_protocol
    composition_analysis --> aggregate_by_protocol
    marker_score_analysis --> aggregate_by_protocol
    maturity_similarity_scoring --> aggregate_by_protocol
    trajectory_summary --> aggregate_by_protocol
    h5ad_manifest_gate --> h5ad_limitations_report
    aggregate_by_protocol --> h5ad_limitations_report
  end
  subgraph integration_optimization_agent["Integration and Protocol Optimization Agent"]
    load_agent_outputs["Load agent outputs"]
    align_protocol_h5ad_units["Align protocol and h5ad units"]
    phenotype_factor_join["Join phenotype with factors"]
    association_analysis["Association analysis"]
    recommendation_classifier["Evidence class assignment"]
    proposed_protocol_builder["Proposed protocol builder"]
    evidence_risk_qc["Evidence and risk QC"]
    export_recommendations["Export recommendations"]
    load_agent_outputs --> align_protocol_h5ad_units
    align_protocol_h5ad_units --> phenotype_factor_join
    phenotype_factor_join --> association_analysis
    association_analysis -->|"correlation, not causality"| recommendation_classifier
    recommendation_classifier --> proposed_protocol_builder
    proposed_protocol_builder --> evidence_risk_qc
    evidence_risk_qc --> export_recommendations
  end
  protocol_difference_report --> load_agent_outputs
  quality_and_evidence_auditor --> load_agent_outputs
  aggregate_by_protocol --> load_agent_outputs
  h5ad_limitations_report --> load_agent_outputs
`

## IPython Usage

`python
from protocol_optimization_agent.subgraphs import render_core_agent_subgraphs_mermaid
from protocol_optimization_agent.visualization.mermaid import display_mermaid

display_mermaid(render_core_agent_subgraphs_mermaid())
`
