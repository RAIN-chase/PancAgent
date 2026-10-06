from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode
from protocol_optimization_agent.runtime.archive import RunArchive, fingerprint
from protocol_optimization_agent.runtime.paths import PROJECT_ROOT

DEFAULT_WORKSPACE_ROOT = Path("runs")


@dataclass(frozen=True)
class RunContext:
    user_id: str
    run_id: str
    trace_id: str
    workspace_root: Path
    project_id: str = "default"

    @classmethod
    def from_values(
        cls,
        user_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        workspace_root: str | Path | None = None,
        project_id: str = "default",
    ) -> RunContext:
        if authenticated_mode():
            policy = AccessPolicy()
            if not user_id:
                raise PermissionError("Authenticated user identity is required.")
            policy.require(user_id, project_id)
            if workspace_root and Path(workspace_root).resolve() != policy.workspace:
                raise PermissionError("Server workspace cannot be overridden.")
            workspace_root = policy.workspace
        return cls(
            user_id=_safe_id(user_id or os.getenv("PROTOCOL_USER_ID") or "local_user"),
            run_id=_safe_id(run_id or os.getenv("PROTOCOL_RUN_ID") or _timestamp_id()),
            trace_id=_safe_id(trace_id or os.getenv("PROTOCOL_TRACE_ID") or f"trace_{uuid4().hex[:12]}"),
            workspace_root=Path(workspace_root or os.getenv("PROTOCOL_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT),
            project_id=project_id,
        )

    def user_root(self) -> Path:
        return self.workspace_root / "users" / self.user_id

    def run_root(self) -> Path:
        if authenticated_mode():
            return AccessPolicy().project_root(self.user_id, self.project_id) / "runs" / self.run_id
        return self.user_root() / "runs" / self.run_id

    def agent_output_dir(self, agent_id: str) -> Path:
        return self.run_root() / _safe_id(agent_id)

    def log_dir(self) -> Path:
        return self.run_root() / "_logs"

    def manifest_path(self) -> Path:
        return self.run_root() / "run_manifest.json"


def resolve_agent_output_dir(
    agent_id: str,
    explicit_value: str | None,
    env_var: str,
    default_value: str,
    context: RunContext,
) -> str:
    if explicit_value:
        return str(authorize_output_path(Path(explicit_value), context))
    if authenticated_mode():
        return str(context.agent_output_dir(agent_id))
    env_value = os.getenv(env_var)
    if env_value:
        return str(authorize_output_path(Path(env_value), context))
    if default_value:
        return str(authorize_output_path(Path(default_value), context))
    return str(context.agent_output_dir(agent_id))


def authorize_output_path(path: Path, context: RunContext) -> Path:
    if authenticated_mode():
        return AccessPolicy().authorize_path(context.user_id, context.project_id, path, "write")
    resolved = path if path.is_absolute() else (Path.cwd() / path).resolve()
    workspace_root = context.workspace_root if context.workspace_root.is_absolute() else (Path.cwd() / context.workspace_root).resolve()
    allowed_roots = [workspace_root, PROJECT_ROOT / "runs"]
    if os.getenv("PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR") == "1":
        return resolved
    if any(_is_relative_to(resolved, root.resolve()) for root in allowed_roots):
        return resolved
    raise ValueError(
        "Output path is outside the authorized runtime workspace. "
        "Use a path under PROTOCOL_WORKSPACE_ROOT or set PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR=1 for local development."
    )


def write_run_manifest(
    context: RunContext,
    *,
    output_dirs: dict[str, str],
    inputs: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    task = {
        "timestamp_utc": _utc_now(),
        "output_dirs": output_dirs,
        "inputs": _input_fingerprints(inputs or {}),
        "extra": extra or {},
    }
    RunArchive(context).record("manifest_task", task)
    return context.manifest_path()


def content_fingerprint(paths: dict[str, str], extra: dict[str, Any] | None = None) -> str:
    digest = hashlib.sha256()
    for key, value in sorted(paths.items()):
        fingerprint = _fingerprint_path(Path(value))
        digest.update(key.encode("utf-8"))
        identity = {field: fingerprint.get(field) for field in ("exists", "kind", "sha256")}
        digest.update(json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8"))
    if extra:
        digest.update(json.dumps(extra, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8"))
    return digest.hexdigest()


def append_tool_call_log(
    context: RunContext,
    *,
    subagent: str,
    operation: str,
    status: str,
    question: str,
    output_files: dict[str, str] | None = None,
) -> Path:
    path = context.log_dir() / "tool_calls.jsonl"
    payload = {
        "schema_version": "tool-call-log-v1",
        "timestamp_utc": _utc_now(),
        "trace_id": context.trace_id,
        "run_id": context.run_id,
        "user_id": context.user_id,
        "subagent": subagent,
        "operation": operation,
        "status": status,
        "question": question,
        "output_files": output_files or {},
    }
    RunArchive(context).record("tool_summary", payload)
    return path


def evidence_response_envelope(
    *,
    context: RunContext,
    subagent: str,
    status: str,
    question: str,
    selected_operation: str,
    result_preview: Any,
    output_files: dict[str, str] | None = None,
    used_tools: list[str] | None = None,
    used_features: list[str] | None = None,
    evidence_refs: list[dict[str, Any]] | None = None,
    confidence_caveats: list[str] | None = None,
    limitations: list[dict[str, Any]] | list[str] | None = None,
    provenance: Any = None,
    validation: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "orchestrator-evidence-envelope-v2",
        "trace_id": context.trace_id,
        "run_id": context.run_id,
        "user_id": context.user_id,
        "subagent": subagent,
        "status": status,
        "question": question,
        "selected_operation": selected_operation,
        "used_tools": used_tools or [],
        "used_features": used_features or [],
        "evidence_refs": evidence_refs or [],
        "result_refs": [],
        "confidence_caveats": confidence_caveats or [],
        "limitations": limitations or [],
        "provenance": provenance or [],
        "result_preview": result_preview,
        "output_files": output_files or {},
        "validation": validation or {},
    }
    if extra:
        payload.update(extra)
    return payload


def _input_fingerprints(inputs: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {key: _fingerprint_path(Path(value)) for key, value in inputs.items()}


def _fingerprint_path(path: Path) -> dict[str, Any]:
    return fingerprint(path)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return cleaned.strip("._-") or "default"


def _timestamp_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid4().hex[:12]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
