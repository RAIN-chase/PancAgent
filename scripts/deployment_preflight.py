"""Read-only preflight for the loopback-only Linux trial."""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from protocol_optimization_agent.runtime.data_catalog import catalog_manifest, dataset_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--port", type=int, action="append", help="Ports to check before launch")
    args = parser.parse_args()
    load_dotenv(args.env, override=False, interpolate=False)
    checks = []

    def check(name, passed, detail=""):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    check("linux", platform.system() == "Linux", platform.system())
    check("python_3_11", sys.version_info[:2] == (3, 11), platform.python_version())
    for command, expected in (("node", "v22."), ("pnpm", "10.5.1")):
        executable = shutil.which(command)
        try:
            version = subprocess.check_output([executable, "--version"], text=True, timeout=10).strip() if executable else ""
        except (OSError, subprocess.SubprocessError):
            version = ""
        check(command, version.startswith(expected) if command == "node" else version == expected, version or "missing")
    check("langgraph_cli", shutil.which("langgraph"))
    check("loopback_local", os.getenv("HOST_ADDRESS", "127.0.0.1") == "127.0.0.1"
          and os.getenv("PROTOCOL_ACCESS_MODE", "local") == "local"
          and os.getenv("PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR", "0") == "0")
    check("code_disabled", os.getenv("PROTOCOL_CODE_EXECUTION", "disabled") == "disabled")
    workspace = Path(os.getenv("PROTOCOL_WORKSPACE_ROOT") or "runs")
    check("workspace", workspace.is_absolute() and workspace.is_dir() and os.access(workspace, os.W_OK | os.X_OK),
          "Use an existing writable absolute path outside the checkout.")
    pa = Path(os.getenv("PROTOCOL_ANALYSIS_OUTPUT_DIR") or "")
    check("pa_demo_or_published_outputs", pa.is_absolute() and pa.resolve().is_relative_to(workspace.resolve())
          and os.access(pa / "protocol_feature_matrix.csv", os.R_OK))
    for port in args.port or [2024, 3000, 8000]:
        try:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", port))
            check(f"port_{port}", True)
        except (OSError, OverflowError):
            check(f"port_{port}", False, "Unavailable loopback port")
    try:
        project = os.getenv("PROTOCOL_RESEARCH_PROJECT_ID") or "default"
        catalog = dataset_catalog("local_user", project)
        ready = [row for row in catalog["datasets"] if row["analysis_available"]]
        check("catalog_readable", catalog["configured"])
        check("ready_non_reference_dataset", any(row["role"] != "reference_atlas" for row in ready))
        check("composition_demo_available", any(
            row["role"] != "reference_atlas" and row.get("cell_type_column") in row.get("obs_columns", [])
            for row in ready
        ), "Composition requires an explicitly mapped cell-type obs column.")
        for row in catalog["datasets"]:
            if row["status"] == "unavailable":
                check("manifest:" + row["dataset_id"], False, "Published entry is no longer readable or version-matched")
        for row in ready:
            catalog_manifest(row["dataset_id"], "local_user", project, row["version"])
            columns = set(row.get("obs_columns", []))
            mapped = [row[key] for key in ("cell_type_column", "sample_column", "protocol_column") if row.get(key)]
            check("manifest:" + row["dataset_id"], set(mapped).issubset(columns), "Published file/version and declared obs columns")
    except (ValueError, OSError, KeyError, TypeError):
        check("catalog_readable", False, "Catalog, version or manifest validation failed")
    key_name = os.getenv("PROTOCOL_CHAT_API_KEY_ENV") or ""
    check("model_configuration", bool(os.getenv("PROTOCOL_CHAT_MODEL") and key_name and os.getenv(key_name)),
          "Presence only; no provider call or credential output.")
    print(json.dumps(checks, indent=2))
    raise SystemExit(0 if all(row["passed"] for row in checks) else 1)


if __name__ == "__main__":
    main()
