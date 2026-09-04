# Pancreas ProtocolOptimizationAgent

ProtocolOptimizationAgent is a downstream multi-agent framework for improving pancreatic / islet organoid differentiation protocols by integrating:

- structured protocol artifacts produced by Agent1
- protocol factor events and evidence quality flags
- per-protocol h5ad single-cell analysis results
- maturity, similarity, marker, composition, QC, and trajectory summaries

The framework is intentionally small: three domain agents plus one lightweight orchestrator.

Mandatory commit rules are documented in [CONTRIBUTING.md](E:/workspace/Pancreas/Agents_test/CONTRIBUTING.md). Every commit must check code correctness and comment quality before staging.

## Repository Layout

```text
backend/
  protocol_optimization_agent/
    api.py                  # Backend HTTP API boundary for the UI
    agents/                 # Domain agents
    workflow.py             # Overall LangGraph workflow skeleton
    ...
  tests/

frontend/
  index.html                # Static UI shell
  README.md

configs/
inputs/
runs/
docs/
```

Layer responsibilities:

- `frontend/`: agent-chat-ui Next.js UI. It connects to LangGraph Server for chat and tool-call visualization.
- `backend API`: connects frontend requests to local Agent/query operations.
- `LangGraph`: performs the actual agent work inside the backend.
- `ProtocolAnalysisAgent`: already runs as an executable LangGraph `StateGraph`.

## Architecture

```text
validate_inputs
load_protocol_artifacts
load_h5ad_manifest
analyze_protocol_factors
analyze_h5ad_profiles
optimize_protocol_from_agent_outputs
generate_improved_protocol
qc_final_recommendations
export_results
```

Core agents:

- `ProtocolAnalysisAgent`: reads Agent1 evidence-grounded artifacts and summarizes protocol / stage / factor differences.
- `DataAnalysisAgent`: reads the h5ad manifest and runs pluggable data analysis tools. The initial implementation emits manifest-based structured placeholders when h5ad dependencies or input files are unavailable.
- `ProtocolOptimizingAgent`: integrates protocol variables and single-cell phenotype summaries into evidence-labeled optimization hypotheses and protocol recommendations.

Optimization orchestration is explicit: `ProtocolOptimizingAgent` consumes audited Protocol/Data Agent outputs. If protocol or data inputs need reinterpretation, rerun the corresponding upstream agent first; the Orchestrator reports `upstream_rerun_required` instead of optimizing from incomplete PA/DA outputs.

## LangGraph Workflow

The overall workflow skeleton lives in `protocol_optimization_agent.workflow`.
`ProtocolAnalysisAgent.run()` already executes its own internal LangGraph `StateGraph`.

```python
from protocol_optimization_agent.workflow import build_langgraph_workflow

workflow = build_langgraph_workflow()
```

Run the backend API:

```powershell
.\scripts\start_backend_api.ps1 -Port 8000
```

## LangGraph Server

The LangGraph Server entrypoint for `agent-chat-ui` is defined in the root `langgraph.json`:

```json
{
  "graphs": {
    "protocol_chat": "./backend/protocol_optimization_agent/langgraph_server.py:graph"
  }
}
```

The server graph exposes a message-based chat agent with visible tool calls:

- `find_protocols`
- `compare_protocols`
- `get_protocol_events`
- `get_feature_evidence`
- `query_matrix`
- `explain_cell`
- `plot_protocol_feature_heatmap`
- `propose_feature_specs_context`

Start a local/LAN LangGraph server from the repository root:

```powershell
cd E:\workspace\Pancreas\Agents_test
.\scripts\start_langgraph_server.ps1 -HostAddress 0.0.0.0 -Port 2024 -OutputDir "runs\protocol_analysis_agent_simplified_autofeatures"
```

Then connect `agent-chat-ui` to the LangGraph server and select the `protocol_chat` graph. The graph accepts standard chat `messages` and streams LLM/tool/answer updates.

For LAN access, do not use `0.0.0.0` in the browser or frontend env. Use the machine LAN IP, for example:

```env
NEXT_PUBLIC_API_URL=http://10.203.168.222:2024
NEXT_PUBLIC_ASSISTANT_ID=protocol_chat
NEXT_PUBLIC_AUTH_SCHEME=
```

Start the Next.js frontend from the repository root:

```powershell
.\scripts\start_frontend.ps1 -HostAddress 0.0.0.0 -Port 5173
```

Open the frontend with the real host address, for example `http://10.203.168.222:5173`. If the browser previously stored another LangGraph URL, clear the agent-chat-ui setup in browser local storage or enter the LAN API URL again.

For all commands above, the backend scripts call the `protocol_agent` environment executables directly instead of `conda run`, avoiding Windows console encoding crashes.

## Agent Subgraph Diagram

