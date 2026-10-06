from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from protocol_optimization_agent.runtime.archive import current_archive


def _record_read(path: Path) -> None:
    archive = current_archive()
    if archive:
        archive.inputs({"read": str(path)}, {"operation": "structured_file_read"})


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    _record_read(path)
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    _record_read(path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_csv_dicts(path: Path) -> list[dict[str, str]]:
    _record_read(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv_dicts(path: Path, records: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    ensure_dir(path.parent)
    if fieldnames is None:
        keys: list[str] = []
        for record in records:
            for key in record:
                if key not in keys:
                    keys.append(key)
        fieldnames = keys
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)
