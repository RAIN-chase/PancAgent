from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_optimization_agent.query_engine import ProtocolQueryEngine


def get_protocol_collection_summary(output_dir: Path | str) -> dict[str, Any]:
    return ProtocolQueryEngine(output_dir).get_protocol_collection_summary()


def find_protocols(output_dir: Path | str, query: str) -> list[dict[str, Any]]:
    return ProtocolQueryEngine(output_dir).find_protocols(query)


def get_protocol_summary(output_dir: Path | str, protocol_id: str) -> dict[str, Any]:
    return ProtocolQueryEngine(output_dir).get_protocol_summary(protocol_id)


def compare_protocols(output_dir: Path | str, protocol_id_a: str, protocol_id_b: str) -> dict[str, Any]:
    return ProtocolQueryEngine(output_dir).compare_protocols(protocol_id_a, protocol_id_b)


def get_stage_diff(output_dir: Path | str, protocol_id_a: str, protocol_id_b: str) -> dict[str, Any]:
    return ProtocolQueryEngine(output_dir).get_stage_diff(protocol_id_a, protocol_id_b)


def get_factor_diff(output_dir: Path | str, protocol_id_a: str, protocol_id_b: str) -> dict[str, Any]:
    return ProtocolQueryEngine(output_dir).get_factor_diff(protocol_id_a, protocol_id_b)


def get_evidence_for_feature(output_dir: Path | str, protocol_id: str, feature: str) -> list[dict[str, Any]]:
    return ProtocolQueryEngine(output_dir).get_evidence_for_feature(protocol_id, feature)


def get_audit_findings(output_dir: Path | str, protocol_id: str) -> list[dict[str, Any]]:
    return ProtocolQueryEngine(output_dir).get_audit_findings(protocol_id)
