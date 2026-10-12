import json

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda

from protocol_optimization_agent.agents.actions import execute_action, get_action, select_action, validate_action_arguments
from protocol_optimization_agent.agents.analysis_flow import inspect_result, question_goal
from protocol_optimization_agent.agents.orchestrator import build_orchestrator_graph
from protocol_optimization_agent.runtime.context import RunContext


@pytest.mark.parametrize("question", ["当前有哪些数据可用", "你现在有什么数据？", "有哪些可用的数据集？", "list available datasets"])
def test_catalog_routing(question):
    assert select_action(question).id == "data.catalog"
    assert question_goal(question) == "catalog"


def test_inventory_words_do_not_override_explicit_analysis():
    assert select_action("分析可用数据的 INS 基因表达").id == "phenotype.gene_expression"
    assert select_action("对可用数据运行差异表达").id == "phenotype.differential_expression"


def run_catalog(tmp_path, question="当前有哪些数据可用"):
    def no_model(_):
        raise AssertionError("Inventory lookup must not depend on model planning or review")
    return build_orchestrator_graph(chat_model=RunnableLambda(no_model)).invoke({
        "messages": [HumanMessage(content=question)], "workspace_root": str(tmp_path),
        "user_id": "alice", "project_id": "project", "run_id": "catalog-test",
        "protocol_output_dir": str(tmp_path / "missing-pa"),
        "data_analysis_output_dir": str(tmp_path / "missing-da"),
        "optimization_output_dir": str(tmp_path / "missing-po"),
    })


def test_orchestrator_catalog_lists_real_metadata_and_preserves_scope(tmp_path, monkeypatch):
    h5py = pytest.importorskip("h5py")
    path = tmp_path / "atlas.h5ad"
    with h5py.File(path, "w") as handle:
        handle.attrs["encoding-type"] = "anndata"
        handle.create_group("obs").create_dataset("_index", data=[1, 2])
        handle.create_group("var").create_dataset("_index", data=[1])
        handle.create_dataset("X", data=[[1], [2]])
    source = {"version": "catalog-v1", "datasets": [
        {"dataset_id": "atlas", "name": "Reference atlas", "role": "reference_atlas", "status": "ready", "version": "atlas-v1",
         "h5ad_path": str(path), "size_bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns},
        {"dataset_id": "organoids", "role": "public_organoid", "status": "transferring"},
        {"dataset_id": "private-bob", "role": "user_scrna", "user_id": "bob", "project_id": "project"},
        {"dataset_id": "other-project", "role": "public_organoid", "project_ids": ["other"]},
    ]}
    manifest = tmp_path / "catalog.json"
    manifest.write_text(json.dumps(source), encoding="utf-8")
    monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(manifest))
    result = run_catalog(tmp_path)
    assert result["stop_reason"] == "completed"
    assert result["plan"]["acceptance"]["answered"]
    assert [step["action_id"] for step in result["step_results"]] == ["data.catalog"]
    assert result["budget"]["model_calls"] == 0
    tool = next(message for message in result["messages"] if isinstance(message, ToolMessage))
    envelope = json.loads(tool.content)
    rows = envelope["result_preview"]["datasets"]
    assert [row["dataset_id"] for row in rows] == ["atlas", "organoids"]
    assert rows[0]["n_obs"] == 2 and rows[0]["n_vars"] == 1
    assert rows[0]["analysis_available"] and not rows[1]["analysis_available"]
    assert "h5ad_path" not in json.dumps(envelope)
    reply = result["messages"][-1].content
    assert "atlas-v1" in reply and "传输中" in reply and "科学 QC" in reply
    assert "private-bob" not in reply and "other-project" not in reply
    assert "| 2 | 1 |" in reply
    assert result["messages"][-1].additional_kwargs["analysis_report"]["quantitative_bindings"] == []


@pytest.mark.parametrize("mode", ["unconfigured", "empty", "invalid", "transferring_collection"])
def test_catalog_reports_empty_configuration_and_failure_honestly(tmp_path, monkeypatch, mode):
    monkeypatch.delenv("PROTOCOL_DATA_CATALOG", raising=False)
    if mode != "unconfigured":
        path = tmp_path / "catalog.json"
        source = {"datasets": [], "collections": [{"name": "Organoids", "status": "transferring", "role": "public_organoid"}]} if mode == "transferring_collection" else {"datasets": []}
        path.write_text("{" if mode == "invalid" else json.dumps(source), encoding="utf-8")
        monkeypatch.setenv("PROTOCOL_DATA_CATALOG", str(path))
    result = run_catalog(tmp_path)
    reply = result["messages"][-1].content
    if mode == "invalid":
        assert result["stop_reason"] != "completed" and "查询失败" in reply
    else:
        assert result["stop_reason"] == "completed"
        assert ("尚未配置" if mode == "unconfigured" else "暂无已登记") in reply
        if mode == "transferring_collection":
            assert "Organoids" in reply and "传输中" in reply
    assert "请指定目录" not in reply


def test_catalog_cannot_accept_caller_identity_or_paths(tmp_path):
    for arguments in ({"user_id": "bob"}, {"project_id": "other"}, {"path": "/private"}):
        with pytest.raises(ValueError):
            validate_action_arguments("data.catalog", arguments)
    assert not inspect_result({"status": "ok", "result_preview": {"configured": True}}, "data.catalog", get_action("data.catalog").outputs)[0]


def test_catalog_action_checks_project_permission_before_listing(tmp_path, monkeypatch):
    from protocol_optimization_agent.runtime import data_catalog
    calls = []
    class Policy:
        def require(self, identity, project, permission):
            calls.append((identity, project, permission))
            raise PermissionError("Denied")
    monkeypatch.setattr(data_catalog, "authenticated_mode", lambda: True)
    monkeypatch.setattr(data_catalog, "AccessPolicy", Policy)
    result = execute_action("data.catalog", {}, RunContext.from_values(
        workspace_root=tmp_path, user_id="alice", project_id="project", run_id="denied"),
        protocol_output_dir=str(tmp_path / "pa"), data_analysis_output_dir=str(tmp_path / "da"),
        optimization_output_dir=str(tmp_path / "po"), max_features=40)
    assert calls == [("alice", "project", "read")]
    assert result["status"] == "analysis_failed" and result["result_preview"] == {}
