# Data Analysis Agent Tool Plan

## Scientific State Integration (2026-09-30)

DA now exports `phenotype_ir.json` and `phenotype_interpretation.json`. Numeric computation and scientific interpretation are separate: the IR preserves source values, method, units, logical sample/condition and row-level evidence. Optional 2-5 interpretation trajectories use copies of one numeric snapshot; no normalization, cell fractions or expression calculations are repeated by the interpreter. Disagreement, incomplete interpretations and invalid metric references require researcher review. The interpreter is disabled by default and can be enabled through `h5ad_config.interpretation` or an injected callback. See [scientific_world_model.md](scientific_world_model.md) for configuration and limits.

This document defines the initial tool surface for quantitative comparison of pancreatic/islet organoid h5ad datasets against in vivo reference atlases.

## Agent Inputs

- `reference_atlas_h5ad`: in vivo reference atlas in h5ad format.
- `organoid_h5ad_manifest`: one row per organoid dataset, sample, condition, or protocol-linked h5ad.
- `gene_programs`: manually curated lineage gene programs, grouped by lineage and program name.
- `metadata_config`: obs column names for cell type, lineage, batch, donor, protocol, sample, condition, and embedding keys.

Manifest rows describe logical cell subsets, not physical files. Multiple rows may point to one h5ad when `dataset_column`, `sample_column`, `condition_column`, or `protocol_column` distinguishes the cells. Leave a selector column empty when `dataset_id` is only an external identifier and does not equal values in `adata.obs`.

## Execution Paths And Quality Gate

`analysis_mode=standard` is the default. The control graph supplies fixed safeguards; `requested_tasks` selects which versioned operations execute. Default: `summary` only. Query genes, marker sets, DE options or gene programs do not implicitly schedule other tasks. `analysis_mode=exploratory` additionally accepts a sandbox-generated reproducibility bundle, without expanding the requested task list or executing arbitrary code in DA. See [task-driven execution and migration](data_analysis_task_execution.md).

Every run writes `data_analysis_validation.json`, separating `technical_qc`, `task_validity`, `metric_status` and `statistical_applicability`. Valid composition does not require maturity/reference calculations. Validation schema v2 removes the global `po_ready` flag. Protocol Optimizing checks technical QC plus task validity for explicit optimization endpoints (maturity by default); unrelated metric failures do not veto a valid task. Old v1 validation snapshots require rerunning DA. Task checks include:

- manifest dataset identity and h5ad path;
- actual selected cells and recorded obs filters;
- requested gene resolution against the chosen matrix source;
- explicit and available `X`, layer, or `raw.X` source;
- non-empty DE comparison groups;
- explicit DE expression scale;
- valid numeric results for the requested task only, with its method and applicability recorded.

The gate is deterministic readiness validation. LLM-authored quality labels are not statistical confidence probabilities.

## Current Framework Tools

The executable framework is in `backend/protocol_optimization_agent/agents/data_analysis.py`. The stable tool contracts are implemented in `backend/protocol_optimization_agent/analysis/phenotype/h5ad_tools.py` and `backend/protocol_optimization_agent/analysis/phenotype/external_tools.py`; old `tools/` aliases have been removed.

External tool source checkouts are stored under:

```text
external_tools/data_analysis/
```

Downloaded external sources:

| Tool | Local path | Source |
| --- | --- | --- |
| Scanpy | `external_tools/data_analysis/scanpy` | `https://github.com/scverse/scanpy` |
| scvi-tools | `external_tools/data_analysis/scvi-tools` | `https://github.com/scverse/scvi-tools` |
| scib-metrics | `external_tools/data_analysis/scib-metrics` | `https://github.com/yoseflab/scib-metrics` |
| CellTypist | `external_tools/data_analysis/celltypist` | `https://github.com/Teichlab/celltypist` |
| GSEApy | `external_tools/data_analysis/gseapy` | `https://github.com/zqfang/GSEApy` |
| pySCENIC | `external_tools/data_analysis/pyscenic` | `https://github.com/aertslab/pySCENIC` |
| CellOracle | `external_tools/data_analysis/celloracle` | `https://github.com/morris-lab/CellOracle` |
| CellRank | `external_tools/data_analysis/cellrank` | `https://github.com/scverse/cellrank` |
| scVelo | `external_tools/data_analysis/scvelo` | `https://github.com/theislab/scvelo` |

