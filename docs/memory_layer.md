# Scoped Scientific Memory

Orchestrator remains the only planning loop. Memory uses the existing per-user,
per-project SQLite scientific store, without a vector database or another Agent.
Implementation: `backend/protocol_optimization_agent/science/memory.py`.

## Three Distinct Records

- Original messages: append-only `conversation_messages`, including tool messages.
- Working summary: `conversations` and immutable `conversation_revisions`, keyed by
  thread within the authenticated user/project store.
- Scientific memory: immutable tool observations and source-bound interpretations
  in the existing `records` table. A summary is not scientific evidence.

Summaries include protocol IDs, endpoints, stages, protocol versions, active user
constraints and their message references, pending questions, result references,
and opposing evidence references. Result references retain the originating run
and hash; a new run does not replace previous result JSON.

## Recovery And Explicit Updates

Keep a stable `config.configurable.thread_id` across turns and the same authorized
user/project/workspace. A newly constructed graph can restore the summary from
SQLite even without a checkpoint. Full LangGraph conversational replay still
requires the server's checkpoint history; summary recovery does not recreate or
substitute the complete message stream. With no thread ID, no shared default
conversation is created.

User/project identity is resolved through the existing authentication adapter.
Local development identity fields are not production authentication. Switching
user/project drops the previous scope from the model's view and restores only
the new scope's memory. Authenticated project write permission is required for
conversation and scientific memory writes.

Explicit updates are supplied as `request.memory_update`, for example:

```json
{
  "protocol_ids": ["p1", "p2"],
  "endpoints": ["maturity"],
  "user_constraints": ["Use independent biological replicates"],
  "remove_constraints": ["Only Beta cells"],
  "resolved_questions": ["Which endpoint should be compared?"]
}
```

Object/endpoint/stage/version lists replace their current summary fields; an
empty list clears that field. Constraints are added or explicitly removed by
exact text. Original messages, earlier revisions and scientific evidence are
not deleted. Replaying an old message cannot restore an explicitly removed
constraint. Natural language endpoint switches recognize the declared aliases;
constraint detection is deliberately conservative, not a general language parser.
Concurrent stale writers fail and must reload, rather than overwrite a newer
summary. Pending questions are resolved explicitly, not assumed answered.

## Retrieval And Writing Policy

`ScientificWorldModel.reasoning_context(question, filters=...)` accepts
`protocol_ids`, `endpoints`, `stages`, `protocol_versions`, and
`hypothesis_versions`. Nonempty fields are conjunctive; requested alternatives
within a field are disjunctive. IDs, stages and versions match exactly. Endpoint
aliases are explicit. Keyword/synonym relevance ranks compatible records only;
it cannot override an incompatible protocol or version. Missing conditions do
not count as matches. Current project-scale retrieval scans SQLite records;
indexed normalized fields or FTS can be added if measured scale requires them.

Each retrieved hypothesis/interpretation expands both supporting and opposing
evidence, including versions and source references. Missing evidence IDs remain
explicit. External literature citations remain citations, not automatically
verified facts. Batch PO history retains its existing numerical data contract.

Usable deterministic PA/DA outputs are stored as `tool_observation` evidence;
technical validity is not causal validity. PO/model conclusions are stored as
`ScientificInterpretation`, classified as interpretation or hypothesis, bound
to verified saved-result field paths or historical snapshot hashes. Status is
always `UNREVIEWED`; repeated generation never increases confidence or upgrades
the record to an established fact. Revised meanings require distinct records.

## Model Context Is Not Storage

Only the model view is bounded: recent complete message groups, compact tool
previews, the latest eight result references, and one source per active constraint.
All active constraints and summary opposition references are retained in that
view. The full retrieved history also contains supporting and opposing evidence.
Omitted tool content is explicitly unknown, not proof of absence. If critical
context cannot fit the budget, the run stops with `budget_exhausted:context`
rather than silently forgetting it. Context character limits are conservative
application bounds, not provider token-limit guarantees.

Every run archives `conversation_messages.json`, `session_summary_input.json`,
`session_summary.json` and `history_context.json`; the existing bundle manifest
hashes them alongside tool results and the final report. Explicit constraint
removal changes working memory only. It is not a personal-data deletion API.

## Verification

`backend/tests/test_memory.py` covers new-graph recovery, endpoint changes,
user/project/thread isolation, cross-scope state reuse, preserved constraints and
counterevidence in long conversations, explicit forgetting without evidence
deletion, stale-writer rejection, structured retrieval, immutable interpretation
status, context views, budget stops and authenticated write permissions.
Model behavior is mocked; real-provider end-to-end evaluation remains separate.
