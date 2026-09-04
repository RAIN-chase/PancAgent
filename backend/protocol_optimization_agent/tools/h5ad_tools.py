from __future__ import annotations

import math
from pathlib import Path
from typing import Any

DEFAULT_CELL_TYPE_COLUMN = "cell_type"
DEFAULT_BATCH_COLUMN = "batch_id"
DEFAULT_DATASET_KIND = "organoid"
DEFAULT_MAX_TARGET_GENES = 200


DATA_ANALYSIS_TOOL_REGISTRY: list[dict[str, Any]] = [
    {
        "tool_id": "dataset_manifest_gate",
        "category": "input_qc",
        "status": "implemented_placeholder",
        "purpose": "Validate reference atlas, organoid h5ad rows, annotation columns, and dataset linkage fields.",
        "expected_inputs": ["h5ad_manifest", "reference_atlas_h5ad", "organoid_h5ad"],
        "outputs": ["dataset_inventory", "manifest_findings"],
    },
    {
        "tool_id": "cell_type_composition",
        "category": "cellular_composition",
        "status": "implemented",
        "purpose": "Quantify annotated cell type abundance per dataset, sample, condition, and protocol.",
        "expected_inputs": ["annotated_organoid_h5ad", "cell_type_column"],
        "outputs": ["cell_type_composition.csv"],
    },
    {
        "tool_id": "query_gene_expression",
        "category": "gene_expression",
        "status": "implemented",
        "purpose": "Summarize target gene expression by dataset and optional obs grouping column.",
        "expected_inputs": ["h5ad_path", "target_genes", "groupby", "layer", "raw/use_raw"],
        "outputs": ["gene_expression_summary.csv"],
    },
    {
        "tool_id": "differential_expression",
        "category": "differential_expression",
        "status": "implemented",
        "purpose": "Compute lightweight target-gene differential expression between two obs groups.",
        "expected_inputs": ["h5ad_path", "groupby", "group_a", "group_b", "target_genes"],
        "outputs": ["differential_expression.csv"],
    },
    {
        "tool_id": "gene_program_maturity_score",
        "category": "transcriptional_maturity",
        "status": "stub_pending_implementation",
        "purpose": "Score lineage-specific maturity from user-provided gene program lists across organoid and reference datasets.",
        "expected_inputs": ["gene_programs", "lineage_column", "expression_matrix"],
        "outputs": ["maturity_scores.csv", "gene_program_scores.csv"],
    },
    {
        "tool_id": "reference_similarity",
        "category": "reference_mapping",
        "status": "stub_pending_implementation",
        "purpose": "Compare matched organoid and in vivo reference cell types with cosine similarity and Euclidean distance.",
        "expected_inputs": ["reference_atlas_h5ad", "organoid_h5ad", "matched_cell_type_pairs"],
        "outputs": ["similarity_scores.csv"],
    },
    {
        "tool_id": "marker_score_analysis",
        "category": "marker_expression",
        "status": "implemented",
        "purpose": "Score canonical and user-provided marker sets for target, immature, stress, proliferation, and off-target states.",
        "expected_inputs": ["marker_sets", "expression_matrix"],
        "outputs": ["marker_scores.csv"],
    },
    {
        "tool_id": "regulatory_program_placeholder",
        "category": "gene_regulatory_network",
        "status": "stub_pending_external_tool",
        "purpose": "Reserve the contract for SCENIC/CellOracle-style TF activity and regulatory network comparison.",
        "expected_inputs": ["expression_matrix", "motif_or_grn_resources"],
        "outputs": ["regulatory_program_scores.csv"],
    },
    {
        "tool_id": "trajectory_placeholder",
        "category": "developmental_dynamics",
        "status": "stub_pending_external_tool",
        "purpose": "Reserve the contract for pseudotime, lineage trajectory, and differentiation-state comparison.",
        "expected_inputs": ["expression_matrix", "neighbors_or_embedding"],
        "outputs": ["trajectory_scores.csv"],
    },
    {
        "tool_id": "scanpy_h5ad_profile_tool",
        "category": "transcriptome_profile",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/scverse/scanpy",
        "purpose": "Load h5ad files and compute QC, composition, marker, DEG, embedding, and clustering summaries.",
        "expected_inputs": ["h5ad_path", "metadata_config", "marker_sets"],
        "outputs": ["h5ad_qc_report.json", "cell_type_composition.csv", "marker_scores.csv"],
    },
    {
        "tool_id": "scvi_reference_mapping_tool",
        "category": "reference_mapping",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/scverse/scvi-tools",
        "purpose": "Map organoid cells into a reference latent space and support scVI/scANVI/scArches label transfer.",
        "expected_inputs": ["reference_atlas_h5ad", "query_h5ad", "model_path"],
        "outputs": ["similarity_scores.csv", "label_transfer_scores.csv"],
    },
    {
        "tool_id": "single_cell_integration_benchmark_tool",
        "category": "integration_quality",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/yoseflab/scib-metrics",
        "purpose": "Quantify batch mixing and biological conservation after integration or de-batching.",
        "expected_inputs": ["integrated_h5ad", "batch_key", "label_key", "embedding_keys"],
        "outputs": ["integration_quality_metrics.csv"],
    },
    {
        "tool_id": "celltypist_label_transfer_tool",
        "category": "annotation_validation",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/Teichlab/celltypist",
        "purpose": "Validate manual annotations using independent classifier-based label transfer.",
        "expected_inputs": ["h5ad_path", "celltypist_model_path", "manual_annotation_key"],
        "outputs": ["label_transfer_scores.csv"],
    },
    {
        "tool_id": "gene_program_enrichment_tool",
        "category": "pathway_and_program_activity",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/zqfang/GSEApy",
        "purpose": "Compute ssGSEA, GSVA-like, or preranked enrichment for lineage and pathway gene programs.",
        "expected_inputs": ["h5ad_path", "gene_programs"],
        "outputs": ["gene_program_scores.csv", "pathway_activity_scores.csv"],
    },
    {
        "tool_id": "regulatory_network_comparison_tool",
        "category": "gene_regulatory_network",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/aertslab/pySCENIC",
        "purpose": "Infer or score regulons and compare TF activity between organoid and reference cell types.",
        "expected_inputs": ["h5ad_path", "tf_list", "motif_rankings", "motif_annotations"],
        "outputs": ["regulatory_program_scores.csv", "regulon_activity_matrix.csv"],
    },
    {
        "tool_id": "celloracle_grn_simulation_tool",
        "category": "gene_regulatory_network",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/morris-lab/CellOracle",
        "purpose": "Build context-specific GRN models and run in silico TF perturbation comparisons.",
        "expected_inputs": ["h5ad_path", "base_grn", "cluster_or_cell_type_key"],
        "outputs": ["grn_edge_scores.csv", "perturbation_simulation_scores.csv"],
    },
    {
        "tool_id": "cellrank_fate_mapping_tool",
        "category": "developmental_dynamics",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/scverse/cellrank",
        "purpose": "Compare terminal-state probabilities, lineage fate bias, driver genes, and gene trends.",
        "expected_inputs": ["h5ad_path", "time_key_or_velocity_key", "lineage_key"],
        "outputs": ["trajectory_scores.csv", "fate_probability_scores.csv"],
    },
    {
        "tool_id": "scvelo_velocity_tool",
        "category": "developmental_dynamics",
        "status": "recommended_external_wrapper",
        "source_url": "https://github.com/theislab/scvelo",
        "purpose": "Compute RNA velocity, latent time, and velocity driver genes when spliced/unspliced layers exist.",
        "expected_inputs": ["h5ad_path", "spliced_layer", "unspliced_layer"],
        "outputs": ["velocity_scores.csv", "latent_time_scores.csv"],
    },
]


