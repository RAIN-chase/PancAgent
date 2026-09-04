# Data Analysis Agent Tool Plan

This document defines the initial tool surface for quantitative comparison of pancreatic/islet organoid h5ad datasets against in vivo reference atlases.

## Agent Inputs

- `reference_atlas_h5ad`: in vivo reference atlas in h5ad format.
- `organoid_h5ad_manifest`: one row per organoid dataset, sample, condition, or protocol-linked h5ad.
- `gene_programs`: manually curated lineage gene programs, grouped by lineage and program name.
- `metadata_config`: obs column names for cell type, lineage, batch, donor, protocol, sample, condition, and embedding keys.

## Current Framework Tools

The executable framework is in `backend/protocol_optimization_agent/agents/data_analysis.py`. The stable tool contracts are in `backend/protocol_optimization_agent/tools/h5ad_tools.py` and `backend/protocol_optimization_agent/tools/external_data_tools.py`.

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
| `dataset_manifest_gate` | implemented placeholder | `h5ad_qc_report.json` |
| `cell_type_composition` | implemented | `cell_type_composition.csv` |
| `query_gene_expression` | implemented | `gene_expression_summary.csv` |
| `differential_expression` | implemented | `differential_expression.csv` |
| `marker_score_analysis` | implemented | `marker_scores.csv` |
| `gene_program_maturity_score` | stub pending implementation | `maturity_scores.csv`, `gene_program_scores.csv` |
| `reference_similarity` | stub pending implementation | `similarity_scores.csv` |
| `regulatory_program_placeholder` | stub pending external tool | `regulatory_program_scores.csv` |
| `trajectory_placeholder` | stub pending external tool | `trajectory_scores.csv` |
| `external_tool_inventory` | implemented | `external_tool_inventory.csv` |

## First-Pass h5ad Execution

The local anndata-backed implementation supports:

- h5ad file and dependency validation.
- Dataset inventory: `n_obs`, `n_vars`, obs columns, layers, embeddings, and raw availability.
- Cell-type composition from a configured annotation column.
- Target gene expression summaries by all cells or by an obs grouping column.
- Marker/gene-program scores as mean expression over found target genes.
- Lightweight differential expression for configured target genes between two obs groups.

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
2. Upgrade `gene_program_maturity_score` from placeholder maturity rows to reference-normalized lineage maturity scores.
3. Implement `reference_similarity` using matched genes and matched cell types with cosine similarity and Euclidean distance on pseudobulk expression and latent embeddings.
4. Add `scib-metrics` to quantify whether the provided de-batched organoid h5ad preserves cell identity while reducing batch structure.
5. Add GRN and trajectory wrappers only after the base transcriptomic comparison is reproducible.

## Output Contract

The agent should always export:

- `h5ad_summary_by_protocol.jsonl`
- `h5ad_qc_report.json`
- `cell_type_composition.csv`
- `marker_scores.csv`
- `gene_program_scores.csv`
- `gene_expression_summary.csv`
- `differential_expression.csv`
- `maturity_scores.csv`
- `similarity_scores.csv`
- `regulatory_program_scores.csv`
- `trajectory_scores.csv`
- `data_analysis_protocol_summary.csv`
- `data_analysis_tool_registry.json`
- `data_analysis_comparison_report.json`
- `external_tool_inventory.csv`
- `data_analysis_query_result.json`
- `h5ad_analysis_limitations.json`
