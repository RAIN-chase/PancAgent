# PO: intervention, phenotype and mechanism evidence

## Scientific scope

PO connects PA intervention descriptions to observed DA endpoints and proposes
competing, falsifiable hypotheses. It is not a validated intervention-effect
predictor. No new Agent, autonomous domain loop or graph database is introduced.
The existing PO graph and Orchestrator Plan/Execute/Review remain in charge.

## Implementation

- `agents/protocol_optimizing.py`: input integration, execution and export.
- `analysis/optimization/evidence.py`: sample-level joint evidence, protocol
  identity checks, per-endpoint cohorts and replicate-aware summaries.
- `analysis/optimization/association.py`: raw-endpoint Pearson screens,
  co-intervention collinearity flags and whole-study removal sensitivity.
- `analysis/optimization/mechanisms.py`: bounded source retrieval plans and
  source-constrained qualitative mechanism explanations with competing hypotheses.
- `runtime/literature.py`: optional PubMed abstract adapter, not a mechanism judge.
- `analysis/optimization/assessment.py`: auxiliary objective scores and
  similar-protocol reference assessment, not counterfactual prediction.
- `analysis/optimization/experiment_design.py`: test specifications and
  researcher-review candidates; ordinal utility is not success probability.

## Identity and quantitative evidence

Each observation retains protocol/dataset/sample/replicate/study identity, raw
endpoint value, unit, method, expression scale, normalization, sampling context,
source references and a JSON field path in `analysis_dataset.json`. Missing
uncertainty is explicitly unknown. Cell counts are not biological replicates.

DA input manifest entries may declare `protocol_snapshot_id` or `protocol_version`.
The snapshot is the existing `stable_id("protocol_snapshot", pa_matrix_row)` of
the PA CSV row as read, not a filename hash or an invented version. Alternatively,
both PA and DA must explicitly provide the same protocol version. Conflicting
versions are excluded before rechecking cohort sizes and biological replicates.
Matching IDs alone permit labeled exploratory joins, not experimental approval.
Version binding is a declared provenance link, not proof that a wet experiment
actually followed every step. Verified target-sample versions are necessary but
not sufficient for approval; scientific and researcher-review gates still apply.

Comparability is checked separately for explicitly requested target endpoints.
An invalid stress measurement need not block a valid maturity comparison.
Use family-qualified names when two metrics share a label, for example
`objective.comparison.endpoints = ["maturity:Beta", "cell_identity:Beta"]`.
Original metric IDs, labels and values remain unchanged in the saved dataset;
the joint comparison exposes both the qualified target and `raw_endpoint`.
Overlapping raw and qualified requests for the same metric are rejected. An
ambiguous raw target is not silently merged across incompatible methods/scales.
Different scales or methods are not silently merged; use an explicit, valid
stratum or externally justified preprocessing rather than implicit correction.

Repeated measurements are averaged within a biological replicate, then across
replicates within each protocol/study. The exploratory protocol-level screen
weights study means equally. Observed replicate ranges are not confidence
intervals. Missing study identity prevents interpreting leave-study-out results
as cross-study stability. The screen does not adjust for batch, cell line, time
or co-intervention confounding, estimate significance, or establish power.

Raw endpoints are primary. Existing normalized composite scores are auxiliary,
cohort-relative summaries; changing the cohort can change the score. Candidate
feature order is screening order, not intervention benefit or success ranking.
Pearson coefficients are labeled correlations, not estimated treatment effects.

Whole-study removal recomputes coefficients after removing every protocol
observed in that study, including protocols appearing in several studies.
Unknown studies, fewer than three studies or insufficient remaining protocols
produce `not_evaluable`. Direction consistency is descriptive sensitivity only,
not prediction on held-out experiments or causal confounder control.

## Mechanism evidence and budgets

PA reagent-use references are labeled `protocol_use`, DA observations
`phenotype_observation`, and literature excerpts `mechanism_literature`.
Feature keywords are topic hints, never mechanism evidence. Without suitable
literature, explanations stay unknown; alternatives and falsification criteria
are still returned. Supplied or historical opposing evidence is preserved even
when a model omits it. Models cannot cite nonexistent observations/sources,
reverse a supplied source classification or add numeric prediction fields.
These schema checks do not replace expert verification of biological entailment.
Qualitative expected directions are recorded per endpoint; one overall direction
is not applied to both maturity and stress. Unspecified endpoints remain unknown.

Default objective settings are offline:

```json
{"mechanism": {"max_llm_calls": 0, "max_literature_queries": 0, "papers_per_query": 3}}
```

To opt in, set explicit budgets, for example 6 model calls, 4 literature queries
and 3 papers per query. Query planning uses the same model budget as explanation.
Calls/queries have hard limits of 20 and papers per query of 5. Model-generated
query plans must include a challenge query; query audit reports whether a
challenge search actually completed. Small budgets can leave coverage incomplete.
Source snapshots, query intent, errors and budget use are saved. Inputs rejected
before dispatch do not count as model calls; literature-query budgets count
attempts, including privacy-blocked or failed queries. Oversized model inputs
return a recorded limitation rather than being silently truncated into proof.
The first 100 candidate observations are supplied to the model with the total
count; all observations remain archived and numeric calculations use the full
validated cohort. References must point to the transmitted subset.

