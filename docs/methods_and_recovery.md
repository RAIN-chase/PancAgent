# Methods, Independent Validation and Failure Recovery

Current increment: 2026-11-01. The six layers and four Agent roles are unchanged.
`agents/actions.py` remains the sole capability registry. Method provenance is
scientific metadata, not a second routing registry.

## Method Admission

Prefer the existing formal expression, composition, differential-expression and
maturity tools over generated scripts. A new pathway/regulatory method must first
have a specific source, implementation version, environment, data-design/scale
prerequisites, output schema, reference data/results and failure tests. Installing
a package or registering an action name is not method validation. No new pathway
or regulatory calculation is claimed or registered in this increment.

Maturity audits now carry explicit input/output contracts, implementation/source
identity, failure cases and validation layers in `method_package`. Existing counts
normalization, gene programs and numeric kernels are unchanged. Notebook parity
and the analytical module-score fixture are distinct from hidden benchmarks and
expert biological validation; the latter two remain `not_performed`. The analytical
fixture validates a scoring primitive, not cross-study organoid maturity.

## Failure Policy

`analysis_flow.failure_policy()` uses explicit statuses and typed exceptions, not
model text or substring matching of errors. Selected DA task failure statuses are
retained; conflicting or unknown causes remain unknown rather than receiving a
guessed repair. Each executed step saves its classification and recovery policy
in `step_results.json`, also exposed in final assessment dimensions.

| Category | Allowed response |
| --- | --- |
| Parameter error | At most one changed-input repair per action in this run |
| Tool unavailable | Alternative action or operator setup; no same-action retry in this run |
| Missing data | Ask for authorized dataset/version or analysis scope; never invent data |
| Scientific prerequisite | Change the scientific question/design with explicit justification, or stop |
| Authorization | Stop; no same-action parameter workaround |
| Unknown failure | Diagnose or stop; no automatic retry |

The existing budget and duplicate-signature guards remain. New step IDs cannot
authorize unchanged-input retries after a failure. Review proposals and execution
both enforce this rule. Different independent actions may still complete; failed
dependencies retain the existing blocking behavior. Setup changes require a new
run. Changed inputs do not themselves prove scientific applicability: existing
task prechecks, evidence validation and question acceptance still apply.

Missing-data steps add an explicit clarification question even without an LLM.
No new autonomous repair Agent or nested retry loop is introduced.

## Independent Acceptance

`final_report.json.assessment_dimensions.steps[].independent_validation` separates
actual reference recomputation, expert review not assessed by Orchestrator, and
the explicit fact that model review is not independent validation. A model's
approval cannot set numerical correctness to verified. Existing hash/field-bound
results remain authoritative; unknown measurements are never replaced by prose.

Tests cover classification, bounded parameter repair, unavailable-tool retries,
missing-data clarification with independent work preserved, wrapped DA failures,
and model approval without independent checks. Expert adjudication, private hidden
benchmarks, Linux isolation acceptance and paper reference-data reproduction remain
separate acceptance tasks; this increment does not claim they were completed.

## Verification (2026-11-01)

Final related regression: **93 passed** (69.96 seconds), covering failure recovery,
Orchestrator goals/execution, maturity scoring, research quality, scoped memory and
architecture constraints. This includes 12 new failure/method-contract tests.
Ruff and whitespace checks passed. The entire backend suite and real Linux
container acceptance were not rerun in this increment; no live model benchmark
or expert biological assessment was performed.
