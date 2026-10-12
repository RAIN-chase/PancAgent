import json

import pytest

from protocol_optimization_agent.agents.actions import validate_action_arguments
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.data_catalog import catalog_manifest, dataset_catalog
from protocol_optimization_agent.runtime.io import write_jsonl


def test_multi_comparison_preserves_values_missingness_and_sources(tmp_path):
    # No chat or model is needed to compare the compiled matrix.
    import csv
    output = tmp_path / "pa"
    output.mkdir()
    with (output / "protocol_feature_matrix.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["protocol_id", "factor_dose", "only_third", "same"])
        writer.writeheader()
        writer.writerows([
            {"protocol_id": "a", "factor_dose": "0", "only_third": "", "same": "3.000"},
            {"protocol_id": "b", "factor_dose": "3.000", "only_third": "", "same": "3.000"},
            {"protocol_id": "c", "factor_dose": "", "only_third": "7", "same": "3.000"},
        ])
    write_jsonl(output / "normalized_factor_events.jsonl", [
        {"protocol_id": pid, "event_id": pid, "evidence_quote": f"source {pid}"} for pid in "abc"])
    write_jsonl(output / "standardized_protocol_representations.jsonl", [{"protocol_id": pid, "stages": []} for pid in "abc"])
    engine = ProtocolQueryEngine(output)
    result = engine.compare_many(["a", "b", "c", "a"])
    assert result["protocol_ids"] == ["a", "b", "c"]
    differences = {row["feature"]: row["values"] for row in result["feature_differences"]}
    assert differences["factor_dose"] == {"a": "0", "b": "3.000", "c": ""}
    assert differences["only_third"]["c"] == "7"
    assert "same" not in differences
    assert {row["protocol_id"] for row in result["event_step_diff"]["source_refs"]} == set("abc")
    assert len(result["event_step_diff"]["anchor_alignments"]) == 2
    assert engine.compare_many(["a", "b", "c"], 1)["features_truncated"]
    assert engine.compare_many(["a", "missing"])["status"] == "not_found"
    pair = engine.compare_many(["a", "b"])
    assert pair["feature_differences"] == engine.compare_protocols("a", "b")["feature_differences"]
    from protocol_optimization_agent.agents.actions import execute_action
    from protocol_optimization_agent.runtime.context import RunContext

    envelope = execute_action("protocol.compare", {"protocol_ids": ["a", "b", "c"]},
        RunContext.from_values(workspace_root=tmp_path, user_id="local_user", run_id="compare-many"),
        protocol_output_dir=str(output), data_analysis_output_dir=str(tmp_path / "da"),
        optimization_output_dir=str(tmp_path / "po"), max_features=40)
    assert envelope["result_preview"]["tool_result"]["protocol_ids"] == ["a", "b", "c"]
    assert {ref["protocol_id"] for ref in envelope["evidence_refs"] if ref.get("protocol_id")} == set("abc")
    assert envelope["used_tools"] == ["compare_many"]
    from protocol_optimization_agent.agents.analysis_flow import inspect_result
    assert "original_protocol_details_incomplete" not in inspect_result(envelope, "protocol.compare", ["feature_diff", "event_step_diff"])[1]


@pytest.mark.parametrize("arguments", [{}, {"protocol_ids": ["a"]}, {"protocol_ids": ["a", "a"]},
    {"protocol_ids": ["a", "b"], "protocol_id_a": "c"}, {"protocol_ids": ["a", 4]}])
def test_compare_rejects_invalid_selection(arguments):
    with pytest.raises(ValueError):
        validate_action_arguments("protocol.compare", arguments)


def test_catalog_scope_transfer_and_metadata_only(tmp_path, monkeypatch):
    h5py = pytest.importorskip("h5py")
    data = tmp_path / "data.h5ad"
    with h5py.File(data, "w") as handle:
        handle.attrs["encoding-type"] = "anndata"
        handle.create_group("obs").create_dataset("_index", data=[1, 2])
        handle.create_group("var").create_dataset("_index", data=[1])
        handle.create_dataset("X", data=[[1], [2]])
    ready = {"dataset_id": "atlas", "role": "reference_atlas", "status": "ready", "version": "v1",
             "h5ad_path": str(data), "size_bytes": data.stat().st_size, "mtime_ns": data.stat().st_mtime_ns}
    source = {"version": "v1", "datasets": [ready,
        {**ready, "dataset_id": "incoming", "role": "public_organoid", "status": "transferring"},
        {**ready, "dataset_id": "mine", "role": "user_scrna", "user_id": "local_user", "project_id": "default"},
        {**ready, "dataset_id": "secret", "role": "user_scrna", "user_id": "bob", "project_id": "default"}]}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(source))
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(path))
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "local")
    catalog = dataset_catalog(None, "default")
    assert {row["dataset_id"] for row in catalog["datasets"]} == {"atlas", "incoming", "mine"}
    assert "h5ad_path" not in json.dumps(catalog)
    assert catalog["datasets"][0]["analysis_available"]
    assert not catalog["datasets"][1]["analysis_available"]
    assert catalog_manifest("atlas", "local_user", "default")[0]["h5ad_path"] == str(data.resolve())
    with pytest.raises(ValueError, match="version changed"):
        catalog_manifest("atlas", "local_user", "default", "old-version")
    for dataset_id in ("incoming", "secret"):
        with pytest.raises(ValueError):
            catalog_manifest(dataset_id, "local_user", "default")
    data.write_bytes(b"partial transfer")
    assert dataset_catalog(None, "default")["datasets"][0]["status"] == "unavailable"


