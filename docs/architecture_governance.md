# Architecture Governance

This document defines project-level constraints for scaling ProtocolOptimizationAgent from local development to server-side multi-agent execution over large h5ad datasets.

## Source, Data, And Runtime Boundaries

Keep repository source code separate from heavy runtime material.

```text
repo/
  backend/
  frontend/
  configs/
  docs/
  examples/

server data, outside git:
  /data/pancreas/h5ad/
  /data/pancreas/reference_atlases/

runtime workspace, outside git or ignored:
  runs/users/<user_id>/runs/<run_id>/
    protocol_analysis/
    data_analysis/
    protocol_optimization/
    _logs/
    run_manifest.json

optional external tool source/cache, outside git or ignored:
  external_tools/
```

Rules:

- `runs/` is runtime output, not source.
- `external_tools/` is dependency/source cache, not first-party source.
- Real `.h5ad` files must stay outside git.
- Commit only source, tests, docs, config templates, and small synthetic fixtures.
- Output contracts must be documented before they are consumed by another SubAgent.

## Multi-User Isolation

Runtime isolation must use explicit context, not only global environment variables.

Required context fields:

- `user_id`
- `run_id`
- `trace_id`
- `workspace_root`

Default scoped layout:

```text
{workspace_root}/users/{user_id}/runs/{run_id}/{agent_id}/
```

Agent IDs:

- `protocol_analysis`
- `data_analysis`
- `protocol_optimization`

Compatibility rule:

- Explicit `*_output_dir` values still win for backward compatibility only when they resolve under the authorized runtime workspace.
- Environment variables are accepted for local development.
- New server workflows should pass `user_id`, `run_id`, `trace_id`, and `workspace_root` through Orchestrator state.
- `PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR=1` may be used for local development, but should not be enabled for multi-user service deployment.
- Server deployment should derive `user_id` from authenticated service context and should not trust user-supplied filesystem paths.

## Cache And Readiness

Scientific state is project-scoped across runs at `{workspace_root}/users/{user_id}/research/{project_id}/scientific_state.sqlite3`. Production services must authorize project access and bind user identity server-side. SQLite revisions and researcher lifecycle transitions are transactional; immutable evidence and version history must be retained. See [scientific_world_model.md](scientific_world_model.md) for the implemented contracts and operator workflow.

Execution readiness does not establish scientific comparability. Missing cell line, stage, modality, reference, expression scale, normalization, method version, cell population or sampling conditions remain unknown and block numeric merging. Valid target endpoint counts and identified biological replicates, not directory/file counts, govern exploratory screening. Chat tools cannot approve an experiment or supply wet-lab feedback. Authenticated API/CLI researcher commands require project review permission and verified identity; local mode remains an explicit trusted operator interface.

Candidate identity and evaluation identity are distinct. A proposed candidate may be reassessed with immutable history; a reviewed/executed evaluation is locked, and approval must specify its exact current evaluation ID. SQLite is the authority for candidate exports. Testable hypothesis identity includes baseline version, intervention and background, rather than just feature/goal. Experimental feedback only enters numerical PO calculations after reviewed admission binds actual compiled PA features, samples and controls. Immutable datasets/results preserve prior numerical outputs and do not imply causality.

Readiness must validate output content, not just file presence.

Current checks:

- required output file existence
- empty required output files
- metadata/content fingerprints for required PA and DA outputs
- objective and new protocol feature payloads included in optimization cache keys

Rules:

- Changing `objective` invalidates cached Protocol Optimizing Agent results.
- Changing PA/DA output content invalidates cached Protocol Optimizing Agent results.
- Researcher experiment reviews and experimental feedback invalidate cached PO experiment-design results.
- A Data Analysis query with requested genes, groups, protocol, dataset, sample, or condition must not return unrelated cached rows.
- If cached rows exist but do not match the structured request, return `no_matching_cached_result` unless structured inputs are provided for execution.
- Only Orchestrator schedules upstream actions. The optimization tool never accepts raw PA/DA inputs or invokes upstream agents internally; absent or invalid outputs produce explicit dependency gaps.

## Orchestrator Evidence Envelope

Every Orchestrator-facing SubAgent response must return:

```json
{
  "schema_version": "orchestrator-evidence-envelope-v2",
  "trace_id": "...",
  "run_id": "...",
  "user_id": "...",
  "subagent": "...",
  "status": "...",
  "question": "...",
  "selected_operation": "...",
  "used_tools": [],
  "used_features": [],
  "evidence_refs": [],
  "result_refs": [],
  "validation": {},
  "confidence_caveats": [],
  "limitations": [],
  "provenance": [],
  "result_preview": {},
  "output_files": {}
}
```

Interpretation:

- `used_tools`: deterministic tool or SubAgent method names that produced the answer.
- `used_features`: protocol feature matrix columns, genes, marker sets, cell types, phenotype targets, or optimization candidate features.
- `evidence_refs`: references to protocol evidence, h5ad datasets, output files, feature provenance, or recommendation records.
- `confidence_caveats`: all caveats that affect scientific interpretation.
- `result_preview`: small answer-ready subset only; full data belongs in output files.
- `output_files`: paths to durable outputs for downstream agents or UI previews.
- `result_refs`: immutable result ID/path/hash/input/tool-version references attached by the scheduler after saving. Raw domain results do not contain a self-referential hash.
- `validation`: task-scoped validity and applicability, not a global readiness flag.