The first-pass multi-agent subgraph design is stored in `docs/agent_subgraphs.md`
and generated from `protocol_optimization_agent.subgraphs`.

The current project architecture, implemented technical details, completed work, and planned orchestrator direction are documented in [docs/current_project_architecture.md](E:/workspace/Pancreas/Agents_test/docs/current_project_architecture.md).

In IPython or Jupyter:

```python
from protocol_optimization_agent.subgraphs import render_core_agent_subgraphs_mermaid
from protocol_optimization_agent.visualization.mermaid import display_mermaid

display_mermaid(render_core_agent_subgraphs_mermaid())
```

## Quick Start

Install the backend package in editable mode before using console scripts:

```powershell
pip install -e .
```

Create a working config from the template:

```powershell
Copy-Item configs/default_config.json configs/run_config.json
```

Edit `configs/run_config.json`, then run:

```powershell
protocol-optimization-agent --config configs/run_config.json
```

Outputs are written to the configured `output_dir`, with one subdirectory per agent plus a pipeline run log.

Run the Protocol Analysis Agent alone against the minimal Agent1 protocol inputs:

```powershell
protocol-analysis-agent --input-dir "inputs/Protocols inputs"
```

This emits the first quantitative `Protocol x protocol features` matrix, filtered factor events, protocol relationships, and an Organoid-UWL-inspired protocol knowledge graph. The graph is the low-loss protocol representation; the matrix is a deterministic projection compiled from graph nodes, item-role edges, action order, reagent order, motifs, timing, and provenance.

`ProtocolAnalysisAgent.run()` now executes an internal LangGraph `StateGraph`:

```text
protocol_input_quality_gate
-> protocol_schema_validator
-> stage_timeline_builder
-> factor_event_normalizer
-> protocol_semantic_interpreter
-> cross_protocol_feature_matrix
-> feature_discovery
-> protocol_relationship_analyzer
-> protocol_difference_report
-> quality_and_evidence_auditor
-> export_results
```

The loader accepts either:

- `protocol_artifacts.jsonl` plus `protocol_factor_events.csv`
- or `protocol_database.sqlite` plus `protocol_factor_events.csv`

When `protocol_artifacts.jsonl` is absent, protocol records are reconstructed from SQLite and `comparison_ready` is inferred from evidence coverage, confidence, stage/reagent availability, and severe validation flags.

Protocol Analysis Agent outputs:

- `normalized_factor_events.jsonl`: evidence-preserving factor/event normalization with rule, LLM, or fallback provenance.
- `semantic_stage_annotations.jsonl`: stage-level biological program annotations for differentiation strategy interpretation.
- `protocol_strategy_summary.json`: protocol-level culture system, strategy modules, maturation factors, risks, and confidence.
- `llm_audit_report.json`: contradiction, duplication, fallback, and evidence-quality findings.
- `schema_validation_report.json`: Pydantic input validation findings for protocol, stage, and factor-event records.
- `standardized_protocol_representations.jsonl`: normalized protocol, stage, action, reagent, timing, and evidence structure.
- `protocol_workflow_graphs.json`: per-protocol Organoid-UWL-style workflow graph preserving stages, actions, action classes, item-role edges, `next_action` order, dependencies, and reagent relationships.
- `protocol_feature_matrix_raw.csv`: deterministic base graph-derived feature matrix before optional FeatureSpec expansion.
- `predefined_feature_registry.json`: run-specific predefined feature definitions for all accepted reagents, factor types, and action categories.
- `base_feature_registry.json`: base feature specs copied into the run output.
- `llm_proposed_feature_registry.json`: unapproved LLM-proposed feature specs for human review.
- `approved_feature_registry.json`: feature specs allowed to enter the final matrix.
- `protocol_candidate_feature_matrix.csv`: exploratory matrix including approved plus LLM-proposed candidate columns.
- `feature_cell_provenance.json`: per-protocol, per-feature evidence and computation provenance.
- `feature_discovery_audit_report.json`: validation and consolidation report for feature discovery.
- `protocol_feature_matrix.csv`: quantitative `Protocol x Feature` matrix for comparison, clustering, retrieval, and downstream modeling.
- `protocol_feature_provenance.json`: feature-to-source traceability for every generated protocol feature.
- `quantification_coverage_report.json`: coverage audit confirming accepted reagents, factor types, action categories, and provenance are represented in the matrix.
- `protocol_similarity_matrix.csv`: pairwise protocol similarity from binary graph/workflow/reagent features.
- `protocol_embedding.csv`: deterministic first-pass low-dimensional protocol embedding and cluster label.
- `protocol_cluster_report.json`: cluster/category summary.
- `protocol_knowledge_graph.json`: global protocol-stage-factor-source graph.

Query Protocol Analysis Agent outputs:

