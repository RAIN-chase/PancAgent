# Structure Cleanup: 2026-11-01

This is a refactoring record, not a scientific capability specification. Use
[current architecture](current_project_architecture.md) for current behavior.

## Scope

Preserve the existing six layers, algorithms, data formats, graph IDs and CLI
commands. Preserve uncommitted work already present before this cleanup. Do not
move or delete input data, scientific databases, run evidence or third-party tools.

DA has two entry interfaces:

- `agents/data_analysis.py`: `DataAnalysisAgent.run()`, task resolution, graph,
  task execution, validation and export. Batch callers keep this interface.
- `agents/data_actions.py`: `execute_data_action()` and the existing decorated
  `data_analysis_agent` tool. Owns request/config translation, authorized input
  resolution, saved-result selection, filtering and evidence-envelope assembly.
  The registry calls this adapter directly; it invokes the same DA execution.

No legacy forwarding import is kept. Internal callers and tests migrate together.
Algorithm extraction, PA/PO restructuring and new scientific methods are separate
work; this change does not create a generic service, utility layer or registry.

## Reused Regression Contracts

| Task | Existing regression authority |
| --- | --- |
| PA comparison | `test_architecture_baseline.py`: frozen matrix, units, missingness, source IDs and step/event details |
| DA expression/composition | `test_data_analysis_agent.py`: direct computation vs task output, task selection and missing-input behavior |
| Maturity | `test_maturity_scoring.py`: notebook kernel, fixed seed, gene-program identity, counts layer, normalization, Early/Late and difference; absolute tolerance `1e-7`, relative tolerance zero |
| PO | `test_po_scientific_evidence.py` and scientific gate tests: version identity, counterevidence, scope and unresolved applicability |
| Orchestrator | `test_orchestrator_goals.py`: dependencies, acceptance, stopping and immutable result bindings |

Do not regenerate `evals/architecture_baseline/expected.json` during refactoring.
Run IDs, timestamps, output roots and source-code fingerprints may change; numerical
values, units, missingness, evidence meaning and permission checks may not.
Model prose need not match verbatim. Existing offline fixtures do not establish
biological validity, online model performance or Linux container isolation.

## Reading and Browsing

README is the navigation entry; current architecture describes only implementation.
Migration plans are marked historical rather than moved en masse. VS Code hides
rebuildable caches/dependencies; run archives and external tools are excluded from
default text search but remain visible and physically untouched. Git ignore and
runtime configuration are unchanged.

## State Authority

Run state owns progress, conversation memory owns intent/context, scientific
records own source-bound versioned observations and hypotheses. See the explicit
authority table in current architecture. None can silently substitute for another;
saved JSON, input identity and evidence references remain numeric authority.

## Baseline and Verification

Before moving DA code, the existing full backend suite completed with **333
passed, 1 skipped**, no failures. The skip is the opt-in real Linux isolation
test; the existing warning is Starlette's httpx TestClient deprecation.

The moved action-adapter function bodies were compared verbatim with their
pre-cleanup source and are unchanged. Imports and locations changed, not request,
filtering, numeric or evidence behavior. All 9 architecture constraint tests passed,
including the new one-way DA dependency/no-forwarding-export check. Ruff and
documentation link checks passed. Full post-cleanup results are recorded below.

Post-cleanup full backend suite: **333 passed, 1 skipped** (295.10 s), the same
existing warning and isolation skip, no failures. The new architecture constraint
was run separately with the architecture module: **9 passed**. Machine-readable
full-run output is in ignored `.tmp/structure-after.xml`; this record retains the
summary without promoting temporary output into scientific evidence.
