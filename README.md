# PancAgent

ProtocolOptimizationAgent is a downstream multi-agent framework for improving pancreatic / islet organoid differentiation protocols by integrating:

- the formal `protocols.jsonl` export produced by Agent1 (stages, reagents, evidence and quality flags)
- per-protocol h5ad single-cell analysis results
- maturity, similarity, marker, composition, QC, and trajectory summaries

The framework is intentionally small: three domain agents plus one lightweight orchestrator, sharing a versioned Scientific World Model.

The primary `orchestrator` graph now follows a validated plan, task-specific evidence checks, bounded revision and deterministic report synthesis. Plans, step results, stopping reasons and saved numeric bindings are visible in the research workbench. See [planned scientific analysis](docs/planned_scientific_analysis.md) for contracts, statistical boundaries and regression commands.

The [research workbench](docs/research_workbench.md) retains Next.js and LangGraph streaming while separating protocol comparisons, phenotype results, hypotheses, run history and source evidence from the expandable conversation panel. It inspects saved structured results without reconstructing measurements from Markdown.

The [multi-protocol comparison and h5ad catalog](docs/data_catalog_and_multi_compare.md) describes N-object comparisons, reference/public/user datasets, Linux path configuration, transfer states and version publication.

The [thin Orchestrator refactor](docs/thin_orchestrator_refactor.md) documents domain tool ownership, retired PA-only interfaces and explicitly enabled experimental-feedback APIs.

The [goal-reviewed Orchestrator](docs/orchestrator_goal_review.md) adds scientific question acceptance, source-bound conclusions, explicit result-field dependencies, bounded history retrieval and local failure recovery without adding agents or graph nodes.

Scientific state, Phenotype IR, registered actions, next-experiment selection and researcher feedback commands are documented in [docs/scientific_world_model.md](docs/scientific_world_model.md). PA's future `Protocol_extraction` workflow integration and final intervention IR design are deferred.

Candidate approvals bind evaluation versions; reviewed feedback can enter immutable quantitative datasets. Multi-user identity, project permissions and artifact access require the [authenticated deployment profile](docs/authenticated_deployment.md); the default local profile is not a multi-user security boundary.

Mandatory commit rules are documented in [CONTRIBUTING.md](CONTRIBUTING.md). Every commit must check code correctness and comment quality before staging.

## Linux Deployment

Linux launch commands, server configuration, data transfer and Git-based updates are documented in [docs/linux_deployment.md](docs/linux_deployment.md). Use `bash scripts/start_linux.sh --help` for the available launch modes. Existing PowerShell scripts remain available for Windows development.

## Repository Layout

```text
backend/
  protocol_optimization_agent/
    agents/                 # Conversation, domain agents, fixed batch pipeline
      orchestrator.py       # Main conversational graph: orchestrator
      pipeline.py           # Fixed PA -> DA -> PO batch execution
    analysis/               # Deterministic protocol, phenotype, optimization calculations
    science/                # Scientific identities, records, datasets and history
    runtime/                # Context, authorization, archives, models, configuration and IO
    interfaces/             # HTTP API, LangGraph Server, authentication adapters and CLI
    extensions/experimental_feedback/ # Explicit review, execution and feedback lifecycle
    schemas.py              # Analysis input/output contracts
    state.py                # Batch execution state
  tests/

frontend/
  src/                      # Next.js research workbench and LangGraph conversation
  README.md

configs/
inputs/
runs/
docs/
```

Layer responsibilities:

- `frontend/`: Next.js research workbench. It connects to LangGraph Server for conversation, saved results and traceable scientific evidence.
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
- `DataAnalysisAgent`: runs fixed input/validation/archive safeguards and only explicitly selected `requested_tasks`; default is metadata `summary`. Interactive and batch analyses share the same operations. See [task-driven DA execution](docs/data_analysis_task_execution.md). Missing inputs and unavailable methods do not produce fabricated metrics.
- `ProtocolOptimizingAgent`: connects sample-level raw phenotypes and version-bound PA interventions, checks study sensitivity, and proposes competing falsifiable hypotheses. Optional literature/model budgets default to zero; composite scores are auxiliary, not absolute quality or success probabilities. See [PO scientific evidence](docs/po_scientific_evidence.md).

