from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

from protocol_optimization_agent.io import read_csv_dicts, read_jsonl


def load_protocol_analysis_inputs(input_dir: Path) -> dict[str, Any]:
    """Load Agent1 protocol outputs from either JSONL artifacts or SQLite exports."""
    protocol_jsonl = input_dir / "protocol_artifacts.jsonl"
    sqlite_path = input_dir / "protocol_database.sqlite"
    factor_events_csv = input_dir / "protocol_factor_events.csv"
    source_inventory_csv = input_dir / "source_identity_inventory.csv"

    if protocol_jsonl.exists():
        protocol_records = read_jsonl(protocol_jsonl)
        source_identity_rows = read_csv_dicts(source_inventory_csv)
        protocol_record_source = "protocol_artifacts.jsonl"
    elif sqlite_path.exists():
        protocol_records = load_protocol_records_from_sqlite(
            sqlite_path,
            evidence_coverage_rows=read_csv_dicts(input_dir / "protocol_evidence_coverage.csv"),
            domain_validation_rows=read_csv_dicts(input_dir / "protocol_domain_validation_flags.csv"),
        )
        source_identity_rows = load_source_identity_rows_from_sqlite(sqlite_path)
        protocol_record_source = "protocol_database.sqlite"
    else:
        protocol_records = []
        source_identity_rows = []
        protocol_record_source = "missing"

    return {
        "protocol_records": protocol_records,
        "factor_events": read_csv_dicts(factor_events_csv),
        "source_identity_rows": source_identity_rows,
        "protocol_database_path": sqlite_path if sqlite_path.exists() else None,
        "protocol_record_source": protocol_record_source,
    }


