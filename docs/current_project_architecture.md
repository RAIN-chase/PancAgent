# ProtocolOptimizationAgent Current Architecture

This document records the current project architecture, implemented technical details, completed work, and near-term design direction.

## Goal

ProtocolOptimizationAgent is a downstream analysis system for pancreatic / islet organoid differentiation protocol optimization.

The upstream Agent1 project extracts evidence-grounded structured protocol artifacts from papers and files. This project consumes those artifacts, converts protocols into computable representations, and prepares them for comparison, visualization, h5ad integration, and protocol optimization.

The intended end-to-end flow is:

```text
Scientific literature and protocol files
  ↓
Agent1 protocol extraction
  ↓
ProtocolOptimizationAgent
  ├─ Protocol Analysis Agent
  ├─ Data Analysis Agent
  └─ Protocol Optimization Agent
  ↓
Evidence-grounded hypotheses and next experiment candidates
```

## Repository Layout

```text
backend/
  protocol_optimization_agent/
    agents/
      protocol_analysis.py        # Implemented Protocol Analysis Agent
      data_analysis.py            # Data Analysis Agent framework and h5ad-compatible output contracts
      protocol_optimizing.py      # Protocol Optimizing Agent framework and optimization output contracts
    orchestrator_agent.py         # External Orchestrator Agent graph and SubAgent routing tools
    optimization_orchestration.py # PA/DA output readiness and rerun planning for optimization
    scientific_models.py         # Protocol/Phenotype/Evidence/Hypothesis/Experiment contracts
    scientific_state.py          # User/project-scoped SQLite scientific state and version history
    scientific_reasoning.py      # Phenotype IR, interpretation consensus, scientific gates, experiment selection
    scientific_ingestion.py      # Existing PA/DA/PO artifact adapters
    action_space.py              # Versioned biomedical action registry
    scientific_state_cli.py      # Explicit researcher review and experimental feedback commands
    api.py                        # FastAPI backend API boundary
    langgraph_server.py           # LangGraph server chat graph for agent-chat-ui
    query_engine.py               # Deterministic protocol query and visualization tools
    input_loader.py               # Agent1 artifact loader
    feature_discovery.py          # FeatureSpec discovery/audit/compiler
    schemas.py                    # Pydantic schemas
    normalization.py              # Factor/event normalization
    semantic_annotation.py        # Stage strategy annotation
    audit.py                      # Evidence and consistency audit
    subgraphs.py                  # Agent subgraph metadata and Mermaid rendering
  tests/

frontend/
  src/                            # agent-chat-ui based Next.js frontend
  next.config.mjs                 # Next dev/LAN config

configs/
  base_feature_registry.json
  approved_feature_registry.json
  default_config.json

inputs/
  Protocols inputs/               # Local Agent1 outputs

runs/
  protocol_analysis_agent_simplified_autofeatures/

docs/
  architecture_governance.md      # Runtime isolation, evidence envelope, deployment, and observability rules
```

## Runtime Boundary

The architecture separates UI, API, and agent execution:

```text
agent-chat-ui frontend
  ↓
LangGraph Server
  ↓
Chat / Orchestrator graph
  ↓
Agent tools and deterministic local query tools
```

Current frontend:

- Based on `agent-chat-ui`.
- Responsible only for chat UI, streaming display, and tool-call visualization.
- Connects to LangGraph Server through `NEXT_PUBLIC_API_URL`.

Current backend:

- Provides local FastAPI endpoints for direct API operations.
- Provides LangGraph Server graph `protocol_chat`.
- Uses deterministic tools for protocol retrieval, comparison, matrix querying, provenance lookup, and heatmap generation.

Current LangGraph graphs:

- `protocol_chat` is exposed in `langgraph.json`.
- It streams LLM responses and visible tool calls.
- It currently routes to Protocol Analysis query tools.
- `orchestrator` is exposed in `langgraph.json`.
- It routes user questions to callable SubAgent tools for Protocol Analysis, Data Analysis, and Protocol Optimizing workflows.

Runtime governance:

- `docs/architecture_governance.md` defines the source/data/runtime boundary, multi-user isolation, evidence envelope, deployment configuration, and observability requirements.
- New Orchestrator-facing SubAgent tools should return `orchestrator-evidence-envelope-v1`.
- Server-side runs should pass `user_id`, `run_id`, `trace_id`, and `workspace_root` through Orchestrator state.
- Default scoped runtime layout is `runs/users/<user_id>/runs/<run_id>/<agent_id>/` unless an explicit output directory is provided for backward compatibility.
- Explicit Orchestrator output paths must resolve under the authorized runtime workspace unless `PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR=1` is set for local development.
- Optimization cache validity is tied to upstream output fingerprints plus objective and candidate protocol features, not just the presence of recommendation files.

