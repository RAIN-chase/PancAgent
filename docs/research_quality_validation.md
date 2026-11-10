# Research Quality Validation

This increment strengthens existing modules without adding another Agent or scheduler.

## Delivered

- **PA input coverage:** formal `protocols.jsonl` records are preserved in
  `raw_protocol_input.json`. `input_coverage_ledger.json` assigns every input leaf
  a disposition: matrix projection, retained metadata/step/condition, filtered
  with reason, pending parsing, or not reported. Matrix projection requires an
  actual compiler mapping. Retention does not imply semantic understanding;
  paper-fulltext completeness is explicitly not evaluated. Direct Python callers
  without raw records receive a projected-input-only scope.
- **Four dimensions:** `final_report.json.assessment_dimensions` separates
  execution, independently checked numerical correctness, scientific
  applicability and question sufficiency. Successful execution is not a pass in
  the other dimensions.
- **Evaluation pilot:** [24 questions and offline grading](../evals/research_tasks/README.md),
  development/test study separation, no-data controls, three baseline names,
  hash-bound numbers, blind expert-rating fields, cost/latency/usage and repeated
  run variation. Questions are drafts, not expert-validated gold answers.
- **Method provenance:** maturity audits record installed Scanpy/library versions,
  scoring-source hash, parameters, gene-program hashes and reference tests.
  The [Scanpy scoring documentation](https://scanpy.readthedocs.io/en/stable/generated/scanpy.tl.score_genes.html)
  identifies the module-scoring primitive and Tirosh et al. (2016); this is not
  validation of organoid maturity. Notebook regression and an independent
  analytical fixture test implementation behavior, not paper-data reproduction.
- **PO:** literature excerpts retain source level/location/license where supplied.
  Explicit cell-type/stage/dose/endpoint mismatches remain archived but cannot
  directly support or oppose the scoped mechanism. Unknown scope remains unknown.
  Optional discriminating tests bind an endpoint and competing explanation with
  different qualitative predictions. Mechanism plausibility, observational
  association and experimental feasibility remain separate, reviewable axes.
- **Memory:** `method_experience` stores successful/failed execution conditions,
  versions, validation, failure reasons and immutable result references. Retrieval
  excludes explicit design mismatches and checks versions/source hashes. Every
  reuse requires current-input validation; no experience becomes biological fact.
- **Workbench:** conclusion support/opposition buttons resolve loaded source IDs
  and hashes. Exploratory results expose saved scripts/environment in the
  read-only evidence drawer. Missing sources remain disabled; no arbitrary server
  path is opened and inspecting a script never executes it.

## Still Required Before Scientific Performance Claims

1. Expert review of pilot questions, private gold, reference quantities and
   original protocol information-unit annotations. Freeze study-scoped inputs;
   a merged h5ad containing held-out studies must not enter development runs.
2. Execute all three baselines with the same frozen model/input/config/budget,
   repeat runs and independently grade evidence/method/answer quality. The offline
   grader does not execute these baselines or establish their performance.
3. Run the real rootless Linux isolation acceptance in
   [controlled execution](controlled_code_execution.md). Windows unit mocks do not
   establish Linux resource/network/credential isolation.
4. Reproduce one licensed paper reference dataset and expected outputs in a pinned
   environment before describing a method package as paper-reproduced. No new DA
   scientific method is promoted automatically by this change.
5. Independently check literature quote entailment and biological applicability;
   abstract retrieval and metadata matching do not constitute fulltext review.

The existing Action Registry remains the capability authority. Missing methods,
missing evidence and unmeasured evaluation fields are not silently filled in.

## Verification (2026-10-14)

- Full backend run: 331 passed, 1 skipped, 1 failed. The failure was an entrypoint
  test whose environment-variable allowlist omitted the two controlled-execution
  settings. After updating that explicit contract, 55 entrypoint/quality/memory/
  Orchestrator regression tests passed. Final grader adjustments: 8 quality tests
  passed. The entire suite was not rerun after these final small changes.
- Frontend: 10 unit/render tests passed; desktop and narrow-screen browser tests
  passed, including loaded conclusion-source navigation and unavailable opposing
  source controls. Browser transport uses fixtures, not live model responses.
- Changed Python modules passed Ruff; changed workbench components passed ESLint.
- The skipped test requires a real Linux rootless Docker engine. It remains an
  unverified deployment acceptance, not a pass.
