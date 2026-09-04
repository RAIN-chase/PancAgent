from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from protocol_optimization_agent.llm_client import LLMClient, OfflineLLMClient
from protocol_optimization_agent.schemas import ActionType, Confidence, FactorType, NormalizedFactor

NORMALIZATION_ONTOLOGY: dict[str, dict[str, Any]] = {
    "TGF-beta/ALK5 inhibition family": {
        "aliases": ("sb431542", "a83-01", "alk5", "alk5i", "alk5 inhibitor", "repsox"),
        "pathway": "TGF-beta/ALK5 inhibition",
        "factor_type": FactorType.REAGENT,
    },
    "TGF-beta/Nodal activation family": {
        "aliases": ("activin", "nodal"),
        "pathway": "TGF-beta/Nodal activation",
        "factor_type": FactorType.REAGENT,
    },
    "WNT activation family": {
        "aliases": ("chir99021", "chir", "wnt3a", "gsk3"),
        "pathway": "WNT activation",
        "factor_type": FactorType.REAGENT,
    },
    "FGF/KGF signaling family": {
        "aliases": ("kgf", "fgf7", "fgf10", "fgf2", "bfgf"),
        "pathway": "FGF signaling",
        "factor_type": FactorType.REAGENT,
    },
    "Retinoic acid signaling family": {
        "aliases": ("retinoic acid", "ttnpb"),
        "pathway": "Retinoic acid signaling",
        "factor_type": FactorType.REAGENT,
    },
    "Notch/gamma-secretase inhibition family": {
        "aliases": ("gamma-secretase", "gamma secretase", "γ-secretase", "gsi", "ro4929097"),
        "pathway": "Notch inhibition",
        "factor_type": FactorType.REAGENT,
    },
    "BMP inhibition family": {
        "aliases": ("ldn", "ldn193189", "noggin", "dorsomorphin"),
        "pathway": "BMP inhibition",
        "factor_type": FactorType.REAGENT,
    },
    "Hedgehog inhibition family": {
        "aliases": ("sant-1", "sant1", "cyclopamine"),
        "pathway": "Hedgehog inhibition",
        "factor_type": FactorType.REAGENT,
    },
    "Matrix/coating family": {
        "aliases": ("matrigel", "laminin", "ecm gel", "geltrex", "collagen"),
        "pathway": "matrix/coating",
        "factor_type": FactorType.MATRIX_OR_COATING,
    },
    "Mechanical/culture format family": {
        "aliases": ("spinner", "orbital", "shaker", "ula", "ultra-low attachment", "suspension"),
        "pathway": "culture format/mechanical condition",
        "factor_type": FactorType.CULTURE_FORMAT,
    },
    "Beta-cell maturation support family": {
        "aliases": ("t3", "triiodothyronine", "heparin", "zinc", "znso4", "betacellulin"),
        "pathway": "beta-cell maturation support",
        "factor_type": FactorType.REAGENT,
    },
}