## Scientific State And Closed-Loop Reasoning

The implemented scientific state upgrade is documented in [scientific_world_model.md](scientific_world_model.md).

- The four existing agents share user/project-scoped structured Protocol, Phenotype, Evidence, Claim, Hypothesis and Experiment records; scientific memory is independent of chat history and run ids.
- DA exports deterministic `phenotype_ir.json` and a separate optional `phenotype_interpretation.json`; independent interpretations share one numerical snapshot and disagreements require researcher review.
- PO exports `next_experiment_candidates.json` through generate, critique, compare, rank and select operators. Utility uses explicit sourced ordinal improvement/information/cost/risk assessments; missing assessments remain unknown.
- Execution readiness and scientific preconditions are separate. Unknown or mismatched cell line/stage/modality/reference metadata restrict inference and experiment eligibility.
- Orchestrator exposes `describe_scientific_actions`, `execute_scientific_action` and `inspect_scientific_state`. Registered unavailable actions are not executed.
- Explicit researcher CLI commands control experiment approval, completion and QC-passed phenotype feedback. Hypotheses become `SUPPORTED`, `REFUTED` or `UNRESOLVED`, retaining contradictory evidence and full revision history.
- Experimental feedback contributes to the PO cache fingerprint and future proposal generation.
- **Deferred by project owner:** PA's unstructured-protocol to standardized intervention representation, final Protocol IR design and invocation of the external `Protocol_extraction` workflow. Existing structured protocol artifact processing remains unchanged.
- This upgrade implements the feedback and experiment-selection framework; it does not supply missing DA maturity/reference computations, a causal model, calibrated information gain or automatic wet-lab execution.

## Recommended Orchestrator Design

The long-term chat entrypoint should be an external Orchestrator Agent rather than a single domain agent.

```mermaid
flowchart TD
  UI[agent-chat-ui] --> LG[LangGraph Server]
  LG --> ORCH[Orchestrator Agent]
  ORCH -->|protocol comparison or protocol evidence| PA[Protocol Analysis Agent]
  ORCH -->|h5ad, markers, cell composition, expression| DA[Data Analysis Agent]
  ORCH -->|integrated optimization request| PO[Protocol Optimization Agent]
  PA --> PO
  DA --> PO
  PO --> ORCH
  ORCH --> UI
```

Routing examples:

- "What are the protocol differences between paper A and paper B?" -> Protocol Analysis Agent.
- "What is beta cell marker expression in this dataset?" -> Data Analysis Agent.
- "Use all protocols and h5ad data to propose a better protocol." -> Protocol Analysis Agent + Data Analysis Agent + Protocol Optimization Agent.

Current implementation status:

- Protocol Analysis Agent: implemented.
- Data Analysis Agent: deterministic standard path, audited exploratory artifact path, and PO readiness gate implemented; maturity and reference similarity remain pending numeric tools.
- Protocol Optimization Agent: framework implemented through `ProtocolOptimizingAgent`; recommendations are evidence-labeled hypotheses, not causal claims.
- External Orchestrator Agent: framework implemented as a LangGraph entrypoint with structured request envelopes, cache/readiness checks, scoped runtime context, and optional upstream SubAgent execution when structured inputs are provided.

## Protocol Analysis Agent

The Protocol Analysis Agent is the first implemented domain agent. It consumes Agent1 structured outputs and builds a Protocol Intelligence Layer.

### Inputs

Minimum intended inputs:

```text
inputs/Protocols inputs/
  protocol_factor_events.csv
  protocol_database.sqlite
  source_identity_inventory.csv
  protocol_artifacts.jsonl        # Optional when SQLite is available
```

Current loader behavior:

- Uses `protocol_artifacts.jsonl` when available.
- Reconstructs protocol records from `protocol_database.sqlite` when JSONL artifacts are absent.
- Uses `protocol_factor_events.csv` as event-level structured input.
- Uses source identity inventory data when available to validate source/paper mapping.

Quality gates:

- Prefer `comparison_ready=true`.
- Require minimum protocol confidence by default.
- Keep only accepted validation statuses by default.
- Reject unparsed numeric fields where configured.
- Require evidence quotes for key factor/event types.

### LangGraph Subgraph

