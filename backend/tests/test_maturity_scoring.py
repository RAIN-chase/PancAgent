from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents import orchestrator
from protocol_optimization_agent.agents.actions import get_action, select_action, validate_action_arguments
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent, data_analysis_agent
from protocol_optimization_agent.analysis.phenotype.maturity_scoring import (
    CELL_TYPES,
    compute_maturity_scores,
    load_maturity_programs,
)
from protocol_optimization_agent.runtime.io import read_csv_dicts


def fixture_data(tmp_path, matrix_kind="csr"):
    import anndata as ad
    import numpy as np
    import pandas as pd
    from scipy import sparse

    rng = np.random.default_rng(17)
    counts = rng.poisson(np.linspace(0.5, 8, 240), (90, 240)).astype(np.float32)
    counts[:, 239] = 0
    matrix = counts if matrix_kind == "dense" else getattr(sparse, f"{matrix_kind}_matrix")(counts)
    obs = pd.DataFrame({"cell_type": ["SC-Beta"] * 30 + ["Alpha"] * 30 + ["SC_Delta"] * 30,
                        "sample_id": ["s1"] * 15 + ["s2"] * 15 + ["s1"] * 60},
                       index=[f"cell{i}" for i in range(90)])
    adata = ad.AnnData(sparse.csr_matrix(np.log1p(counts)), obs=obs,
                       var=pd.DataFrame(index=[f"g{i}.1" for i in range(240)]))
    adata.layers["counts"] = matrix
    path = tmp_path / "organoid.h5ad"
    adata.write_h5ad(path)
    directory = tmp_path / "programs"
    directory.mkdir()
    for target in CELL_TYPES:
        for phase, indexes in (("Early", [0, 20, 40, 60, 80]), ("Late", [10, 30, 50, 70, 90])):
            with (directory / f"{target}_Stability_{phase}_Program.csv").open("w", newline="") as out:
                writer = csv.writer(out)
                writer.writerow(["gene"])
                writer.writerows([[f"G{i}"] for i in indexes] + [["G239"], ["MISSING"], ["G0"]] if phase == "Early"
                                 else [[f"G{i}"] for i in indexes])
    row = {"h5ad_path": str(path), "dataset_id": "d1", "protocol_id": "p1"}
    config = {"requested_tasks": ["maturity"], "maturity": {"program_dir": str(directory), "cell_types": ["Beta"]}}
    return counts, adata, row, config


@pytest.mark.parametrize("matrix_kind", ["csr", "csc", "dense"])
def test_matches_notebook_kernel(tmp_path, matrix_kind):
    import anndata as ad
    import numpy as np
    import scanpy as sc
    from scipy import sparse

    counts, original, row, config = fixture_data(tmp_path, matrix_kind)
    programs = load_maturity_programs(config["maturity"]["program_dir"], ["Beta"])
    result = compute_maturity_scores(row, config, programs, tmp_path / "cells.csv")
    assert result["audit"]["status"] == "ok", result
    baseline = ad.AnnData(sparse.csr_matrix(counts[:30]), obs=original.obs.iloc[:30].copy(), var=original.var.copy())
    sc.pp.normalize_total(baseline, target_sum=1e4)
    sc.pp.log1p(baseline)
    for phase, indexes in (("early", [0, 20, 40, 60, 80]), ("late", [10, 30, 50, 70, 90])):
        sc.tl.score_genes(baseline, [f"g{i}.1" for i in indexes], gene_pool=list(original.var_names[:239]),
                          ctrl_size=50, n_bins=25, random_state=0, use_raw=False, score_name=phase)
    early = baseline.obs["early"].to_numpy(dtype=np.float32)
    late = baseline.obs["late"].to_numpy(dtype=np.float32)
    cells = read_csv_dicts(result["cell_scores_path"])
    np.testing.assert_allclose([float(item["early_score"]) for item in cells], early, rtol=0, atol=1e-7)
    np.testing.assert_allclose([float(item["late_score"]) for item in cells], late, rtol=0, atol=1e-7)
    np.testing.assert_allclose([float(item["maturity_score"]) for item in cells], late - early, rtol=0, atol=1e-7)
    assert result["rows"][0]["maturity_score"] == float(np.median(late - early))
    assert result["audit"]["expression_source"] == "layers/counts"
    coverage = result["audit"]["lineages"]["Beta"]["coverage"]["Early"]
    assert coverage["n_used"] == 5 and coverage["missing"] == ["MISSING"] and coverage["all_zero"] == ["G239"]
    assert {cell["sample_id"] for cell in cells} == {"s1", "s2"}
    np.testing.assert_allclose(ad.read_h5ad(row["h5ad_path"]).X.toarray(), original.X.toarray())


