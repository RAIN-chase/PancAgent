from __future__ import annotations

import builtins
import csv
import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents import data_analysis as da
from protocol_optimization_agent.agents.data_analysis import (
    TASK_OUTPUTS,
    DataAnalysisAgent,
    data_analysis_agent,
    resolve_data_tasks,
)
from protocol_optimization_agent.analysis.phenotype.external_tools import get_external_data_analysis_tools
from protocol_optimization_agent.analysis.phenotype.h5ad_tools import (
    compute_differential_expression,
    query_gene_expression,
    validate_data_analysis_inputs,
)


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
        h5ad_config={"requested_tasks": ["cell_type_composition", "gene_program_score"], "reference_dataset_id": "atlas_vivo"},
        reference_atlas_path=tmp_path / "missing_reference.h5ad",
        gene_programs=gene_programs,
        project_root=tmp_path,
    ).run(manifest_rows)

    expected = {
        "h5ad_summary_by_protocol",
        "cell_type_composition",
        "gene_program_scores",
        "data_analysis_protocol_summary",
        "h5ad_qc_report",
        "h5ad_analysis_limitations",
        "data_analysis_tool_registry",
        "data_analysis_comparison_report",
        "data_analysis_query_result",
        "data_analysis_execution_plan",
        "data_analysis_validation",
    }
    assert expected.issubset(outputs)
    assert all(path.exists() for path in outputs.values())

    assert not {"maturity_scores", "similarity_scores", "regulatory_program_scores", "trajectory_scores"}.intersection(outputs)
    assert not any(key.startswith("exploratory_") for key in outputs)
    assert not (outputs["data_analysis_validation"].parent / "exploratory").exists()

    gene_program_rows = list(csv.DictReader(outputs["gene_program_scores"].open(encoding="utf-8")))
    assert gene_program_rows[0]["status"] == "dependency_failed"

    comparison = json.loads(outputs["data_analysis_comparison_report"].read_text(encoding="utf-8"))
    assert comparison["schema_version"] == "data-analysis-comparison-report-v2"
    assert comparison["comparison_axes"] == ["cell_type_composition", "gene_program_score"]
    registry = json.loads(outputs["data_analysis_tool_registry"].read_text(encoding="utf-8"))
    assert any(tool["tool_id"] == "gene_program_maturity_score" for tool in registry["tools"])
    assert "scanpy_h5ad_profile_tool" in registry["callable_tools"]

    qc_report = json.loads(outputs["h5ad_qc_report"].read_text(encoding="utf-8"))
    assert qc_report["input_gate_report"]["summary"]["n_gene_program_lineages"] == 2
    assert [step["node"] for step in qc_report["step_log"]][:3] == [
        "data_input_gate",
        "resolve_tasks",
        "dataset_inventory_builder",
    ]

    validation = json.loads(outputs["data_analysis_validation"].read_text(encoding="utf-8"))
    assert validation["task_validity"]["maturity"]["valid"] is False
    assert validation["task_validity"]["maturity"]["checks"]["status"] == "not_requested"
    assert validation["quality_interpretation"].endswith("not a statistical confidence probability.")

    assert "external_tool_inventory" not in outputs

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
            "requested_tasks": ["cell_type_composition", "gene_expression", "marker_score", "differential_expression"],
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
    validation = json.loads(outputs["data_analysis_validation"].read_text(encoding="utf-8"))
    checks = {row["check_id"]: row for row in validation["checks"]}
    assert checks["cell_selection"]["passed"] is True
    assert checks["expression_scale"]["passed"] is True
    assert validation["task_validity"]["maturity"]["valid"] is False


