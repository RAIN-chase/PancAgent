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

from protocol_optimization_agent.io import ensure_dir


DEFAULT_WORKSPACE_ROOT = Path("runs")


@dataclass(frozen=True)
class RunContext:
    user_id: str
    run_id: str
    trace_id: str
    workspace_root: Path

    @classmethod
    def from_values(
        cls,
        user_id: str | None = None,
        run_id: str | None = None,
        trace_id: str | None = None,
        workspace_root: str | Path | None = None,
    ) -> RunContext:
        return cls(
            user_id=_safe_id(user_id or os.getenv("PROTOCOL_USER_ID") or "local_user"),
            run_id=_safe_id(run_id or os.getenv("PROTOCOL_RUN_ID") or _timestamp_id()),
            trace_id=_safe_id(trace_id or os.getenv("PROTOCOL_TRACE_ID") or f"trace_{uuid4().hex[:12]}"),
            workspace_root=Path(workspace_root or os.getenv("PROTOCOL_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT),
        )

    def user_root(self) -> Path:
        return self.workspace_root / "users" / self.user_id

    def run_root(self) -> Path:
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
        return explicit_value
    env_value = os.getenv(env_var)
    if env_value:
        return env_value
    if os.getenv("PROTOCOL_WORKSPACE_ROOT") or os.getenv("PROTOCOL_USER_ID") or os.getenv("PROTOCOL_RUN_ID"):
        return str(context.agent_output_dir(agent_id))
    return default_value


def write_run_manifest(
    context: RunContext,
    *,
    output_dirs: dict[str, str],
    inputs: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    payload = {
        "schema_version": "run-manifest-v1",
        "created_at_utc": _utc_now(),
        "user_id": context.user_id,
        "run_id": context.run_id,
        "trace_id": context.trace_id,
        "workspace_root": str(context.workspace_root),
        "output_dirs": output_dirs,
        "inputs": _input_fingerprints(inputs or {}),
        "output_version": "protocol-optimization-agent-v1",
        "extra": extra or {},
    }
    path = context.manifest_path()
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


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
    ensure_dir(path.parent)
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
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
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
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "orchestrator-evidence-envelope-v1",
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
        "confidence_caveats": confidence_caveats or [],
        "limitations": limitations or [],
        "provenance": provenance or [],
        "result_preview": result_preview,
        "output_files": output_files or {},
    }
    if extra:
        payload.update(extra)
    return payload


def _input_fingerprints(inputs: dict[str, str]) -> dict[str, dict[str, Any]]:
    return {key: _fingerprint_path(Path(value)) for key, value in inputs.items()}


def _fingerprint_path(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    if path.is_dir():
        files = sorted(file for file in path.rglob("*") if file.is_file())
        digest = hashlib.sha256()
        for file in files[:1000]:
            stat = file.stat()
            digest.update(str(file.relative_to(path)).encode("utf-8", errors="ignore"))
            digest.update(str(stat.st_size).encode("ascii"))
            digest.update(str(int(stat.st_mtime)).encode("ascii"))
        return {
            "path": str(path),
            "exists": True,
            "kind": "directory",
            "n_files_sampled": min(len(files), 1000),
            "n_files_total": len(files),
            "sha256_metadata": digest.hexdigest(),
        }
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "kind": "file",
        "size_bytes": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": _sha256_file(path) if stat.st_size <= 100 * 1024 * 1024 else "",
        "sha256_status": "computed" if stat.st_size <= 100 * 1024 * 1024 else "skipped_large_file",
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value).strip())
    return cleaned.strip("._-") or "default"


def _timestamp_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
