# Thin Orchestrator Refactor

Date: 2026-10-06.

## Execution Boundary

The only conversation entrypoint is `agents/orchestrator.py:graph`, graph ID
`orchestrator`. Its flow remains `configure -> plan -> execute -> review ->
execute or finalize`. Initialization and immutable run archives are retained.
No new Planner, Critic, Reporter or nested autonomous domain loop was added.

`agents/actions.py` owns the sole action contract, availability, input validation
and dispatch. The model is bound only to `execute_scientific_action`; dispatch
delegates to the owning domain, without another tool registry.

| Module | Owns |
| --- | --- |
| `agents/orchestrator.py` | Conversation, analysis plan, budget, bounded execution, review, report and archive |
| `agents/protocol_analysis.py` | PA action adapter, protocol assessment/query, source evidence and caveats |
| `agents/data_analysis.py` | DA adapter, expression request parsing, result filtering, task validation and evidence |
| `agents/protocol_optimizing.py` | PO adapter, upstream readiness, objective/input/code/feedback cache and candidate persistence |
| `analysis/` | Existing deterministic computations, independent of chat and database state |
| `science/` | Persistent scientific objects, evidence, evaluation versions and datasets |
| `runtime/context.py` | Shared authorized output resolution, tool context, evidence envelope and task logging |

Chat messages, a single analysis plan and the persistent scientific database
remain distinct. Endpoint comparison is owned by DA, not by the scheduler.
Batch execution still calls the same domain agents and calculation modules.

## Intentional Interface Changes

- Removed `agents/protocol_chat.py`, `interfaces/langgraph_server.py`, the
  `protocol_chat` graph and `POST /protocol-query/chat`. There are no forwarding aliases.
- Both LangGraph profiles and the frontend startup script select only `orchestrator`.
- Removed default maturity, reference-similarity, GRN and trajectory placeholder
  stages/files from DA. Maturity/reference actions remain partial, non-executable
  and explicitly invalid in task validation; unavailable methods do not create numeric evidence.
- Standard mode does not export `exploratory/*`; explicit exploratory mode still
  archives its script/environment/config/diagnostics and validation checks.
- Wet-lab API routes live in `extensions/experimental_feedback/api.py` and are
  disabled by default. To enable them, set
  `PROTOCOL_EXPERIMENTAL_FEEDBACK_ENABLED=1` before starting the API, or call
  `create_app(enable_experimental_feedback=True)`. Enabled routes retain server
  authentication, project permissions and evaluation-bound approval.
- Explicit `scientific-state` CLI lifecycle commands remain available. Existing
  admitted feedback, historical evidence and scientific records remain readable.

## Verification

Before refactoring: full backend suite, 159 passed, zero skipped. The existing
Starlette/httpx deprecation warning was present before this change.

Regression checks cover the frozen PA matrix/units/missingness/provenance,
direct deterministic comparison versus Orchestrator results, h5ad expression and
replicate-level DE, PO comparability/cache/evaluation versions, archive integrity,
server identity and extension gating. Numeric algorithms and scientific IDs were
not redesigned; missing placeholder files and the DA ownership label are intentional
contract changes, not altered quantitative results.

After refactoring:

- `D:/anaconda3/envs/protocol_agent/python.exe -X utf8 -m pytest -q backend/tests`:
  160 passed, zero skipped, one existing Starlette/httpx warning.
- `python -m ruff check backend`: passed.
- `git diff --check`: passed.
- `python -m mypy backend/protocol_optimization_agent --follow-imports=silent`:
  still fails with 25 errors in nine unchanged modules; no reported errors in
  the changed scheduler, domain adapters, runtime context or API extension.

The historical entrypoint snapshots remain unchanged. Regression tests explicitly
allow the retired PA-only graph/chat API and the new extension toggle, while
checking that all remaining CLI names, graph IDs and enabled API routes retain
their original contracts.
