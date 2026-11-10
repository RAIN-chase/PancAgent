# Current PancAgent Architecture

Current implementation, reviewed 2026-11-01. Start with the [README](../README.md).
Dated research notes and migration plans are historical context, not capability contracts.

## Execution Entrypoints

- Conversation: LangGraph graph ID `orchestrator`, configured in `langgraph.json`
  (authenticated deployment uses `langgraph.authenticated.json`).
- Batch: `agents/pipeline.py`, exposed by `protocol-optimization-agent`.
- HTTP: `interfaces/api.py`, exposed by `protocol-api`.
- Workbench: existing Next.js frontend; shared conversation across domain views.
- PA-only chat has been retired. Extraction remains an upstream workflow.

Orchestrator owns `configure -> plan -> execute -> review -> finalize`, with
bounded continuation from review. `agents/actions.py` is the sole capability
and routing registry. PA/DA/PO do not own nested autonomous planning loops.
Each execute wave takes up to three ready steps. Distinct read-only catalog,
protocol-step/assessment, phenotype-comparison and scientific-state actions
may run concurrently; their step records are committed in plan order before
one review. Actions that update domain outputs or scientific state remain
serial. This is in-process concurrency, not a distributed worker queue.
Set `PROTOCOL_PARALLEL_READ_ONLY=0` to run the same ready wave serially for
evaluation; the setting is recorded in each run's configuration.
Completed step records are journaled before the batch state export and are
recovered after a node retry. A process loss during a domain action before its
step record is journaled still requires action-level idempotency or operator
review; the in-process scheduler does not promise exactly-once side effects.
The action dispatcher passes PA its protocol output directory, DA its data
analysis output directory, and PO the three directories it needs to combine
upstream results. User/project/run identity is shared for authorization and
provenance. Cross-domain step inputs use validated result references.

## Six Layers

| Layer | Responsibility |
| --- | --- |
| `agents/` | Orchestrator, domain execution and fixed batch composition |
| `analysis/` | Deterministic protocol, phenotype and optimization computations |
| `science/` | Versioned entities, evidence, datasets and scoped scientific history |
| `runtime/` | Authorization, paths, model access, configuration and archives |
| `interfaces/` | CLI, HTTP and authenticated server adapters |
| `extensions/` | Opt-in experimental review, execution and feedback |

The frontend is an interface to these layers, not another analysis scheduler.
No directory migration of `runs/`, `users/` or `external_tools/` is required.

## Capability Boundaries

| Capability | Implemented / prerequisites | Validation boundary |
| --- | --- | --- |
| PA lookup, N-protocol comparison | Formal `inputs/Protocols inputs/protocols.jsonl`; deterministic compiler plus retained steps/events | Frozen matrix/unit/evidence regression; raw JSON coverage ledger does not establish paper completeness |
| DA expression, composition, marker/program scores | Authorized h5ad, declared expression layer and annotation as required by task | Task-level validity; metadata and scale remain explicit prerequisites |
| DA Alpha/Beta/Delta maturity | Six Early/Late gene programs; counts -> normalize_total(1e4) -> log1p; Early, Late and Late-minus-Early | Notebook regression and analytical scoring fixture, not cross-study biological validation |
| DA differential expression | Explicit comparison groups, scale and sample design | Cell-level or replicate-level exploratory results; no automatic causal/confirmatory interpretation |
| DA reference similarity / optional trajectory and GRN | Reference similarity remains partial; optional tool installation alone is not a validated method | Unavailable operations stay unavailable; no invented metrics |
| DA controlled sensitivity | Opt-in pinned rootless Linux executor, existing metric and independent-replicate design | Trusted numeric recomputation; exploratory; real Linux isolation acceptance remains required |
| PO hypotheses / proposals | PA/DA evidence, requested endpoints, comparability and scoped literature | Association and unvalidated competing hypotheses, not calibrated intervention success probabilities |

DA's `DataAnalysisAgent.run()` executes selected tasks with fixed input checks,
validation and export. Its conversation/action adapter owns request translation,
result filtering and evidence-envelope assembly. Both paths use the same phenotype
calculations; file naming and graph-node details are not caller responsibilities.

## State Authority

| State | Authority | Must not replace |
| --- | --- | --- |
| Run state | This run's plan, dependencies, results, budget and stop reason | Persistent scientific facts |
| Conversation memory | User intent, objects, constraints, clarification and source references | Saved measurements or original evidence |
| Scientific records | Source-bound observations, hypotheses, interpretations and evaluation versions | Live execution progress |

Saved result JSON plus input/config/method versions is the quantitative authority.
Reports and charts read those results, not measurements reconstructed from chat
Markdown. Interpretations remain interpretations even when repeatedly generated.
Historical evidence and method experiences require version, source-integrity and
current-design checks before reuse. Approvals bind a specific candidate evaluation;
completed run archives must not be rewritten.

## Contracts and Operations

- [Protocol input](protocol_input_contract.md), [PA semantics](pa_step_semantics.md)
- [DA execution](data_analysis_task_execution.md), [maturity method](maturity_scoring_tool.md)
- [PO evidence](po_scientific_evidence.md), [question acceptance](orchestrator_goal_review.md)
- [Data catalog and N-way comparison](data_catalog_and_multi_compare.md)
- [Memory](memory_layer.md), [scientific records](scientific_world_model.md), [run archives](run_archives.md)
- [Linux deployment](linux_deployment.md), [authorization](authenticated_deployment.md)
- [Research quality and outstanding validation](research_quality_validation.md)
- [Method admission, independent checks and failure recovery](methods_and_recovery.md)

The default local profile is not a multi-user security boundary. Server identity,
project permissions and artifact authorization are required before shared deployment.