def test_authenticated_catalog_api_binds_identity_and_project(tmp_path, monkeypatch):
    import hashlib
    from fastapi.testclient import TestClient
    from protocol_optimization_agent.interfaces.api import create_app

    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"workspace_root": str(tmp_path), "users": {
        "alice": {"token_sha256": hashlib.sha256(b"token").hexdigest(), "projects": {"p": ["read", "write"]}}
    }}))
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"datasets": [
        {"dataset_id": "public", "role": "public_organoid", "status": "transferring"},
        {"dataset_id": "mine", "role": "user_scrna", "user_id": "alice", "project_id": "p", "status": "pending"},
        {"dataset_id": "foreign", "role": "user_scrna", "user_id": "bob", "project_id": "p", "status": "pending"},
        {"dataset_id": "restricted", "role": "reference_atlas", "project_ids": ["q"], "status": "pending"},
    ]}))
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "authenticated")
    monkeypatch.setenv("PROTOCOL_AUTH_CONFIG", str(auth))
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(catalog))
    with TestClient(create_app()) as client:
        assert client.get("/data-catalog?project_id=p").status_code == 401
        headers = {"Authorization": "Bearer token"}
        assert client.get("/data-catalog?project_id=q", headers=headers).status_code == 403
        response = client.get("/data-catalog?project_id=p", headers=headers)
        assert response.status_code == 200
        assert {row["dataset_id"] for row in response.json()["datasets"]} == {"public", "mine"}
        assert "user_id" not in response.text


def test_publish_catalog_hashes_without_copy_and_detects_changed_version(tmp_path, monkeypatch, capsys):
    import anndata
    import numpy as np
    from protocol_optimization_agent.interfaces.run_archive_cli import main

    path = tmp_path / "small.h5ad"
    anndata.AnnData(np.ones((3, 2))).write_h5ad(path)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"datasets": [{"dataset_id": "test", "role": "public_organoid",
                                                "status": "transferring", "h5ad_path": str(path)}]}))
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "local")
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(catalog))
    monkeypatch.setattr("sys.argv", ["archive", "publish-catalog", "--catalog", str(catalog), "--dataset-id", "test"])
    main()
    published = json.loads(capsys.readouterr().out)
    assert published["version"].startswith("sha256:")
    row = dataset_catalog(None, "default")["datasets"][0]
    assert (row["n_obs"], row["n_vars"], row["analysis_available"]) == (3, 2, True)
    import os
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1000000))
    assert not dataset_catalog(None, "default")["datasets"][0]["analysis_available"]


def test_da_can_inspect_public_catalog_without_upload_or_client_path(tmp_path, monkeypatch):
    import anndata
    import numpy as np
    from protocol_optimization_agent.agents.data_analysis import data_analysis_agent

    path = tmp_path / "public.h5ad"
    anndata.AnnData(np.ones((3, 2))).write_h5ad(path)
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"datasets": [{"dataset_id": "public", "role": "public_organoid",
        "status": "ready", "h5ad_path": str(path), "version": "v1", "size_bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns}]}))
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "local")
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(catalog))
    result = data_analysis_agent.invoke({"question": "Inspect public dataset", "request": {
        "operation": "summary", "dataset_id": "public", "dataset_version": "v1"},
        "data_analysis_output_dir": str(tmp_path / "out"), "workspace_root": str(tmp_path),
        "user_id": "local_user", "run_id": "public-inspection"})
    assert result["execution_mode"] == "executed"
    assert result["dataset_summaries"][0]["dataset_id"] == "public"
    assert result["structured_request"]["dataset_version"] == "v1"
    assert result["execution_plan"]["requested_tasks"] == ["summary"]
    assert "cell_type_composition.csv" not in result["available_output_files"]
