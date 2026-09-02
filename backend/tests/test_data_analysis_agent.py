from __future__ import annotations

import csv
import json
from pathlib import Path

from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent


def test_data_analysis_agent_exports_framework_outputs(tmp_path: Path) -> None:
    manifest_rows = [
        {
            "protocol_id": "p1",
            "dataset_id": "organoid_d1",
            "sample_id": "s1",
            "condition_id": "baseline",
            "h5ad_path": str(tmp_path / "missing_organoid.h5ad"),
            "cell_type_col": "manual_cell_type",
            "batch_col": "integration_batch",
            "lineage_col": "lineage",
            "matched_reference_dataset_id": "atlas_vivo",
        }
    ]
    gene_programs = {
        "beta": {
            "mature_beta": ["INS", "IAPP", "MAFA"],
            "immature_beta": ["SLC2A1", "MKI67"],
        },
        "ductal": {"ductal_identity": ["KRT19", "SOX9"]},
    }

    outputs = DataAnalysisAgent(
        output_dir=tmp_path,
        h5ad_config={"reference_dataset_id": "atlas_vivo"},
        reference_atlas_path=tmp_path / "missing_reference.h5ad",
        gene_programs=gene_programs,
    ).run(manifest_rows)

    expected = {
        "h5ad_summary_by_protocol",
        "maturity_scores",
        "similarity_scores",
        "cell_type_composition",
        "marker_scores",
        "gene_program_scores",
        "regulatory_program_scores",
        "trajectory_scores",
        "data_analysis_protocol_summary",
        "h5ad_qc_report",
        "h5ad_analysis_limitations",
        "data_analysis_tool_registry",
        "data_analysis_comparison_report",
    }
    assert expected.issubset(outputs)
    assert all(path.exists() for path in outputs.values())

    maturity_rows = list(csv.DictReader(outputs["maturity_scores"].open(encoding="utf-8")))
    assert {row["lineage"] for row in maturity_rows} == {"beta", "ductal"}
    assert {row["status"] for row in maturity_rows} == {"pending"}

    gene_program_rows = list(csv.DictReader(outputs["gene_program_scores"].open(encoding="utf-8")))
    assert {row["gene_program"] for row in gene_program_rows} == {
        "mature_beta",
        "immature_beta",
        "ductal_identity",
    }

    similarity_rows = list(csv.DictReader(outputs["similarity_scores"].open(encoding="utf-8")))
    assert similarity_rows[0]["reference_dataset_id"] == "atlas_vivo"
    assert similarity_rows[0]["cosine_similarity"] == ""
    assert similarity_rows[0]["euclidean_distance"] == ""

    comparison = json.loads(outputs["data_analysis_comparison_report"].read_text(encoding="utf-8"))
    assert comparison["schema_version"] == "data-analysis-comparison-report-v1"
    assert "organoid_reference_similarity" in comparison["comparison_axes"]
    assert any(tool["tool_id"] == "gene_program_maturity_score" for tool in comparison["tool_registry"])

    qc_report = json.loads(outputs["h5ad_qc_report"].read_text(encoding="utf-8"))
    assert qc_report["input_gate_report"]["summary"]["n_gene_program_lineages"] == 2
    assert [step["node"] for step in qc_report["step_log"]][:3] == [
        "data_input_gate",
        "dataset_inventory_builder",
        "metadata_and_qc_summary",
    ]