def test_deterministic_and_program_versioned(tmp_path):
    _, _, row, config = fixture_data(tmp_path)
    directory = config["maturity"]["program_dir"]
    programs = load_maturity_programs(directory, ["Beta"])
    first = compute_maturity_scores(row, config, programs, tmp_path / "first.csv")
    second = compute_maturity_scores(row, config, programs, tmp_path / "second.csv")
    assert first["rows"] == second["rows"]
    source = Path(directory) / "Beta_Stability_Late_Program.csv"
    source.write_text("gene\nG11\nG31\nG51\nG71\nG91\n", encoding="utf-8")
    changed = load_maturity_programs(directory, ["Beta"])
    third = compute_maturity_scores(row, config, changed, tmp_path / "third.csv")
    assert changed["Beta"]["Late"]["sha256"] != programs["Beta"]["Late"]["sha256"]
    assert first["rows"][0]["method_version"] != third["rows"][0]["method_version"]


@pytest.mark.parametrize("problem,expected", [
    ("normalized_only", "invalid_raw_counts"), ("missing_layer", "counts_source_missing"),
    ("annotation", "obs_column_missing"), ("genes", "insufficient_cells_or_program_genes"),
    ("few_cells", "insufficient_cells_or_program_genes"), ("collision", "ambiguous_gene_identifiers"),
    ("negative_counts", "invalid_raw_counts"), ("memory", "maturity_analysis_failed"),
])
def test_invalid_input_cannot_produce_scores(tmp_path, problem, expected):
    _, adata, row, config = fixture_data(tmp_path)
    programs = load_maturity_programs(config["maturity"]["program_dir"], ["Beta"])
    if problem == "normalized_only":
        del adata.layers["counts"]
    elif problem == "missing_layer":
        config["maturity"]["counts_layer"] = "missing"
    elif problem == "annotation":
        config["annotation_cell_type_column"] = "missing"
    elif problem == "genes":
        programs["Beta"]["Late"]["genes"] = ["MISSING"]
    elif problem == "few_cells":
        row["sample_id"] = "s2"
    elif problem == "collision":
        adata.var_names = ["G1.2", *adata.var_names[1:]]
    elif problem == "negative_counts":
        adata.layers["counts"][0, 0] = -1
    elif problem == "memory":
        config["maturity"]["max_matrix_bytes"] = 1
    adata.write_h5ad(row["h5ad_path"])
    result = compute_maturity_scores(row, config, programs, tmp_path / "cells.csv")
    assert result["rows"][0]["status"] == expected, result
    assert result["rows"][0]["maturity_score"] == ""


def test_da_exports_scores_validation_and_program_snapshots(tmp_path):
    _, _, row, config = fixture_data(tmp_path)
    config["maturity"]["cell_types"] = list(CELL_TYPES)
    outputs = DataAnalysisAgent(tmp_path / "out", h5ad_config=config).run([row])
    rows = read_csv_dicts(outputs["maturity_scores"])
    assert {item["cell_type"] for item in rows} == set(CELL_TYPES)
    assert all(item["status"] == "ok" for item in rows)
    validation = json.loads(outputs["data_analysis_validation"].read_text())
    assert validation["task_validity"]["maturity"]["valid"] is True
    assert validation["task_validity"]["reference_similarity"]["valid"] is False
    assert "cell_type_composition" not in outputs
    assert not (outputs["maturity_scores"].parent / "cell_type_composition.csv").exists()
    audit = json.loads(outputs["maturity_audit"].read_text())
    assert audit["analyses"][0]["confidence_caveats"]
    archive_inputs = outputs["maturity_scores"].parent.parent / "inputs"
    assert len(list(archive_inputs.rglob("*_Program.csv"))) == 6
    ir = json.loads(outputs["phenotype_ir"].read_text())
    metrics = [metric for phenotype in ir["phenotypes"] for metric in phenotype["metrics"] if metric["family"] == "maturity"]
    assert len(metrics) == 3
    assert all(len(metric["evidence_refs"]) == 3 for metric in metrics)
    assert [metric["value"] for metric in metrics] == [float(item["maturity_score"]) for item in rows]