def summarize_h5ad_manifest_entry(row: dict[str, str], h5ad_config: dict[str, Any]) -> dict[str, Any]:
    """Summarize one h5ad manifest row without requiring scanpy/anndata.

    This function is the stable tool boundary for future scanpy/anndata-backed analysis.
    The current implementation is deterministic and reports placeholder metrics when
    the h5ad file is unavailable or h5ad dependencies have not been installed.
    """
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    file_exists = Path(normalized["h5ad_path"]).exists() if normalized["h5ad_path"] else False

    limitation = {
        **common,
        "h5ad_path": normalized["h5ad_path"],
        "limitation": "h5ad_file_not_found" if not file_exists else "h5ad_numeric_analysis_not_yet_connected",
    }

    return {
        "summary": {
            **common,
            "h5ad_path": normalized["h5ad_path"],
            "h5ad_file_exists": file_exists,
            "dataset_kind": normalized["dataset_kind"],
            "analysis_status": "pending_real_h5ad_analysis",
            "cell_type_column": normalized["cell_type_column"],
            "batch_column": normalized["batch_column"],
            "lineage_column": normalized["lineage_column"],
        },
        "cell_type_composition": build_placeholder_composition_rows(normalized),
        "marker_scores": build_placeholder_marker_score_rows(normalized),
        "maturity_scores": score_gene_program_maturity_placeholder(normalized, h5ad_config.get("gene_programs", {})),
        "similarity_scores": compare_reference_similarity_placeholder(normalized, h5ad_config),
        "gene_program_scores": build_placeholder_gene_program_rows(normalized, h5ad_config.get("gene_programs", {})),
        "regulatory_program_scores": build_placeholder_regulatory_rows(normalized),
        "trajectory_scores": build_placeholder_trajectory_rows(normalized),
        "limitations": [limitation],
    }