`ProtocolAnalysisAgent.run()` executes an internal LangGraph `StateGraph`:

```mermaid
flowchart TD
  A[protocol_input_quality_gate] --> B[protocol_schema_validator]
  B --> C[stage_timeline_builder]
  C --> D[factor_event_normalizer]
  D --> E[protocol_semantic_interpreter]
  E --> F[cross_protocol_feature_matrix]
  F --> G[feature_discovery]
  G --> H[protocol_relationship_analyzer]
  H --> I[protocol_difference_report]
  I --> J[quality_and_evidence_auditor]
  J --> K[export_results]
```

### Hybrid Rule + LLM Design

LLM usage is deliberately constrained:

- LLM may normalize ambiguous factor/event names.
- LLM may annotate biological stage strategy.
- LLM may propose candidate FeatureSpec columns.
- Each LLM-proposed FeatureSpec is validated separately from proposal generation and receives executable positive,
  empty-source negative, and relation-specific controlled cases before it is treated as a candidate feature.
- LLM may support audit explanations.
- LLM must not write final matrix values.

Deterministic code remains responsible for:

- Input filtering.
- Schema validation.
- Graph construction.
- Matrix value computation.
- Similarity calculation.
- Embedding and clustering.
- Heatmap generation.
- Provenance export.

Every final matrix value must be computable from accepted structured events, protocol/stage fields, graph structure, and evidence provenance.

`feature_spec_validation_report.json` independently reports source-detail preservation, quantification coverage, and
matrix recomputation consistency. It also validates source fields, unit comparability, stage scope, and explicit
missing-value semantics. Same-stage co-occurrence specifications execute controlled cases for only A, only B,
same-stage A+B, cross-stage A+B, and neither reported.

## Organoid-UWL-Inspired Representation

The project borrows the useful idea from UWL-style protocol representation: the graph is the low-loss representation, and the matrix is a projection compiled from the graph.

Current per-protocol workflow graph:

```text
Protocol
  ├─ Stage nodes
  ├─ Action nodes
  ├─ Reagent / factor item nodes
  ├─ next_stage edges
  ├─ next_action edges
  └─ action -> item edges with item_role
```

Current action nodes include:

- `action_class`: `add`, `remove`, or `modify`
- `operation`
- `factor_type`
- `timing`
- `day_start`
- `day_end`
- `evidence_quote`

Current item-role edge examples:

- `mobile_item_added_or_applied`
- `environment_or_stationary_context`
- `measurement_target_or_readout`
- `associated_item_or_parameter`

The graph is saved in:

```text
runs/protocol_analysis_agent_simplified_autofeatures/protocol_workflow_graphs.json
```

## Feature Matrix Design

The final matrix is:

```text
Protocol x Feature
```

Current generated matrix:

```text
runs/protocol_analysis_agent_simplified_autofeatures/protocol_feature_matrix.csv
runs/protocol_analysis_agent_simplified_autofeatures/feature_spec_validation_report.json
```

Current default run statistics:

```text
protocols: 102
features: 2369
coverage: pass
```

Main feature families:

- Metadata features.
- Temporal features.
- Core reagent family features.
- All accepted reagent identity features.
- Structured factor type features.
- Standardized action category features.
- Normalized factor family features.
- Semantic strategy module features.
- Reagent order features.
- Graph topology features.
- Action sequence bigrams.
- Factor type sequence bigrams.
- All-reagent transition features.
- Action-item motif features.
- Dependency features.
- Approved registry FeatureSpec features.

### Predefined Feature Registry

The project now treats full reagent/action/factor coverage as predefined quantitative features.

Registry output:

```text
runs/protocol_analysis_agent_simplified_autofeatures/predefined_feature_registry.json
```

Current predefined feature count:

```text
all_reagent:      139 entities x 6 features = 834
factor_type:       13 entities x 4 features = 52
action_category:   18 entities x 3 features = 54
total predefined features: 940
```

Each predefined feature includes:

- `feature_name`
- `feature_type`
- `level`
- `biological_category`
- `definition`
- `value_rule`
- `source_fields`
- `required_evidence`
- `computed_by`
- `approved_status`

### Graph-Derived Features

Current graph-derived sequence/topology features include:

```text
graph_*                       # topology statistics
action_bigram_*               # action order
factor_type_bigram_*          # factor type order
all_reagent_transition_*      # all reagent order transitions
motif_action_*_uses_reagent_* # action-item motifs
```

Current default run counts:

```text
graph_: 8
action_bigram_: 119
factor_type_bigram_: 122
all_reagent_transition_: 578
motif_action_: 139
```

