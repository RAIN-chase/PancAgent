# Data Analysis Agent Tool Plan

This document defines the initial tool surface for quantitative comparison of pancreatic/islet organoid h5ad datasets against in vivo reference atlases.

## Agent Inputs

- `reference_atlas_h5ad`: in vivo reference atlas in h5ad format.
- `organoid_h5ad_manifest`: one row per organoid dataset, sample, condition, or protocol-linked h5ad.
- `gene_programs`: manually curated lineage gene programs, grouped by lineage and program name.
- `metadata_config`: obs column names for cell type, lineage, batch, donor, protocol, sample, condition, and embedding keys.

## Current Framework Tools

The executable framework is in `backend/protocol_optimization_agent/agents/data_analysis.py`. The stable tool contracts are in `backend/protocol_optimization_agent/tools/h5ad_tools.py`.

| Tool id | Current status | Output |
| --- | --- | --- |
| `dataset_manifest_gate` | implemented placeholder | `h5ad_qc_report.json` |
| `cell_type_composition` | implemented placeholder | `cell_type_composition.csv` |
| `marker_score_analysis` | implemented placeholder | `marker_scores.csv` |
| `gene_program_maturity_score` | stub pending implementation | `maturity_scores.csv`, `gene_program_scores.csv` |
| `reference_similarity` | stub pending implementation | `similarity_scores.csv` |
| `regulatory_program_placeholder` | stub pending external tool | `regulatory_program_scores.csv` |
| `trajectory_placeholder` | stub pending external tool | `trajectory_scores.csv` |

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
2. Implement `gene_program_maturity_score` with user-provided lineage programs using average scaled expression, AUCell/ssGSEA, and reference-normalized z scores.
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
- `maturity_scores.csv`
- `similarity_scores.csv`
- `regulatory_program_scores.csv`
- `trajectory_scores.csv`
- `data_analysis_protocol_summary.csv`
- `data_analysis_tool_registry.json`
- `data_analysis_comparison_report.json`
- `h5ad_analysis_limitations.json`