Envelope v2 removes duplicate `result` and PO top-level numeric aliases. Consumers read `result_preview`; final reports hold references and render values from saved JSON.

LLM policy:

- The LLM may summarize an evidence envelope.
- The LLM must not invent `used_features` or `evidence_refs`.
- The LLM must not write final numeric matrix values.
- Optimization language must label recommendations as hypotheses requiring experimental validation.

## Deterministic Protocol Matrix Policy

The Protocol Analysis matrix is a governed quantitative artifact.

Allowed matrix writers:

- deterministic compiler code
- validated registry rules
- structured JSON/events/stage fields
- graph topology derived from accepted events

Disallowed matrix writers:

- free-form LLM generation
- unvalidated natural-language claims
- inferred biology without structured source fields

Required governance outputs:

- `protocol_feature_matrix.csv`
- `protocol_feature_provenance.json`
- `quantification_coverage_report.json`
- `feature_discovery_audit_report.json`
- `llm_audit_report.json`

Every matrix value must be traceable to accepted structured input, deterministic transformation logic, or documented fallback behavior.

## Data Analysis Server Policy

Large h5ad execution is server-side work.

Rules:

- Use Linux absolute h5ad paths in manifests.
- Prefer backed or chunked access where possible.
- Do not densify full expression matrices by default.
- Emit aggregate outputs, not massive intermediate matrices.
- Store query outputs under the run-scoped Data Analysis directory.
- Record dataset path, obs columns, layer/raw settings, and filters in provenance.

Minimum data query operations:

- `summary`
- `gene_expression`
- `differential_expression`
- `marker_score`
- `cell_type_composition`
- `maturity`
- `reference_similarity`

## Frontend Protocol-Specific Contract

The current frontend may remain generic chat UI, but SubAgent outputs should support protocol-specific rendering.

The default frontend assistant id should point to `orchestrator`; `protocol_chat` remains available as a lower-level Protocol Analysis chat graph.

Recommended UI blocks:

- feature diff table
- evidence cards
- stage timeline
- h5ad query result table
- cell composition summary
- DE result preview
- heatmap preview
- optimization recommendation cards
- validation plan panel

Frontend should render from the evidence envelope and `output_files`, not parse free-form assistant prose.

## Deployment Configuration

Deployment should converge on one environment model.

Required variables:

```bash
PROTOCOL_WORKSPACE_ROOT=/srv/protocol-agent/runs
PROTOCOL_USER_ID=local_user
PROTOCOL_RUN_ID=manual_test
PROTOCOL_TRACE_ID=trace_manual_test
PROTOCOL_ANALYSIS_OUTPUT_DIR=
DATA_ANALYSIS_OUTPUT_DIR=
PROTOCOL_OPTIMIZATION_OUTPUT_DIR=
PROTOCOL_CHAT_LLM_PROVIDER=deepseek
PROTOCOL_CHAT_MODEL=deepseek-chat
PROTOCOL_CHAT_API_KEY_ENV=DEEPSEEK_API_KEY
PROTOCOL_CHAT_BASE_URL=https://api.deepseek.com
```

Notes:

- Empty explicit output-dir variables should be omitted in production so scoped paths are used.
- Local Windows scripts may remain for development, but Linux server docs should be treated as deployment authority.
- `.env.example` should document all variables; `.env` must stay untracked.

## Observability

Every Orchestrator run should write:

```text
runs/users/<user_id>/runs/<run_id>/run_manifest.json
runs/users/<user_id>/runs/<run_id>/_logs/tool_calls.jsonl
```

Run manifest fields:

- `schema_version`
- `created_at_utc`
- `user_id`
- `run_id`
- `trace_id`
- `workspace_root`
- `output_dirs`
- `inputs`
- `output_version`
- `extra`

Tool-call log fields:

- `timestamp_utc`
- `trace_id`
- `run_id`
- `user_id`
- `subagent`
- `operation`
- `status`
- `question`
- `output_files`

Run archival extension (2026-10-01):

- `RunArchive` now records actual prompts/responses, model parameters, reported token usage, model/tool latency, complete parameter files and intermediate results.
- Costs and unreported provider model revisions remain unknown, not zero.
- Input identity uses complete content hashes; registered, server-owned read-only dataset versions reuse their ingestion hash under an explicit immutable-storage contract. Metadata alone does not define data identity.
- Configured output roots are no longer reusable execution directories. Existing PA/DA/PO outputs are snapshotted into fresh run directories; completed call outputs have separate artifact copies.
- SQLite serializes journal updates, and manifests/logs are atomic views. Completed runs are sealed with `bundle_manifest.json` and reject updates.
- See [Run Archives](run_archives.md) for the complete file contract, migration, dataset registration, verification and crash/retention limits.

## Implementation Priorities

1. Keep `.gitignore` strict for `runs/`, `external_tools/`, `inputs/`, `.env`, and `*.h5ad`.
2. Make Orchestrator tools return evidence envelopes.
3. Route multi-user runs through `RunContext`.
4. Implement real Data Analysis h5ad operations on Linux.
5. Add frontend renderers for evidence envelopes.
6. Validate live streaming/provider usage reporting and apply server archive retention/backups.