def test_same_h5ad_can_define_multiple_manifest_datasets(tmp_path: Path) -> None:
    adata_path = _write_synthetic_h5ad(tmp_path)
    outputs = DataAnalysisAgent(
        output_dir=tmp_path / "out",
        h5ad_config={
            "requested_tasks": ["cell_type_composition"],
            "annotation_cell_type_column": "cell_type",
            "batch_column": "batch",
            "lineage_column": "lineage",
            "dataset_column": "dataset",
            "backed": True,
        },
    ).run(
        [
            {"protocol_id": "p1", "dataset_id": "organoid_a", "h5ad_path": str(adata_path)},
            {"protocol_id": "p2", "dataset_id": "organoid_b", "h5ad_path": str(adata_path)},
        ]
    )

    rows = list(csv.DictReader(outputs["cell_type_composition"].open(encoding="utf-8")))
    a_rows = [row for row in rows if row["dataset_id"] == "organoid_a"]
    b_rows = [row for row in rows if row["dataset_id"] == "organoid_b"]
    assert {row["n_cells_selected"] for row in a_rows} == {"5"}
    assert {row["n_cells_selected"] for row in b_rows} == {"1"}
    assert {row["cell_filter"] for row in a_rows} == {"dataset=organoid_a"}


def test_exploratory_mode_archives_reproducibility_bundle(tmp_path: Path) -> None:
    outputs = DataAnalysisAgent(
        output_dir=tmp_path / "out",
        h5ad_config={
            "analysis_mode": "exploratory",
            "exploratory_analysis": {
                "script": "print('sandboxed analysis')\n",
                "environment": {"python": "3.11", "packages": {"scanpy": "1.11"}},
                "config": {"method": "custom_metric"},
                "diagnostics": {"execution_succeeded": True, "exit_code": 0},
                "validation_checks": [{"check_id": "output_schema", "passed": True}],
            },
        },
    ).run([])

    assert outputs["exploratory_analysis_script"].read_text(encoding="utf-8") == "print('sandboxed analysis')\n"
    plan = json.loads(outputs["data_analysis_execution_plan"].read_text(encoding="utf-8"))
    assert plan["analysis_mode"] == "exploratory"
    validation = json.loads(outputs["data_analysis_validation"].read_text(encoding="utf-8"))
    checks = {row["check_id"]: row for row in validation["checks"]}
    assert checks["exploratory_artifacts"]["passed"] is True
    assert validation["task_validity"]["maturity"]["valid"] is False


def test_manifest_gate_rejects_duplicate_cell_selection(tmp_path: Path) -> None:
    path = str(tmp_path / "combined.h5ad")
    report = validate_data_analysis_inputs(
        [
            {"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": path},
            {"protocol_id": "p2", "dataset_id": "d2", "h5ad_path": path},
        ],
        None,
        {},
        {},
    )

    assert report["findings"][-1]["issue_type"] == "duplicate_h5ad_cell_selection"


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
            "dataset": ["organoid_a", "organoid_a", "organoid_a", "organoid_a", "organoid_a", "organoid_b"],
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


def _task_config():
    return {"annotation_cell_type_column": "cell_type", "batch_column": "batch", "lineage_column": "lineage",
            "query_genes": ["INS"], "marker_sets": {"beta": ["INS", "IAPP"]},
            "gene_programs": {"beta": {"identity": ["INS", "IAPP"]}},
            "differential_expression": {"groupby": "cell_type", "group_a": "beta", "group_b": "alpha", "genes": ["INS"]},
            "expression_scale": "normalized", "maturity": {"cell_types": ["Beta"]}}


def _task_spies(monkeypatch):
    calls = []
    for name in ("compute_cell_type_composition", "query_gene_expression", "compute_differential_expression",
                 "compute_marker_scores", "_compute_gene_program_scores"):
        original = getattr(da, name)

        def traced(*args, _name=name, _original=original, **kwargs):
            calls.append(_name)
            return _original(*args, **kwargs)

        monkeypatch.setattr(da, name, traced)

    def programs(*args, **kwargs):
        calls.append("load_maturity_programs")
        return {"Beta": {}}

    def maturity(row, config, programs, path):
        calls.append("compute_maturity_scores")
        return {"rows": [{"protocol_id": row["protocol_id"], "dataset_id": row["dataset_id"],
                          "lineage": "Beta", "maturity_score": 0.3, "status": "ok"}], "audit": {"status": "ok"}}

    monkeypatch.setattr(da, "load_maturity_programs", programs)
    monkeypatch.setattr(da, "compute_maturity_scores", maturity)
    return calls