def load_protocol_records_from_sqlite(
    sqlite_path: Path,
    evidence_coverage_rows: list[dict[str, Any]] | None = None,
    domain_validation_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        protocols = [_row_to_dict(row) for row in con.execute("select * from protocols order by protocol_id")]
        stages_by_protocol = _rows_by_key(con, "stages", "protocol_id", "stage_index, stage_id")
        reagents_by_protocol = _rows_by_key(con, "reagents", "protocol_id", "stage_id, reagent_instance_id")
        evidence_by_protocol = _rows_by_key(con, "evidence_spans", "protocol_id", "evidence_span_id")
        field_evidence_by_protocol = _rows_by_key(con, "field_evidence", "protocol_id", "field_evidence_id")
        lineage_by_protocol = _rows_by_key(con, "lineage_candidate_edges", "protocol_id", "lineage_edge_id")
        validation_by_protocol = _load_validation_flags(con, domain_validation_rows or [])
    finally:
        con.close()

    coverage_by_protocol = {str(row.get("protocol_id") or ""): row for row in evidence_coverage_rows or []}
    records: list[dict[str, Any]] = []
    for protocol_row in protocols:
        protocol_id = str(protocol_row.get("protocol_id") or "")
        extra = _json_object(protocol_row.pop("extra_json", None))
        protocol = {**protocol_row, **extra}
        stages = [_merge_extra_json(row) for row in stages_by_protocol.get(protocol_id, [])]
        reagents = [_merge_extra_json(row) for row in reagents_by_protocol.get(protocol_id, [])]
        validation_flags = validation_by_protocol.get(protocol_id, [])
        coverage = coverage_by_protocol.get(protocol_id, {})
        comparison_ready, reasons = _infer_comparison_ready(protocol, stages, reagents, validation_flags, coverage)
        records.append(
            {
                "protocol_schema_version": "sqlite-reconstructed-protocol-artifact-v1",
                "protocol_id": protocol_id,
                "paper_id": protocol.get("paper_id"),
                "source_doc_id": protocol.get("source_file"),
                "comparison_ready": comparison_ready,
                "comparison_ready_source": "inferred_from_sqlite_and_qc_reports",
                "comparison_ready_reasons": reasons,
                "confidence": {"extraction": protocol.get("confidence_level")},
                "protocol": protocol,
                "stages": stages,
                "reagents": reagents,
                "evidence_spans": evidence_by_protocol.get(protocol_id, []),
                "field_evidence": field_evidence_by_protocol.get(protocol_id, []),
                "lineage_candidate_edges": lineage_by_protocol.get(protocol_id, []),
                "validation_flags": validation_flags,
                "evidence_coverage": coverage,
            }
        )
    return records


def load_source_identity_rows_from_sqlite(sqlite_path: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(sqlite_path)
    con.row_factory = sqlite3.Row
    try:
        rows = [_row_to_dict(row) for row in con.execute("select * from papers order by paper_id")]
    finally:
        con.close()

    source_rows = []
    for row in rows:
        extra = _json_object(row.get("extra_json"))
        source_rows.append(
            {
                "file_id": "",
                "path": row.get("source_file") or "",
                "name": row.get("source_file") or "",
                "canonical_source_id": row.get("source_doc_id") or row.get("paper_id") or "",
                "canonical_paper_id": row.get("paper_id") or "",
                "canonical_filename": row.get("source_file") or "",
                "source_role": "paper_table",
                "source_identity_status": "from_sqlite_papers_table",
                "guessed_paper_key": row.get("paper_id") or "",
                "guessed_author": extra.get("authors") or row.get("first_author") or "",
                "guessed_year": row.get("year") or "",
                "guessed_title": row.get("title") or "",
                "dataset_id": row.get("dataset_id") or "",
            }
        )
    return source_rows


def _rows_by_key(con: sqlite3.Connection, table: str, key: str, order_by: str) -> dict[str, list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute(f"select * from {table} order by {order_by}"):
        item = _row_to_dict(row)
        rows[str(item.get(key) or "")].append(item)
    return rows


def _load_validation_flags(
    con: sqlite3.Connection,
    domain_validation_rows: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in con.execute("select * from validation_flags order by flag_id"):
        item = _merge_extra_json(_row_to_dict(row))
        protocol_id = str(item.get("protocol_id") or item.get("paper_or_protocol_id") or "")
        if protocol_id:
            by_protocol[protocol_id].append(item)
    for row in domain_validation_rows:
        item = dict(row)
        protocol_id = str(item.get("paper_or_protocol_id") or item.get("protocol_id") or "")
        if protocol_id:
            by_protocol[protocol_id].append(item)
    return by_protocol


def _infer_comparison_ready(
    protocol: dict[str, Any],
    stages: list[dict[str, Any]],
    reagents: list[dict[str, Any]],
    validation_flags: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    stage_quote_coverage = _safe_float(coverage.get("stage_quote_coverage"), None)
    reagent_quote_coverage = _safe_float(coverage.get("reagent_quote_coverage"), None)
    high_severity_flags = [flag for flag in validation_flags if str(flag.get("severity") or "").lower() == "high"]

    if not protocol.get("evidence_quote"):
        reasons.append("protocol_evidence_quote_missing")
    if not stages:
        reasons.append("no_stages")
    if not reagents:
        reasons.append("no_reagents")
    if stage_quote_coverage is not None and stage_quote_coverage < 0.5:
        reasons.append("low_stage_quote_coverage")
    if reagent_quote_coverage is not None and reagent_quote_coverage < 0.5:
        reasons.append("low_reagent_quote_coverage")
    if len(high_severity_flags) > 10:
        reasons.append("many_high_severity_validation_flags")
    if _confidence_score(protocol.get("confidence_level")) < 0.65:
        reasons.append("low_protocol_confidence")

    return not reasons, reasons or ["sufficient_sqlite_reconstruction_evidence"]


def _merge_extra_json(row: dict[str, Any]) -> dict[str, Any]:
    extra = _json_object(row.pop("extra_json", None))
    return {**row, **extra}


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _json_object(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _safe_float(value: Any, default: float | None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _confidence_score(value: Any) -> float:
    label = str(value or "").lower()
    if label == "high":
        return 0.9
    if label == "medium":
        return 0.7
    if label == "low":
        return 0.4
    return 0.5