def test_registered_action_and_conversation_tool(tmp_path, monkeypatch):
    _, _, row, config = fixture_data(tmp_path)
    monkeypatch.setenv("PROTOCOL_MATURITY_PROGRAM_DIR", config["maturity"]["program_dir"])
    assert get_action("phenotype.beta_maturity").availability == "available"
    assert select_action("Beta cell maturity expression").id == "phenotype.beta_maturity"
    validate_action_arguments("phenotype.beta_maturity", {"h5ad_manifest_rows": [row]})
    result = data_analysis_agent.invoke({"question": "Beta maturity", "h5ad_manifest_rows": [row],
        "data_analysis_output_dir": str(tmp_path / "out"), "workspace_root": str(tmp_path),
        "run_id": "maturity_test", "user_id": "test"})
    assert result["status"] == "ok", result
    assert result["execution_mode"] == "executed"
    assert result["validation"]["task_validity"]["maturity"]["valid"] is True
    assert result["used_tools"] == ["gene_program_maturity_score"]
    assert any(ref.get("evidence_type") == "gene_program" for ref in result["evidence_refs"])


def test_project_programs_are_available():
    programs = load_maturity_programs()
    assert {target: len(programs[target]["Late"]["genes"]) for target in CELL_TYPES} == {"Alpha": 25, "Beta": 25, "Delta": 8}


def test_orchestrator_executes_and_reviews_maturity(tmp_path, monkeypatch):
    from langchain_core.messages import HumanMessage

    _, _, row, config = fixture_data(tmp_path)
    monkeypatch.setenv("PROTOCOL_MATURITY_PROGRAM_DIR", config["maturity"]["program_dir"])
    config["maturity"].pop("program_dir")
    monkeypatch.setattr(orchestrator, "_build_chat_model_from_env", lambda: None)
    step = {"id": "maturity", "action_id": "phenotype.beta_maturity", "depends_on": [],
            "expected_outputs": get_action("phenotype.beta_maturity").outputs,
            "inputs": {"question": "Beta maturity", "h5ad_manifest_rows": [row], "h5ad_config": config}}
    result = orchestrator.build_orchestrator_graph().invoke({
        "messages": [HumanMessage(content="Beta maturity")], "workspace_root": str(tmp_path),
        "user_id": "test", "run_id": "maturity_flow",
        "protocol_output_dir": str(tmp_path / "pa"), "data_analysis_output_dir": str(tmp_path / "da"),
        "optimization_output_dir": str(tmp_path / "po"),
        "request": {"plan": {"question": "Beta maturity", "steps": [step]}},
    })
    assert result["stop_reason"] == "completed", result
    assert len(result["step_results"]) == 1
    report = result["messages"][-1].additional_kwargs["analysis_report"]
    assert report["stop_reason"] == "completed"
    assert report["quantitative_bindings"]


def test_missing_or_malformed_programs_rejected(tmp_path):
    with pytest.raises(OSError):
        load_maturity_programs(tmp_path, ["Beta"])
    with pytest.raises(ValueError):
        load_maturity_programs(tmp_path, ["Unknown"])
    (tmp_path / "Beta_Stability_Early_Program.csv").write_text("not_gene\nINS\n")
    with pytest.raises(ValueError, match="gene column"):
        load_maturity_programs(tmp_path, ["Beta"])


def test_integer_parameters_and_missing_dependency(tmp_path, monkeypatch):
    import builtins

    _, _, row, config = fixture_data(tmp_path)
    programs = load_maturity_programs(config["maturity"]["program_dir"], ["Beta"])
    config["maturity"]["chunk_size"] = 12.0
    result = compute_maturity_scores(row, config, programs, tmp_path / "cells.csv")
    assert result["rows"][0]["status"] == "ok"
    config["maturity"]["chunk_size"] = 12.5
    result = compute_maturity_scores(row, config, programs, tmp_path / "bad.csv")
    assert result["rows"][0]["status"] == "maturity_analysis_failed"
    original = builtins.__import__

    def no_scanpy(name, *args, **kwargs):
        if name == "scanpy":
            raise ImportError("scanpy unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_scanpy)
    result = compute_maturity_scores(row, config, programs, tmp_path / "missing.csv")
    assert result["rows"][0]["status"] == "dependency_missing"
