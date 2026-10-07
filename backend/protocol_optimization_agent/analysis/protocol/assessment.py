"""Detail-preserving protocol assessment and comparison evidence."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from protocol_optimization_agent.analysis.protocol.step_semantics import align_steps
from protocol_optimization_agent.runtime.io import read_json, read_jsonl


def assess_protocols(engine: Any, protocol_ids: list[str]) -> dict[str, Any]:
    selected = protocol_ids or [row["protocol_id"] for row in engine.matrix_rows]
    representations = read_jsonl(engine.output_dir / "standardized_protocol_representations.jsonl")
    annotations = read_jsonl(engine.output_dir / "semantic_step_annotations.jsonl")
    rows = []
    for protocol_id in selected:
        matrix = next((row for row in engine.matrix_rows if row["protocol_id"] == protocol_id), None)
        events = engine.get_protocol_events(protocol_id)
        structured = [row for row in representations if row.get("protocol_id") == protocol_id]
        semantic_steps = [row for row in annotations if row.get("protocol_id") == protocol_id]
        gaps = []
        if matrix is None:
            gaps.append("compiled_protocol_not_found")
        if not structured:
            gaps.append("lossless_structured_steps_unavailable")
        if not events:
            gaps.append("source_events_unavailable")
        if not semantic_steps:
            gaps.append("step_semantics_unavailable_recompile_required")
        gaps.extend(f"{step['step_id']}:{note}" for step in semantic_steps for note in step["validation_notes"]
                    if note != "conditional_execution_not_fixed_schedule")
        for index, event in enumerate(events):
            if not event.get("evidence_quote") and not event.get("source_refs"):
                gaps.append(f"event:{index}:source_evidence_missing")
            if event.get("raw_concentration") and not event.get("normalized_unit"):
                gaps.append(f"event:{index}:unit_unresolved")
        rows.append(
            {
                "protocol_id": protocol_id,
                "matrix": matrix,
                "events": events,
                "structured_steps": structured,
                "semantic_steps": semantic_steps,
                "evidence_gaps": gaps,
                "audit_findings": engine.get_audit_findings(protocol_id),
            }
        )
    return {
        "status": "ok" if rows and all(not row["evidence_gaps"] for row in rows) else "partial",
        "protocol_assessments": rows,
        "evidence_gaps": [gap for row in rows for gap in row["evidence_gaps"]],
        "matrix_release_gate": read_json(engine.output_dir / "matrix_release_gate_report.json"),
        "step_semantic_audit": read_json(engine.output_dir / "step_semantic_audit.json"),
        "policy": "Missing extracted events never establish explicit non-use.",
    }


def detailed_comparison(engine: Any, a: str, b: str) -> dict[str, Any]:
    rows = read_jsonl(engine.output_dir / "standardized_protocol_representations.jsonl")
    left, right = engine.get_protocol_events(a), engine.get_protocol_events(b)
    steps_a = [row for row in rows if row.get("protocol_id") == a]
    steps_b = [row for row in rows if row.get("protocol_id") == b]
    annotations = read_jsonl(engine.output_dir / "semantic_step_annotations.jsonl")
    semantic_alignment = align_steps([row for row in annotations if row.get("protocol_id") == a],
                                     [row for row in annotations if row.get("protocol_id") == b])

    def identity(event: dict[str, Any]) -> str:
        return json.dumps(
            {
                key: value
                for key, value in event.items()
                if key not in {"protocol_id", "event_id", "paper_id", "source_doc_id"}
            },
            sort_keys=True,
        )

    left_counts, right_counts = Counter(map(identity, left)), Counter(map(identity, right))
    return {
        "protocol_a_events": left,
        "protocol_b_events": right,
        "protocol_a_steps": steps_a,
        "protocol_b_steps": steps_b,
        "semantic_alignment": semantic_alignment,
        "only_a": [json.loads(value) for value in (left_counts - right_counts).elements()],
        "only_b": [json.loads(value) for value in (right_counts - left_counts).elements()],
        "source_refs": [
            {"protocol_id": protocol_id, "event_index": index, "event": event}
            for protocol_id, events in ((a, left), (b, right))
            for index, event in enumerate(events)
        ],
        "detail_status": "available" if left and right and steps_a and steps_b else "source_detail_incomplete",
        "matrix_zero_policy": "A compiled zero is not evidence of explicit non-use; check original events and source statements.",
        "absence_policy": {
            "unreported": "no affirmative source statement",
            "explicit_not_used": "requires explicit negative source evidence",
            "unparseable": "reported value cannot be deterministically parsed",
        },
    }
