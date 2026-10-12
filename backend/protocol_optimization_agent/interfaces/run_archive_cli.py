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
    publish = commands.add_parser("publish-catalog")
    publish.add_argument("--catalog", type=Path, required=True)
    publish.add_argument("--dataset-id", required=True)
    args = parser.parse_args()
    if args.command == "register-input":
        print(json.dumps(register_dataset(args.source, args.storage, args.registry), indent=2))
        return
    if args.command == "publish-catalog":
        from protocol_optimization_agent.runtime.archive import atomic_json
        import h5py

        catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
        entries = [row for row in catalog["datasets"] if row["dataset_id"] == args.dataset_id]
        if len(entries) != 1:
            raise ValueError("Publication requires one unambiguous dataset ID")
        entry = entries[0]
        path = Path(entry["h5ad_path"])
        before = path.stat()
        checksum = file_hash(path)
        with h5py.File(path, "r") as handle:
            if handle.attrs.get("encoding-type") != "anndata" or "X" not in handle:
                raise ValueError("Not a supported AnnData h5ad")
            for axis, field in (("obs", "n_obs"), ("var", "n_vars")):
                entry[field] = handle[axis][handle[axis].attrs.get("_index", "_index")].shape[0]
        after = path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("File changed while publishing; finish transfer first")
        entry.update(status="ready", version=f"sha256:{checksum}", size_bytes=after.st_size, mtime_ns=after.st_mtime_ns)
        catalog["version"] = f"sha256:{file_hash(args.catalog)}+{checksum}"
        atomic_json(args.catalog, catalog)
        print(json.dumps({key: entry[key] for key in ("dataset_id", "status", "version", "n_obs", "n_vars")}))
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
