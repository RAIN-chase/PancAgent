"""Account for the supplied JSON, including information rejected before compilation."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from fnmatch import fnmatchcase


def information_units(value, path=""):
    if isinstance(value, dict) and value:
        for key, child in value.items():
            yield from information_units(child, path + "/" + str(key).replace("~", "~0").replace("/", "~1"))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from information_units(child, path + "/" + str(index))
    else:
        yield path, value


def build_coverage_ledger(raw_records, original_events, validated_events, kept_events, accepted_ids,
                          filter_report, schema_report, field_map, *, formal_source=True):
    decisions = {row["event_id"]: row for row in filter_report.get("decisions", []) if row.get("event_id")}
    validated = {row.get("event_id") for row in validated_events}
    kept = {row.get("event_id") for row in kept_events}
    mappings = {row["event_identity"]: row.get("field_mappings", []) for row in field_map.get("field_rows", [])}
    events_by_protocol = defaultdict(list)
    for event in original_events:
        if event.get("source_record_path"):
            events_by_protocol[event.get("protocol_id")].append(event)
    rows, counts, summaries = [], Counter(), {}
    # Only explicit field-to-feature associations count as numerical projection; event provenance alone does not.
    aliases = {"concentration": "concentration_value", "original_name": "factor_name", "standardized_name": "factor_name",
               "unit": "concentration_unit", "key_operations": "operation"}
    structural = {"protocol_id", "paper_id", "stage_id", "stage_index", "protocol_name", "evidence_quote", "evidence_ids",
                  "source_file", "source_doc_id", "confidence", "confidence_level", "comparison_ready", "validation_status"}
    for index, record in enumerate(raw_records):
        pid = record.get("protocol_id") or record.get("protocol", {}).get("protocol_id", "")
        per_protocol = Counter()
        for path, value in information_units(record):
            key = path.rsplit("/", 1)[-1]
            related = [event for event in events_by_protocol[pid] if path == event["source_record_path"]
                       or path.startswith(event["source_record_path"] + "/")]
            # Stage-level operation events must not claim ownership of unrelated reagent fields.
            related = [event for event in related if "/reagents/" not in path or "/reagents/" in event["source_record_path"]]
            targets = []
            for event in related:
                for mapping in mappings.get(event.get("event_id"), []):
                    if mapping.get("source_path") == "event." + aliases.get(key, key) and mapping.get("conversion_status") in {"mapped", "standardized"}:
                        targets.extend(feature for feature in mapping.get("target_feature_candidates", [])
                                       if any(fnmatchcase(feature, pattern) for pattern in mapping.get("target_columns", [])))
            rejection = []
            for event in related:
                eid = event.get("event_id")
                if eid not in validated:
                    rejection.append("event_schema_rejected")
                elif eid not in kept:
                    rejection.append(decisions.get(eid, {}).get("reason", "filtered_without_detailed_reason"))
            if value is None or value == "" or value == [] or value == {}:
                status, reason = "not_reported", "null_or_empty_in_supplied_JSON; not_explicit_non_use"
            elif pid not in accepted_ids:
                status, reason = "filtered", "protocol_quality_or_schema_gate; retained_in_raw_snapshot"
            elif related and all(event.get("event_id") not in kept for event in related):
                status, reason = "filtered", ";".join(sorted(set(rejection)))
            elif key in {"concentration", "concentration_value"} and related and any(event.get("concentration_parse_status") == "non_scalar" for event in related):
                status, reason = "pending_parse", "non_scalar_quantity_retained; no_invented_numeric_projection"
            elif targets:
                status, reason = "matrix", "explicit_field_mapping; inspect_mapping_not_presence_only"
            elif key in structural or path.startswith(("/paper/", "/protocol/")):
                status, reason = "retained_metadata", "source_identity_or_audit_metadata"
            elif path.startswith(("/stages/", "/preparation/")) and related:
                status, reason = "retained_step_condition", "raw_field_preserved; event_link_does_not_prove_semantic_parsing"
            else:
                status, reason = "pending_parse", "raw_only; no_verified_event_or_step_projection"
            snapshot_path = f"/{index}{path}"
            digest = hashlib.sha256(json.dumps([pid, path, value], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            rows.append({"unit_id": digest, "protocol_id": pid, "source_record_index": index, "source_pointer": path,
                         "raw_snapshot_pointer": snapshot_path, "source_value_sha256": hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                         "disposition": status, "reason": reason, "event_ids": sorted({event["event_id"] for event in related if event.get("event_id")}),
                         "matrix_targets": sorted(set(targets)), "semantic_validation": "not_adjudicated"})
            counts[status] += 1
            per_protocol[status] += 1
        summaries[pid] = {"n_units": sum(per_protocol.values()), "dispositions": dict(per_protocol),
                          "complete_understanding": False, "pending_parse": per_protocol["pending_parse"]}
    return {"schema_version": "protocol-input-coverage-ledger-v1", "scope": "supplied_protocols_JSON_only" if formal_source else "caller_supplied_projected_records_only",
            "raw_snapshot": "raw_protocol_input.json", "n_units": len(rows), "dispositions": dict(counts),
            "protocols": summaries, "units": rows, "schema_findings": schema_report.get("findings", []),
            "complete_understanding": False, "paper_fulltext_completeness": "not_evaluated",
            "policy": "Accounting and retention are not expert semantic coverage. Unknown operations remain pending; no inferred non-use or fixed time."}