@pytest.mark.parametrize("task,expected", [
    ("cell_type_composition", ["compute_cell_type_composition"]),
    ("gene_expression", ["query_gene_expression"]),
    ("differential_expression", ["compute_differential_expression"]),
    ("marker_score", ["compute_marker_scores"]),
    ("gene_program_score", ["_compute_gene_program_scores", "compute_marker_scores"]),
    ("maturity", ["load_maturity_programs", "compute_maturity_scores"]),
])
def test_only_requested_task_is_called_and_exported(tmp_path, monkeypatch, task, expected):
    path = _write_synthetic_h5ad(tmp_path)
    calls = _task_spies(monkeypatch)
    outputs = DataAnalysisAgent(tmp_path / "out", {**_task_config(), "requested_tasks": [task]}).run(
        [{"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}])
    assert calls == expected
    assert {item[1] for item in TASK_OUTPUTS.values()}.intersection(outputs) == {TASK_OUTPUTS[task][1]}
    validation = json.loads(outputs["data_analysis_validation"].read_text())
    assert validation["requested_tasks"] == [task]
    assert validation["task_validity"][task]["valid"] is True
    plan = json.loads(outputs["data_analysis_execution_plan"].read_text())
    assert [step["task"] for step in plan["steps"]] == [task]
    assert plan["steps"][0]["depends_on"] == ["dataset_inventory_builder"]
    assert "standard_tools" not in plan


def test_default_summary_does_not_infer_tasks_from_options(tmp_path, monkeypatch):
    path = _write_synthetic_h5ad(tmp_path)
    calls = _task_spies(monkeypatch)
    outputs = DataAnalysisAgent(tmp_path / "out", _task_config()).run([
        {"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}])
    assert calls == []
    assert not {item[1] for item in TASK_OUTPUTS.values()}.intersection(outputs)
    report = json.loads(outputs["data_analysis_validation"].read_text())
    assert report["validation_status"] == "passed"
    assert report["requested_tasks"] == ["summary"]
    assert "external_tool_inventory" not in outputs


def test_plan_parameters_control_execution_not_mutated_config(tmp_path, monkeypatch):
    path = _write_synthetic_h5ad(tmp_path)
    calls = _task_spies(monkeypatch)
    agent = DataAnalysisAgent(tmp_path / "out")
    state = agent._node_data_input_gate({"manifest_rows": [{"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}],
        "h5ad_config": {**_task_config(), "requested_tasks": ["gene_expression"]}})
    state = agent._node_dataset_inventory_builder(agent._node_resolve_tasks(state))
    state["h5ad_config"]["requested_tasks"] = ["maturity"]
    state["h5ad_config"]["query_genes"].append("MISSING")
    assert state["execution_plan"]["steps"][0]["parameters"]["query_genes"] == ["INS"]
    state["execution_plan"]["steps"][0]["parameters"]["query_genes"] = ["IAPP"]
    result = agent._node_execute_tasks(state)
    assert calls == ["query_gene_expression"]
    assert result["gene_expression_rows"][0]["gene"] == "IAPP"
    validation = agent._node_data_analysis_audit(result)["validation_report"]
    assert validation["requested_tasks"] == ["gene_expression"]
    assert validation["task_validity"]["gene_expression"]["valid"] is True


def test_multiple_tasks_are_deduplicated_and_numerically_unchanged(tmp_path, monkeypatch):
    path = _write_synthetic_h5ad(tmp_path)
    row = {"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}
    config = _task_config()
    expected_expression = query_gene_expression(row, config, ["INS"])
    calls = _task_spies(monkeypatch)
    outputs = DataAnalysisAgent(tmp_path / "out", {**config, "requested_tasks": ["gene_expression", "cell_type_composition", "gene_expression"]}).run([row])
    assert calls == ["query_gene_expression", "compute_cell_type_composition"]
    actual = list(csv.DictReader(outputs["gene_expression_summary"].open(encoding="utf-8")))
    assert float(actual[0]["mean_expression"]) == expected_expression[0]["mean_expression"]
    plan = json.loads(outputs["data_analysis_execution_plan"].read_text())
    assert plan["requested_tasks"] == ["gene_expression", "cell_type_composition"]


def test_task_failure_does_not_trigger_other_methods_or_erase_success(tmp_path, monkeypatch):
    path = _write_synthetic_h5ad(tmp_path)

    def broken(*args, **kwargs):
        raise ValueError("expression layer is incompatible")

    monkeypatch.setattr(da, "query_gene_expression", broken)
    outputs = DataAnalysisAgent(tmp_path / "out", {**_task_config(), "requested_tasks": ["gene_expression", "cell_type_composition"]}).run([
        {"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}])
    validation = json.loads(outputs["data_analysis_validation"].read_text())
    assert validation["task_validity"]["gene_expression"]["valid"] is False
    assert validation["task_validity"]["cell_type_composition"]["valid"] is True
    outcomes = json.loads(outputs["data_analysis_task_results"].read_text())["tasks"]
    assert outcomes[0]["failures"][0]["message"] == "expression layer is incompatible"
    assert outcomes[1]["status"] == "ok"
    assert "maturity_scores" not in outputs


def test_missing_parameters_and_unavailable_methods_are_reported(tmp_path, monkeypatch):
    path = _write_synthetic_h5ad(tmp_path)
    calls = _task_spies(monkeypatch)
    outputs = DataAnalysisAgent(tmp_path / "out", {"requested_tasks": ["gene_expression", "reference_similarity", "unknown"]}).run([
        {"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}])
    assert calls == []
    outcomes = json.loads(outputs["data_analysis_task_results"].read_text())["tasks"]
    assert [row["status"] for row in outcomes] == ["inputs_missing", "unavailable", "unavailable"]
    assert all(row["executed"] is False for row in outcomes)
    assert "similarity_scores" not in outputs
    assert json.loads(outputs["data_analysis_validation"].read_text())["validation_status"] == "failed"


def test_chat_operation_executes_only_expression_and_returns_plan(tmp_path, monkeypatch):
    path = _write_synthetic_h5ad(tmp_path)
    calls = _task_spies(monkeypatch)
    result = data_analysis_agent.invoke({"question": "INS expression", "data_analysis_output_dir": str(tmp_path / "out"),
        "workspace_root": str(tmp_path), "user_id": "test", "run_id": "expression_only",
        "h5ad_manifest_rows": [{"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}],
        "h5ad_config": _task_config()})
    assert calls == ["query_gene_expression"]
    assert result["status"] == "ok"
    assert result["execution_plan"]["requested_tasks"] == ["gene_expression"]
    assert result["task_results"][0]["status"] == "ok"
    assert result["used_tools"] == ["query_gene_expression"]
    assert "cell_type_composition.csv" not in result["available_output_files"]


def test_task_list_type_is_validated_before_any_analysis():
    with pytest.raises(ValueError, match="requested_tasks"):
        resolve_data_tasks({"requested_tasks": "gene_expression"})
    assert resolve_data_tasks({"requested_tasks": []})["steps"] == []


def test_de_validation_uses_the_executed_plan_scale(tmp_path):
    path = _write_synthetic_h5ad(tmp_path)
    agent = DataAnalysisAgent(tmp_path / "out")
    state = agent._node_data_input_gate({"manifest_rows": [{"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}],
        "h5ad_config": {**_task_config(), "requested_tasks": ["differential_expression"]}})
    state = agent._node_dataset_inventory_builder(agent._node_resolve_tasks(state))
    state["h5ad_config"]["expression_scale"] = "unknown"
    result = agent._node_data_analysis_audit(agent._node_execute_tasks(state))
    assert result["differential_expression_rows"][0]["expression_scale"] == "normalized"
    assert result["validation_report"]["task_validity"]["differential_expression"]["valid"] is True
