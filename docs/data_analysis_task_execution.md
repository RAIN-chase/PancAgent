# Task-Driven DA Execution

Updated: 2026-10-07.

## Responsibility

Orchestrator owns Plan/Execute/Review and decides whether to change methods or
stop. DA is a domain executor, not another autonomous researcher or code-writing
agent. No runtime-generated nodes, nested planning loop, silent fallback, new
task registry, or new numeric algorithm has been introduced.

```text
data_input_gate -> resolve_tasks -> dataset_inventory_builder
                -> execute_tasks -> data_analysis_audit
                -> phenotype_representation -> export_results
```

The Action Registry in `agents/actions.py` is the source of capability and
availability. Local `TASK_OUTPUTS` describes table/field contracts only.
Operations have separate execution methods in `agents/data_analysis.py`, reusing
the deterministic functions in `analysis/phenotype/`.

## Selection

`h5ad_config.requested_tasks` is the only task selector. Its default is
`["summary"]`; an explicit empty list performs only safeguards. Configuring
`query_genes`, DE groups, marker sets or programs does not schedule computations.
Task order is preserved and duplicates are removed.

| Task | Required Parameters | Result |
| --- | --- | --- |
| `summary` | Authorized manifest and declared cell filters | Metadata and technical QC, no phenotype calculations |
| `cell_type_composition` | Actual cell-type annotation | `cell_type_composition.csv` |
| `gene_expression` | Nonempty `query_genes`; expression source and optional groupby | `gene_expression_summary.csv` |
| `differential_expression` | Distinct groups, groupby, explicit expression scale; optional target genes | `differential_expression.csv` |
| `marker_score` | `marker_sets` mapping names to gene lists | `marker_scores.csv` |
| `gene_program_score` | `gene_programs` grouped by lineage/program | `gene_program_scores.csv` |
| `maturity` | Raw counts, annotation, Early/Late CSV coverage | `maturity_scores.csv`, cell files and audit |

Gene-program means reuse marker-mean arithmetic as an internal calculation, not
as another selected analysis. Maturity retains the notebook's counts-based
Scanpy method; no algorithm or maturity-probability claim changed.

`resolve_data_tasks()` generates plan v2 with actual steps, registry action IDs,
scoped parameters, precheck results and shared input/metadata prerequisites.
`execute_tasks` consumes those step parameters, not an independently inferred
list from options. Data inspection is shared; individual numeric tools can
perform their own scientific source/selection checks. Method-specific loading
(such as maturity CSVs) occurs only within that selected operation.

## Interactive And Batch

An Orchestrator action maps to exactly one DA task. For example,
`phenotype.gene_expression` requests only expression, even if the supplied config
contains composition, maturity, marker or DE settings. The returned envelope
includes `execution_plan`, `task_results`, task validation and result references.

For a multi-operation batch, explicitly supply the same task list:

```python
agent = DataAnalysisAgent(output_dir, h5ad_config={
    "requested_tasks": ["cell_type_composition", "gene_expression", "maturity"],
    "query_genes": ["INS", "IAPP"],
    "annotation_cell_type_column": "epi_cell_type",
    "maturity": {"cell_types": ["Alpha", "Beta", "Delta"]},
})
outputs = agent.run(manifest_rows)
```

The batch pipeline passes its `h5ad` config to this same implementation; it does
not maintain a second calculation path. `configs/default_config.json` now
explicitly requests composition and maturity for its optimization objective.
Reference similarity remains unavailable; it is not added to a full batch until
implemented and validated. DE without independent biological units remains
descriptive, even when technical computation succeeds.

## Failure And Archives

`data_analysis_task_results.json` records each selected task's status, whether its
numeric tool was attempted, row count, dataset failures and statistical caveats.
Missing parameters, invalid expression layers, missing repetitions, unavailable
methods and dependency failures remain visible. Independent requested operations
can still complete after another fails; DA does not automatically substitute an
unrequested method. Unknown/partial methods are rejected without executing tools
or fabricating metric tables. Malformed task-list types fail input validation.

Validation reads the resolved task list, rather than appending tasks because an
option exists. Unrequested metric checks return `not_requested`. Technical QC,
each metric's numeric validity and statistical applicability remain distinct.

Guaranteed files include the input/metadata summaries, QC, plan, task outcomes,
validation, limitations, phenotype IR and existing run archive/provenance files.
Only selected implemented analyses get result CSVs, including explicit failure
rows where appropriate. No unrequested empty CSVs or external inventory scan are
produced. Previously archived artifacts remain readable and are not deleted.

Migration: existing batch configs that relied on implicit computations must add
an explicit task list. The default optimization batch selects composition and
Beta maturity to match its stated objective. Consumers must not assume every result CSV exists. The
core phenotype tables, numeric fields and evidence bindings remain unchanged;
comparison-report metadata now uses schema v2 and lists actual requested axes.

## Regression Coverage

Tests exercise isolated operation calls/exports, shared metadata defaults,
deduplication, plan-parameter authority, batch numeric equivalence, independent
success after failures, missing inputs, unavailable methods, chat dispatch and
existing maturity numerical baselines. See `backend/tests/test_data_analysis_agent.py`
and `backend/tests/test_maturity_scoring.py`.
