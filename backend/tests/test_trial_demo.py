from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.interfaces.run_archive_cli import main
from protocol_optimization_agent.runtime.archive import file_hash
from protocol_optimization_agent.runtime.data_catalog import catalog_manifest, dataset_catalog
from protocol_optimization_agent.runtime.io import read_csv_dicts


def test_synthetic_catalog_runs_composition(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "examples/tutorial/prepare_h5ad_demo.py"
    root = tmp_path / "demo"
    subprocess.run([sys.executable, str(script), str(root)], check=True)
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(root / "catalog.json"))
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "local")
    dataset = dataset_catalog("local_user", "default")["datasets"][0]
    assert dataset["analysis_available"]
    assert dataset["n_obs"] == 4 and dataset["n_vars"] == 2
    manifest = catalog_manifest(dataset["dataset_id"], "local_user", "default", dataset["version"])
    output = DataAnalysisAgent(tmp_path / "da", {"requested_tasks": ["cell_type_composition"]}).run(manifest)
    rows = read_csv_dicts(output["cell_type_composition"])
    assert {row["cell_type"]: float(row["proportion"]) for row in rows} == {"demo_a": 0.5, "demo_b": 0.5}


def test_export_preview_checks_hash_and_keeps_run_identity(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    result = root / "result.json"
    result.write_text('{"schema_version":"orchestrator-evidence-envelope-v2","result_preview":[0]}')
    ref = {"result_id": "r", "path": "result.json", "sha256": file_hash(result), "input_version": "v1"}
    report = root / "final_report.json"
    report.write_text(json.dumps({"run_id": "original", "complete_results": {"result_refs": [ref]}}))
    (root / "bundle_manifest.json").write_text(json.dumps({"files": {"final_report.json": file_hash(report)}}))
    output = tmp_path / "preview.json"
    monkeypatch.setattr(sys, "argv", ["run-archive", "export-preview", "--run-dir", str(root), "--output", str(output)])
    main()
    tool = json.loads(json.loads(output.read_text(encoding="utf-8"))["messages"][0]["content"])
    assert tool["run_id"] == "original" and tool["result_refs"] == [ref] and tool["result_preview"] == [0]
    result.write_text("{}")
    with pytest.raises(ValueError, match="reference is unauthorized or changed"):
        main()
