from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import platform
import shutil
import sqlite3
import subprocess
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from importlib.metadata import distributions
from pathlib import Path
from threading import RLock
from typing import Any, get_type_hints
from uuid import uuid4

from protocol_optimization_agent.runtime.paths import PACKAGE_ROOT, PROJECT_ROOT

_current: ContextVar[Any] = ContextVar("run_archive", default=None)
_call: ContextVar[str] = ContextVar("archive_call", default="")
TERMINAL = {"succeeded", "partial", "failed", "cancelled"}


def _credential_key(name: str) -> bool:
    key = name.lower().replace("-", "_")
    if key == "api_key_env" or key.endswith("_api_key_env"):
        return False
    return key in {"token", "bearer", "credentials", "credential", "secret", "secrets", "authorization", "password"} or key.endswith(
        ("api_key", "auth_token", "access_token", "refresh_token", "client_secret", "_secret", "secret_key", "secret_access_key", "password")
    ) or key.startswith("token_sha")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): ("[REDACTED]" if _credential_key(str(key))
                else redact(child)) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(child) for child in value]
    if isinstance(value, (set, frozenset)):
        return {"python_type": type(value).__name__, "items": sorted((redact(child) for child in value), key=repr)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, str):
        for name, secret in os.environ.items():
            if len(secret) >= 8 and _credential_key(name):
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return {"non_finite_number": str(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(redact(value), indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise ValueError("Symlink inputs must be resolved by the authorized artifact resolver.")
    path = path.resolve()
    if not path.exists():
        return {"path": str(path), "exists": False}
    if path.is_symlink():
        raise ValueError("Symlink inputs must be resolved by the authorized artifact resolver.")
    if path.is_dir():
        entries = sorted(path.rglob("*"))
        if any(item.is_symlink() or not item.resolve().is_relative_to(path) for item in entries):
            raise ValueError("Directory input contains a symlink or an escaping member.")
        files = {str(item.relative_to(path)): fingerprint(item) for item in entries if item.is_file()}
        identity = {name: item["sha256"] for name, item in files.items()}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        return {"path": str(path), "exists": True, "kind": "directory", "sha256": digest, "files": files}
    from protocol_optimization_agent.runtime.dataset_registry import registered_fingerprint

    registered = registered_fingerprint(path)
    if registered:
        return registered
    before = path.stat()
    digest = file_hash(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError(f"Input changed during hashing: {path}")
    return {"path": str(path), "exists": True, "kind": "file", "size_bytes": after.st_size,
            "mtime_ns": after.st_mtime_ns, "ctime_ns": after.st_ctime_ns, "sha256": digest,
            "sha256_status": "computed", "artifact_id": f"sha256:{digest}", "version": digest}


class RunArchive:
    """SQLite serializes journal updates; exported files are views of the authoritative journal."""

    def __init__(self, context: Any) -> None:
        from protocol_optimization_agent.runtime.access_control import AccessPolicy, authenticated_mode

        if authenticated_mode():
            AccessPolicy().require(context.user_id, context.project_id, "read")
        self.context = context
        self.root = context.run_root().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "run_journal.sqlite"
        self.input_cache = {}
        with self._transaction() as con:
            con.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def _transaction(self):
        con = sqlite3.connect(self.database, timeout=60)
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def scope(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def record(self, kind: str, payload: Any) -> None:
        with self._transaction() as con:
            rows = [(row[0], json.loads(row[1])) for row in con.execute("SELECT kind,payload FROM events ORDER BY seq")]
            if any(event == "finish" for event, _ in rows):
                raise ValueError("Completed runs are immutable; create a child run.")
            if kind == "begin" and any(event == "begin" for event, _ in rows):
                raise ValueError("Run ID is already reserved.")
            if kind == "finish" and payload.get("status") in {"succeeded", "partial"}:
                started = {value["call_id"] for event, value in rows if event == "call_start"}
                ended = {value["call_id"] for event, value in rows if event == "call_end"}
                if started - ended:
                    raise ValueError("Cannot seal a run with active tool/model calls.")
            item = redact({"timestamp_utc": now(), **payload})
            con.execute("INSERT INTO events(kind,payload) VALUES (?,?)", (kind, json.dumps(item, ensure_ascii=False, allow_nan=False)))
            self._export([*rows, (kind, item)], kind)

    def _assert_open(self) -> None:
        with sqlite3.connect(self.database) as con:
            if con.execute("SELECT 1 FROM events WHERE kind='finish' LIMIT 1").fetchone():
                raise ValueError("Completed runs are immutable; create a child run.")

    def start(self, config: dict[str, Any], *, parent_run_id: str = "", thread_id: str = "") -> None:
        source_root = PROJECT_ROOT
        source_files = [*sorted(PACKAGE_ROOT.rglob("*.py")),
                        source_root / "pyproject.toml"]
        source_identity = {str(path.relative_to(source_root)): file_hash(path) for path in source_files if path.is_file()}
        try:
            revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=source_root, capture_output=True,
                                      text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            revision = "unavailable"
        self.record("begin", {"run_id": self.context.run_id, "trace_id": self.context.trace_id,
                    "user_id": self.context.user_id, "project_id": self.context.project_id,
                    "parent_run_id": parent_run_id, "thread_id": thread_id, "config": config,
                    "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "git_commit": revision, "source_sha256": source_identity,
                        "packages": {dist.metadata["Name"]: dist.version for dist in distributions() if dist.metadata["Name"]}}})
        # Preserve the actual working source, not just HEAD, including uncommitted Python changes.
        for path in source_files:
            if path.is_file():
                target = self.root / "source" / path.relative_to(source_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)

    def inputs(self, paths: dict[str, str], metadata: dict[str, Any] | None = None) -> None:
        self._assert_open()
        identities = {}
        for key, value in paths.items():
            path = Path(value)
            signature = None
            if path.is_file() and not path.is_symlink():
                info = path.stat()
                signature = (info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            cache_key = (str(path.resolve()), signature)
            identity = self.input_cache.get(cache_key) if signature else None
            if identity is None:
                identity = fingerprint(path)
                if signature:
                    self.input_cache[cache_key] = dict(identity)
            identities[key] = dict(identity)
        for identity in identities.values():
            if identity.get("kind") == "file":
                source = Path(identity["path"])
                if source.suffix != ".h5ad":
                    target = self.root / "inputs" / identity["sha256"] / source.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        shutil.copyfile(source, target)
                    if file_hash(target) != identity["sha256"]:
                        raise ValueError("Input changed while archiving.")
                    identity["snapshot_path"] = str(target)
        self.record("inputs", {"call_id": _call.get(), "inputs": identities,
                               "metadata": metadata or {}})

    def seed(self, source: Path, agent: str) -> Path:
        self._assert_open()
        target = self.root / agent
        source = source.resolve()
        if source == target:
            return target
        if self.root.is_relative_to(source):
            raise ValueError("A source directory cannot contain the new run archive.")
        if source.exists():
            if target.exists():
                raise ValueError("An analysis workspace can only be seeded once.")
            if any(path.suffix.lower() == ".h5ad" for path in source.rglob("*") if path.is_file()):
                raise ValueError("Source collections must not contain h5ad; submit versioned dataset artifacts separately.")
            self.inputs({agent: str(source)}, {"mode": "cached_source_snapshot"})
            shutil.copytree(source, target, symlinks=False)
            if fingerprint(source)["sha256"] != fingerprint(target)["sha256"]:
                raise ValueError("Source changed while making its run snapshot.")
            cache_id = f"cache_{uuid4().hex}"
            self.snapshot_outputs({str(path.relative_to(target)): path for path in target.rglob("*") if path.is_file()},
                                  cache_id, role="input")
            self.record("cache_source", {"agent": agent, "source": str(source), "snapshot": str(target),
                                          "immutable_snapshot_id": cache_id})
        return target

    def snapshot_outputs(self, outputs: dict[str, Any], call_id: str, *, role: str = "output") -> list[dict[str, Any]]:
        self._assert_open()
        artifacts = []
        for name, value in outputs.items():
            source = Path(value)
            if not source.is_file():
                continue
            identity = fingerprint(source)
            target = self.root / "artifacts" / call_id / f"{len(artifacts):03d}_{source.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if file_hash(target) != identity["sha256"]:
                raise ValueError("Output changed while archiving.")
            artifacts.append({"name": name, "source": str(source), "path": str(target),
                              "artifact_id": identity["artifact_id"], "version": identity["sha256"],
                              "sha256": identity["sha256"], "call_id": call_id,
                              "role": role,
                              "schema_version": _schema_version(target), "format": source.suffix.lstrip(".")})
        if artifacts:
            self.record("artifacts", {"artifacts": artifacts})
        return artifacts

    @contextmanager
    def operation(self, name: str, arguments: Any, *, kind: str = "tool"):
        self._assert_open()
        call_id = uuid4().hex
        parent = _call.get()
        started = time.perf_counter()
        parameter_path = self.root / "parameters" / f"{call_id}.json"
        atomic_json(parameter_path, arguments)
        self.record("call_start", {"call_id": call_id, "parent_call_id": parent, "kind": kind, "tool": name,
                                   "tool_version": "environment.json", "arguments_path": str(parameter_path),
                                   "arguments_sha256": file_hash(parameter_path)})
        token = _call.set(call_id)
        try:
            yield call_id
        except BaseException as exc:
            self.record("error", {"call_id": call_id, "type": type(exc).__name__, "message": str(exc),
                                  "traceback": traceback.format_exc()})
            self.record("call_end", {"call_id": call_id, "status": "cancelled" if isinstance(exc, KeyboardInterrupt)
                                      else "failed", "duration_ms": (time.perf_counter() - started) * 1000})
            raise
        else:
            self.record("call_end", {"call_id": call_id, "status": "succeeded",
                                      "duration_ms": (time.perf_counter() - started) * 1000})
        finally:
            _call.reset(token)

    def result(self, call_id: str, result: Any) -> None:
        self._assert_open()
        target = self.root / "intermediate" / f"{call_id}.json"
        atomic_json(target, result)
        evidence = {key: result[key] for key in ("used_tools", "used_features", "evidence_refs", "confidence_caveats", "scientific_preconditions",
                    "selected_operation", "status", "provenance") if isinstance(result, dict) and key in result}
        if isinstance(result, dict):
            preview = result.get("result_preview", result)
            report = preview.get("next_experiment_candidates", {}) if isinstance(preview, dict) else {}
            candidates = report.get("candidates", []) if isinstance(report, dict) else []
            evidence["candidate_versions"] = [{key: candidate.get(key) for key in
                ("id", "evaluation_id", "evaluation_version", "evaluation_fingerprint")}
                for candidate in candidates if candidate.get("evaluation_id")]
        self.record("result", {"call_id": call_id, "result_path": str(target), "sha256": file_hash(target), **evidence})

    def state_snapshot(self, state: dict[str, Any]) -> dict[str, Any]:
        self._assert_open()
        fields = {}
        for name, value in state.items():
            encoded = json.dumps(redact(value), sort_keys=True, ensure_ascii=False, allow_nan=False)
            checksum = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            path = self.root / "state_objects" / f"{checksum}.json"
            if not path.exists():
                self._atomic_text(path, encoded)
            fields[name] = {"path": str(path), "sha256": checksum}
        return {"schema_version": "graph-state-snapshot-v1", "field_refs": fields}

    def finish(self, result: Any, *, status: str = "succeeded") -> None:
        if status not in TERMINAL:
            raise ValueError("Invalid terminal run status.")
        with sqlite3.connect(self.database) as con:
            inputs = [json.loads(row[0]) for row in con.execute("SELECT payload FROM events WHERE kind='inputs'")]
            errors = con.execute("SELECT 1 FROM events WHERE kind='error' LIMIT 1").fetchone()
            results = [json.loads(row[0]) for row in con.execute("SELECT payload FROM events WHERE kind='result' ORDER BY seq")]
        changed = set()
        for entry in inputs:
            if entry.get("metadata", {}).get("mode") == "cached_source_snapshot":
                continue
            for identity in entry.get("inputs", {}).values():
                if identity.get("kind") != "file" or Path(identity["path"]).is_relative_to(self.root):
                    continue
                path = Path(identity["path"])
                current = path.stat() if path.exists() else None
                if current is None or (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                        identity["size_bytes"], identity["mtime_ns"], identity["ctime_ns"]):
                    changed.add(identity["path"])
        if changed:
            self.record("error", {"type": "InputVersionChanged", "paths": sorted(changed),
                                  "message": "Consumed inputs changed during execution; results require review."})
        if status == "succeeded" and (changed or errors):
            status = "partial"
        if isinstance(result, dict):
            result = {**result, "archive_provenance": {"run_id": self.context.run_id, "trace_id": self.context.trace_id,
                      "run_status": status, "evidence_index": "evidence.json", "input_manifest": "input_manifest.json",
                      "result_refs": [{key: row[key] for key in ("call_id", "result_path", "sha256")}
                                      for row in results],
                      "used_tools": sorted({tool for row in results for tool in row.get("used_tools", [])}),
                      "used_features": sorted({feature for row in results for feature in row.get("used_features", [])}),
                      "evidence_refs": [ref for row in results for ref in row.get("evidence_refs", [])],
                      "confidence_caveats": [caveat for row in results for caveat in row.get("confidence_caveats", [])],
                      "candidate_versions": [candidate for row in results for candidate in row.get("candidate_versions", [])],
                      "claim_mapping": "run-level evidence; natural-language sentence alignment is not automatically verified"}}
        self.record("finish", {"status": status, "result": result})
        files = {str(path.relative_to(self.root)): file_hash(path) for path in sorted(self.root.rglob("*"))
                 if path.is_file() and path.name not in {"bundle_manifest.json", "run_journal.sqlite",
                                                        "run_journal.sqlite-journal", "run_journal.sqlite-wal",
                                                        "run_journal.sqlite-shm"}}
        atomic_json(self.root / "bundle_manifest.json", {"schema_version": "run-bundle-v1", "files": files})

    def _export(self, rows: list[tuple[str, dict[str, Any]]], changed: str) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for kind, value in rows:
            grouped.setdefault(kind, []).append(value)
        begin = grouped.get("begin", [{}])[0]
        finish = grouped.get("finish", [{}])[-1]
        tasks = grouped.get("manifest_task", [])
        artifacts = [artifact for item in grouped.get("artifacts", []) for artifact in item["artifacts"]]
        manifest = {"schema_version": "run-manifest-v2", "created_at_utc": begin.get("timestamp_utc"),
                    "run_id": self.context.run_id, "trace_id": self.context.trace_id,
                    "user_id": self.context.user_id, "project_id": self.context.project_id,
                    "parent_run_id": begin.get("parent_run_id", ""), "thread_id": begin.get("thread_id", ""),
                    "status": finish.get("status", "running"), "finished_at_utc": finish.get("timestamp_utc"),
                    "output_version": "run-archive-v1", "tasks": tasks, "artifacts": artifacts,
                    "output_dirs": {key: val for task in tasks for key, val in task.get("output_dirs", {}).items()},
                    "inputs": {key: val for task in tasks for key, val in task.get("inputs", {}).items()}}
        if begin.get("timestamp_utc") and finish.get("timestamp_utc"):
            manifest["duration_seconds"] = (datetime.fromisoformat(finish["timestamp_utc"]) -
                                            datetime.fromisoformat(begin["timestamp_utc"])).total_seconds()
        if changed in {"begin", "manifest_task", "artifacts", "finish"}:
            atomic_json(self.root / "run_manifest.json", manifest)
        if changed in {"begin", "effective_config"}:
            atomic_json(self.root / "config.json", {"initial": begin.get("config", {}),
                       "effective_calls": grouped.get("effective_config", [])})
        if changed == "begin":
            atomic_json(self.root / "environment.json", begin.get("environment", {}))
        if changed in {"begin", "inputs", "cache_source"}:
            atomic_json(self.root / "input_manifest.json", {"schema_version": "input-manifest-v1",
                       "entries": grouped.get("inputs", []), "cache_sources": grouped.get("cache_source", [])})
        if changed in {"begin", "model"}:
            atomic_json(self.root / "model_manifest.json", {"calls": grouped.get("model", []),
                       "cost_policy": "unknown_unless_provider_reports_usage_and_cost"})
        results = grouped.get("result", [])
        if changed in {"begin", "artifacts", "result"}:
            atomic_json(self.root / "evidence.json", {"schema_version": "run-evidence-v1", "artifacts": artifacts,
                       "results": results, "claim_policy": "Evidence links do not imply causal validity."})
        if changed in {"begin", "error", "artifacts", "finish"}:
            atomic_json(self.root / "audit_report.json", {"status": finish.get("status", "running"),
                       "errors": grouped.get("error", []), "artifacts": artifacts,
                       "checks": {"config_saved": bool(begin), "final_result_saved": bool(finish),
                                  "evidence_index_saved": True, "source_snapshot_recorded": bool(
                                      begin.get("environment", {}).get("source_sha256"))}})
        for filename, entries in (("tool_calls.jsonl", [value for kind, value in rows if kind in
                                    {"call_start", "call_end", "tool_summary"}]),
                                  ("prompts.jsonl", grouped.get("model", [])), ("errors.jsonl", grouped.get("error", []))):
            text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)
            relevant = {"tool_calls.jsonl": {"begin", "call_start", "call_end", "tool_summary"},
                        "prompts.jsonl": {"begin", "model"}, "errors.jsonl": {"begin", "error"}}
            if changed not in relevant[filename]:
                continue
            self._atomic_text(self.root / filename, text)
            if filename == "tool_calls.jsonl":
                self._atomic_text(self.root / "_logs" / filename, text)
        if finish:
            atomic_json(self.root / "final_response.json", finish["result"])
            content = finish["result"].get("content", "") if isinstance(finish["result"], dict) else ""
            header = f"# Run {self.context.run_id}\n\nStatus: {finish['status']}\n\n"
            body = str(content) if content else "```json\n" + json.dumps(finish["result"], indent=2, ensure_ascii=False) + "\n```\n"
            self._atomic_text(self.root / "final_report.md", header + body + "\n\nEvidence: [evidence.json](evidence.json)\n")

    @staticmethod
    def _atomic_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _schema_version(path: Path) -> str:
    if path.suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get("schema_version", "unspecified")) if isinstance(data, dict) else "unspecified"
        except (ValueError, OSError):
            return "unreadable"
    return "unspecified"


def current_archive() -> RunArchive | None:
    return _current.get()


def audited_operation(function):
    @wraps(function)
    def run(*args, **kwargs):
        archive = current_archive()
        if archive is None:
            return function(*args, **kwargs)
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        instance = arguments.pop("self", None)
        if instance:
            settings = {key: getattr(instance, key) for key in ("quality_filters", "h5ad_config", "gene_programs",
                        "objective", "max_candidate_features", "reference_atlas_path") if hasattr(instance, key)}
            if function.__name__ == "run":
                archive.record("effective_config", {"agent": type(instance).__name__, "settings": settings,
                                                     "arguments": arguments})
                archive.inputs({**_input_paths(arguments), **_input_paths(settings)})
        graph_stage = function.__name__.startswith("_node_") and isinstance(arguments.get("state"), dict)
        recorded_arguments = {**arguments, "state": archive.state_snapshot(arguments["state"])} if graph_stage else arguments
        with archive.operation(f"{function.__module__}.{function.__qualname__}", recorded_arguments) as call_id:
            result = function(*args, **kwargs)
            archive.result(call_id, archive.state_snapshot(result) if graph_stage and isinstance(result, dict) else result)
            if isinstance(result, dict) and result and all(isinstance(value, Path) for value in result.values()):
                archive.snapshot_outputs(result, call_id)
            elif isinstance(result, dict) and result.get("plot_type") and result.get("path"):
                archive.snapshot_outputs({str(result["plot_type"]): result["path"]}, call_id)
            return result
    run.__annotations__ = get_type_hints(function, include_extras=True)
    return run


def audited_agent_run(function):
    traced = audited_operation(function)

    @wraps(function)
    def run(self, *args, **kwargs):
        if current_archive():
            return traced(self, *args, **kwargs)
        from protocol_optimization_agent.runtime.access_control import authenticated_mode
        from protocol_optimization_agent.runtime.context import RunContext

        context = RunContext.from_values(user_id=os.getenv("PROTOCOL_USER_ID"),
                    workspace_root=None if authenticated_mode() else self.output_dir,
                    run_id=f"run_{uuid4().hex[:16]}")
        archive = RunArchive(context)
        original = self.output_dir
        arguments = dict(inspect.signature(function).bind(self, *args, **kwargs).arguments)
        arguments.pop("self", None)
        archive.start({"entrypoint": type(self).__name__, "arguments": arguments})
        self.output_dir = archive.root / type(self).__name__
        try:
            with archive.scope():
                paths = _input_paths(arguments)
                paths.update(_input_paths({key: getattr(self, key) for key in
                             ("quality_filters", "h5ad_config", "reference_atlas_path") if hasattr(self, key)}))
                archive.inputs(paths)
                outputs = traced(self, *args, **kwargs)
            status = "succeeded"
            for key in ("data_analysis_validation", "matrix_release_gate_report", "optimization_input_gate_report"):
                if key in outputs:
                    gate = json.loads(outputs[key].read_text(encoding="utf-8"))
                    if gate.get("validation_status") == "failed" or gate.get("release_ready") is False or gate.get("ready") is False:
                        status = "partial"
            archive.finish({"outputs": outputs, "run_id": context.run_id}, status=status)
            return outputs
        except BaseException as exc:
            archive.record("error", {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
            archive.finish({"error": str(exc)}, status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed")
            raise
        finally:
            self.output_dir = original
    return run


def _input_paths(value: Any, prefix: str = "") -> dict[str, str]:
    result = {}
    if isinstance(value, dict):
        for key, item in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if item and (str(key).endswith(("_path", "_dir")) or isinstance(item, Path)):
                result[name] = str(item)
            else:
                result.update(_input_paths(item, name))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(_input_paths(item, f"{prefix}.{index}"))
    return result


def audited_tool(function):
    @wraps(function)
    def run(*args, **kwargs):
        from protocol_optimization_agent.runtime.context import RunContext, authorize_output_path

        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = bound.arguments
        owner = current_archive() is None
        context = RunContext.from_values(**{key: arguments.get(key) for key in
                                         ("user_id", "run_id", "trace_id", "workspace_root", "project_id")})
        archive = current_archive() or RunArchive(context)
        parent_run_id = ""
        if owner:
            with sqlite3.connect(archive.database) as con:
                exists = con.execute("SELECT 1 FROM events WHERE kind='begin' LIMIT 1").fetchone()
            if exists:
                parent_run_id = context.run_id
                context = RunContext.from_values(user_id=context.user_id, project_id=context.project_id,
                            workspace_root=context.workspace_root, run_id=f"run_{uuid4().hex[:16]}")
                archive = RunArchive(context)
            for key in ("user_id", "run_id", "trace_id", "workspace_root", "project_id"):
                arguments[key] = getattr(context, key)
            archive.start({"entrypoint": function.__name__, "arguments": arguments}, parent_run_id=parent_run_id)
        try:
            with archive.scope(), archive.operation(function.__name__, arguments) as call_id:
                if owner:
                    for key, agent in (("protocol_output_dir", "protocol_analysis"),
                                       ("data_analysis_output_dir", "data_analysis"),
                                       ("optimization_output_dir", "protocol_optimization")):
                        if arguments.get(key):
                            source = authorize_output_path(Path(arguments[key]), context)
                            arguments[key] = str(archive.seed(source, agent))
                archive.record("effective_config", {"call_id": call_id, "tool": function.__name__, "arguments": arguments})
                result = function(*bound.args, **bound.kwargs)
                archive.result(call_id, result)
                if isinstance(result, dict):
                    archive.snapshot_outputs(result.get("output_files", {}), call_id)
            if owner:
                archive.finish(result, status="partial" if _partial(result) else "succeeded")
            return result
        except BaseException as exc:
            if owner:
                archive.finish({"error": type(exc).__name__, "message": str(exc)},
                               status="cancelled" if isinstance(exc, KeyboardInterrupt) else "failed")
            raise
    run.__annotations__ = get_type_hints(function, include_extras=True)
    return run


def _partial(result: Any) -> bool:
    return isinstance(result, dict) and str(result.get("status", "ok")) not in {
        "ok", "ok_cached_optimization_outputs", "optimization_ran", "found", "ready"}


def record_model_call(model: Any, messages: Any, response: Any, duration_ms: float, error: str = "") -> None:
    archive = current_archive()
    if archive is None:
        return
    bound_kwargs = getattr(model, "kwargs", {})
    model = getattr(model, "bound", model)
    parameters = {key: getattr(model, key, None) for key in ("model_name", "model", "temperature", "top_p", "seed", "max_tokens",
                                                           "base_url", "openai_api_base", "timeout_seconds")}
    prompt = redact(messages)
    archive.record("model", {"call_id": _call.get(), "model": type(model).__name__, "parameters": parameters,
                   "bound_kwargs": bound_kwargs,
                   "prompt_sha256": hashlib.sha256(json.dumps(prompt, sort_keys=True).encode()).hexdigest(),
                   "messages": prompt, "response": response, "duration_ms": duration_ms, "error": error,
                   "usage": response.get("usage") if isinstance(response, dict) else getattr(response, "usage_metadata", None),
                   "provider_model": response.get("model") if isinstance(response, dict) else
                       getattr(response, "response_metadata", {}).get("model_name"),
                   "cost": None})


def graph_error_handler():
    """Seal framework-level errors without wrapping the compiled graph type."""
    from langchain_core.callbacks import BaseCallbackHandler

    class ArchiveErrors(BaseCallbackHandler):
        def __init__(self):
            self.roots = {}
            self.contexts = {}
            self.lock = RLock()

        def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
            with self.lock:
                self.roots[run_id] = self.roots.get(parent_run_id, run_id)

        def on_chain_end(self, outputs, *, run_id, **kwargs):
            with self.lock:
                root = self.roots.get(run_id, run_id)
                if isinstance(outputs, dict) and all(key in outputs for key in ("run_id", "user_id", "workspace_root")):
                    self.contexts[root] = {key: outputs.get(key) for key in
                                           ("run_id", "user_id", "workspace_root", "project_id", "trace_id")}
                if root == run_id:
                    self._clear(root)

        def on_chain_error(self, error, *, run_id, **kwargs):
            with self.lock:
                root = self.roots.get(run_id, run_id)
                if root != run_id:
                    return
                context = self.contexts.get(root)
                self._clear(root)
            if context:
                from protocol_optimization_agent.runtime.context import RunContext

                archive = RunArchive(RunContext.from_values(**context))
                with sqlite3.connect(archive.database) as con:
                    closed = con.execute("SELECT 1 FROM events WHERE kind='finish' LIMIT 1").fetchone()
                if not closed:
                    archive.record("error", {"type": type(error).__name__, "message": str(error)})
                    archive.finish({"error": str(error)}, status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed")

        def _clear(self, root):
            self.contexts.pop(root, None)
            self.roots = {key: value for key, value in self.roots.items() if value != root}

    return ArchiveErrors()