Batch uses the configured JSON LLM client; the conversation tool uses server-owned
`PROTOCOL_CHAT_*` provider settings. Direct Python use may inject `llm_client` and
`literature_search`; no credentials or arbitrary file URLs come from model output.
Never enable hosted model calls unless sending authorized scientific context to
the configured provider is permitted. PubMed queries reject sample/dataset IDs
and paths, but this guard is not a substitute for deployment privacy review.

Provide local sources as `mechanism_literature_evidence.json` in the authorized DA
output directory, or as `objective.mechanism.evidence`. Example input:

```json
{"records": [{
  "feature": "reagent_factor_x", "endpoint": "beta",
  "doi": "REPLACE_WITH_VERIFIED_DOI", "quote": "REPLACE_WITH_EXACT_SOURCE_EXCERPT",
  "stance": "opposes", "applicability": {"cell_line": "unknown", "stage": "unknown"}
}]}
```

Do not treat the example placeholders as scientific evidence. A real source needs
an excerpt and a PMID, DOI or HTTP(S) URL. Provided excerpts and classifications
remain unverified until reviewed. Retrieval returns abstracts, not full papers,
and source IDs bind saved records, not independently verified provided excerpts,
relevance or causal proof. The adapter
follows NCBI [E-utilities documentation](https://www.ncbi.nlm.nih.gov/books/NBK25501/)
and [usage limits](https://www.ncbi.nlm.nih.gov/books/NBK25497/), with fixed NCBI
endpoints, timeouts, response-size limits and per-process pacing. Multiple server
workers require a shared rate limiter before enabling concurrent retrieval.

Explicit queries are also accepted in `objective.mechanism.queries`, with fields
`feature`, `query` and `intent` (`neutral`, `support`, `challenge`). They are
executed only within the enabled retrieval budget. Retrieval failures produce
audited partial evidence rather than fabricated mechanism explanations.

## Saved outputs and cache

- `joint_evidence.json`: full raw observations, PA steps/events, source links,
  identity/version status, per-endpoint gates and exclusions.
- `endpoint_associations.json`: replicate-aware endpoint summaries, correlations,
  exact metric/observation IDs, confounding flags and leave-study-out folds.
- `mechanism_evidence.json`: supplied/retrieved excerpts, support/opposition,
  explanations, competitors, falsification, retrieval/model audit and budgets.
- `similar_protocol_reference.json`: similar observed protocols, endpoint
  references and an explicitly null candidate outcome estimate.
- Existing `analysis_dataset.json`, `analysis_result.json`, hypothesis/candidate
  records, proposal JSON/Markdown and run archives remain available.

The three new evidence artifacts are returned to chat as labeled previews with
complete-artifact references, rather than duplicating all samples and abstracts
in the Orchestrator context. Complete evidence is preserved in saved JSON.

The current reference artifact and graph node replace the misleading
`counterfactual_protocol_predictions` / `counterfactual_protocol_scorer` names.
Historical run files are not deleted or rewritten. Cache contract v4 includes
PA semantic annotations, local mechanism sources, objective, upstream data,
code and conversation-model configuration. New evidence artifacts must exist
for reuse. Cached retrieved sources are frozen evidence snapshots; finding newer
papers requires an explicit rerun, not an invisible cache refresh. Scientific
candidate persistence and approval continue to use the existing versioned store.

## Verification

Run `python -m pytest -q backend/tests/test_po_scientific_evidence.py` and the
existing PO/scientific-state tests. The new regressions cover source/value
binding, identity mismatch, endpoint-specific validity, replicate weighting,
study-driven reversals, opposition retention, invalid model citations and bounded
retrieval using local mocks. No live NCBI or hosted-model invocation is required.

`evals/protocol_optimization/run_evaluation.py --output .tmp/po_boundary_eval.json`
compares simple correlations, study sensitivity, observed-only similar references
and a source/no-source mechanism ablation on five explicitly synthetic fixtures.
It tests scientific boundaries, not measured ranking gains or biological efficacy.
Real retrospective study-held-out performance and new-protocol experimental
validation remain future work.

Verification on 2026-10-07 in `protocol_agent`: full backend regression passed
236 tests before the final preview-only regression was added; final PO evidence
suite passed 27 tests. Final relevant PO/scientific-state/Orchestrator regression
passed 74 tests after endpoint qualification and chat previews. Ruff and targeted
mypy passed for the nine touched core modules. Five synthetic evaluation cases
passed; frozen matrix/unit/missingness/evidence/Phenotype-IR baseline was unchanged.
One existing Starlette/httpx deprecation warning remains. Live PubMed and hosted
LLM behavior, biological efficacy and retrospective ranking gains were not tested.