`DataAnalysisAgent.call_tool(tool_id, config)` can call the registered wrapper for each external tool. The wrappers currently return structured readiness results and dependency/source status; heavier numeric execution should be implemented inside the wrapper functions after runtime dependencies and h5ad test fixtures are available.

| Tool id | Current status | Output |
| --- | --- | --- |
| `dataset_manifest_gate` | implemented | `h5ad_qc_report.json` |
| `exploratory_analysis_bundle` | implemented artifact/validation contract | `exploratory/*` |
| `cell_type_composition` | implemented | `cell_type_composition.csv` |
| `query_gene_expression` | implemented | `gene_expression_summary.csv` |
| `differential_expression` | implemented | `differential_expression.csv` |
| `marker_score_analysis` | implemented | `marker_scores.csv` |
| gene-program mean expression | implemented, not reference-calibrated maturity | `gene_program_scores.csv` |
| `gene_program_maturity_score` | implemented; notebook Early/Late Scanpy scores from counts | `maturity_scores.csv`, `maturity_cells_*.csv`, `maturity_audit.json` when requested |
| `reference_similarity` | partial, unavailable for default execution | validation only; no placeholder metric file |
| GRN / trajectory wrappers | optional, numeric implementation pending | no default placeholder metric file |
| external tool discovery | optional, explicit wrapper calls | no default directory/dependency scan |

## First-Pass h5ad Execution

The local anndata-backed implementation supports:

- h5ad file and dependency validation.
- Dataset inventory: `n_obs`, `n_vars`, obs columns, layers, embeddings, and raw availability.
- Cell-type composition from a configured annotation column.
- Target gene expression summaries by all cells or by an obs grouping column.
- Marker/gene-program scores as mean expression over found target genes.
- Descriptive target-gene differences between two obs groups; explicit independent biological-repeat metadata enables exploratory Welch testing on repeat means with BH correction over the valid requested genes. Unknown design/scale, raw counts, paired units or fully confounded batches do not receive p/q values. This is not a validated counts/pseudobulk GLM or causal evidence; see [analysis contract](planned_scientific_analysis.md).

Example config:

```python
h5ad_config = {
    "annotation_cell_type_column": "cell_type",
    "batch_column": "batch",
    "lineage_column": "lineage",
    "marker_sets": {"beta_marker": ["INS", "IAPP", "MAFA"]},
    "query_genes": ["INS", "IAPP", "GCG", "SOX9"],
    "expression_groupby": "cell_type",
    "differential_expression": {
        "groupby": "cell_type",
        "group_a": "beta",
        "group_b": "alpha",
        "genes": ["INS", "GCG"],
    },
    "backed": True,
    "max_target_genes": 200,
}
```

Server-side constraints:

- Use `backed=True` for metadata and target-gene reads unless a downstream method requires in-memory AnnData.
- Do not densify the full matrix by default; current tools only materialize one target-gene vector at a time.
- Keep `.h5ad`, large `inputs/`, large `runs/`, and `external_tools/` out of commits.
- Use Linux absolute paths in server manifests.

Local test-data metadata observed on 2026-09-10 (files remain ignored and are not part of the production contract):

| File | Shape | Useful selectors/annotations |
| --- | ---: | --- |
| `GSE143783_Weng_2020.h5ad` | 440,000 x 22,804 | `Dataset`, `sample`, `stage`, `condition`, `replicate`; no final cell-type annotation column observed |
| `GSE268013_Meng_2025.h5ad` | 80,634 x 112,137 | `Dataset`, `sample`, `timepoint`; no final cell-type annotation column observed |
| `PHASE_MT_1_6_Epi_final_20260908.h5ad` | 68,155 x 5,000 | `Dataset`, `sample`, `corrected_cell_label`, `cell_class`, `batch`; `counts` layer, `raw`, scVI/scANVI embeddings |

These files validate backed metadata loading and flexible manifest slicing. They do not define future filenames or require one h5ad per organoid dataset. Before composition or cell-type matching is interpreted biologically, the two organoid test files still need a declared final annotation column or an updated annotated file.

## Recommended External Tool Categories

These tools can be added to the local tools registry as wrappers instead of being called directly from the agent. Each wrapper should accept h5ad paths plus a config dictionary and write deterministic CSV/JSON outputs.

