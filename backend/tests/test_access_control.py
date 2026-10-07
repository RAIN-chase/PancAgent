from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from protocol_optimization_agent.agents.orchestrator import build_orchestrator_graph
from protocol_optimization_agent.runtime.access_control import (
    AccessPolicy,
    configure_authenticated_state,
    forbid_client_paths,
    resolve_manifest_artifacts,
)
from protocol_optimization_agent.runtime.context import RunContext, authorize_output_path
from protocol_optimization_agent.science.models import Hypothesis
from protocol_optimization_agent.science.state import ScientificWorldModel


@pytest.fixture
def policy(tmp_path: Path, monkeypatch):
    root = tmp_path / "runtime"
    pa = root / "users" / "alice" / "projects" / "study" / "pa"
    pa.mkdir(parents=True)
    (pa / "protocol_feature_matrix.csv").write_text("protocol_id,protocol_name\np1,A\np2,B\n", encoding="utf-8")
    config = {"workspace_root": str(root), "users": {
        "alice": {"token_sha256": hashlib.sha256(b"alice_test_token").hexdigest(),
                  "projects": {"study": ["read", "write", "review"]}},
        "bob": {"token_sha256": hashlib.sha256(b"bob_test_token").hexdigest(),
                "projects": {"other": ["read", "write"]}},
        "reader": {"token_sha256": hashlib.sha256(b"reader_test_token").hexdigest(),
                   "projects": {"study": ["read"]}},
    }, "artifacts": {
        "pa": {"user_id": "alice", "project_id": "study", "path": str(pa), "permissions": ["read", "write"]},
        "readonly": {"user_id": "alice", "project_id": "study", "path": str(pa), "permissions": ["read"]},
        "foreign": {"user_id": "bob", "project_id": "other", "path": str(root / "users" / "bob"), "permissions": ["read"]},
    }}
    path = tmp_path / "server_auth.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "authenticated")
    monkeypatch.setenv("PROTOCOL_AUTH_CONFIG", str(path))
    return AccessPolicy()


def test_server_identity_project_and_artifacts_fail_closed(policy, tmp_path: Path) -> None:
    assert policy.authenticate("Bearer alice_test_token") == "alice"
    with pytest.raises(PermissionError):
        policy.authenticate("Bearer alice")
    with pytest.raises(PermissionError):
        policy.artifact("alice", "study", "foreign")
    with pytest.raises(PermissionError):
        policy.require("alice", "other")
    config = {"configurable": {"langgraph_auth_user": {"identity": "alice"}}}
    state = configure_authenticated_state({"project_id": "study", "protocol_output_artifact_id": "pa"}, config)
    assert state["user_id"] == "alice"
    assert state["workspace_root"] == str(policy.workspace)
    assert configure_authenticated_state(state, config)["protocol_output_dir"] == state["protocol_output_dir"]
    for input_state in ({"project_id": "study", "user_id": "bob"},
                        {"project_id": "study", "workspace_root": str(tmp_path)},
                        {"project_id": "study", "protocol_output_dir": str(tmp_path / "foreign")},
                        {"project_id": "study", "protocol_output_artifact_id": "readonly"}):
        with pytest.raises(PermissionError):
            configure_authenticated_state(input_state, config)
    with pytest.raises(PermissionError):
        configure_authenticated_state({"project_id": "study", "user_id": "alice"}, {})
    rows = resolve_manifest_artifacts([{"artifact_id": "pa", "dataset_id": "d"}], "alice", "study")
    assert rows[0]["h5ad_path"] == str(policy.artifact("alice", "study", "pa"))
    with pytest.raises(PermissionError):
        resolve_manifest_artifacts([{"h5ad_path": str(tmp_path)}], "alice", "study")
    with pytest.raises(PermissionError):
        forbid_client_paths({"annotation": {"model_path": "other_user_model"}})
    context = RunContext.from_values(user_id="alice", project_id="study")
    with pytest.raises(PermissionError):
        authorize_output_path(policy.workspace / "users" / "bob" / "results", context)
    reader = RunContext.from_values(user_id="reader", project_id="study")
    with pytest.raises(PermissionError):
        ScientificWorldModel(reader, "study").put([Hypothesis(id="h", statement="x", feature="x", objective="y")], "S4")


