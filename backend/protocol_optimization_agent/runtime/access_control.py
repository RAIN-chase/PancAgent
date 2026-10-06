from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4


def authenticated_mode() -> bool:
    mode = os.getenv("PROTOCOL_ACCESS_MODE", "local")
    if mode not in {"local", "authenticated"}:
        raise PermissionError("Unknown access mode; refusing access.")
    return mode == "authenticated"


def _identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise PermissionError("Invalid access identifier.")
    return value


class AccessPolicy:
    """Server-owned credentials, memberships and artifact paths; never populated from request state."""

    def __init__(self) -> None:
        if not authenticated_mode():
            raise PermissionError("Authenticated mode is required for server credential access.")
        path = os.getenv("PROTOCOL_AUTH_CONFIG")
        if not path:
            raise PermissionError("PROTOCOL_AUTH_CONFIG is required in authenticated mode.")
        self.config = json.loads(Path(path).read_text(encoding="utf-8"))
        self.workspace = Path(self.config["workspace_root"]).resolve()

    def authenticate(self, authorization: str) -> str:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise PermissionError("A valid Bearer credential is required.")
        digest = hashlib.sha256(token.encode()).hexdigest()
        for identity, user in self.config["users"].items():
            if hmac.compare_digest(digest, user.get("token_sha256", "")):
                return _identifier(identity)
        raise PermissionError("Invalid credential.")

    def require(self, identity: str, project_id: str, permission: str = "read") -> None:
        _identifier(identity)
        _identifier(project_id)
        permissions = self.config.get("users", {}).get(identity, {}).get("projects", {}).get(project_id, [])
        if permission not in permissions:
            raise PermissionError("Project membership or permission denied.")

    def project_root(self, identity: str, project_id: str) -> Path:
        self.require(identity, project_id)
        root = (self.workspace / "users" / identity / "projects" / project_id).resolve()
        if not root.is_relative_to(self.workspace):
            raise PermissionError("Project root escapes the server workspace.")
        return root

    def artifact(self, identity: str, project_id: str, artifact_id: str, permission: str = "read") -> Path:
        self.require(identity, project_id, permission)
        entry = self.config.get("artifacts", {}).get(artifact_id)
        if (not entry or entry.get("user_id") != identity or entry.get("project_id") != project_id
                or permission not in entry.get("permissions", [])):
            raise PermissionError("Artifact access denied.")
        path = Path(entry["path"]).resolve()
        if not path.is_relative_to(self.project_root(identity, project_id)):
            raise PermissionError("Artifact path escapes its authorized project.")
        return path

    def authorize_path(self, identity: str, project_id: str, path: Path, permission: str) -> Path:
        self.require(identity, project_id, permission)
        resolved = path.resolve()
        if not resolved.is_relative_to(self.project_root(identity, project_id)):
            raise PermissionError("Path escapes the authenticated project workspace.")
        return resolved


def configure_authenticated_state(state: dict[str, Any], config: dict[str, Any] | None) -> dict[str, Any]:
    if not authenticated_mode():
        return state
    policy = AccessPolicy()
    # This reserved field is supplied by the LangGraph server's authentication handler, not graph input.
    user = (config or {}).get("configurable", {}).get("langgraph_auth_user", {})
    identity = user.get("identity") if isinstance(user, dict) else getattr(user, "identity", None)
    if not identity:
        raise PermissionError("Authenticated server runtime identity is required.")
    project = state.get("project_id") or os.getenv("PROTOCOL_RESEARCH_PROJECT_ID") or "default"
    policy.require(identity, project, "read")
    if state.get("user_id") and state["user_id"] != identity:
        raise PermissionError("Client user_id cannot override authenticated identity.")
    if state.get("workspace_root") and Path(state["workspace_root"]).resolve() != policy.workspace:
        raise PermissionError("Client workspace paths are not accepted in authenticated mode.")
    run_id = _identifier(state.get("run_id") or f"run_{uuid4().hex[:16]}")
    result = {**state, "user_id": identity, "workspace_root": str(policy.workspace), "project_id": project, "run_id": run_id}
    for key in ("protocol_output_dir", "data_analysis_output_dir", "optimization_output_dir", "output_dir"):
        artifact_id = state.get(key.replace("_dir", "_artifact_id"))
        if artifact_id:
            result[key] = str(policy.artifact(identity, project, artifact_id, "write"))
        else:
            agent = {"protocol_output_dir": "protocol_analysis", "data_analysis_output_dir": "data_analysis",
                     "optimization_output_dir": "protocol_optimization", "output_dir": "protocol_analysis"}[key]
            result[key] = str(policy.project_root(identity, project) / "runs" / run_id / agent)
        if state.get(key) and Path(state[key]).resolve() != Path(result[key]).resolve():
            raise PermissionError("Submit artifact IDs, not client paths.")
    return result


def resolve_manifest_artifacts(rows: list[dict[str, Any]], user_id: str, project_id: str) -> list[dict[str, Any]]:
    if not authenticated_mode():
        return rows
    policy = AccessPolicy()
    policy.require(user_id, project_id, "write")
    result = []
    for row in rows:
        if row.get("h5ad_path") or row.get("path"):
            raise PermissionError("Client h5ad paths are not accepted; submit artifact_id.")
        result.append({**row, "h5ad_path": str(policy.artifact(user_id, project_id, row.get("artifact_id", "")))})
    return result


def forbid_client_paths(value: Any) -> None:
    if not authenticated_mode():
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if child and key in {"llm", "api_key", "api_key_env", "base_url", "command", "script"}:
                raise PermissionError("Execution and LLM credentials/configuration are server-owned.")
            if child and (key.endswith(("_path", "_dir")) or key in {"path", "filename", "filename_prefix"}):
                raise PermissionError("Client-supplied tool file paths are disabled in authenticated mode.")
            forbid_client_paths(child)
    elif isinstance(value, list):
        for child in value:
            forbid_client_paths(child)


def require_researcher(context: Any, project_id: str, researcher: str) -> None:
    if authenticated_mode():
        AccessPolicy().require(context.user_id, project_id, "review")
        if researcher != context.user_id:
            raise PermissionError("Researcher must be the authenticated identity.")