Important implementation detail:

- Event ordering is deterministic.
- `day_start=0` is correctly preserved as a real day, not treated as missing.
- Sorting falls back to `stage_index`, `stage_id`, `event_id`, and factor name only when needed.

### Coverage Audit

Coverage report:

```text
runs/protocol_analysis_agent_simplified_autofeatures/quantification_coverage_report.json
```

Current status:

```text
status: pass
missing_reagents: []
missing_factor_types: []
missing_action_categories: []
n_uncategorized_action_events: 0
missing_provenance_columns: []
```

The coverage scope is `accepted_filtered_analysis_events_only`. Excluded protocols/events remain in quality reports and filtered event reports, but they do not enter the final matrix.

## Query And Visualization Tools

The LangGraph chat agent currently exposes these tools:

```text
find_protocols
compare_protocols
get_protocol_events
get_feature_evidence
query_matrix
explain_cell
plot_protocol_feature_heatmap
plot_layered_protocol_feature_heatmaps
propose_feature_specs_context
```

Visualization capabilities:

- Single clustered feature heatmap.
- Layered clustered heatmaps.
- HTML index page for layered heatmaps.

Layered heatmap families:

- semantic
- workflow
- action_category
- factor_type
- reagent
- normalized_family
- temporal_exposure
- graph_topology
- motif
- sequence, optional because it is sparse and high-dimensional

Example output:

```text
runs/protocol_analysis_agent_simplified_autofeatures/visualizations/layered_protocol_feature_heatmap_index.html
```

## Data Analysis Agent

The Data Analysis Agent quantifies organoid h5ad datasets against in vivo reference atlases. It uses verified deterministic tools first and reserves generated code for an explicitly selected exploratory path. The first-pass anndata-backed tools cover h5ad inspection, cell type composition, marker/gene program scoring, target-gene expression summaries, and lightweight differential expression. Methods that require heavier modeling, including reference latent mapping, GRN inference, and trajectory modeling, remain wrapper-backed placeholders until their runtime dependencies and reference resources are configured.

Execution paths:

- `standard` (default): fixed tools and declared expression/sample/group contracts.
- `exploratory`: keeps the standard baseline and archives sandbox-generated code, environment, configuration, diagnostics, and deterministic validation checks. The Data Analysis Agent does not execute arbitrary generated code in its main process.

Both paths end at `data_analysis_validation.json`. Protocol Optimizing Agent readiness requires this report to contain `validation_status=passed` and `po_ready=true`; file existence or an LLM quality score alone is insufficient. The gate checks manifest identity, actual selected cells, requested genes, expression source, DE groups, expression scale, maturity output, and reference-similarity output. An LLM assessment is qualitative commentary, not a statistical confidence probability.

Implementation:

```text
backend/protocol_optimization_agent/agents/data_analysis.py
backend/protocol_optimization_agent/tools/h5ad_tools.py
docs/data_analysis_agent_tools.md
```

Minimum intended inputs:

```text
reference_atlas_h5ad                  # in vivo reference atlas
organoid_h5ad_manifest                # protocol/dataset/sample/condition-linked h5ad rows
gene_programs                         # lineage -> program -> genes
metadata_config                       # obs column names and matching keys
```

`DataAnalysisAgent.run()` executes an internal LangGraph `StateGraph`:

```mermaid
flowchart TD
  A[data_input_gate] --> P[analysis_execution_planner]
  P --> B[dataset_inventory_builder]
  B --> C[metadata_and_qc_summary]
  C --> D[cell_type_composition_analysis]
  D --> E[marker_and_gene_program_scoring]
  E --> F[maturity_scoring]
  F --> G[reference_similarity_analysis]
  G --> H[regulatory_and_trajectory_placeholders]
  H --> I[external_tool_inventory]
  I --> J[aggregate_quantitative_comparison]
  J --> K[data_analysis_audit]
  K --> L[export_results]
```

The manifest is logical rather than file-based: multiple rows may reference the same h5ad and select different cell subsets through `dataset_column`, `sample_column`, `condition_column`, and `protocol_column`. This supports both one-file-per-dataset inputs and a future combined organoid h5ad without changing the analysis tools.

Current outputs:

```text
h5ad_summary_by_protocol.jsonl
h5ad_qc_report.json
cell_type_composition.csv
marker_scores.csv
gene_program_scores.csv
gene_expression_summary.csv
differential_expression.csv
maturity_scores.csv
similarity_scores.csv
regulatory_program_scores.csv
trajectory_scores.csv
external_tool_inventory.csv
data_analysis_protocol_summary.csv
data_analysis_tool_registry.json
data_analysis_comparison_report.json
data_analysis_query_result.json
h5ad_analysis_limitations.json
data_analysis_execution_plan.json
data_analysis_validation.json
exploratory/analysis.py
exploratory/environment.json
exploratory/config.json
exploratory/diagnostics.json
```

The h5ad implementation avoids default dense full-matrix conversion. It reads metadata in backed mode where possible and computes expression statistics by target genes and groups. The maturity scoring and organoid-reference similarity functions are still stable stubs in this iteration; their output schemas reserve slots for lineage gene programs, cosine similarity, and Euclidean distance.

## Protocol Optimizing Agent

The Protocol Optimizing Agent integrates protocol features with h5ad phenotype summaries and produces evidence-labeled optimization hypotheses. It is intentionally conservative: small-sample associations are never promoted to causal claims, and every recommendation is marked as requiring experimental validation.

Implementation:

```text
backend/protocol_optimization_agent/agents/protocol_optimizing.py
```

Minimum intended inputs:

```text
protocol_output_dir/
  protocol_feature_matrix.csv
  stage_factor_matrix_or_events_summary.csv
  protocol_feature_provenance.json

data_analysis_output_dir/
  maturity_scores.csv
  similarity_scores.csv
  cell_type_composition.csv
  marker_scores.csv
  gene_program_scores.csv
  regulatory_program_scores.csv
  trajectory_scores.csv
  data_analysis_protocol_summary.csv
  h5ad_summary_by_protocol.jsonl
```

`ProtocolOptimizingAgent.run()` executes an internal LangGraph `StateGraph`:

```mermaid
flowchart TD
  A[integration_input_gate] --> B[phenotype_target_builder]
  B --> C[protocol_phenotype_joiner]
  C --> D[protocol_phenotype_association_analyzer]
  D --> E[mechanistic_evidence_ranker]
  E --> F[counterfactual_protocol_scorer]
  F --> G[optimized_protocol_generator]
  G --> H[optimization_audit]
  H --> I[export_results]
```

Current outputs:

```text
optimization_input_gate_report.json
phenotype_targets.csv
protocol_phenotype_summary.csv
protocol_phenotype_associations.csv
optimization_candidate_features.csv
optimized_protocol_edits.json
counterfactual_protocol_predictions.json
proposed_improved_protocol.json
proposed_improved_protocol.md
factor_recommendation_table.csv
evidence_and_risk_report.json
experimental_validation_plan.json
experimental_validation_plan.md
```

Current optimization behavior:

- Uses an orchestration readiness plan before running optimization.
- Requires Protocol Analysis Agent and Data Analysis Agent output contracts to be present before Protocol Optimizing Agent execution.
- Returns `upstream_rerun_required` when PA/DA outputs are missing or the caller requests upstream recomputation.
- Builds multi-endpoint phenotype targets from maturity, reference similarity, composition, marker, gene-program, regulatory, and trajectory tables.
- Assigns each phenotype endpoint a `direction`, `weight`, `confidence`, and objective-aware score so positive maturity/readiness metrics and negative stress/off-target metrics are not averaged in the wrong direction.
- Joins protocol features to phenotype summaries by `protocol_id`.
- Carries confounder markers such as dataset, paper, cell line, assay, timepoint, batch, condition, and sample when those fields are available.
- Runs a conservative Pearson correlation screen against the objective-weighted phenotype score only when at least five numeric protocol-phenotype pairs are available.
- Falls back to biologically prioritized protocol-feature review when numeric phenotype outputs are pending.
- Ranks candidate features with mechanism labels such as maturity/beta identity, developmental pathway, microenvironment, and workflow sequence axes.
- Converts ranked candidates into stage-level protocol edits with target stage/day, reagent or operation, dose or condition range, evidence refs, negative controls, and validation readouts.
- Scores an optional new protocol as a scaled similar-protocol reference report with feature coverage and out-of-distribution flags; this is not treated as a causal or supervised prediction model.
- Writes protocol recommendations as hypotheses with confidence, risk, provenance status, and experimental-validation requirements.

Optimization orchestration statuses:

```text
upstream_rerun_required          # PA or DA outputs are missing, stale by caller policy, or explicitly forced for recomputation
optimization_ran                 # PO executed against current PA/DA outputs
ok_cached_optimization_outputs   # existing PO outputs are reused
no_prior_optimization_outputs    # PA/DA are ready but no PO output exists
```