Optimization orchestration is explicit: `ProtocolOptimizingAgent` consumes audited Protocol/Data Agent outputs. If protocol or data inputs need reinterpretation, rerun the corresponding upstream agent first; the Orchestrator reports `upstream_rerun_required` instead of optimizing from incomplete PA/DA outputs.

## Execution Entrypoints

| Entry | Purpose | Implementation |
| --- | --- | --- |
| `orchestrator` | Main conversation and SubAgent dispatch | `agents/orchestrator.py:graph` |
| `protocol-optimization-agent --config configs/run_config.json` | Reproducible PA -> DA -> PO batch | `agents/pipeline.py` through `interfaces/cli.py` |

Each domain agent executes its own internal LangGraph `StateGraph`. The unused
no-op `workflow.py` skeleton has been removed; it was not an executable analysis.

Architecture simplification is recorded in [steps 1-3](docs/architecture_simplification_steps_1_3.md)
and [steps 4-8](docs/architecture_simplification_steps_4_8.md), alongside a frozen scientific baseline.
Legacy import aliases and the PA-only conversation have been removed. Use canonical
module paths and the single `orchestrator` conversation entrypoint.

```python
from protocol_optimization_agent.agents.pipeline import ProtocolOptimizationAgent
from protocol_optimization_agent.runtime.config import ProtocolOptimizationConfig

state = ProtocolOptimizationAgent(ProtocolOptimizationConfig.from_file("configs/run_config.json")).run()
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
    "orchestrator": "./backend/protocol_optimization_agent/agents/orchestrator.py:graph"
  }
}
```

The model proposes registered actions through `execute_scientific_action`.
`agents/actions.py` is the sole capability and availability list. PA/DA/PO own
their tool adapters, evidence extraction, result filtering and domain cache rules.
The main graph owns only configuration, planning, bounded execution, review and finalization.

Start a local/LAN LangGraph server from the repository root:

```powershell
cd E:\workspace\Pancreas\Agents_test
.\scripts\start_langgraph_server.ps1 -HostAddress 0.0.0.0 -Port 2024 -OutputDir "runs\protocol_analysis_agent_simplified_autofeatures"
```

Then connect `agent-chat-ui` to the LangGraph server using `orchestrator`.
It accepts standard chat `messages` and streams plan/tool/answer updates.

For LAN access, do not use `0.0.0.0` in the browser or frontend env. Use the machine LAN IP, for example:

```env
NEXT_PUBLIC_API_URL=http://10.203.168.222:2024
NEXT_PUBLIC_ASSISTANT_ID=orchestrator
NEXT_PUBLIC_AUTH_SCHEME=
```

Start the Next.js frontend from the repository root:

```powershell
.\scripts\start_frontend.ps1 -HostAddress 0.0.0.0 -Port 5173 -LangGraphApiUrl "http://10.203.168.222:2024" -AssistantId "orchestrator"
```

Open the frontend with the real host address, for example `http://10.203.168.222:5173`. If the browser previously stored another LangGraph URL, clear the agent-chat-ui setup in browser local storage or enter the LAN API URL again.

For all commands above, the backend scripts call the `protocol_agent` environment executables directly instead of `conda run`, avoiding Windows console encoding crashes.

## Agent Subgraph Diagram

The subgraph diagram is generated from the executable agent graphs by
`protocol_optimization_agent.subgraphs`, with human-readable scientific annotations.

The current project architecture, implemented technical details, completed work, and planned orchestrator direction are documented in [docs/current_project_architecture.md](E:/workspace/Pancreas/Agents_test/docs/current_project_architecture.md).

In IPython or Jupyter:

```python
from protocol_optimization_agent.subgraphs import render_core_agent_subgraphs_mermaid
from protocol_optimization_agent.analysis.visualization.mermaid import display_mermaid

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

The configured `output_dir` is a storage root, not a reusable result directory. Each pipeline/agent execution creates a unique run directory and prints or returns its actual output paths. Conversation graphs snapshot their configured PA/DA/PO source directories into the new run rather than modifying historical results. See [Run Archives](docs/run_archives.md) for input registration, manifests, verification, and migration details.

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

The only formal protocol input is `inputs/Protocols inputs/protocols.jsonl`.
The loader projects its structured stages/preparation/reagents into internal factor events and source identity rows.
It does not read sidecar CSVs or reconstruct protocols from SQLite. Missing, empty or invalid formal input fails explicitly.
Readiness and unknown numerical values are preserved; being a formal export does not imply scientific comparison readiness.
See [docs/protocol_input_contract.md](docs/protocol_input_contract.md) for the contract and migration details.

Protocol Analysis Agent outputs:

- `normalized_factor_events.jsonl`: evidence-preserving factor/event normalization with rule, LLM, or fallback provenance.
- `semantic_stage_annotations.jsonl`: stage-level biological program annotations for differentiation strategy interpretation.
- `semantic_step_annotations.jsonl` and `step_semantic_audit.json`: source-bound operations, scoped parameters, conditions and conservative step alignment. See [PA step semantics](docs/pa_step_semantics.md) for budgets, retrieval and evidence limits.
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
protocol-query collection
protocol-query find "Akgün 2024"
protocol-query compare protocol_a protocol_b
protocol-query chat "Akgün 2024 和 Sakuma 2023 protocols 有什么区别？"
```

The query layer reads only generated structured outputs such as `protocol_feature_matrix.csv`, `normalized_factor_events.jsonl`, `semantic_stage_annotations.jsonl`, provenance, relationship, and audit reports. It does not re-read papers or infer unsupported facts.

Backend API query endpoints:

- `POST /protocol-query/find`
- `POST /protocol-query/compare`

Experimental approval and feedback endpoints are disabled by default. Set
`PROTOCOL_EXPERIMENTAL_FEEDBACK_ENABLED=1` before starting the backend API to
enable them with the same authentication and project-permission checks.
The CLI `protocol-query chat` remains a single deterministic query, not a second conversational agent.

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

Formal Agent1 protocol export:

- `inputs/Protocols inputs/protocols.jsonl` (the only protocol source)

H5AD manifest:

- `protocol_h5ad_manifest.csv`

The manifest should minimally contain:

```csv
protocol_id,dataset_id,h5ad_path,condition_id,sample_id,cell_type_col,batch_col
```

## Design Notes

Conversation summaries, scoped scientific retrieval, source-bound memory writes,
and explicit recovery/forgetting rules are documented in [Memory Layer](docs/memory_layer.md).

ProtocolOptimizationAgent does not re-parse papers or protocols. It consumes Agent1 artifacts and treats low-confidence or validation-flagged protocol fields as lower-weight evidence.

Recommendations are explicitly labeled as:

- `evidence_supported_recommendation`
- `correlation_based_hypothesis`
- `low_confidence_suggestion`
- `requires_experimental_validation`

Correlation is not reported as causality unless an external literature or experimental evidence layer is later added.

### Execution Simplification (2026-10-06)

The primary graph is `configure -> plan -> execute -> review -> execute or finalize`. Only Orchestrator owns planning and bounded iteration; PA/DA/PO execute domain tasks. Action Registry is the sole capability list. Loop state is `request`, `plan`, `step_results`, `budget`, `stop_reason`; saved JSON is the numeric authority. DA validation v2 uses per-task checks instead of `po_ready`.

Legacy Python forwarding modules and `runtime` symbol re-exports have been removed. Use canonical paths such as `protocol_optimization_agent.agents.orchestrator`, `agents.pipeline`, `runtime.context`, and `interfaces.cli`. CLI command names, API routes and graph IDs remain unchanged. Regenerate legacy DA validation snapshots before optimization. See [execution and migration details](docs/planned_scientific_analysis.md).
