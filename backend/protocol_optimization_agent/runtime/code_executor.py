"""Fail-closed rootless Docker execution. No host Python execution fallback."""
from __future__ import annotations

import ast
import io
import json
import os
import re
import subprocess
import tarfile
import time
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from protocol_optimization_agent.runtime.archive import _credential_key, atomic_json, file_hash

_cleanup_failed = False


class CodeBudget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    memory_mb: int = Field(default=256, ge=128, le=1024)
    cpus: float = Field(default=1.0, ge=0.1, le=2.0)


class IsolationUnavailable(RuntimeError):
    pass


def scan_code(code: str) -> dict:
    if not isinstance(code, str) or len(code.encode()) > 64000:
        raise ValueError("Candidate script must be at most 64 KB")
    tree = ast.parse(code)
    imports = sorted({node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} |
                     {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names})
    if any(name.split(".")[0] not in {"json", "math", "statistics", "random", "collections"} for name in imports):
        raise ValueError("Candidate imports exceed the task's allowed standard-library methods")
    return {"syntax_valid": True, "imports": imports, "security_boundary": "container_not_AST"}


def _docker(*args: str, timeout: float = 15, silent: bool = False) -> subprocess.CompletedProcess:
    # Docker connection configuration remains operator-owned; no environment is passed into the container.
    return subprocess.run(["docker", *args], stdout=subprocess.DEVNULL if silent else subprocess.PIPE,
                          stderr=subprocess.DEVNULL if silent else subprocess.PIPE, timeout=timeout, check=True)


