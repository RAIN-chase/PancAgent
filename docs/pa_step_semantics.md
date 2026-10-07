# PA Step Semantics Update

## Scope

PA keeps its existing fixed compilation graph and the formal `inputs/Protocols inputs/protocols.jsonl` input. The existing `protocol_semantic_interpreter` node now also calls `analysis/protocol/step_semantics.py::annotate_steps`. No new agent or planning loop was added. These annotations **never enter the numerical feature compiler**; matrix values, units and existing evidence mappings retain their original contract.

## Artifacts And Evidence

Each PA run additionally exports:

- `semantic_step_annotations.jsonl`: preparation/stage operations, optional nested steps, original text, stable IDs, JSON pointers, source-record/text hashes, operations, scoped parameters, states, conditions and dependencies.
- `step_semantic_audit.json`: retained source-unit counts, unknown operations, evidence support, rejected model proposals, unresolved references, unit/time conflicts and limitations. Also included in `llm_audit_report.json`.

The source array order is retained. It is not asserted to be a biological dependency. Input/output states and structured parameters carry their actual source-unit or stage scope; stage states are not assigned as every operation's before/after state. Textual model parameter/condition/dependency scopes retain exact source character spans. They are interpretations, not newly measured or compiled quantities.

Action labels from rules and models are explicitly interpretations. `source_support=verbatim_evidence` means the retained action excerpt was found in an upstream exported evidence quote/context. `structured_only_unverified` means the operation is present in a structured field but its quote support was not confirmed. Neither label independently verifies the publication or proves biological correctness. Unrelated stage quotes do not count as supporting operations. Novel/unparseable text remains in full, including unknown source units. Unreported values, explicit negation and unparseable operations remain distinct; no absence claim is inferred from a missing match.

Conditional execution is retained as text, not converted into a fixed date. Stage dates stay contextual. Negative/optional statements are not asserted to be executed operations. Unit checks reuse the compiler's existing deterministic concentration rules; semantic parsing does not supply missing units, doses or dates. Textual pronouns remain unresolved rather than being silently replaced with a cell identity.

## Bounded Model Parsing

`quality_filters.max_llm_step_semantics` bounds step-level model attempts per PA run, across all protocols. The default configuration explicitly sets it to **8**. An online `llm` provider and credentials are also required; the offline client makes no model calls. Set the budget to `0` for rule-only reproducible compilation. Existing normalization/stage-semantic/feature-discovery budgets remain explicitly `0`, and are independent of this new budget.

The request includes a JSON schema and versioned parsing instructions. Model output must cover the source text using contiguous nonoverlapping spans. Targets must occur in their action spans; scoped parameter/guard spans must lie inside their operation. Extra fields such as fabricated numeric doses/dates are rejected. Recognized operations cannot be omitted, relabeled or merged. Invalid output or a model transport failure falls back to rules and is audited. This validates grounding/coverage, not the scientific correctness of every model interpretation. No real online model call is part of the regression suite.

## Retrieval And Comparison

`protocol.compare` now includes `event_step_diff.semantic_alignment`, alongside the original matrix, complete events and structured representations. `align_steps` pairs unique canonical operations and literal targets, records parameter/condition/context/wording differences and source-order inversions. Repeated or unknown operations remain unresolved instead of being forced into pairs. Target-free pairs are expressly uncertain; every pair is `candidate_alignment_not_equivalence`. No global claim of biological equivalence or causal explanation is produced.

The registered `protocol.steps` action calls `ProtocolQueryEngine.get_protocol_steps(protocol_id, stage_id="")` through the existing PA adapter. Orchestrator can retrieve original operations, scoped parameters and conditions without recompiling protocols or calling an LLM. `protocol.assess` also includes step annotations and findings. The evidence envelope references persisted artifacts and source text; final explanations must respect their provenance and limitations.

Older run directories without the new annotations return `unavailable`/`semantic_artifact_missing`. Re-run PA once to create them; ordinary dialogue retrieval never repairs old outputs by inventing annotations.

## Verification And Limits

`backend/tests/test_protocol_step_semantics.py` covers source retention, condition/negation/repetition, parameter scope, unsupported units, contradictory times, model grounding and budgets, uncertain alignment, order inversions, old artifacts and registered retrieval. The frozen architecture baseline continues to check numerical matrices, units, missingness and original evidence.

`evals/protocol_language/cases.json` contains two exact excerpts checked against the local Nair 2019 protocol export and two synthetic edge cases. Expected operation order and guard types are developer-authored labels, **not an independently expert-adjudicated benchmark**. The tests check source-character retention, operation labels/order, guard retention and excerpt support. They do not estimate accuracy across the full corpus. Rules have a limited vocabulary; compound clauses, implicit operations and anaphora can remain unresolved. Add expert-labeled examples before claiming language-understanding accuracy, parameter-attachment accuracy or full omission detection.

Run `python evals/protocol_language/run_evaluation.py` in the `protocol_agent` environment to save fixture metrics and their denominators to `runs/evals_protocol_language/report.json`. The report explicitly lists unmeasured capabilities; perfect scores on four fixtures are not a claim of general PA accuracy.

Verified on 2026-10-07 in the local `protocol_agent` environment: **210 backend tests passed**, with one existing Starlette/httpx deprecation warning; backend/evaluation Ruff checks and targeted mypy checks for the six changed backend modules passed. The frozen numerical/evidence baseline remained unchanged. All seven fixture metrics were 1.0 on these four cases only. Real online model quality and full-corpus omission/parameter-attachment accuracy remain unverified.