def test_main_graph_requires_authenticated_server_runtime(policy) -> None:
    class ToolModel(FakeMessagesListChatModel):
        def bind_tools(self, tools):
            return self

    graph = build_orchestrator_graph(ToolModel(responses=[AIMessage(content="done")]))
    state = {"project_id": "study", "messages": [HumanMessage(content="hello")]}
    with pytest.raises(PermissionError, match="identity"):
        graph.invoke(state)
    result = graph.invoke(state, {"configurable": {"langgraph_auth_user": {"identity": "alice"}}})
    assert result["user_id"] == "alice"


def test_fastapi_authentication_path_and_approval_identity(policy) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from protocol_optimization_agent.interfaces.api import create_app

    client = TestClient(create_app(enable_experimental_feedback=True))
    payload = {"project_id": "study", "output_artifact_id": "pa", "query": "A"}
    headers = {"Authorization": "Bearer alice_test_token"}
    assert client.post("/protocol-query/find", json=payload).status_code == 401
    assert client.post("/protocol-query/find", json=payload, headers=headers).status_code == 200
    assert client.post("/protocol-query/find", json={**payload, "project_id": "other"}, headers=headers).status_code == 403
    assert client.post("/protocol-query/find", json={**payload, "output_dir": "/another-user"}, headers=headers).status_code == 403
    assert client.post("/protocol-query/find", json={**payload, "output_artifact_id": "foreign"}, headers=headers).status_code == 403
    assert client.post("/protocol-query/find", json={**payload, "user_id": "bob"}, headers=headers).status_code == 422
    context = RunContext.from_values(user_id="alice", project_id="study")
    store = ScientificWorldModel(context, "study")
    from protocol_optimization_agent.science.models import ExperimentCandidate
    store.put([Hypothesis(id="h", statement="x", feature="x", objective="y"),
               ExperimentCandidate(id="exp", hypothesis_ids=["h"], intervention={})], "S5")
    evaluation = store.get("experiment", "exp")["evaluation_id"]
    response = client.post("/scientific/experiments/review", headers=headers, json={
        "project_id": "study", "experiment_id": "exp", "evaluation_id": evaluation,
        "approve": False, "reason": "Incomplete design"})
    assert response.status_code == 200, response.text
    assert response.json()["reviewer"] == "alice"
    with pytest.raises(PermissionError, match="Researcher"):
        store.mark_performed("exp", "bob")


def test_authenticated_mode_without_server_config_refuses_access(monkeypatch) -> None:
    monkeypatch.setenv("PROTOCOL_ACCESS_MODE", "authenticated")
    monkeypatch.delenv("PROTOCOL_AUTH_CONFIG", raising=False)
    with pytest.raises(PermissionError, match="required"):
        AccessPolicy()


def test_langgraph_auth_handlers_stamp_owner_and_deny_unhandled_operations(policy) -> None:
    from protocol_optimization_agent.interfaces.server_auth import (
        authenticate,
        create_thread,
        deny_unhandled,
        own_threads,
    )

    assert asyncio.run(authenticate("Bearer alice_test_token")) == {"identity": "alice"}
    ctx = SimpleNamespace(user=SimpleNamespace(identity="alice"))
    request = {"metadata": {"owner": "bob"}}
    asyncio.run(create_thread(ctx, request))
    assert request["metadata"]["owner"] == "alice"
    assert asyncio.run(own_threads(ctx, request)) == {"owner": "alice"}
    assert asyncio.run(deny_unhandled(ctx, {})) is False