def isolation_environment() -> dict:
    if _cleanup_failed:
        raise IsolationUnavailable("Executor is quarantined after unconfirmed cleanup; inspect containers and restart the backend")
    if os.getenv("PROTOCOL_CODE_EXECUTION", "disabled") != "rootless-docker":
        raise IsolationUnavailable("Code execution is disabled; configure rootless Docker on the Linux server")
    image = os.getenv("PROTOCOL_CODE_IMAGE", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise IsolationUnavailable("An operator-pinned local image ID (sha256:...) is required")
    try:
        info = json.loads(_docker("info", "--format", "{{json .}}").stdout)
        if (info.get("OSType") != "linux" or info.get("CgroupVersion") != "2"
                or not any("rootless" in item for item in info.get("SecurityOptions", []))
                or not any("name=seccomp" in item for item in info.get("SecurityOptions", []))
                or not all(info.get(key) for key in ("MemoryLimit", "CpuCfsQuota", "PidsLimit"))):
            raise IsolationUnavailable("Linux rootless Docker with delegated cgroup v2 memory/CPU/PID limits is required")
        details = json.loads(_docker("image", "inspect", image).stdout)[0]
        if details["Id"] != image or details.get("Os") != "linux":
            raise IsolationUnavailable("Pinned image is missing or is not a Linux image")
        if any(_credential_key(value.partition("=")[0]) for value in details.get("Config", {}).get("Env", [])):
            raise IsolationUnavailable("Credential-bearing image environment is forbidden")
        return {"image_id": image, "docker_version": info["ServerVersion"],
                "security_options": info["SecurityOptions"], "cgroup_version": info["CgroupVersion"],
                "image_created": details.get("Created"), "image_repo_digests": details.get("RepoDigests", [])}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise IsolationUnavailable("Docker daemon or pinned image is unavailable; no host fallback") from exc


def container_command(task: Path, name: str, environment: dict, budget: CodeBudget) -> list[str]:
    source = str(task.resolve())
    if any(char in source for char in (",", "\n", "\r")):
        raise ValueError("Unsupported mount path")
    return ["run", "--detach", "--name", name, "--pull=never", "--network=none", "--read-only",
            "--user=65534:65534", "--cap-drop=ALL", "--security-opt=no-new-privileges=true", "--ipc=private",
            "--pids-limit=32", f"--cpus={budget.cpus}", f"--memory={budget.memory_mb}m",
            f"--memory-swap={budget.memory_mb}m", "--ulimit=nofile=128:128", "--ulimit=nproc=32:32",
            "--log-driver=local", "--log-opt=max-size=1m", "--log-opt=max-file=1",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "--tmpfs=/out:rw,noexec,nosuid,nodev,size=8m,mode=1777",
            "--mount", f"type=bind,source={source},target=/task,readonly",
            "--env=PYTHONHASHSEED=0", "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1",
            "--entrypoint=/usr/local/bin/python", environment["image_id"], "-I", "-B", "-c", "import time; time.sleep(300)"]


def decode_file(archive: bytes, name: str = "result.json") -> bytes:
    if len(archive) > 9 * 1024 * 1024:
        raise ValueError("Container output exceeds transfer budget")
    # Never extract untrusted paths, symlinks, devices or hard links onto the host.
    with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
        members = handle.getmembers()
        if len(members) != 1 or not members[0].isfile() or members[0].name != name or members[0].size > 1024 * 1024:
            raise ValueError("Expected one regular result.json of at most 1 MB")
        stream = handle.extractfile(members[0])
        if stream is None:
            raise ValueError("Output is absent")
        return stream.read()


def decode_output(archive: bytes) -> dict:
    try:
        value = json.loads(decode_file(archive), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Nonfinite output")))
    except tarfile.TarError as exc:
        raise ValueError("Invalid container output archive") from exc
    if not isinstance(value, dict):
        raise ValueError("Result must be a JSON object")
    return value


def execute_code(task: Path, budget: CodeBudget, environment: dict) -> dict:
    """Only server-created task directories are mounted; output stays on bounded container tmpfs."""
    global _cleanup_failed
    if _cleanup_failed:
        raise IsolationUnavailable("Executor is quarantined after unconfirmed cleanup")
    scan = scan_code((task / "candidate.py").read_text(encoding="utf-8"))
    atomic_json(task / "scan.json", scan)
    name = "pancagent-code-" + uuid4().hex
    started = time.monotonic()
    record = {"container_name": name, "environment": environment, "budget": budget.model_dump(),
              "script_sha256": file_hash(task / "candidate.py"), "input_sha256": file_hash(task / "input.json"),
              "status": "starting"}
    def call(*args, silent=False):
        remaining = budget.timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired("docker", budget.timeout_seconds)
        return _docker(*args, timeout=remaining if args[0] == "exec" else min(15, remaining), silent=silent)
    try:
        call(*container_command(task, name, environment, budget))
        # Keep the container alive until output has been collected: tmpfs disappears on container stop.
        try:
            call("exec", name, "/usr/local/bin/python", "-I", "-B", "/task/runner.py", silent=True)
            record["exit_code"] = 0
        except subprocess.CalledProcessError as exc:
            record["exit_code"] = exc.returncode
        record["container_state"] = json.loads(call("inspect", "--format", "{{json .State}}", name).stdout)
        for log in ("stdout.log", "stderr.log"):
            try:
                (task / log).write_bytes(decode_file(call("cp", name + ":/out/" + log, "-").stdout, log)[-64000:])
            except (subprocess.CalledProcessError, ValueError):
                pass
        if record["exit_code"] != 0:
            raise ValueError("Candidate exited unsuccessfully; inspect archived container diagnostics")
        result = decode_output(call("cp", name + ":/out/result.json", "-").stdout)
        record["status"] = "executed"
        return result
    except subprocess.TimeoutExpired as exc:
        record["status"] = "timeout"
        raise TimeoutError("Isolated script exceeded wall-time budget") from exc
    except BaseException:
        record["status"] = "failed" if record["status"] != "timeout" else "timeout"
        raise
    finally:
        try:
            _docker("rm", "--force", name)
            record["container_removed"] = True
        except (OSError, subprocess.SubprocessError):
            record["container_removed"] = False
            _cleanup_failed = True
        record["elapsed_seconds"] = time.monotonic() - started
        atomic_json(task / "execution.json", record)
        if not record["container_removed"]:
            raise IsolationUnavailable("Container cleanup could not be confirmed; operator intervention required")


RUNNER = '''import resource
import subprocess
resource.setrlimit(resource.RLIMIT_FSIZE, (1048576, 1048576))
with open('/out/stdout.log', 'wb') as stdout, open('/out/stderr.log', 'wb') as stderr:
    process = subprocess.run(['/usr/local/bin/python', '-I', '-B', '/task/candidate.py'], stdout=stdout, stderr=stderr)
raise SystemExit(process.returncode if process.returncode >= 0 else 128 - process.returncode)
'''
