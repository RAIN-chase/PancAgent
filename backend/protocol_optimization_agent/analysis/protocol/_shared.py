"""Extracted  shared calculations; no agent execution."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from protocol_optimization_agent.schemas import Confidence, TemporalAnnotation

REAGENT_FAMILIES: dict[str, tuple[str, ...]] = {
    "Activin": ("activin", "acta"),
    "CHIR": ("chir99021", "chir", "gsk3"),
    "KGF": ("kgf", "fgf7"),
    "RA": ("retinoic acid", "ttnpb"),
    "ALK5i": ("alk5", "sb431542", "repsox", "a83-01", "alk inhibitor"),
    "BMP": ("bmp4", "bmp"),
    "FGF": ("fgf10", "fgf2", "bfgf"),
    "EGF": ("egf",),
    "SANT": ("sant-1", "sant1"),
    "LDN": ("ldn", "ldn193189", "bmp inhibitor"),
    "T3": (" t3", "triiodothyronine"),
    "Heparin": ("heparin",),
    "Betacellulin": ("betacellulin",),
    "Nicotinamide": ("nicotinamide",),
    "GammaSecretaseInhibitor": ("gamma-secretase", "gamma secretase", "γ-secretase", "gsi", "ro4929097"),
    "ROCKi": ("y-27632", "rock", "rocki"),
    "Matrigel": ("matrigel",),
    "VitaminC": ("vitamin c", "vitc", "ascorbic"),
    "ZnSO4": ("znso4", "zinc"),
}

KEY_FACTOR_TYPES_REQUIRING_EVIDENCE = {
    "reagent",
    "matrix_or_coating",
    "medium_base",
    "culture_format",
    "operation",
    "aggregation",
    "mechanical_condition",
    "sorting",
    "oxygen_condition",
    "transplantation",
}

DEFAULT_ALLOWED_EVENT_STATUSES = {"evidence_supported"}

ACTION_CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "add_reagent_or_factor": (
        "add",
        "added",
        "addition",
        "supplement",
        "supplemented",
        "treat",
        "treated",
        "expos",
        "containing",
        "with",
    ),
    "medium_change_or_replace": ("change", "changed", "replace", "replaced", "refresh", "feed", "fed"),
    "culture_or_incubate": (
        "culture",
        "cultured",
        "incubat",
        "maintain",
        "maintained",
        "differentiat",
        "grown",
        "grow",
    ),
    "wash": ("wash", "washed", "rinse", "rinsed"),
    "dissociation": ("dissociat", "detach", "detached", "trypsin", "accutase", "single-cell", "single cell"),
    "passage": ("passag", "split", "subcultur"),
    "seed_or_plate": ("seed", "seeded", "plate", "plated", "replate", "replated"),
    "transfer": ("transfer", "transferred", "move", "moved"),
    "aggregation_or_3d_formation": ("aggregat", "reaggregat", "cluster", "spheroid", "organoid", "embryoid bod"),
    "matrix_or_coating": (
        "coat",
        "coated",
        "matrix",
        "matrigel",
        "laminin",
        "collagen",
        "embed",
        "embedded",
        "overlay",
    ),
    "suspension_or_shaking": ("suspension", "spinner", "shak", "rotat", "orbital", "stir"),
    "sorting_or_enrichment": ("sort", "sorted", "facs", "macs", "enrich", "enriched", "purif"),
    "infection_or_transduction": ("infect", "infection", "transduc", "lentivirus", "virus", "spinfection"),
    "genetic_perturbation": (
        "crispr",
        "knockdown",
        "knockout",
        "sirna",
        "shrna",
        "transfect",
        "transfection",
        "overexpress",
    ),
    "assay_or_qc": (
        "assay",
        "stain",
        "staining",
        "immunostain",
        "qpcr",
        "rt-pcr",
        "flow cytometry",
        "facs analysis",
        "measure",
        "marker",
        "glucose",
        "gsis",
    ),
    "transplantation": ("transplant", "graft", "implanted", "kidney capsule", "in vivo"),
    "cryopreservation": ("cryopreserv", "freeze", "frozen", "thaw", "thawed"),
}


def _build_temporal_annotations(
    protocol_records: list[dict[str, Any]],
    accepted_protocol_ids: set[str],
) -> list[dict[str, Any]]:
    annotations: list[dict[str, Any]] = []
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        for stage in record.get("stages") or []:
            stage_id = str(stage.get("stage_id") or "")
            if not stage_id:
                continue
            day_start = _safe_float(stage.get("day_start"), None)
            day_end = _safe_float(stage.get("day_end"), None)
            duration_days = _safe_float(stage.get("duration_days"), None)
            notes = []
            if duration_days is None and day_start is not None and day_end is not None:
                duration_days = max(day_end - day_start, 0.0)
                notes.append("duration_inferred_from_day_start_end")
            confidence = Confidence.HIGH if day_start is not None and day_end is not None else Confidence.LOW
            payload = {
                "protocol_id": protocol_id,
                "stage_id": stage_id,
                "day_start": day_start,
                "day_end": day_end,
                "duration_days": duration_days,
                "temporal_confidence": confidence,
                "evidence_quote": stage.get("evidence_quote") or "",
                "source": "rule" if confidence != Confidence.LOW else "fallback",
                "validation_notes": notes
                if notes
                else ([] if confidence != Confidence.LOW else ["missing_complete_stage_time"]),
            }
            annotations.append(TemporalAnnotation.model_validate(payload).model_dump(mode="json"))
    return annotations


def _build_analysis_events(
    filtered_events: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    analysis_events: list[dict[str, Any]] = []
    for index, event in enumerate(filtered_events):
        normalized = normalized_events[index] if index < len(normalized_events) else {}
        merged = dict(event)
        if normalized:
            merged.update(
                {
                    "original_name": normalized.get("original_name") or _factor_name(event),
                    "factor_name": normalized.get("original_name") or _factor_name(event),
                    "standardized_name": normalized.get("normalized_name") or _factor_name(event),
                    "normalized_name": normalized.get("normalized_name") or "",
                    "normalized_family": normalized.get("normalized_family") or "",
                    "pathway": normalized.get("pathway") or "",
                    "factor_type": normalized.get("factor_type") or event.get("factor_type"),
                    "action_type": normalized.get("action_type") or _operation_label(event),
                    "normalization_confidence": normalized.get("confidence") or "",
                    "normalization_source": normalized.get("normalization_source") or "",
                    "normalization_status": normalized.get("validation_status") or "",
                    "normalization_notes": "|".join(normalized.get("validation_notes") or []),
                    "evidence_quote": normalized.get("evidence_quote") or event.get("evidence_quote"),
                }
            )
            for key in ("day_start", "day_end", "duration_days", "concentration_value", "concentration_unit"):
                if normalized.get(key) is not None:
                    merged[key] = normalized.get(key)
        merged["analysis_event_source"] = (
            "normalized_factor_events" if normalized else "filtered_protocol_factor_events"
        )
        merged.update(_standardized_concentration_fields(merged))
        analysis_events.append(merged)
    return analysis_events


def _ordered_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        events,
        key=lambda event: (
            _sort_float(event.get("day_start")),
            _sort_float(event.get("stage_index")),
            str(event.get("stage_id") or ""),
            str(event.get("event_id") or ""),
            _factor_name(event),
        ),
    )


def _sort_float(value: Any, default: float = 1_000_000) -> float:
    parsed = _safe_float(value, None)
    return parsed if parsed is not None else default


def _event_identity(event: dict[str, Any]) -> str:
    event_id = str(event.get("event_id") or "")
    if event_id:
        return event_id
    return "|".join(
        str(event.get(key) or "")
        for key in ("protocol_id", "stage_id", "factor_type", "factor_name", "day_start", "day_end", "operation")
    )


def _operation_label(event: dict[str, Any]) -> str:
    action_type = str(event.get("action_type") or "").strip()
    if action_type:
        return action_type
    operation = str(event.get("operation") or "").strip()
    factor_type = str(event.get("factor_type") or "").strip()
    if factor_type == "reagent":
        return "add_reagent"
    if factor_type == "matrix_or_coating":
        return "set_matrix_or_coating"
    if factor_type == "medium_base":
        return "set_medium"
    if factor_type == "culture_format":
        return "set_culture_format"
    if factor_type == "cell_state_transition":
        return "cell_state_transition"
    if factor_type in {"assay", "qc_marker"}:
        return "assay"
    operation_text = operation.lower()
    if "wash" in operation_text:
        return "wash"
    if any(token in operation_text for token in ("dissociat", "accutase", "trypsin")):
        return "dissociate"
    if any(token in operation_text for token in ("transfer", "resuspend", "seed")):
        return "transfer_or_seed"
    if any(token in operation_text for token in ("aggregat", "cluster", "spheroid")):
        return "aggregate"
    if any(token in operation_text for token in ("culture", "incubat", "differentiat")):
        return "culture"
    if operation:
        return "operation"
    if factor_type:
        return factor_type
    return "protocol_action"


def _action_categories_for_text(text: str, event: dict[str, Any] | None) -> set[str]:
    lowered = str(text or "").lower()
    categories = {
        category
        for category, patterns in ACTION_CATEGORY_PATTERNS.items()
        if any(pattern in lowered for pattern in patterns)
    }
    if event:
        factor_type = str(event.get("factor_type") or "").strip()
        action_type = _operation_label(event)
        if factor_type == "reagent" or action_type == "add_reagent":
            categories.add("add_reagent_or_factor")
        if factor_type in {"assay", "qc_marker"} or action_type == "assay":
            categories.add("assay_or_qc")
        if factor_type == "matrix_or_coating" or action_type == "set_matrix_or_coating":
            categories.add("matrix_or_coating")
        if factor_type == "aggregation" or action_type == "aggregate":
            categories.add("aggregation_or_3d_formation")
        if factor_type == "sorting":
            categories.add("sorting_or_enrichment")
        if factor_type == "transplantation":
            categories.add("transplantation")
        if factor_type in {"culture_format", "mechanical_condition"}:
            categories.add("culture_or_incubate")
        if action_type in {"dissociate", "transfer_or_seed", "wash"}:
            mapped = {
                "dissociate": "dissociation",
                "transfer_or_seed": "seed_or_plate",
                "wash": "wash",
            }
            categories.add(mapped[action_type])
        if not categories and (factor_type != "reagent" or _has_text(event.get("operation"))):
            categories.add("other_protocol_action")
    elif lowered and not categories:
        categories.add("other_protocol_action")
    return categories


def _stage_ref(stage: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "stage",
        "entity_id": stage.get("stage_id") or "",
        "stage_index": stage.get("stage_index"),
        "quote": stage.get("evidence_quote") or "",
        "source_location": stage.get("source_location") or "",
    }


def _event_ref(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "protocol_factor_event",
        "event_identity": _event_identity(event),
        "event_id": event.get("event_id") or "",
        "stage_id": event.get("stage_id") or "",
        "factor_type": event.get("factor_type") or "",
        "factor_name": _factor_name(event),
        "normalized_family": event.get("normalized_family") or "",
        "normalization_source": event.get("normalization_source") or "",
        "confidence": event.get("normalization_confidence") or "",
        "quote": event.get("evidence_quote") or "",
        "source_doc_id": event.get("source_doc_id") or "",
        "source_location": event.get("source_location") or "",
        "concentration_value": event.get("concentration_value"),
        "concentration_unit": event.get("concentration_unit") or event.get("unit"),
        "concentration_standard_value": event.get("concentration_standard_value"),
        "concentration_standard_unit": event.get("concentration_standard_unit"),
        "concentration_comparability_status": event.get("concentration_comparability_status"),
    }


def _semantic_ref(annotation: dict[str, Any]) -> dict[str, Any]:
    return {
        "entity_type": "semantic_stage_annotation",
        "stage_id": annotation.get("stage_id") or "",
        "stage_index": annotation.get("stage_index"),
        "biological_role": annotation.get("biological_role") or "",
        "strategy_modules": annotation.get("strategy_modules") or [],
        "quote": annotation.get("evidence_quote") or "",
        "source": annotation.get("source") or "",
        "confidence": annotation.get("confidence") or "",
    }


def _deduplicate_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for event in events:
        key = (
            event.get("protocol_id"),
            event.get("stage_id"),
            event.get("factor_type"),
            _factor_name(event).lower(),
            event.get("day_start"),
            event.get("day_end"),
            event.get("concentration_raw") or event.get("concentration_value"),
            event.get("concentration_unit") or event.get("unit"),
            event.get("concentration_standard_value"),
            event.get("concentration_standard_unit"),
            event.get("operation"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _protocol_confidence_score(record: dict[str, Any]) -> float:
    confidence = record.get("confidence")
    protocol = record.get("protocol") or {}
    if isinstance(confidence, (int, float)):
        return float(confidence)
    if isinstance(confidence, dict):
        return max(
            _confidence_label_score(confidence.get("extraction")),
            _confidence_label_score(protocol.get("confidence_level")),
        )
    return _confidence_label_score(protocol.get("confidence_level"), default=1.0)


def _confidence_label_score(value: Any, default: float = 0.5) -> float:
    label = str(value or "").lower()
    if label == "high":
        return 0.9
    if label == "medium":
        return 0.7
    if label == "low":
        return 0.4
    if label in {"not_evaluated", ""}:
        return default
    return default


def _validation_flag_names(value: Any) -> set[str]:
    if not value:
        return set()
    if isinstance(value, list):
        names = set()
        for item in value:
            if isinstance(item, dict):
                names.add(str(item.get("issue_type") or item.get("flag") or item.get("message") or "unknown_flag"))
            else:
                names.add(str(item))
        return names
    if isinstance(value, str):
        return {part.strip() for part in value.replace(";", "|").split("|") if part.strip()}
    return {str(value)}


def _has_unparsed_numeric_field(event: dict[str, Any]) -> bool:
    return (
        str(event.get("parse_status") or "").lower() == "unparsed"
        or str(event.get("concentration_parse_status") or "").lower() == "unparsed"
    )


def _factor_name(event: dict[str, Any]) -> str:
    return str(
        event.get("normalized_name")
        or event.get("standardized_name")
        or event.get("factor_name")
        or event.get("factor")
        or event.get("reagent")
        or event.get("reagent_name")
        or ""
    ).strip()


def _factor_search_text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in (
            "normalized_name",
            "normalized_family",
            "standardized_name",
            "factor_name",
            "value_raw",
            "concentration_raw",
            "timing",
            "operation",
        )
    ).lower()


def _factor_identity_text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in ("normalized_name", "normalized_family", "standardized_name", "factor_name")
    ).lower()


def _event_text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in (
            "factor_type",
            "action_type",
            "factor_name",
            "standardized_name",
            "normalized_name",
            "normalized_family",
            "operation",
            "timing",
            "value_raw",
        )
    )


def _matches_aliases(text: str, aliases: tuple[str, ...]) -> bool:
    return any(alias.strip().lower() in text for alias in aliases)


def _feature_name(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    name = "".join(chars).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    return name


def _reagent_feature_key_map(events: list[dict[str, Any]]) -> dict[str, str]:
    labels_by_slug: dict[str, set[str]] = defaultdict(set)
    for event in events:
        if str(event.get("factor_type") or "") != "reagent":
            continue
        label = _factor_name(event)
        slug = _feature_name(label)
        if slug:
            labels_by_slug[slug].add(label)

    key_by_label: dict[str, str] = {}
    for slug, labels in labels_by_slug.items():
        if len(labels) == 1:
            label = next(iter(labels))
            key_by_label[label] = slug
            continue
        for label in labels:
            digest = hashlib.sha1(label.lower().encode("utf-8")).hexdigest()[:8]
            key_by_label[label] = f"{slug}__id_{digest}"
    return key_by_label


def _event_reagent_feature_key(event: dict[str, Any], reagent_feature_keys: dict[str, str]) -> str:
    label = _factor_name(event)
    return reagent_feature_keys.get(label, _feature_name(label))


def _metadata_columns() -> tuple[str, ...]:
    return (
        "protocol_id",
        "paper_id",
        "dataset_id",
        "protocol_name",
        "source_identity_status",
        "confidence_score",
        "comparison_ready",
        "ordered_reagent_program",
    )


def _stage_count(protocol: dict[str, Any], stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> int:
    from_protocol = _safe_float(protocol.get("number_of_stages"), None)
    if from_protocol is not None:
        return int(from_protocol)
    if stages:
        return len(stages)
    return len({event.get("stage_id") for event in events if event.get("stage_id")})


def _total_days(protocol: dict[str, Any], stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> float | None:
    from_protocol = _safe_float(protocol.get("total_duration_days"), None)
    if from_protocol is not None:
        return from_protocol
    ends = [_safe_float(stage.get("day_end"), None) for stage in stages] + [
        _safe_float(event.get("day_end"), None) for event in events
    ]
    values = [value for value in ends if value is not None]
    return max(values) if values else None


def _mean_stage_duration(stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> float | None:
    durations = [_safe_float(stage.get("duration_days"), None) for stage in stages]
    if not any(value is not None for value in durations):
        durations = [_safe_float(event.get("duration_days"), None) for event in events]
    values = [value for value in durations if value is not None]
    return round(sum(values) / len(values), 3) if values else None


def _max_stage_duration(stages: list[dict[str, Any]], events: list[dict[str, Any]]) -> float | None:
    durations = [_safe_float(stage.get("duration_days"), None) for stage in stages]
    if not any(value is not None for value in durations):
        durations = [_safe_float(event.get("duration_days"), None) for event in events]
    values = [value for value in durations if value is not None]
    return max(values) if values else None


def _standardized_concentration_fields(event: dict[str, Any]) -> dict[str, Any]:
    value = _safe_float(event.get("concentration_value"), None)
    unit = str(event.get("concentration_unit") or event.get("unit") or "").strip()
    if value is None:
        return {
            "concentration_standard_value": "",
            "concentration_standard_unit": "",
            "concentration_comparability_status": "missing_value",
        }
    normalized_unit = _normalize_concentration_unit(unit)
    unit_map = {
        "ng/ml": ("ug/ml", 0.001),
        "ug/ml": ("ug/ml", 1.0),
        "mg/ml": ("ug/ml", 1000.0),
        "nm": ("um", 0.001),
        "um": ("um", 1.0),
        "mm": ("um", 1000.0),
        "%": ("%", 1.0),
    }
    if not normalized_unit:
        status = "unit_missing"
        return {
            "concentration_standard_value": "",
            "concentration_standard_unit": "",
            "concentration_comparability_status": status,
        }
    if normalized_unit not in unit_map:
        return {
            "concentration_standard_value": "",
            "concentration_standard_unit": normalized_unit,
            "concentration_comparability_status": "unsupported_unit",
        }
    standard_unit, multiplier = unit_map[normalized_unit]
    return {
        "concentration_standard_value": round(value * multiplier, 9),
        "concentration_standard_unit": standard_unit,
        "concentration_comparability_status": "standardized",
    }


def _normalize_concentration_unit(unit: str) -> str:
    text = unit.lower().strip().replace("μ", "u").replace("µ", "u")
    text = text.replace(" ", "")
    aliases = {
        "ng/ml": "ng/ml",
        "ng/mL".lower(): "ng/ml",
        "ngml": "ng/ml",
        "ug/ml": "ug/ml",
        "ug/mL".lower(): "ug/ml",
        "ugml": "ug/ml",
        "microgram/ml": "ug/ml",
        "mg/ml": "mg/ml",
        "mg/mL".lower(): "mg/ml",
        "mgml": "mg/ml",
        "nm": "nm",
        "nmol/l": "nm",
        "nanomolar": "nm",
        "um": "um",
        "umol/l": "um",
        "umolar": "um",
        "micromolar": "um",
        "mm": "mm",
        "mmol/l": "mm",
        "millimolar": "mm",
        "%": "%",
        "percent": "%",
    }
    return aliases.get(text, text)


def _max_comparable_concentration(events: list[dict[str, Any]]) -> tuple[float | None, str, str]:
    standardized_events = [
        event for event in events if str(event.get("concentration_comparability_status") or "") == "standardized"
    ]
    if not standardized_events:
        statuses = sorted(
            {
                str(event.get("concentration_comparability_status") or "missing_value")
                for event in events
                if event.get("concentration_value") not in (None, "")
            }
        )
        return None, "", "|".join(statuses) if statuses else "not_reported"
    units = {str(event.get("concentration_standard_unit") or "") for event in standardized_events}
    if len(units) != 1:
        return None, "|".join(sorted(units)), "mixed_standard_units_not_comparable"
    values = [_safe_float(event.get("concentration_standard_value"), None) for event in standardized_events]
    present_values = [value for value in values if value is not None]
    return (max(present_values), next(iter(units)), "standardized") if present_values else (None, "", "missing_value")


def _total_exposure_days(events: list[dict[str, Any]]) -> float:
    intervals: set[tuple[float, float]] = set()
    fallback_count = 0
    for event in events:
        start = _safe_float(event.get("day_start"), None)
        end = _safe_float(event.get("day_end"), None)
        if start is not None and end is not None and end >= start:
            intervals.add((start, end))
        else:
            fallback_count += 1
    exposure = sum(end - start for start, end in intervals)
    return exposure if intervals else float(fallback_count)


def _keyword_count(text_blocks: list[str], keywords: tuple[str, ...]) -> int:
    return sum(1 for text in text_blocks if any(keyword in text.lower() for keyword in keywords))


def _binary_any(text: str, keywords: tuple[str, ...]) -> int:
    return int(any(keyword in text for keyword in keywords))


def _safe_float(value: Any, default: float | None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _blank_if_none(value: Any) -> Any:
    return "" if value is None else value


def _has_text(value: Any) -> bool:
    return bool(str(value or "").strip())


def _first_non_empty(rows: list[dict[str, Any]], key: str) -> Any:
    for row in rows:
        if _has_text(row.get(key)):
            return row.get(key)
    return ""


def _min_numeric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := _safe_float(row.get(key), None)) is not None]
    return min(values) if values else None


def _max_numeric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := _safe_float(row.get(key), None)) is not None]
    return max(values) if values else None


def _truthy_feature(value: Any) -> bool:
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "") not in {"", "0", "False", "false"}


def _norm_text(value: str) -> str:
    return " ".join(value.replace("\\", "/").split("/")[-1].lower().split())


def _is_accepted_protocol(protocol_id: str, accepted_protocol_ids: set[str]) -> bool:
    return bool(protocol_id) and protocol_id in accepted_protocol_ids


def _compact_event(event: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "event_id",
        "protocol_id",
        "paper_id",
        "dataset_id",
        "stage_id",
        "stage_index",
        "day_start",
        "day_end",
        "duration_days",
        "factor_type",
        "factor_name",
        "standardized_name",
        "value_raw",
        "concentration_value",
        "concentration_unit",
        "unit",
        "operation",
        "confidence",
        "validation_status",
        "parse_status",
        "evidence_quote",
        "source_doc_id",
    ]
    return {key: event.get(key) for key in keys if key in event}
