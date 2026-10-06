from __future__ import annotations

import argparse
import json
from pathlib import Path

from protocol_optimization_agent.runtime.archive import file_hash
from protocol_optimization_agent.runtime.dataset_registry import register_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Register immutable input versions or verify archived run outputs.")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("register-input")
    ingest.add_argument("--source", type=Path, required=True)
    ingest.add_argument("--storage", type=Path, required=True)
    ingest.add_argument("--registry", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "register-input":
        print(json.dumps(register_dataset(args.source, args.storage, args.registry), indent=2))
        return
    manifest = json.loads((args.run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    invalid = [entry["path"] for entry in manifest.get("artifacts", [])
               if not Path(entry["path"]).is_file() or file_hash(Path(entry["path"])) != entry["sha256"]]
    bundle = args.run_dir / "bundle_manifest.json"
    if bundle.exists():
        for name, checksum in json.loads(bundle.read_text(encoding="utf-8"))["files"].items():
            path = (args.run_dir / name).resolve()
            if not path.is_relative_to(args.run_dir.resolve()) or not path.is_file() or file_hash(path) != checksum:
                invalid.append(name)
    else:
        invalid.append("bundle_manifest.json is missing: run is not sealed")
    print(json.dumps({"run_id": manifest["run_id"], "status": manifest["status"], "invalid_artifacts": invalid}, indent=2))
    if invalid:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