def inspect_h5ad_dataset(row: dict[str, Any], h5ad_config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    dependency = _h5ad_dependency_status()
    path = Path(normalized["h5ad_path"])
    if not dependency["available"]:
        return {
            **common,
            "h5ad_path": str(path),
            "status": "dependency_missing",
            "dependency_status": dependency,
            "limitations": [_limitation(common, "dependency_missing", dependency["message"])],
        }
    if not path.exists():
        return {
            **common,
            "h5ad_path": str(path),
            "status": "h5ad_file_missing",
            "dependency_status": dependency,
            "limitations": [_limitation(common, "h5ad_file_missing", str(path))],
        }

    adata = None
    try:
        adata = _read_h5ad(path, backed=bool(h5ad_config.get("backed", True)))
        obs_columns = [str(column) for column in adata.obs.columns]
        var_names = [str(name) for name in list(adata.var_names[: int(h5ad_config.get("var_preview_limit", 20))])]
        layers = [str(layer) for layer in getattr(adata, "layers", {}).keys()]
        obsm_keys = [str(key) for key in getattr(adata, "obsm", {}).keys()]
        missing_columns = [
            column
            for column in (
                normalized["cell_type_column"],
                normalized["batch_column"],
                normalized["lineage_column"],
                normalized["donor_column"],
            )
            if column and column not in obs_columns
        ]
        return {
            **common,
            "h5ad_path": str(path),
            "status": "ok" if not missing_columns else "metadata_column_missing",
            "dependency_status": dependency,
            "n_obs": int(adata.n_obs),
            "n_vars": int(adata.n_vars),
            "obs_columns": obs_columns,
            "var_names_preview": var_names,
            "layers": layers,
            "obsm_keys": obsm_keys,
            "raw_available": getattr(adata, "raw", None) is not None,
            "cell_type_column": normalized["cell_type_column"],
            "batch_column": normalized["batch_column"],
            "lineage_column": normalized["lineage_column"],
            "missing_metadata_columns": missing_columns,
            "limitations": [
                _limitation(common, "obs_column_missing", column)
                for column in missing_columns
            ],
        }
    except Exception as exc:  # pragma: no cover - exercised by malformed user files
        return {
            **common,
            "h5ad_path": str(path),
            "status": "h5ad_read_error",
            "dependency_status": dependency,
            "limitations": [_limitation(common, "h5ad_read_error", str(exc))],
        }
    finally:
        _close_backed(adata)


def compute_cell_type_composition(row: dict[str, Any], h5ad_config: dict[str, Any]) -> list[dict[str, Any]]:
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    inspection = inspect_h5ad_dataset(normalized, h5ad_config)
    if inspection["status"] in {"dependency_missing", "h5ad_file_missing", "h5ad_read_error"}:
        return [{**common, "cell_type": "", "proportion": "", "n_cells": "", "status": inspection["status"]}]
    cell_type_column = normalized["cell_type_column"]
    if cell_type_column not in inspection.get("obs_columns", []):
        return [{**common, "cell_type": "", "proportion": "", "n_cells": "", "status": "obs_column_missing"}]
    adata = None
    try:
        adata = _read_h5ad(Path(normalized["h5ad_path"]), backed=True)
        counts = adata.obs[cell_type_column].astype(str).value_counts(dropna=False)
        total = int(counts.sum())
        return [
            {
                **common,
                "cell_type": str(cell_type),
                "proportion": round(int(n_cells) / total, 6) if total else "",
                "n_cells": int(n_cells),
                "status": "ok",
            }
            for cell_type, n_cells in counts.items()
        ]
    finally:
        _close_backed(adata)


def query_gene_expression(
    row: dict[str, Any],
    h5ad_config: dict[str, Any],
    genes: list[str],
    groupby: str | None = None,
) -> list[dict[str, Any]]:
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    dependency = _h5ad_dependency_status()
    if not dependency["available"]:
        return [{**common, "gene": "", "group": "", "status": "dependency_missing"}]
    path = Path(normalized["h5ad_path"])
    if not path.exists():
        return [{**common, "gene": "", "group": "", "status": "h5ad_file_missing"}]

    target_genes = _limit_genes(genes, h5ad_config)
    adata = None
    try:
        adata = _read_h5ad(path, backed=bool(h5ad_config.get("backed", False)))
        if groupby and groupby not in adata.obs.columns:
            return [{**common, "gene": "", "group": "", "status": "obs_column_missing", "obs_column": groupby}]
        groups = _group_masks(adata, groupby)
        rows: list[dict[str, Any]] = []
        var_index = {str(gene): index for index, gene in enumerate(adata.var_names)}
        for gene in target_genes:
            if gene not in var_index:
                rows.append({**common, "gene": gene, "group": "", "mean_expression": "", "pct_expressing": "", "n_cells": "", "status": "gene_not_found"})
                continue
            vector = _gene_vector(adata, var_index[gene], h5ad_config)
            for group, mask in groups:
                values = vector[mask]
                rows.append(
                    {
                        **common,
                        "gene": gene,
                        "groupby": groupby or "",
                        "group": group,
                        "mean_expression": round(float(values.mean()), 6) if values.size else "",
                        "pct_expressing": round(float((values > 0).sum()) / float(values.size), 6) if values.size else "",
                        "n_cells": int(values.size),
                        "layer": str(h5ad_config.get("layer") or ""),
                        "use_raw": bool(h5ad_config.get("use_raw", False)),
                        "status": "ok",
                    }
                )
        return rows
    finally:
        _close_backed(adata)


def compute_marker_scores(
    row: dict[str, Any],
    h5ad_config: dict[str, Any],
    marker_sets: dict[str, list[str]],
    groupby: str | None = None,
) -> list[dict[str, Any]]:
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    rows: list[dict[str, Any]] = []
    for marker_set, genes in marker_sets.items():
        expression_rows = [r for r in query_gene_expression(normalized, h5ad_config, genes, groupby=groupby) if r.get("status") == "ok"]
        grouped: dict[str, list[float]] = {}
        for expr in expression_rows:
            grouped.setdefault(str(expr.get("group", "")), []).append(float(expr.get("mean_expression") or 0.0))
        found_genes = {str(expr.get("gene")) for expr in expression_rows}
        if not expression_rows:
            rows.append(
                {
                    **common,
                    "marker_set": marker_set,
                    "groupby": groupby or "",
                    "group": "",
                    "score": "",
                    "n_genes_input": len(genes),
                    "n_genes_found": 0,
                    "status": "no_marker_genes_found",
                }
            )
            continue
        for group, values in grouped.items():
            rows.append(
                {
                    **common,
                    "marker_set": marker_set,
                    "groupby": groupby or "",
                    "group": group,
                    "score": round(sum(values) / len(values), 6) if values else "",
                    "n_genes_input": len(genes),
                    "n_genes_found": len(found_genes),
                    "status": "ok",
                }
            )
    return rows


def compute_differential_expression(
    row: dict[str, Any],
    h5ad_config: dict[str, Any],
    groupby: str,
    group_a: str,
    group_b: str,
    genes: list[str] | None = None,
) -> list[dict[str, Any]]:
    normalized = normalize_manifest_row(row, h5ad_config)
    common = _common_dataset_fields(normalized)
    dependency = _h5ad_dependency_status()
    if not dependency["available"]:
        return [{**common, "gene": "", "status": "dependency_missing"}]
    path = Path(normalized["h5ad_path"])
    if not path.exists():
        return [{**common, "gene": "", "status": "h5ad_file_missing"}]
    adata = None
    try:
        adata = _read_h5ad(path, backed=bool(h5ad_config.get("backed", False)))
        if groupby not in adata.obs.columns:
            return [{**common, "gene": "", "groupby": groupby, "status": "obs_column_missing"}]
        labels = adata.obs[groupby].astype(str)
        mask_a = labels == str(group_a)
        mask_b = labels == str(group_b)
        if int(mask_a.sum()) < 1 or int(mask_b.sum()) < 1:
            return [
                {
                    **common,
                    "gene": "",
                    "groupby": groupby,
                    "group_a": group_a,
                    "group_b": group_b,
                    "status": "insufficient_groups_for_de",
                }
            ]
        target_genes = _limit_genes(genes or list(map(str, adata.var_names)), h5ad_config)
        var_index = {str(gene): index for index, gene in enumerate(adata.var_names)}
        rows: list[dict[str, Any]] = []
        for gene in target_genes:
            if gene not in var_index:
                rows.append({**common, "gene": gene, "groupby": groupby, "group_a": group_a, "group_b": group_b, "status": "gene_not_found"})
                continue
            vector = _gene_vector(adata, var_index[gene], h5ad_config)
            values_a = vector[mask_a.to_numpy()]
            values_b = vector[mask_b.to_numpy()]
            mean_a = float(values_a.mean()) if values_a.size else 0.0
            mean_b = float(values_b.mean()) if values_b.size else 0.0
            rows.append(
                {
                    **common,
                    "gene": gene,
                    "groupby": groupby,
                    "group_a": group_a,
                    "group_b": group_b,
                    "mean_a": round(mean_a, 6),
                    "mean_b": round(mean_b, 6),
                    "mean_diff": round(mean_a - mean_b, 6),
                    "log2_fold_change": round(math.log2((mean_a + 1e-9) / (mean_b + 1e-9)), 6),
                    "p_value": _welch_p_value(values_a, values_b),
                    "n_cells_a": int(values_a.size),
                    "n_cells_b": int(values_b.size),
                    "status": "ok",
                }
            )
        return sorted(rows, key=lambda r: abs(float(r.get("mean_diff") or 0.0)), reverse=True)
    finally:
        _close_backed(adata)


def normalize_manifest_row(row: dict[str, Any], h5ad_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": str(row.get("protocol_id") or ""),
        "dataset_id": str(row.get("dataset_id") or row.get("h5ad_dataset_id") or ""),
        "sample_id": str(row.get("sample_id") or ""),
        "condition_id": str(row.get("condition_id") or ""),
        "h5ad_path": str(row.get("h5ad_path") or row.get("path") or ""),
        "dataset_kind": str(row.get("dataset_kind") or row.get("role") or DEFAULT_DATASET_KIND),
        "cell_type_column": str(
            row.get("cell_type_col")
            or row.get("cell_type_column")
            or h5ad_config.get("annotation_cell_type_column", DEFAULT_CELL_TYPE_COLUMN)
        ),
        "batch_column": str(
            row.get("batch_col") or row.get("batch_column") or h5ad_config.get("batch_column", DEFAULT_BATCH_COLUMN)
        ),
        "lineage_column": str(
            row.get("lineage_col") or row.get("lineage_column") or h5ad_config.get("lineage_column", "lineage")
        ),
        "donor_column": str(row.get("donor_col") or row.get("donor_column") or h5ad_config.get("donor_column", "")),
        "matched_reference_dataset_id": str(
            row.get("matched_reference_dataset_id") or h5ad_config.get("reference_dataset_id", "")
        ),
    }


def validate_data_analysis_inputs(
    manifest_rows: list[dict[str, Any]],
    reference_atlas_path: Path | str | None,
    gene_programs: dict[str, Any] | None,
    h5ad_config: dict[str, Any],
) -> dict[str, Any]:
    normalized_rows = [normalize_manifest_row(row, h5ad_config) for row in manifest_rows]
    findings: list[dict[str, Any]] = []
    for index, row in enumerate(normalized_rows):
        if not row["dataset_id"]:
            findings.append({"severity": "high", "row_index": index, "issue_type": "missing_dataset_id"})
        if not row["h5ad_path"]:
            findings.append({"severity": "high", "row_index": index, "issue_type": "missing_h5ad_path"})

    reference_path = Path(reference_atlas_path) if reference_atlas_path else None
    if reference_path and not reference_path.exists():
        findings.append(
            {"severity": "medium", "issue_type": "reference_atlas_file_not_found", "path": str(reference_path)}
        )

    gene_program_count = sum(len(programs) for programs in (gene_programs or {}).values() if isinstance(programs, dict))
    return {
        "schema_version": "data-analysis-input-gate-v1",
        "summary": {
            "n_manifest_rows": len(manifest_rows),
            "n_normalized_rows": len(normalized_rows),
            "n_reference_atlas_paths": int(reference_path is not None),
            "n_gene_program_lineages": len(gene_programs or {}),
            "n_gene_programs": gene_program_count,
            "n_findings": len(findings),
        },
        "normalized_manifest_rows": normalized_rows,
        "findings": findings,
    }


def score_gene_program_maturity_placeholder(
    row: dict[str, Any], gene_programs: dict[str, Any] | None
) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    lineages = list((gene_programs or {}).keys()) or ["all_lineages"]
    return [
        {
            **common,
            "lineage": lineage,
            "maturity_score": "",
            "score_method": "gene_program_maturity_score_pending",
            "status": "pending",
        }
        for lineage in lineages
    ]


def compare_reference_similarity_placeholder(row: dict[str, Any], h5ad_config: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [
        {
            **common,
            "reference_dataset_id": row.get("matched_reference_dataset_id")
            or h5ad_config.get("reference_dataset_id", ""),
            "matched_cell_type": "",
            "cosine_similarity": "",
            "euclidean_distance": "",
            "similarity_score": "",
            "status": "pending",
        }
    ]


def build_placeholder_composition_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [
        {**common, "cell_type": "beta_like", "proportion": "", "n_cells": "", "status": "pending"},
        {**common, "cell_type": "endocrine", "proportion": "", "n_cells": "", "status": "pending"},
        {**common, "cell_type": "off_target", "proportion": "", "n_cells": "", "status": "pending"},
    ]


def build_placeholder_marker_score_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [
        {**common, "marker_set": "beta_maturity", "score": "", "status": "pending"},
        {**common, "marker_set": "stress", "score": "", "status": "pending"},
        {**common, "marker_set": "proliferation", "score": "", "status": "pending"},
        {**common, "marker_set": "off_target", "score": "", "status": "pending"},
    ]


def build_placeholder_gene_program_rows(
    row: dict[str, Any], gene_programs: dict[str, Any] | None
) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    rows: list[dict[str, Any]] = []
    for lineage, programs in (gene_programs or {}).items():
        if isinstance(programs, dict):
            program_names = list(programs)
        elif isinstance(programs, list):
            program_names = ["default_program"]
        else:
            program_names = []
        for program_name in program_names:
            rows.append(
                {
                    **common,
                    "lineage": lineage,
                    "gene_program": str(program_name),
                    "n_genes_input": _program_gene_count(
                        programs[program_name] if isinstance(programs, dict) else programs
                    ),
                    "program_score": "",
                    "status": "pending",
                }
            )
    return rows


def build_placeholder_regulatory_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [
        {
            **common,
            "regulatory_method": "pySCENIC_or_CellOracle_pending",
            "tf_or_module": "",
            "activity_score": "",
            "status": "pending",
        }
    ]


def build_placeholder_trajectory_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [
        {
            **common,
            "trajectory_method": "pseudotime_pending",
            "lineage": "",
            "trajectory_score": "",
            "status": "pending",
        }
    ]


def _common_dataset_fields(row: dict[str, Any]) -> dict[str, str]:
    return {
        "protocol_id": str(row.get("protocol_id") or ""),
        "dataset_id": str(row.get("dataset_id") or ""),
        "condition_id": str(row.get("condition_id") or ""),
        "sample_id": str(row.get("sample_id") or ""),
    }


def _program_gene_count(program: Any) -> int:
    if isinstance(program, list):
        return len(program)
    if isinstance(program, dict):
        genes = program.get("genes")
        return len(genes) if isinstance(genes, list) else 0
    return 0


def _h5ad_dependency_status() -> dict[str, Any]:
    missing: list[str] = []
    for module in ("anndata", "numpy", "pandas"):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    return {
        "available": not missing,
        "missing": missing,
        "message": "ok" if not missing else "Missing Python dependencies: " + ", ".join(missing),
    }


def _read_h5ad(path: Path, backed: bool = False) -> Any:
    import anndata as ad

    return ad.read_h5ad(path, backed="r" if backed else None)


def _close_backed(adata: Any) -> None:
    if adata is not None and getattr(adata, "isbacked", False):
        file_handle = getattr(adata, "file", None)
        close = getattr(file_handle, "close", None)
        if callable(close):
            close()


def _limit_genes(genes: list[str], h5ad_config: dict[str, Any]) -> list[str]:
    max_genes = int(h5ad_config.get("max_target_genes", DEFAULT_MAX_TARGET_GENES))
    return [str(gene) for gene in genes[:max_genes]]


def _group_masks(adata: Any, groupby: str | None) -> list[tuple[str, Any]]:
    import numpy as np

    if not groupby:
        return [("all", np.ones(adata.n_obs, dtype=bool))]
    labels = adata.obs[groupby].astype(str)
    return [(str(group), (labels == str(group)).to_numpy()) for group in sorted(labels.unique())]


def _gene_vector(adata: Any, gene_index: int, h5ad_config: dict[str, Any]) -> Any:
    import numpy as np

    matrix_source = adata.raw if bool(h5ad_config.get("use_raw", False)) and getattr(adata, "raw", None) is not None else adata
    layer = h5ad_config.get("layer")
    if layer and hasattr(matrix_source, "layers") and layer in matrix_source.layers:
        matrix = matrix_source.layers[layer][:, gene_index]
    else:
        matrix = matrix_source.X[:, gene_index]
    if hasattr(matrix, "toarray"):
        return np.asarray(matrix.toarray()).ravel()
    return np.asarray(matrix).ravel()


def _welch_p_value(values_a: Any, values_b: Any) -> float | str:
    if len(values_a) < 2 or len(values_b) < 2:
        return ""
    try:
        from scipy import stats

        result = stats.ttest_ind(values_a, values_b, equal_var=False, nan_policy="omit")
        if result.pvalue is None or math.isnan(float(result.pvalue)):
            return ""
        return round(float(result.pvalue), 8)
    except Exception:
        return ""


def _limitation(common: dict[str, Any], issue_type: str, message: str) -> dict[str, Any]:
    return {
        **common,
        "issue_type": issue_type,
        "message": message,
    }
