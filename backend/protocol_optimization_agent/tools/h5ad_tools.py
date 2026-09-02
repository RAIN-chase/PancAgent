from __future__ import annotations

from pathlib import Path
from typing import Any


DEFAULT_CELL_TYPE_COLUMN = "cell_type"
DEFAULT_BATCH_COLUMN = "batch_id"
DEFAULT_DATASET_KIND = "organoid"


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
        "status": "implemented_placeholder",
        "purpose": "Quantify annotated cell type abundance per dataset, sample, condition, and protocol.",
        "expected_inputs": ["annotated_organoid_h5ad", "cell_type_column"],
        "outputs": ["cell_type_composition.csv"],
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
        "status": "implemented_placeholder",
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


def normalize_manifest_row(row: dict[str, Any], h5ad_config: dict[str, Any]) -> dict[str, Any]:
    return {
        "protocol_id": str(row.get("protocol_id") or ""),
        "dataset_id": str(row.get("dataset_id") or row.get("h5ad_dataset_id") or ""),
        "sample_id": str(row.get("sample_id") or ""),
        "condition_id": str(row.get("condition_id") or ""),
        "h5ad_path": str(row.get("h5ad_path") or row.get("path") or ""),
        "dataset_kind": str(row.get("dataset_kind") or row.get("role") or DEFAULT_DATASET_KIND),
        "cell_type_column": str(row.get("cell_type_col") or row.get("cell_type_column") or h5ad_config.get("annotation_cell_type_column", DEFAULT_CELL_TYPE_COLUMN)),
        "batch_column": str(row.get("batch_col") or row.get("batch_column") or h5ad_config.get("batch_column", DEFAULT_BATCH_COLUMN)),
        "lineage_column": str(row.get("lineage_col") or row.get("lineage_column") or h5ad_config.get("lineage_column", "lineage")),
        "donor_column": str(row.get("donor_col") or row.get("donor_column") or h5ad_config.get("donor_column", "donor_id")),
        "matched_reference_dataset_id": str(row.get("matched_reference_dataset_id") or h5ad_config.get("reference_dataset_id", "")),
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
        findings.append({"severity": "medium", "issue_type": "reference_atlas_file_not_found", "path": str(reference_path)})

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


def score_gene_program_maturity_placeholder(row: dict[str, Any], gene_programs: dict[str, Any] | None) -> list[dict[str, Any]]:
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
            "reference_dataset_id": row.get("matched_reference_dataset_id") or h5ad_config.get("reference_dataset_id", ""),
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


def build_placeholder_gene_program_rows(row: dict[str, Any], gene_programs: dict[str, Any] | None) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    rows: list[dict[str, Any]] = []
    for lineage, programs in (gene_programs or {}).items():
        if isinstance(programs, dict):
            program_names = programs.keys()
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
                    "n_genes_input": _program_gene_count(programs[program_name] if isinstance(programs, dict) else programs),
                    "program_score": "",
                    "status": "pending",
                }
            )
    return rows


def build_placeholder_regulatory_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [{**common, "regulatory_method": "pySCENIC_or_CellOracle_pending", "tf_or_module": "", "activity_score": "", "status": "pending"}]


def build_placeholder_trajectory_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    common = _common_dataset_fields(row)
    return [{**common, "trajectory_method": "pseudotime_pending", "lineage": "", "trajectory_score": "", "status": "pending"}]


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
