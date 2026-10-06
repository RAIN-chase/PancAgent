"""Extracted feature compiler calculations; no agent execution."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from protocol_optimization_agent.analysis.protocol._shared import (
    REAGENT_FAMILIES,
    _action_categories_for_text,
    _binary_any,
    _blank_if_none,
    _compact_event,
    _deduplicate_events,
    _event_reagent_feature_key,
    _event_ref,
    _event_text,
    _factor_identity_text,
    _factor_name,
    _feature_name,
    _first_non_empty,
    _has_text,
    _is_accepted_protocol,
    _keyword_count,
    _matches_aliases,
    _max_comparable_concentration,
    _max_numeric,
    _max_stage_duration,
    _mean_stage_duration,
    _metadata_columns,
    _min_numeric,
    _operation_label,
    _ordered_events,
    _protocol_confidence_score,
    _reagent_feature_key_map,
    _semantic_ref,
    _stage_count,
    _stage_ref,
    _total_days,
    _total_exposure_days,
)


def _build_stage_and_factor_summary(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events_by_stage: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        protocol_id = str(event.get("protocol_id") or "")
        stage = str(event.get("stage_id") or event.get("stage") or event.get("stage_name") or "unknown_stage")
        events_by_stage[(protocol_id, stage)].append(event)

    stage_rows: list[dict[str, Any]] = []
    factor_report: dict[str, Any] = {"protocols": {}, "factor_index": {}}
    for (protocol_id, stage), stage_events in sorted(events_by_stage.items()):
        factors = sorted({_factor_name(event) for event in stage_events if _factor_name(event)})
        stage_rows.append(
            {
                "protocol_id": protocol_id,
                "stage": stage,
                "stage_index": _first_non_empty(stage_events, "stage_index"),
                "day_start": _blank_if_none(_min_numeric(stage_events, "day_start")),
                "day_end": _blank_if_none(_max_numeric(stage_events, "day_end")),
                "n_factor_events": len(stage_events),
                "factors": "|".join(factors),
            }
        )
        factor_report["protocols"].setdefault(protocol_id, {"stages": {}})
        factor_report["protocols"][protocol_id]["stages"][stage] = {
            "n_factor_events": len(stage_events),
            "factors": factors,
            "events": [_compact_event(event) for event in stage_events],
        }
        for factor in factors:
            factor_report["factor_index"].setdefault(factor, [])
            factor_report["factor_index"][factor].append({"protocol_id": protocol_id, "stage": stage})
    factor_report["summary"] = {
        "n_protocols": len(factor_report["protocols"]),
        "n_unique_factors": len(factor_report["factor_index"]),
        "n_filtered_events": len(events),
    }
    return stage_rows, factor_report


def _build_protocol_feature_matrix(
    protocol_records: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source_report: dict[str, Any],
    accepted_protocol_ids: set[str],
    semantic_annotations: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in _deduplicate_events(events):
        by_protocol[str(event.get("protocol_id") or "")].append(event)

    record_by_protocol = {
        str(record.get("protocol_id") or (record.get("protocol") or {}).get("protocol_id") or ""): record
        for record in protocol_records
    }
    source_by_protocol = {
        row["protocol_id"]: row for row in source_report.get("protocol_source_checks", []) if row.get("protocol_id")
    }
    reagent_feature_keys = _reagent_feature_key_map(events)

    dynamic_feature_keys: set[str] = set()
    partial_rows: list[dict[str, Any]] = []
    provenance: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(dict)
    for protocol_id in sorted(set(record_by_protocol).union(by_protocol)):
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        record = record_by_protocol.get(protocol_id, {})
        protocol = record.get("protocol") or {}
        stages = record.get("stages") or []
        protocol_events = by_protocol.get(protocol_id, [])
        row: dict[str, Any] = {
            "protocol_id": protocol_id,
            "paper_id": str(record.get("paper_id") or protocol.get("paper_id") or ""),
            "dataset_id": _first_non_empty(protocol_events, "dataset_id"),
            "protocol_name": str(protocol.get("protocol_name") or ""),
            "source_identity_status": source_by_protocol.get(protocol_id, {}).get("source_identity_status", ""),
            "confidence_score": round(_protocol_confidence_score(record), 3),
            "comparison_ready": bool(record.get("comparison_ready", True)),
        }
        _add_temporal_features(row, protocol, stages, protocol_events, provenance[protocol_id])
        _add_action_and_operation_features(row, protocol_events, stages, provenance[protocol_id])
        _add_reagent_family_features(row, protocol_events, provenance[protocol_id])
        _add_all_reagent_identity_features(row, protocol_events, provenance[protocol_id], reagent_feature_keys)
        _add_factor_type_features(row, protocol_events, provenance[protocol_id])
        _add_action_category_features(row, protocol_events, stages, provenance[protocol_id])
        _add_normalized_factor_features(row, protocol_events, provenance[protocol_id])
        _add_semantic_strategy_features(row, semantic_annotations or [], protocol_id, provenance[protocol_id])
        _add_order_features(row, protocol_events, provenance[protocol_id])
        _add_graph_sequence_and_topology_features(
            row, protocol_events, stages, provenance[protocol_id], reagent_feature_keys
        )
        _add_dependency_features(row, protocol_events, stages, provenance[protocol_id])
        dynamic_feature_keys.update(key for key in row if key not in _metadata_columns())
        partial_rows.append(row)

    ordered_columns = list(_metadata_columns()) + sorted(dynamic_feature_keys)
    rows = [
        {column: row.get(column, 0 if column not in _metadata_columns() else "") for column in ordered_columns}
        for row in partial_rows
    ]
    return rows, {"protocols": dict(provenance)}


def _add_temporal_features(
    row: dict[str, Any],
    protocol: dict[str, Any],
    stages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    stage_count = _stage_count(protocol, stages, events)
    row["total_days"] = _blank_if_none(_total_days(protocol, stages, events))
    row["number_of_stages"] = stage_count
    row["stage_transition_number"] = max(stage_count - 1, 0)
    row["mean_stage_duration"] = _blank_if_none(_mean_stage_duration(stages, events))
    row["max_stage_duration"] = _blank_if_none(_max_stage_duration(stages, events))
    refs = [_stage_ref(stage) for stage in stages] or [_event_ref(event) for event in events[:5]]
    for feature in (
        "total_days",
        "number_of_stages",
        "stage_transition_number",
        "mean_stage_duration",
        "max_stage_duration",
    ):
        provenance[feature] = refs


def _add_action_and_operation_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    text_blocks = [_event_text(event) for event in events] + [
        str(stage.get("key_operations") or "") for stage in stages
    ]
    text = " ".join(text_blocks).lower()
    row["num_add_reagent_actions"] = sum(1 for event in events if str(event.get("factor_type") or "") == "reagent")
    row["num_wash_actions"] = _keyword_count(text_blocks, ("wash", "dpbs"))
    row["num_dissociation_actions"] = _keyword_count(text_blocks, ("dissociat", "accutase", "trypsin"))
    row["num_aggregation_actions"] = _keyword_count(text_blocks, ("aggregat", "cluster", "spheroid"))
    row["has_medium_change"] = _binary_any(text, ("medium change", "changed medium", "fresh medium"))
    row["has_cell_passage"] = _binary_any(text, ("passage", "passaged", "split"))
    row["has_suspension_culture"] = _binary_any(text, ("suspension", "ultra-low attachment", "low attachment"))
    row["has_shaking"] = _binary_any(text, ("shaker", "shaking", "orbital"))
    row["has_dissociation"] = int(row["num_dissociation_actions"] > 0)
    row["has_aggregation"] = int(row["num_aggregation_actions"] > 0)
    row["has_2d_adherent_culture"] = _binary_any(text, ("plate", "coated", "monolayer", "adherent"))
    refs = [_stage_ref(stage) for stage in stages if _has_text(stage.get("key_operations"))]
    refs.extend(
        _event_ref(event)
        for event in events
        if str(event.get("factor_type") or "") in {"operation", "aggregation", "mechanical_condition"}
    )
    refs = refs[:20]
    for feature in (
        "num_add_reagent_actions",
        "num_wash_actions",
        "num_dissociation_actions",
        "num_aggregation_actions",
        "has_medium_change",
        "has_cell_passage",
        "has_suspension_culture",
        "has_shaking",
        "has_dissociation",
        "has_aggregation",
        "has_2d_adherent_culture",
    ):
        provenance[feature] = refs


def _add_reagent_family_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    for family, aliases in REAGENT_FAMILIES.items():
        family_events = [event for event in events if _matches_aliases(_factor_identity_text(event), aliases)]
        prefix = _feature_name(family)
        row[f"{prefix}_presence"] = int(bool(family_events))
        row[f"{prefix}_n_events"] = len(family_events)
        row[f"{prefix}_total_exposure"] = round(_total_exposure_days(family_events), 3)
        row[f"{prefix}_start_day"] = _blank_if_none(_min_numeric(family_events, "day_start"))
        row[f"{prefix}_end_day"] = _blank_if_none(_max_numeric(family_events, "day_end"))
        max_concentration, concentration_unit, concentration_status = _max_comparable_concentration(family_events)
        row[f"{prefix}_max_concentration"] = _blank_if_none(max_concentration)
        row[f"{prefix}_max_concentration_unit"] = concentration_unit
        row[f"{prefix}_concentration_comparability"] = concentration_status
        refs = [_event_ref(event) for event in family_events[:20]]
        for suffix in (
            "presence",
            "n_events",
            "total_exposure",
            "start_day",
            "end_day",
            "max_concentration",
            "max_concentration_unit",
            "concentration_comparability",
        ):
            provenance[f"{prefix}_{suffix}"] = refs


def _add_all_reagent_identity_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
    reagent_feature_keys: dict[str, str],
) -> None:
    reagent_events_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        if str(event.get("factor_type") or "") != "reagent":
            continue
        reagent_name = _event_reagent_feature_key(event, reagent_feature_keys)
        if reagent_name:
            reagent_events_by_name[reagent_name].append(event)

    for reagent_name, reagent_events in sorted(reagent_events_by_name.items()):
        prefix = f"all_reagent_{reagent_name}"
        row[f"{prefix}_presence"] = 1
        row[f"{prefix}_n_events"] = len(reagent_events)
        row[f"{prefix}_total_exposure"] = round(_total_exposure_days(reagent_events), 3)
        row[f"{prefix}_start_day"] = _blank_if_none(_min_numeric(reagent_events, "day_start"))
        row[f"{prefix}_end_day"] = _blank_if_none(_max_numeric(reagent_events, "day_end"))
        max_concentration, concentration_unit, concentration_status = _max_comparable_concentration(reagent_events)
        row[f"{prefix}_max_concentration"] = _blank_if_none(max_concentration)
        row[f"{prefix}_max_concentration_unit"] = concentration_unit
        row[f"{prefix}_concentration_comparability"] = concentration_status
        refs = [_event_ref(event) for event in reagent_events]
        for suffix in (
            "presence",
            "n_events",
            "total_exposure",
            "start_day",
            "end_day",
            "max_concentration",
            "max_concentration_unit",
            "concentration_comparability",
        ):
            provenance[f"{prefix}_{suffix}"] = refs


def _add_factor_type_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    events_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        factor_type = _feature_name(str(event.get("factor_type") or "unknown"))
        if factor_type:
            events_by_type[factor_type].append(event)

    for factor_type, type_events in sorted(events_by_type.items()):
        prefix = f"factor_type_{factor_type}"
        row[f"{prefix}_presence"] = 1
        row[f"{prefix}_n_events"] = len(type_events)
        row[f"{prefix}_start_day"] = _blank_if_none(_min_numeric(type_events, "day_start"))
        row[f"{prefix}_end_day"] = _blank_if_none(_max_numeric(type_events, "day_end"))
        refs = [_event_ref(event) for event in type_events]
        for suffix in ("presence", "n_events", "start_day", "end_day"):
            provenance[f"{prefix}_{suffix}"] = refs


def _add_action_category_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    categorized_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    categorized_stage_refs: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for event in events:
        text = _event_text(event)
        for category in _action_categories_for_text(text, event):
            categorized_refs[category].append(_event_ref(event))

    for stage in stages:
        text = str(stage.get("key_operations") or "")
        for category in _action_categories_for_text(text, None):
            categorized_stage_refs[category].append(_stage_ref(stage))

    for category in sorted(set(categorized_refs).union(categorized_stage_refs)):
        prefix = f"action_category_{category}"
        row[f"{prefix}_presence"] = 1
        row[f"{prefix}_n_events"] = len(categorized_refs.get(category, []))
        row[f"{prefix}_n_stage_mentions"] = len(categorized_stage_refs.get(category, []))
        provenance_refs = categorized_refs.get(category, []) + categorized_stage_refs.get(category, [])
        for suffix in ("presence", "n_events", "n_stage_mentions"):
            provenance[f"{prefix}_{suffix}"] = provenance_refs


def _add_normalized_factor_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    families = sorted({str(event.get("normalized_family") or "") for event in events if event.get("normalized_family")})
    for family in families:
        family_events = [event for event in events if str(event.get("normalized_family") or "") == family]
        prefix = "norm_family_" + _feature_name(family)
        row[f"{prefix}_presence"] = int(bool(family_events))
        row[f"{prefix}_n_events"] = len(family_events)
        row[f"{prefix}_total_exposure"] = round(_total_exposure_days(family_events), 3)
        row[f"{prefix}_start_day"] = _blank_if_none(_min_numeric(family_events, "day_start"))
        row[f"{prefix}_end_day"] = _blank_if_none(_max_numeric(family_events, "day_end"))
        row[f"{prefix}_fallback_fraction"] = round(
            sum(1 for event in family_events if event.get("normalization_source") == "fallback") / len(family_events),
            3,
        )
        refs = [_event_ref(event) for event in family_events[:20]]
        for suffix in ("presence", "n_events", "total_exposure", "start_day", "end_day", "fallback_fraction"):
            provenance[f"{prefix}_{suffix}"] = refs


def _add_semantic_strategy_features(
    row: dict[str, Any],
    semantic_annotations: list[dict[str, Any]],
    protocol_id: str,
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    annotations = [
        annotation for annotation in semantic_annotations if str(annotation.get("protocol_id") or "") == protocol_id
    ]
    modules = sorted(
        {
            str(module)
            for annotation in annotations
            for module in (annotation.get("strategy_modules") or [])
            if module and str(module) != "unknown"
        }
    )
    row["n_semantic_stage_annotations"] = len(annotations)
    row["n_strategy_modules"] = len(modules)
    provenance["n_semantic_stage_annotations"] = [_semantic_ref(annotation) for annotation in annotations[:20]]
    provenance["n_strategy_modules"] = [_semantic_ref(annotation) for annotation in annotations[:20]]
    for module in modules:
        feature = "semantic_module_" + _feature_name(module) + "_presence"
        row[feature] = 1
        provenance[feature] = [
            _semantic_ref(annotation)
            for annotation in annotations
            if module in (annotation.get("strategy_modules") or [])
        ][:20]


def _add_order_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    ordered: list[tuple[float, str]] = []
    refs_by_family: dict[str, list[dict[str, Any]]] = {}
    for family, aliases in REAGENT_FAMILIES.items():
        family_events = [event for event in events if _matches_aliases(_factor_identity_text(event), aliases)]
        refs_by_family[_feature_name(family)] = [_event_ref(event) for event in family_events[:5]]
        start = _min_numeric(family_events, "day_start")
        if start is None:
            start = _min_numeric(family_events, "stage_index")
        if start is not None:
            ordered.append((float(start), _feature_name(family)))
    deduped: list[str] = []
    for _, family in sorted(ordered):
        if not deduped or deduped[-1] != family:
            deduped.append(family)
    row["ordered_reagent_program"] = ">".join(deduped)
    provenance["ordered_reagent_program"] = [ref for family in deduped for ref in refs_by_family.get(family, [])]
    for previous, current in zip(deduped, deduped[1:], strict=False):
        feature = f"transition_{previous}_to_{current}"
        row[feature] = 1
        provenance[feature] = refs_by_family.get(previous, []) + refs_by_family.get(current, [])


def _add_graph_sequence_and_topology_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
    reagent_feature_keys: dict[str, str],
) -> None:
    ordered_events = _ordered_events(events)
    stage_ids = {str(stage.get("stage_id") or "") for stage in stages if stage.get("stage_id")}
    event_stage_ids = {str(event.get("stage_id") or "") for event in events if event.get("stage_id")}
    reagent_events = [
        event
        for event in ordered_events
        if str(event.get("factor_type") or "") == "reagent" and _event_reagent_feature_key(event, reagent_feature_keys)
    ]
    action_labels = [_feature_name(_operation_label(event)) for event in ordered_events]
    factor_types = [_feature_name(str(event.get("factor_type") or "unknown")) for event in ordered_events]

    row["graph_n_stage_nodes"] = len(stage_ids or event_stage_ids)
    row["graph_n_action_nodes"] = len(ordered_events)
    row["graph_n_reagent_item_nodes"] = len(
        {_event_reagent_feature_key(event, reagent_feature_keys) for event in reagent_events}
    )
    row["graph_n_event_edges"] = len(ordered_events)
    row["graph_n_next_action_edges"] = max(len(ordered_events) - 1, 0)
    row["graph_n_next_stage_edges"] = max(row["graph_n_stage_nodes"] - 1, 0)
    row["graph_mean_actions_per_stage"] = (
        round(len(ordered_events) / row["graph_n_stage_nodes"], 3) if row["graph_n_stage_nodes"] else 0
    )
    row["graph_max_actions_per_stage"] = max(
        Counter(str(event.get("stage_id") or "") for event in ordered_events).values() or [0]
    )
    refs = [_event_ref(event) for event in ordered_events]
    for feature in (
        "graph_n_stage_nodes",
        "graph_n_action_nodes",
        "graph_n_reagent_item_nodes",
        "graph_n_event_edges",
        "graph_n_next_action_edges",
        "graph_n_next_stage_edges",
        "graph_mean_actions_per_stage",
        "graph_max_actions_per_stage",
    ):
        provenance[feature] = refs or [_stage_ref(stage) for stage in stages]

    for previous, current in zip(action_labels, action_labels[1:], strict=False):
        if not previous or not current:
            continue
        feature = f"action_bigram_{previous}_to_{current}"
        row[feature] = row.get(feature, 0) + 1
    _attach_adjacent_pair_provenance("action_bigram", action_labels, ordered_events, provenance)

    for previous, current in zip(factor_types, factor_types[1:], strict=False):
        if not previous or not current:
            continue
        feature = f"factor_type_bigram_{previous}_to_{current}"
        row[feature] = row.get(feature, 0) + 1
    _attach_adjacent_pair_provenance("factor_type_bigram", factor_types, ordered_events, provenance)

    reagent_labels = [_event_reagent_feature_key(event, reagent_feature_keys) for event in reagent_events]
    for previous, current in zip(reagent_labels, reagent_labels[1:], strict=False):
        if not previous or not current:
            continue
        feature = f"all_reagent_transition_{previous}_to_{current}"
        row[feature] = row.get(feature, 0) + 1
    _attach_adjacent_pair_provenance("all_reagent_transition", reagent_labels, reagent_events, provenance)

    for event in reagent_events:
        action = _feature_name(_operation_label(event))
        reagent = _event_reagent_feature_key(event, reagent_feature_keys)
        if not action or not reagent:
            continue
        feature = f"motif_action_{action}_uses_reagent_{reagent}"
        row[feature] = 1
        provenance.setdefault(feature, []).append(_event_ref(event))


def _attach_adjacent_pair_provenance(
    prefix: str,
    labels: list[str],
    events: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    for index, (previous, current) in enumerate(zip(labels, labels[1:], strict=False)):
        if not previous or not current:
            continue
        feature = f"{prefix}_{previous}_to_{current}"
        provenance.setdefault(feature, []).extend([_event_ref(events[index]), _event_ref(events[index + 1])])


def _add_dependency_features(
    row: dict[str, Any],
    events: list[dict[str, Any]],
    stages: list[dict[str, Any]],
    provenance: dict[str, list[dict[str, Any]]],
) -> None:
    text = " ".join(
        [_event_text(event) for event in events] + [str(stage.get("key_operations") or "") for stage in stages]
    ).lower()
    row["requires_previous_stage_completion"] = _binary_any(text, ("after ", "following ", "on stage", "during stage"))
    row["requires_cell_dissociation"] = _binary_any(text, ("dissociat", "accutase", "trypsin"))
    row["requires_cell_number_threshold"] = _binary_any(
        text, ("cells/cm", "cells per", "x10", "×10", "cell density", "seeded at")
    )
    refs = [_stage_ref(stage) for stage in stages if _has_text(stage.get("key_operations"))]
    refs.extend(_event_ref(event) for event in events[:10])
    for feature in (
        "requires_previous_stage_completion",
        "requires_cell_dissociation",
        "requires_cell_number_threshold",
    ):
        provenance[feature] = refs[:20]


def _build_predefined_quantification_registry(events: list[dict[str, Any]]) -> dict[str, Any]:
    reagent_labels_by_feature: dict[str, set[str]] = defaultdict(set)
    factor_types: set[str] = set()
    action_categories: set[str] = set()
    reagent_feature_keys = _reagent_feature_key_map(events)
    for event in events:
        factor_type_raw = str(event.get("factor_type") or "unknown")
        factor_type = _feature_name(factor_type_raw)
        if factor_type:
            factor_types.add(factor_type)
        if factor_type_raw == "reagent":
            reagent_name = _factor_name(event)
            reagent_key = _event_reagent_feature_key(event, reagent_feature_keys)
            if reagent_key:
                reagent_labels_by_feature[reagent_key].add(reagent_name)
        action_categories.update(_action_categories_for_text(_event_text(event), event))

    features: list[dict[str, Any]] = []
    for reagent_key, labels in sorted(reagent_labels_by_feature.items()):
        base = f"all_reagent_{reagent_key}"
        features.extend(
            [
                _predefined_feature(
                    base,
                    "presence",
                    "binary",
                    "reagent_identity",
                    f"1 if reagent `{reagent_key}` appears in accepted factor events.",
                ),
                _predefined_feature(
                    base,
                    "n_events",
                    "count",
                    "reagent_identity",
                    f"Count of accepted events for reagent `{reagent_key}`.",
                ),
                _predefined_feature(
                    base,
                    "total_exposure",
                    "numeric_sum",
                    "reagent_timing",
                    f"Sum of exposure duration for reagent `{reagent_key}` when day_start/day_end are available.",
                ),
                _predefined_feature(
                    base,
                    "start_day",
                    "numeric_min",
                    "reagent_timing",
                    f"Earliest day_start for reagent `{reagent_key}`.",
                ),
                _predefined_feature(
                    base, "end_day", "numeric_max", "reagent_timing", f"Latest day_end for reagent `{reagent_key}`."
                ),
                _predefined_feature(
                    base,
                    "max_concentration",
                    "numeric_max",
                    "reagent_dose",
                    f"Maximum parsed concentration_value for reagent `{reagent_key}`.",
                ),
            ]
        )
        for feature in features[-6:]:
            feature["canonical_labels"] = sorted(labels)
            feature["selector"] = {"factor_type": "reagent", "feature_key": reagent_key}

    for factor_type in sorted(factor_types):
        base = f"factor_type_{factor_type}"
        features.extend(
            [
                _predefined_feature(
                    base,
                    "presence",
                    "binary",
                    "factor_type_coverage",
                    f"1 if accepted events include factor_type `{factor_type}`.",
                ),
                _predefined_feature(
                    base,
                    "n_events",
                    "count",
                    "factor_type_coverage",
                    f"Count of accepted events with factor_type `{factor_type}`.",
                ),
                _predefined_feature(
                    base,
                    "start_day",
                    "numeric_min",
                    "factor_type_timing",
                    f"Earliest day_start for factor_type `{factor_type}`.",
                ),
                _predefined_feature(
                    base,
                    "end_day",
                    "numeric_max",
                    "factor_type_timing",
                    f"Latest day_end for factor_type `{factor_type}`.",
                ),
            ]
        )
        for feature in features[-4:]:
            feature["selector"] = {"factor_type": factor_type}

    for category in sorted(action_categories):
        base = f"action_category_{category}"
        features.extend(
            [
                _predefined_feature(
                    base,
                    "presence",
                    "binary",
                    "action_category_coverage",
                    f"1 if accepted events or stage operations match action category `{category}`.",
                ),
                _predefined_feature(
                    base,
                    "n_events",
                    "count",
                    "action_category_coverage",
                    f"Count of accepted events matching action category `{category}`.",
                ),
                _predefined_feature(
                    base,
                    "n_stage_mentions",
                    "count",
                    "action_category_coverage",
                    f"Count of accepted stage operation summaries matching action category `{category}`.",
                ),
            ]
        )
        for feature in features[-3:]:
            feature["selector"] = {"action_category": category}

    return {
        "schema_version": "predefined-feature-registry-v1",
        "source": "ProtocolAnalysisAgent deterministic quantification layer",
        "scope": "accepted_filtered_analysis_events_only",
        "families": [
            {
                "name": "all_reagent",
                "description": "One predefined feature group for every reagent identity observed in accepted reagent events.",
                "n_entities": len(reagent_labels_by_feature),
                "features_per_entity": [
                    "presence",
                    "n_events",
                    "total_exposure",
                    "start_day",
                    "end_day",
                    "max_concentration",
                    "max_concentration_unit",
                    "concentration_comparability",
                ],
            },
            {
                "name": "factor_type",
                "description": "One predefined feature group for every structured factor_type observed in accepted events.",
                "n_entities": len(factor_types),
                "features_per_entity": ["presence", "n_events", "start_day", "end_day"],
            },
            {
                "name": "action_category",
                "description": "One predefined feature group for every standardized experimental action category detected in accepted events or stage operations.",
                "n_entities": len(action_categories),
                "features_per_entity": ["presence", "n_events", "n_stage_mentions"],
            },
        ],
        "n_features": len(features),
        "features": features,
    }


def _predefined_feature(base: str, suffix: str, feature_type: str, category: str, definition: str) -> dict[str, Any]:
    return {
        "feature_name": f"{base}_{suffix}",
        "feature_type": feature_type,
        "level": "protocol",
        "biological_category": category,
        "definition": definition,
        "value_rule": suffix,
        "source_fields": [
            "analysis_events.factor_type",
            "analysis_events.factor_name",
            "analysis_events.standardized_name",
            "analysis_events.day_start",
            "analysis_events.day_end",
            "analysis_events.concentration_value",
            "analysis_events.operation",
            "stages.key_operations",
            "evidence_quote",
        ],
        "required_evidence": True,
        "computed_by": "ProtocolAnalysisAgent deterministic predefined feature compiler",
        "proposed_by": "rule",
        "approved_status": "approved",
    }
