from __future__ import annotations

import builtins
import csv
import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.tools.external_data_tools import get_external_data_analysis_tools
from protocol_optimization_agent.tools.h5ad_tools import compute_differential_expression, query_gene_expression


def test_data_analysis_agent_exports_framework_outputs(tmp_path: Path) -> None:
    (tmp_path / "external_tools" / "data_analysis" / "scanpy").mkdir(parents=True)
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
        project_root=tmp_path,
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
        "external_tool_inventory",
        "data_analysis_protocol_summary",
        "h5ad_qc_report",
        "h5ad_analysis_limitations",
        "data_analysis_tool_registry",
        "data_analysis_comparison_report",
        "data_analysis_query_result",
    }
    assert expected.issubset(outputs)
    assert all(path.exists() for path in outputs.values())

    maturity_rows = list(csv.DictReader(outputs["maturity_scores"].open(encoding="utf-8")))
    assert {row["lineage"] for row in maturity_rows} == {"beta", "ductal"}
    assert {row["status"] for row in maturity_rows} == {"input_data_pending"}

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
    assert "scanpy_h5ad_profile_tool" in comparison["callable_tools"]
    assert any(tool["tool_id"] == "scanpy_h5ad_profile_tool" for tool in comparison["external_tool_inventory"])

    qc_report = json.loads(outputs["h5ad_qc_report"].read_text(encoding="utf-8"))
    assert qc_report["input_gate_report"]["summary"]["n_gene_program_lineages"] == 2
    assert [step["node"] for step in qc_report["step_log"]][:3] == [
        "data_input_gate",
        "dataset_inventory_builder",
        "metadata_and_qc_summary",
    ]

    external_rows = list(csv.DictReader(outputs["external_tool_inventory"].open(encoding="utf-8")))
    scanpy_row = next(row for row in external_rows if row["tool_id"] == "scanpy_h5ad_profile_tool")
    assert scanpy_row["source_downloaded"] == "True"

    agent = DataAnalysisAgent(output_dir=tmp_path, project_root=tmp_path)
    call_result = agent.call_tool("scanpy_h5ad_profile_tool")
    assert call_result["tool_id"] == "scanpy_h5ad_profile_tool"
    assert call_result["source_downloaded"] is True


def test_external_data_analysis_tools_are_callable(tmp_path: Path) -> None:
    tools = get_external_data_analysis_tools(tmp_path)

    assert "scanpy_h5ad_profile_tool" in tools
    result = tools["scanpy_h5ad_profile_tool"]({"project_root": str(tmp_path)})
    assert result["tool_id"] == "scanpy_h5ad_profile_tool"
    assert result["status"] == "callable_wrapper_ready_numeric_execution_pending"


def test_data_analysis_agent_runs_small_h5ad_analysis(tmp_path: Path) -> None:
    adata_path = _write_synthetic_h5ad(tmp_path)
    outputs = DataAnalysisAgent(
        output_dir=tmp_path / "out",
        h5ad_config={
            "annotation_cell_type_column": "cell_type",
            "batch_column": "batch",
            "lineage_column": "lineage",
            "marker_sets": {"beta_marker": ["INS", "IAPP"]},
            "query_genes": ["INS", "SOX9", "NOT_A_GENE"],
            "expression_groupby": "cell_type",
            "differential_expression": {
                "groupby": "cell_type",
                "group_a": "beta",
                "group_b": "alpha",
                "genes": ["INS", "GCG"],
            },
            "expression_scale": "normalized",
            "backed": False,
        },
    ).run(
        [
            {
                "protocol_id": "p1",
                "dataset_id": "d1",
                "sample_id": "s1",
                "condition_id": "baseline",
                "h5ad_path": str(adata_path),
            }
        ]
    )

    composition_rows = list(csv.DictReader(outputs["cell_type_composition"].open(encoding="utf-8")))
    beta = next(row for row in composition_rows if row["cell_type"] == "beta")
    assert beta["n_cells"] == "3"
    assert beta["n_cells_selected"] == "5"
    assert beta["cell_filter"] == "sample_id=s1;condition_id=baseline"
    assert beta["status"] == "ok"

    expression_rows = list(csv.DictReader(outputs["gene_expression_summary"].open(encoding="utf-8")))
    beta_ins = next(row for row in expression_rows if row["gene"] == "INS" and row["group"] == "beta")
    assert float(beta_ins["mean_expression"]) > 4
    assert any(row["gene"] == "NOT_A_GENE" and row["status"] == "gene_not_found" for row in expression_rows)

    marker_rows = list(csv.DictReader(outputs["marker_scores"].open(encoding="utf-8")))
    assert any(row["marker_set"] == "beta_marker" and row["status"] == "ok" for row in marker_rows)

    de_rows = list(csv.DictReader(outputs["differential_expression"].open(encoding="utf-8")))
    ins_de = next(row for row in de_rows if row["gene"] == "INS")
    assert float(ins_de["mean_a"]) > float(ins_de["mean_b"])
    assert ins_de["status"] == "ok"

    qc_report = json.loads(outputs["h5ad_qc_report"].read_text(encoding="utf-8"))
    assert qc_report["qc_rows"][0]["analysis_status"] == "ok"
    assert qc_report["qc_rows"][0]["n_cells_selected"] == 5
    query_contract = json.loads(outputs["data_analysis_query_result"].read_text(encoding="utf-8"))
    assert "differential_expression" in query_contract["supported_operations"]