def normalize_factor_events(
    events: list[dict[str, Any]],
    llm_client: LLMClient | None = None,
    max_llm_events: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    client = llm_client or OfflineLLMClient()
    normalized: list[dict[str, Any]] = []
    audit_events: list[dict[str, Any]] = []
    llm_calls = 0
    for event in events:
        candidate = _rule_normalize_event(event)
        if candidate is None and llm_calls < max_llm_events:
            llm_calls += 1
            candidate = _llm_normalize_event(event, client)
        if candidate is None:
            candidate = _fallback_normalize_event(event)
        try:
            normalized_factor = NormalizedFactor.model_validate(candidate)
            normalized.append(normalized_factor.model_dump(mode="json"))
        except ValidationError as exc:
            fallback = _fallback_normalize_event(event)
            fallback["validation_status"] = "fallback"
            fallback["validation_notes"] = [f"schema_validation_failed: {exc.errors()[0]['msg']}"]
            normalized_factor = NormalizedFactor.model_validate(fallback)
            normalized.append(normalized_factor.model_dump(mode="json"))
            audit_events.append(
                {
                    "severity": "medium",
                    "issue_type": "normalization_schema_fallback",
                    "protocol_id": event.get("protocol_id") or "",
                    "stage_id": event.get("stage_id") or "",
                    "factor_name": _factor_name(event),
                    "message": fallback["validation_notes"][0],
                    "evidence_quote": event.get("evidence_quote") or "",
                    "check_source": "schema",
                }
            )
    return normalized, {"llm_calls": llm_calls, "normalization_audit_events": audit_events}


def _rule_normalize_event(event: dict[str, Any]) -> dict[str, Any] | None:
    factor_type = _coerce_factor_type(event.get("factor_type"))
    identity_text = _factor_identity_text(event)
    context_text = _factor_context_text(event)
    for family, spec in NORMALIZATION_ONTOLOGY.items():
        ontology_type = spec["factor_type"]
        if ontology_type == FactorType.REAGENT and factor_type != FactorType.REAGENT:
            continue
        if ontology_type in {FactorType.MATRIX_OR_COATING, FactorType.CULTURE_FORMAT}:
            text = (
                context_text
                if factor_type
                in {ontology_type, FactorType.OPERATION, FactorType.AGGREGATION, FactorType.MECHANICAL_CONDITION}
                else identity_text
            )
        else:
            text = identity_text
        if any(alias in text for alias in spec["aliases"]):
            return _normalized_payload(
                event,
                normalized_family=family,
                normalized_name=_factor_name(event) or family,
                factor_type=factor_type,
                pathway=spec["pathway"],
                source="rule",
                confidence=Confidence.HIGH,
            )
    return None


def _llm_normalize_event(event: dict[str, Any], client: LLMClient) -> dict[str, Any] | None:
    response = client.complete_json(
        task="factor_event_normalizer",
        schema_name="NormalizedFactor",
        payload={
            "protocol_id": event.get("protocol_id"),
            "stage_id": event.get("stage_id"),
            "factor_type": event.get("factor_type"),
            "factor_name": _factor_name(event),
            "operation": event.get("operation"),
            "timing": event.get("timing"),
            "evidence_quote": event.get("evidence_quote"),
        },
    )
    if response is None:
        return None
    response.setdefault("protocol_id", event.get("protocol_id") or "")
    response.setdefault("stage_id", event.get("stage_id") or "")
    response.setdefault("original_name", _factor_name(event))
    response.setdefault("evidence_quote", event.get("evidence_quote") or "")
    response.setdefault("normalization_source", "llm")
    response.setdefault("validation_status", "valid")
    return response


def _fallback_normalize_event(event: dict[str, Any]) -> dict[str, Any]:
    factor_type = _coerce_factor_type(event.get("factor_type"))
    name = _factor_name(event) or "unknown_factor"
    return _normalized_payload(
        event,
        normalized_family=factor_type.value,
        normalized_name=name,
        factor_type=factor_type,
        pathway=None,
        source="fallback",
        confidence=Confidence.LOW,
        validation_status="fallback",
        validation_notes=["no_rule_or_llm_normalization_available"],
    )


def _normalized_payload(
    event: dict[str, Any],
    *,
    normalized_family: str,
    normalized_name: str,
    factor_type: FactorType,
    pathway: str | None,
    source: str,
    confidence: Confidence,
    validation_status: str = "valid",
    validation_notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "protocol_id": event.get("protocol_id") or "",
        "stage_id": event.get("stage_id") or "",
        "original_name": _factor_name(event) or normalized_name,
        "normalized_name": normalized_name,
        "normalized_family": normalized_family,
        "factor_type": factor_type,
        "pathway": pathway,
        "action_type": _action_type(event),
        "day_start": _safe_float(event.get("day_start")),
        "day_end": _safe_float(event.get("day_end")),
        "duration_days": _safe_float(event.get("duration_days")),
        "concentration_value": _safe_float(event.get("concentration_value")),
        "concentration_unit": event.get("concentration_unit") or event.get("unit") or None,
        "evidence_quote": event.get("evidence_quote") or "",
        "confidence": confidence,
        "normalization_source": source,
        "validation_status": validation_status,
        "validation_notes": validation_notes or [],
    }


def _action_type(event: dict[str, Any]) -> ActionType:
    factor_type = str(event.get("factor_type") or "")
    operation = str(event.get("operation") or "").lower()
    if factor_type == "reagent":
        return ActionType.ADD_REAGENT
    if factor_type == "medium_base":
        return ActionType.SET_MEDIUM
    if factor_type == "matrix_or_coating":
        return ActionType.SET_MATRIX_OR_COATING
    if factor_type == "culture_format":
        return ActionType.SET_CULTURE_FORMAT
    if factor_type == "cell_state_transition":
        return ActionType.CELL_STATE_TRANSITION
    if factor_type in {"assay", "qc_marker"}:
        return ActionType.ASSAY
    if "wash" in operation:
        return ActionType.WASH
    if any(token in operation for token in ("dissociat", "accutase", "trypsin")):
        return ActionType.DISSOCIATE
    if any(token in operation for token in ("transfer", "seed", "resuspend")):
        return ActionType.TRANSFER_OR_SEED
    if any(token in operation for token in ("aggregat", "cluster", "spheroid")):
        return ActionType.AGGREGATE
    if any(token in operation for token in ("culture", "incubat", "differentiat")):
        return ActionType.CULTURE
    return ActionType.OPERATION if operation else ActionType.PROTOCOL_ACTION


def _coerce_factor_type(value: Any) -> FactorType:
    try:
        return FactorType(str(value or "other"))
    except ValueError:
        return FactorType.OTHER


def _factor_name(event: dict[str, Any]) -> str:
    return str(event.get("standardized_name") or event.get("factor_name") or event.get("value_raw") or "").strip()


def _factor_identity_text(event: dict[str, Any]) -> str:
    return " ".join(str(event.get(key) or "") for key in ("standardized_name", "factor_name")).lower()


def _factor_context_text(event: dict[str, Any]) -> str:
    return " ".join(
        str(event.get(key) or "")
        for key in ("standardized_name", "factor_name", "value_raw", "operation", "culture_format")
    ).lower()


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