```bash
protocol-query --output-dir runs/protocol_analysis_agent_run find "Akgün 2024"
protocol-query --output-dir runs/protocol_analysis_agent_run compare protocol_a protocol_b
protocol-query --output-dir runs/protocol_analysis_agent_run chat "Akgün 2024 和 Sakuma 2023 protocols 有什么区别？"
```

The query layer reads only generated structured outputs such as `protocol_feature_matrix.csv`, `normalized_factor_events.jsonl`, `semantic_stage_annotations.jsonl`, provenance, relationship, and audit reports. It does not re-read papers or infer unsupported facts.

Backend API query endpoints:

- `POST /protocol-query/find`
- `POST /protocol-query/compare`
- `POST /protocol-query/chat`

## Model And API Configuration

LLM use is opt-in. The default client is offline and returns no model output, so rule validation and deterministic fallbacks remain reproducible.

Configure model/API settings in `configs/default_config.json` or your run config:

```json
{
  "llm": {
    "provider": "offline",
    "model": "",
    "api_key_env": "OPENAI_API_KEY",
    "base_url": "https://api.openai.com/v1",
    "timeout_seconds": 60
  }
}
```

For DeepSeek / OpenAI-compatible JSON calls:

```json
{
  "llm": {
    "provider": "deepseek",
    "model": "deepseek-chat",
    "api_key_env": "DEEPSEEK_API_KEY",
    "base_url": "https://api.deepseek.com"
  }
}
```

The API key itself should be set as an environment variable, not written into config files.
LangGraph chat uses a real streaming chat model configured from environment variables. For DeepSeek, put this in `.env`:

```env
DEEPSEEK_API_KEY=your_key_here
PROTOCOL_CHAT_LLM_PROVIDER=deepseek
PROTOCOL_CHAT_MODEL=deepseek-chat
PROTOCOL_CHAT_API_KEY_ENV=DEEPSEEK_API_KEY
PROTOCOL_CHAT_BASE_URL=https://api.deepseek.com
PROTOCOL_CHAT_TEMPERATURE=0
PROTOCOL_CHAT_TIMEOUT_SECONDS=60
```

The chat graph is now an LLM tool-calling loop: the LLM chooses tools such as `find_protocols`, `compare_protocols`, `get_feature_evidence`, and `explain_cell`; LangGraph executes those tools; the final answer is generated by the LLM from tool results. The `output_dir` and `max_features` values are injected from graph state, so the model does not need to guess local paths. Agent Chat UI will show tool calls and streamed answer tokens. Private chain-of-thought is not exposed; answers should include concise evidence-grounded reasoning summaries instead.

LLM-assisted feature discovery is also disabled by default. Enable a bounded novelty scan with:

```json
{
  "quality_filters": {
    "base_feature_registry_path": "configs/base_feature_registry.json",
    "approved_feature_registry_path": "configs/approved_feature_registry.json",
    "max_llm_feature_discovery_protocols": 10
  }
}
```

Feature discovery governance:

- The first-version `configs/base_feature_registry.json` is intentionally empty; project-specific columns should come from a human-approved registry or a bounded LLM discovery run.
- LLM can propose column definitions in `llm_proposed_feature_registry.json`.
- Code validates each `FeatureSpec` with Pydantic.
- Approved `FeatureSpec.value_rule` must be executable by the deterministic compiler, for example `presence`, `count`, `min(day_start)`, `max(concentration_value)`, `sum_duration`, `category_set(factor_type)`, or `ordered_sequence(normalized_family)`.
- Code computes values from canonical stages/events using `trigger_patterns`, `source_fields`, `required_evidence`, and `value_rule`.
- Unapproved LLM columns go only to `protocol_candidate_feature_matrix.csv`.
- Final `protocol_feature_matrix.csv` uses base, approved, and optional run-specific registries.
- Invalid or duplicate formal registry specs fail the run; invalid LLM candidate specs are reported in `feature_discovery_audit_report.json`.

## Expected Inputs

Agent1 artifacts:

- `protocol_artifacts.jsonl`
- `protocol_factor_events.csv`
- `protocol_database.sqlite`
- `lineage_candidate_edges.csv`
- `protocol_evidence_coverage.csv`

H5AD manifest:

- `protocol_h5ad_manifest.csv`

The manifest should minimally contain:

```csv
protocol_id,dataset_id,h5ad_path,condition_id,sample_id,cell_type_col,batch_col
```

## Design Notes

ProtocolOptimizationAgent does not re-parse papers or protocols. It consumes Agent1 artifacts and treats low-confidence or validation-flagged protocol fields as lower-weight evidence.

Recommendations are explicitly labeled as:

- `evidence_supported_recommendation`
- `correlation_based_hypothesis`
- `low_confidence_suggestion`
- `requires_experimental_validation`

Correlation is not reported as causality unless an external literature or experimental evidence layer is later added.