def test_gene_expression_reports_missing_obs_column(tmp_path: Path) -> None:
    adata_path = _write_synthetic_h5ad(tmp_path)
    rows = query_gene_expression(
        {
            "protocol_id": "p1",
            "dataset_id": "d1",
            "h5ad_path": str(adata_path),
        },
        {},
        ["INS"],
        groupby="missing_column",
    )

    assert rows[0]["status"] == "obs_column_missing"
    assert rows[0]["obs_column"] == "missing_column"


def test_gene_expression_dependency_missing_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anndata":
            raise ImportError("synthetic missing dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    rows = query_gene_expression(
        {"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": "missing.h5ad"},
        {},
        ["INS"],
    )

    assert rows[0]["status"] == "dependency_missing"


def test_gene_expression_uses_raw_gene_index(tmp_path: Path) -> None:
    adata_path = _write_raw_reordered_h5ad(tmp_path)
    rows = query_gene_expression(
        {
            "protocol_id": "p1",
            "dataset_id": "d1",
            "sample_id": "s1",
            "condition_id": "baseline",
            "h5ad_path": str(adata_path),
        },
        {"use_raw": True, "backed": False},
        ["INS"],
    )

    assert rows[0]["status"] == "ok"
    assert rows[0]["expression_source"] == "raw.X"
    assert rows[0]["mean_expression"] == 5.0


def test_gene_expression_reports_missing_layer(tmp_path: Path) -> None:
    adata_path = _write_synthetic_h5ad(tmp_path)
    rows = query_gene_expression(
        {
            "protocol_id": "p1",
            "dataset_id": "d1",
            "sample_id": "s1",
            "condition_id": "baseline",
            "h5ad_path": str(adata_path),
        },
        {"layer": "missing_layer", "backed": False},
        ["INS"],
    )

    assert rows[0]["status"] == "expression_source_missing"
    assert "missing_layer" in rows[0]["message"]


def test_differential_expression_log_scale_uses_mean_difference_only(tmp_path: Path) -> None:
    adata_path = _write_synthetic_h5ad(tmp_path)
    rows = compute_differential_expression(
        {
            "protocol_id": "p1",
            "dataset_id": "d1",
            "sample_id": "s1",
            "condition_id": "baseline",
            "h5ad_path": str(adata_path),
        },
        {"expression_scale": "log", "backed": False},
        groupby="cell_type",
        group_a="beta",
        group_b="alpha",
        genes=["INS"],
    )

    assert rows[0]["status"] == "ok"
    assert rows[0]["mean_diff"] != ""
    assert rows[0]["log2_fold_change"] == ""
    assert rows[0]["effect_size_type"] == "mean_difference_only_expression_scale_not_fold_change_compatible"


def _write_synthetic_h5ad(tmp_path: Path) -> Path:
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    x = np.array(
        [
            [5.0, 4.0, 0.0, 0.0],
            [6.0, 5.0, 0.0, 0.0],
            [5.5, 4.5, 0.0, 1.0],
            [0.1, 0.0, 7.0, 0.0],
            [0.0, 0.0, 8.0, 0.0],
            [0.0, 0.0, 0.0, 6.0],
        ]
    )
    obs = pd.DataFrame(
        {
            "cell_type": ["beta", "beta", "beta", "alpha", "alpha", "ductal"],
            "batch": ["b1", "b1", "b2", "b1", "b2", "b2"],
            "lineage": ["endocrine", "endocrine", "endocrine", "endocrine", "endocrine", "ductal"],
            "sample_id": ["s1", "s1", "s1", "s1", "s1", "s2"],
            "condition_id": ["baseline", "baseline", "baseline", "baseline", "baseline", "baseline"],
        },
        index=[f"cell{i}" for i in range(x.shape[0])],
    )
    var = pd.DataFrame(index=["INS", "IAPP", "GCG", "SOX9"])
    adata = ad.AnnData(X=x, obs=obs, var=var)
    path = tmp_path / "synthetic.h5ad"
    adata.write_h5ad(path)
    return path


def _write_raw_reordered_h5ad(tmp_path: Path) -> Path:
    ad = pytest.importorskip("anndata")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    obs = pd.DataFrame(
        {
            "sample_id": ["s1", "s1"],
            "condition_id": ["baseline", "baseline"],
        },
        index=["cell0", "cell1"],
    )
    adata = ad.AnnData(
        X=np.array([[100.0, 0.0], [200.0, 0.0]]),
        obs=obs,
        var=pd.DataFrame(index=["INS", "IAPP"]),
    )
    adata.raw = ad.AnnData(
        X=np.array([[1.0, 5.0, 9.0], [1.0, 5.0, 9.0]]),
        obs=obs.copy(),
        var=pd.DataFrame(index=["GCG", "INS", "IAPP"]),
    )
    path = tmp_path / "raw_reordered.h5ad"
    adata.write_h5ad(path)
    return path