### h5ad and Standard scRNA-seq Analysis

- `scanpy` / `anndata`: base h5ad loading, normalization audit, HVG selection, PCA, UMAP, neighbors, clustering, differential expression, marker scoring, and plotting.
- Wrapper to add: `scanpy_h5ad_profile_tool`.

### Reference Mapping and Latent-Space Similarity

- `scvi-tools` with scVI/scANVI/scArches: train or load a reference model, map organoid query cells into reference latent space, transfer labels, compare cell-type-level embeddings.
- Wrapper to add: `scvi_reference_mapping_tool`.

### Integration and Batch-Correction Quality

- `scib` or `scib-metrics`: batch mixing and biology-conservation metrics such as iLISI/cLISI, silhouette label/batch, graph connectivity, ARI/NMI, isolated label, kBET, and PCR comparison.
- Wrapper to add: `single_cell_integration_benchmark_tool`.

### Annotation and Label Transfer

- `CellTypist`: independent label transfer and manual annotation validation, preferably with custom pancreas/islet reference models when available.
- Wrapper to add: `celltypist_label_transfer_tool`.

### Gene Program and Pathway Activity

- `GSEApy`: ssGSEA/GSVA/preranked GSEA for lineage programs, stress programs, endocrine maturation, proliferation, EMT, hypoxia, unfolded protein response, and off-target lineage signatures.
- Add `decoupler` where pathway or TF activity scoring from PROGENy/Dorothea-style resources is needed.
- Wrapper to add: `gene_program_enrichment_tool`.

### Gene Regulatory Networks

- `pySCENIC`: regulon inference and AUCell TF activity scoring.
- `CellOracle`: context-specific GRN modeling, network comparison, and in silico perturbation simulation.
- Wrapper to add: `regulatory_network_comparison_tool`.

### Developmental Trajectory and Fate Bias

- `CellRank`: fate mapping, terminal-state probabilities, driver genes, and gene trend comparison.
- `scVelo`: RNA velocity and latent time when spliced/unspliced layers are present.
- `Palantir`: diffusion-map pseudotime, branch probability, and differentiation potential.
- Wrapper to add: `developmental_trajectory_tool`.

### Cell-Cell Communication

- Recommended future wrappers: `cellchatdb_or_liana_communication_tool` and `nichenet_ligand_target_tool`.
- Use for endocrine/mesenchymal/endothelial/ductal interaction differences when the organoid system contains support-cell populations.

### Perturbation and Protocol-Linking Metrics

- Add a local `protocol_phenotype_join_tool` to join protocol features from Protocol Analysis Agent with Data Analysis Agent outputs.
- Add a local `association_analysis_tool` that reports correlations and model coefficients with strict correlation-vs-causality labels.

## Minimum Next Implementation Order

1. Implement `scanpy_h5ad_profile_tool` for real h5ad loading, obs/var audit, composition, QC, marker scores, and gene program scores.
2. The counts-based `gene_program_maturity_score` is available; see [maturity scoring tool](maturity_scoring_tool.md). Reference calibration remains a separate scientific extension, not a claim of this descriptive delta score.
3. Implement `reference_similarity` using matched genes and matched cell types with cosine similarity and Euclidean distance on pseudobulk expression and latent embeddings.
4. Add `scib-metrics` to quantify whether the provided de-batched organoid h5ad preserves cell identity while reducing batch structure.
5. Add GRN and trajectory wrappers only after the base transcriptomic comparison is reproducible.

## Output Contract

The agent should always export:

- `h5ad_summary_by_protocol.jsonl`
- `h5ad_qc_report.json`
- `data_analysis_protocol_summary.csv`
- `data_analysis_tool_registry.json`
- `data_analysis_comparison_report.json`
- `data_analysis_query_result.json`
- `h5ad_analysis_limitations.json`
- `data_analysis_execution_plan.json`
- `data_analysis_task_results.json`
- `data_analysis_validation.json`

Analysis CSVs are exported only for explicitly selected operations. An unavailable
method returns a task failure without a fabricated metric table. `gene_program_score`
reuses the existing marker-mean calculation internally; it does not schedule a
separate marker task. `exploratory/analysis.py`, `environment.json`, `config.json` and `diagnostics.json`
are exported only in explicitly selected exploratory mode. Maturity/reference/GRN/
trajectory placeholder files are no longer part of the default output contract.
