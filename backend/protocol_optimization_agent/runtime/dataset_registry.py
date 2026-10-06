from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4


def register_dataset(source: Path, storage: Path, registry: Path) -> dict:
    """Copy once into content-addressed, read-only storage owned by the server operator."""
    from protocol_optimization_agent.runtime.archive import atomic_json, file_hash

    source = source.resolve()
    storage = storage.resolve()
    storage.mkdir(parents=True, exist_ok=True)
    staging = storage / f".ingest_{uuid4().hex}"
    digest = hashlib.sha256()
    before = source.stat()
    try:
        with source.open("rb") as reader, staging.open("xb") as writer:
            for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
                writer.write(chunk)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("Dataset changed during registration.")
        checksum = digest.hexdigest()
        registry.mkdir(parents=True, exist_ok=True)
        # Only publication is serialized; the expensive ingestion streams can run in parallel.
        with sqlite3.connect(registry / "registrations.sqlite", timeout=60) as con:
            con.execute("BEGIN IMMEDIATE")
            target = storage / checksum / ("data" + source.suffix)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if file_hash(target) != checksum:
                    raise ValueError("Existing dataset version was modified.")
            else:
                os.link(staging, target)
            staging.unlink(missing_ok=True)
            if target.stat().st_mode & stat.S_IWUSR:
                target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            registered = target.stat()
            entry = {"path": str(target), "sha256": checksum, "version": checksum, "immutable": True,
                     "size_bytes": registered.st_size, "mtime_ns": registered.st_mtime_ns,
                     "ctime_ns": registered.st_ctime_ns, "artifact_id": f"sha256:{checksum}"}
            atomic_json(registry / f"{checksum}.json", entry)
            return entry
    finally:
        staging.unlink(missing_ok=True)


def registered_fingerprint(path: Path) -> dict | None:
    location = os.getenv("PROTOCOL_DATASET_REGISTRY")
    if not location:
        return None
    for receipt in Path(location).glob("*.json"):
        entry = json.loads(receipt.read_text(encoding="utf-8"))
        if Path(entry["path"]).resolve() != path.resolve():
            continue
        current = path.stat()
        if not entry.get("immutable") or current.st_mode & stat.S_IWUSR:
            raise ValueError("Registered dataset is no longer read-only.")
        if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != (
                entry["size_bytes"], entry["mtime_ns"], entry["ctime_ns"]):
            raise ValueError("Registered dataset changed; register a new version.")
        return {**entry, "exists": True, "kind": "file", "sha256_status": "registered_readonly_version",
                "verification_policy": "server-owned read-only storage and receipt; full hash at ingestion"}
    return None
