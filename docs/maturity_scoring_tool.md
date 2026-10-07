# DA maturity scoring tool

## Implementation

- Core deterministic tool: `backend/protocol_optimization_agent/analysis/phenotype/maturity_scoring.py::compute_maturity_scores`.
- Program loader: `load_maturity_programs` in the same module.
- DA adapter: `agents/data_analysis.py`; Action Registry: `agents/actions.py`.
- Available action ID: `phenotype.beta_maturity` (existing ID), operation: `maturity`.
  This action also supports Alpha and Delta, not only Beta.
- Provenance: extracted from the full counts-based pipeline of the owner's
  `Maturity_Scoring.ipynb`, rather than executing that notebook or its absolute paths.

## Calculation

1. Select cells using authorized manifest filters and the configured annotation
   column. Recognize Alpha/Beta/Delta and SC-Alpha/SC_Beta/SC-Delta aliases.
2. Prefer `layers/counts`; when no layer is specified, integer-like X may be used.
   An explicitly requested missing counts layer is an error. All source values
   must be finite, nonnegative and integer-like; saved normalized/log X is never
   a fallback. Integer-like values are a technical check, not proof of provenance.
3. Resolve gene names case-insensitively and remove terminal version suffixes.
   Ambiguous identifiers are rejected; absent and full-source all-zero genes are
   audited separately. Genes are unweighted memberships from the `gene` column.
4. Normalize target-cell counts to 1e4 per cell and log1p, then use
   [Scanpy score_genes](https://scanpy.readthedocs.io/en/stable/generated/scanpy.tl.score_genes.html)
   for Early and Late with `use_raw=False`, `ctrl_as_ref=True`, `n_bins=25`,
   `ctrl_size=50` (bounded by pool minus program union), and `random_state=0`.
   The background pool contains nonzero genes from the complete source dataset,
   before manifest or cell-type selection, matching the notebook.
5. Store float32 Early and Late scores and their Late-minus-Early delta per cell.
   `maturity_score` is the median cell delta in the selected cohort; also export
   mean delta, standard deviation, mean Early/Late, and cell count. Default gates:
   at least 20 target cells, 5 covered genes per program and 10 pool genes.

This is a **descriptive transcriptional program shift**, not an absolute maturity
scale, a maturity probability, independent-sample inference, or a causal effect.
Control bins are dataset-dependent. Method signatures bind Scanpy version,
parameters, program hashes, covered genes and gene pool; downstream comparisons
must also satisfy scientific context checks. Neither organoid/reference pairing
nor calibration is inferred automatically. To analyze a reference, submit a
separate manifest row with its actual annotation column and dataset_kind.

## Inputs And Invocation

Default programs: `inputs/Gene_Programs/{Alpha,Beta,Delta}_Stability_{Early,Late}_Program.csv`.
Only these six CSVs are allow-listed for source control; large h5ad and other
inputs remain ignored. A server may override their directory using
`PROTOCOL_MATURITY_PROGRAM_DIR`. Authenticated clients cannot supply arbitrary
program or h5ad paths: use the existing authorized artifact ID flow.

Configuration example: `configs/maturity_scoring.example.json`. Choose the real
annotation column, such as `epi_cell_type`, `scANVI_predictions_scANVI_label`, or
`scANVI_cell_label`; no implicit label-source guessing is performed.

Standalone local example:

```python
from pathlib import Path
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent

outputs = DataAnalysisAgent(
    output_dir=Path("runs/maturity_local"),
    h5ad_config={
        "requested_tasks": ["maturity"],
        "annotation_cell_type_column": "epi_cell_type",
        "maturity": {"cell_types": ["Alpha", "Beta", "Delta"]},
    },
).run([{
    "dataset_id": "organoid_001",
    "protocol_id": "protocol_001",
    "h5ad_path": "/server/datasets/organoid.h5ad",
}])
print(outputs["maturity_scores"])
```

For Orchestrator, submit action `phenotype.beta_maturity` with
`h5ad_manifest_rows`, `h5ad_config`, and optional `request.cell_type`.
English Alpha/Beta/Delta in a maturity question also select that lineage.
Without a requested lineage, all three are assessed. New maturity execution
requires a manifest, so a plan cannot silently use an unrelated cached score.

Install project optional dependencies with `pip install -e ".[h5ad]"` in the
server environment. Locally verified with Scanpy 1.11.5 in `protocol_agent`.
If the environment directory is read-only, point `NUMBA_CACHE_DIR` and
`MPLCONFIGDIR` to writable cache directories; no package-directory writes are
needed by the scoring implementation itself.

## Outputs And Limits

- `maturity_scores.csv`: cohort summaries and explicit failure rows.
- `maturity_cells_000.csv`, etc.: cell IDs, actual sample labels when available,
  Early/Late/delta scores for each manifest row. No large arrays enter LLM state.
- `maturity_audit.json`: parameters, pool fingerprint, source/covered/missing/
  all-zero genes, coverage and scientific caveats.
- Existing DA validation, phenotype IR, tool-call logs, run input hashes and
  immutable snapshots include this calculation and all six requested program CSVs.

Metadata is read without loading expression layers. Full-source raw-count
validation and pool detection are streamed. Each target cohort's sparse matrix
must still fit in memory for exact Scanpy binning; `max_matrix_bytes` guards an
estimated working size, not a hard process-memory ceiling. Independent chunk
scoring is deliberately not substituted because it changes the result. When
one file contains multiple samples, default cohort summaries remain pooled,
as in the notebook; submit explicit sample-selected manifest rows for separate
sample summaries. This tool does not rank protocols or perform replicate tests.

Regression tests: `backend/tests/test_maturity_scoring.py` compare dense/CSR/CSC
results against the notebook's direct Scanpy kernel, validate input failures,
program version changes, immutable snapshots, DA validation and conversation
dispatch. Production-size h5ad and the notebook's original study data must be
validated on the server separately; those data were not executed here.
