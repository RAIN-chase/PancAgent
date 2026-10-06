from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from protocol_optimization_agent.runtime.io import read_jsonl

PROTOCOL_INPUT_FILENAME = "protocols.jsonl"


def load_protocol_analysis_inputs(input_dir: Path) -> dict[str, Any]:
    """Project the formal protocol export into PA records and deterministic events."""
    source = input_dir / PROTOCOL_INPUT_FILENAME
    if not source.is_file():
        raise FileNotFoundError(
            f"Required formal protocol input is missing: {source}. No CSV/SQLite fallback is allowed."
        )
    records = read_jsonl(source)
    if not records:
        raise ValueError(f"Formal protocol input is empty: {source}")
    protocols, events, identities = [], [], []
    seen = set()
    for index, record in enumerate(records):
        location = f"{source}: record {index + 1}"
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("protocol_id"), str)
            or not record["protocol_id"].strip()
        ):
            raise ValueError(f"{location}: a non-empty protocol_id is required")
        pid = record["protocol_id"]
        if pid in seen:
            raise ValueError(f"{location}: duplicate protocol_id {pid}")
        seen.add(pid)
        if "comparison_ready" in record and not isinstance(record["comparison_ready"], bool):
            raise ValueError(f"{location}: comparison_ready must be a boolean")
        paper = record.get("paper") or {}
        if not isinstance(paper, dict):
            raise ValueError(f"{location}: paper must be an object")
        metadata = {key: value for key, value in record.items() if key not in {"stages", "preparation", "protocol"}}
        stages = [
            {**stage, "stage_id": stage.get("stage_id") or f"{pid}:stages:{i}"}
            for i, stage in enumerate(_object_list(record, "stages", location))
        ]
        preparations = _object_list(record, "preparation", location)
        protocols.append(
            {
                **record,
                "protocol": metadata,
                "stages": stages,
                "confidence": record.get("confidence")
                if record.get("confidence") is not None
                else {"extraction": record.get("confidence_level")},
                "comparison_ready": record.get("comparison_ready", False),
                "source_doc_id": record.get("source_doc_id") or record.get("source_file") or "",
            }
        )
        identities.append(
            {
                "canonical_paper_id": record.get("paper_id") or "",
                "canonical_source_id": record.get("source_doc_id") or record.get("source_file") or "",
                "canonical_filename": record.get("source_file") or "",
                "source_identity_status": "from_protocols_jsonl",
                "dataset_id": paper.get("dataset_id") or record.get("dataset_id") or "",
            }
        )
        for collection, items in (("preparation", preparations), ("stages", stages)):
            for stage_index, stage in enumerate(items):
                stage_path = f"/{collection}/{stage_index}"
                stage_id = stage.get("stage_id") or f"{pid}:{collection}:{stage_index}"
                if not isinstance(stage_id, str) or not stage_id.strip():
                    raise ValueError(f"{location} {stage_path}: stage_id must be a non-empty string")
                _check_owner(stage, pid, location, stage_path)
                common = {
                    "protocol_id": pid,
                    "paper_id": record.get("paper_id") or "",
                    "dataset_id": paper.get("dataset_id") or record.get("dataset_id") or "",
                    "stage_id": stage_id,
                    "stage_index": stage.get("stage_index"),
                    "stage_name": stage.get("stage_name"),
                    "stage_role": collection,
                    "timing_convention": stage.get("timing_convention"),
                    "source_file": str(source),
                    "source_record_index": index,
                    "cell_state_in": stage.get("input_cell_state"),
                    "cell_state_out": stage.get("output_cell_state"),
                }
                for field, factor_type in (
                    ("medium_base", "medium_base"),
                    ("coating_or_matrix", "matrix_or_coating"),
                    ("culture_format", "culture_format"),
                    ("key_operations", "operation"),
                    ("operation", "operation"),
                ):
                    if stage.get(field):
                        events.append(_event(common, stage, factor_type, str(stage[field]), stage_path, field))
                for reagent_index, reagent in enumerate(_object_list(stage, "reagents", f"{location} {stage_path}")):
                    reagent_path = f"{stage_path}/reagents/{reagent_index}"
                    _check_owner(reagent, pid, location, reagent_path)
                    if reagent.get("stage_id") not in (None, "", stage_id):
                        raise ValueError(f"{location} {reagent_path}: reagent stage_id does not match its stage")
                    name = reagent.get("original_name") or reagent.get("standardized_name")
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError(f"{location} {reagent_path}: reagent name is required")
                    event = _event(common, reagent, "reagent", name, reagent_path, "concentration")
                    raw = reagent.get("concentration_value", reagent.get("concentration"))
                    number = _number(raw)
                    event.update(
                        concentration_value=number,
                        concentration_unit=reagent.get("concentration_unit") or reagent.get("unit"),
                        concentration_parse_status="single_value"
                        if number is not None
                        else "missing"
                        if raw in (None, "")
                        else "non_scalar",
                    )
                    events.append(event)
    return {
        "protocol_records": protocols,
        "factor_events": events,
        "source_identity_rows": identities,
        "protocol_record_source": PROTOCOL_INPUT_FILENAME,
    }


def _object_list(parent: dict[str, Any], key: str, location: str) -> list[dict[str, Any]]:
    items = parent.get(key, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError(f"{location}: {key} must be a list of objects")
    return items


def _check_owner(item: dict[str, Any], pid: str, location: str, path: str) -> None:
    if item.get("protocol_id") not in (None, "", pid):
        raise ValueError(f"{location} {path}: protocol_id does not match its parent")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _event(
    common: dict[str, Any], item: dict[str, Any], factor_type: str, name: str, path: str, field: str
) -> dict[str, Any]:
    # Only explicit structured fields are projected; quotes never supply missing quantities.
    status = item.get("validation_status") or (
        "has_flags"
        if item.get("validation_flags")
        else "evidence_supported"
        if item.get("evidence_quote")
        else "missing_evidence"
    )
    details = {key: value for key, value in item.items() if key != "reagents"}
    return {
        **details,
        **common,
        "event_id": f"{common['protocol_id']}:{path}:{factor_type}:{field}",
        "factor_type": factor_type,
        "factor_name": name,
        "standardized_name": item.get("standardized_name") or name,
        "confidence": item.get("confidence_level"),
        "day_start": item.get("day_start"),
        "day_end": item.get("day_end"),
        "duration_days": item.get("duration_days"),
        "evidence_quote": item.get("evidence_quote") or "",
        "validation_status": status,
        "source_record_path": path,
        "source_field": field,
        "input_source": PROTOCOL_INPUT_FILENAME,
    }