## Model And API Configuration

LLM usage is opt-in.

Default behavior:

- Offline client.
- No real LLM calls.
- Deterministic fallback behavior.

DeepSeek/OpenAI-compatible configuration is supported for chat and LLM-assisted nodes.

Typical environment variables:

```env
PROTOCOL_WORKSPACE_ROOT=runs
PROTOCOL_USER_ID=local_user
PROTOCOL_RUN_ID=manual_test
PROTOCOL_TRACE_ID=trace_manual_test
PROTOCOL_CHAT_LLM_PROVIDER=deepseek
PROTOCOL_CHAT_MODEL=deepseek-chat
PROTOCOL_CHAT_API_KEY_ENV=DEEPSEEK_API_KEY
PROTOCOL_CHAT_BASE_URL=https://api.deepseek.com
DEEPSEEK_API_KEY=...
```

The current policy is:

- LLM can propose structure.
- LLM can call tools.
- LLM can summarize evidence.
- LLM cannot directly write final matrix values.

Orchestrator answer policy:

- LLM-facing SubAgent outputs must include `used_tools`, `used_features`, `evidence_refs`, and `confidence_caveats`.
- Frontend-specific renderers should consume structured envelope fields and durable `output_files`, not scrape assistant prose.
- Tool-call logs and run manifests are written under the scoped run directory when Orchestrator context is provided.

## Current Completed Work

Completed:

- Repository split into backend and frontend.
- Next.js `agent-chat-ui` frontend integrated.
- LangGraph server entrypoint added via `langgraph.json`.
- Protocol chat graph exposed as `protocol_chat`.
- Streaming LLM/tool-call chat path implemented.
- DeepSeek/OpenAI-compatible chat configuration supported.
- Windows startup scripts added for backend API, LangGraph server, and frontend.
- LAN frontend/server configuration added.
- Protocol Analysis Agent converted to executable LangGraph StateGraph.
- Input loader supports SQLite reconstruction when JSONL artifacts are absent.
- Pydantic schema validation added.
- Factor event normalization with rule/LLM/fallback provenance added.
- Semantic stage annotation added.
- Evidence and consistency audit added.
- Organoid-UWL-style workflow graph added.
- Predefined reagent/factor/action feature registry added.
- Deterministic graph-to-matrix sequence/topology/motif compiler added.
- Protocol similarity, embedding, and cluster outputs added.
- Query engine added for deterministic protocol lookup, comparison, evidence retrieval, and matrix cell explanation.
- Heatmap and layered heatmap tools added.
- Tests cover the core Protocol Analysis Agent, query tools, LangGraph tool exposure, and visualization outputs.

Current validation:

```text
backend tests: 20 passed
git diff --check: passed
```

## Planned Work

Near-term:

- Add an external Orchestrator Agent graph as the main `agent-chat-ui` entrypoint.
- Route protocol questions to Protocol Analysis tools.
- Implement calibrated maturity and reference-similarity tools so validated Data Analysis outputs can pass the PO readiness gate.
- Expand Protocol Optimizing Agent from framework outputs to stronger small-sample association, mechanism prior, and active-learning recommendation modules.
- Add graph-specific visualization for sequence features, such as transition networks or Sankey-style summaries.
- Add better frontend rendering for generated SVG/HTML artifacts.

Longer-term:

- Expand Data Analysis Agent beyond the current transcriptomic baseline to validated GRN, trajectory, communication, and protocol-level comparisons.
- Build Integration/Optimization Agent that joins protocol features with h5ad phenotype metrics.
- Add association analysis with strict correlation-vs-causality labeling.
- Produce evidence-supported optimized pancreatic/islet organoid differentiation protocol proposals.

## Development Guidance

Recommended development organization:

- Keep all code in one repository.
- Use separate Codex tasks for large independent agents if useful.
- Store decisions in docs and tests, not only in chat history.
- Expose one final orchestrator graph to the frontend.
- Keep domain agents modular and tool-callable.
- Keep heavy runtime outputs in ignored or external directories: `runs/`, `external_tools/`, `inputs/`, and real `.h5ad` files are not source.
- Preserve deterministic governance of `protocol_feature_matrix.csv`; LLMs may propose registry candidates or summarize evidence, but deterministic compiler code owns final matrix values.

Commit discipline:

- Follow `CONTRIBUTING.md`.
- Before each commit, check code correctness and comment quality.
- Do not commit generated run artifacts, local input datasets, `.env`, or dependency folders.
