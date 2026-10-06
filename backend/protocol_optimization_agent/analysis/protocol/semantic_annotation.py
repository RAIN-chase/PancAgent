from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from protocol_optimization_agent.runtime.llm_client import LLMClient, OfflineLLMClient
from protocol_optimization_agent.schemas import (
    Confidence,
    ProtocolStrategySummary,
    SemanticStageAnnotation,
    StrategyModule,
)


def annotate_protocol_semantics(
    protocol_records: list[dict[str, Any]],
    normalized_events: list[dict[str, Any]],
    accepted_protocol_ids: set[str],
    llm_client: LLMClient | None = None,
    max_llm_stages: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    client = llm_client or OfflineLLMClient()
    events_by_stage: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    events_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in normalized_events:
        protocol_id = str(event.get("protocol_id") or "")
        stage_id = str(event.get("stage_id") or "")
        events_by_stage[(protocol_id, stage_id)].append(event)
        events_by_protocol[protocol_id].append(event)

    annotations: list[dict[str, Any]] = []
    llm_calls = 0
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        for stage in sorted(
            record.get("stages") or [], key=lambda item: _safe_float(item.get("stage_index"), 9999) or 9999
        ):
            candidate = _rule_stage_annotation(
                protocol_id, stage, events_by_stage[(protocol_id, str(stage.get("stage_id") or ""))]
            )
            if candidate["confidence"] == Confidence.LOW and llm_calls < max_llm_stages:
                llm_candidate = _llm_stage_annotation(
                    protocol_id, stage, events_by_stage[(protocol_id, str(stage.get("stage_id") or ""))], client
                )
                llm_calls += 1
                if llm_candidate is not None:
                    candidate = llm_candidate
            try:
                annotations.append(SemanticStageAnnotation.model_validate(candidate).model_dump(mode="json"))
            except ValidationError:
                candidate["biological_role"] = StrategyModule.UNKNOWN
                candidate["strategy_modules"] = [StrategyModule.UNKNOWN]
                candidate["confidence"] = Confidence.LOW
                candidate["source"] = "fallback"
                candidate.setdefault("validation_notes", []).append("semantic_annotation_schema_fallback")
                annotations.append(SemanticStageAnnotation.model_validate(candidate).model_dump(mode="json"))

    summaries = _build_protocol_strategy_summaries(
        protocol_records, annotations, events_by_protocol, accepted_protocol_ids
    )
    return annotations, {"protocols": summaries}, {"llm_calls": llm_calls}


def _rule_stage_annotation(protocol_id: str, stage: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join(
        [
            str(stage.get("stage_name") or ""),
            str(stage.get("input_cell_state") or ""),
            str(stage.get("output_cell_state") or ""),
            str(stage.get("key_operations") or ""),
            " ".join(str(event.get("normalized_family") or "") for event in events),
            " ".join(str(event.get("normalized_name") or "") for event in events),
        ]
    ).lower()
    modules: list[StrategyModule] = []
    if any(token in text for token in ("definitive endoderm", "sox17", "foxa2", "activin")):
        modules.append(StrategyModule.DEFINITIVE_ENDODERM_INDUCTION)
    if any(token in text for token in ("primitive gut", "posterior foregut", "kgf", "fgf", "retinoic acid", "sant")):
        modules.append(StrategyModule.POSTERIOR_FOREGUT_PATTERNING)
    if any(token in text for token in ("pancreatic progenitor", "pdx1", "nkx6.1", "pancreatic endoderm")):
        modules.append(StrategyModule.PANCREATIC_PROGENITOR_SPECIFICATION)
    if any(token in text for token in ("endocrine", "ngn3", "neurog3", "gamma-secretase", "notch")):
        modules.append(StrategyModule.ENDOCRINE_INDUCTION)
    if any(token in text for token in ("maturation", "mature", "t3", "heparin", "zinc", "betacellulin", "alk5")):
        modules.append(StrategyModule.BETA_CELL_MATURATION)
    if any(token in text for token in ("rock", "y-27632", "survival", "stress")):
        modules.append(StrategyModule.STRESS_REDUCTION_SURVIVAL)
    if any(token in text for token in ("aggregate", "suspension", "matrigel", "laminin", "matrix", "shaker")):
        modules.append(StrategyModule.AGGREGATION_MATRIX_REMODELING)
    if not modules:
        modules = [StrategyModule.UNKNOWN]

    confidence = (
        Confidence.HIGH if modules != [StrategyModule.UNKNOWN] and stage.get("evidence_quote") else Confidence.LOW
    )
    culture_system = _culture_system(stage, events)
    return {
        "protocol_id": protocol_id,
        "stage_id": stage.get("stage_id") or "",
        "stage_index": stage.get("stage_index"),
        "biological_role": modules[0],
        "strategy_modules": _dedupe_modules(modules),
        "key_factors": sorted(
            {str(event.get("normalized_name") or "") for event in events if event.get("normalized_name")}
        )[:20],
        "culture_system": culture_system,
        "evidence_quote": stage.get("evidence_quote") or _first_event_quote(events),
        "confidence": confidence,
        "source": "rule" if confidence != Confidence.LOW else "fallback",
        "validation_notes": [] if confidence != Confidence.LOW else ["low_semantic_signal"],
    }


def _llm_stage_annotation(
    protocol_id: str,
    stage: dict[str, Any],
    events: list[dict[str, Any]],
    client: LLMClient,
) -> dict[str, Any] | None:
    response = client.complete_json(
        task="protocol_semantic_interpreter",
        schema_name="SemanticStageAnnotation",
        payload={
            "protocol_id": protocol_id,
            "stage": stage,
            "normalized_events": events[:30],
            "evidence_quote": stage.get("evidence_quote") or _first_event_quote(events),
        },
    )
    if response is None:
        return None
    response.setdefault("protocol_id", protocol_id)
    response.setdefault("stage_id", stage.get("stage_id") or "")
    response.setdefault("stage_index", stage.get("stage_index"))
    response.setdefault("evidence_quote", stage.get("evidence_quote") or _first_event_quote(events))
    response.setdefault("source", "llm")
    return response


def _build_protocol_strategy_summaries(
    protocol_records: list[dict[str, Any]],
    annotations: list[dict[str, Any]],
    events_by_protocol: dict[str, list[dict[str, Any]]],
    accepted_protocol_ids: set[str],
) -> list[dict[str, Any]]:
    annotations_by_protocol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for annotation in annotations:
        annotations_by_protocol[str(annotation.get("protocol_id") or "")].append(annotation)

    summaries: list[dict[str, Any]] = []
    for record in protocol_records:
        protocol = record.get("protocol") or {}
        protocol_id = str(record.get("protocol_id") or protocol.get("protocol_id") or "")
        if not _is_accepted_protocol(protocol_id, accepted_protocol_ids):
            continue
        modules = _dedupe_modules(
            StrategyModule(module)
            for annotation in annotations_by_protocol.get(protocol_id, [])
            for module in annotation.get("strategy_modules", [])
        )
        if not modules:
            modules = [StrategyModule.UNKNOWN]
        events = events_by_protocol.get(protocol_id, [])
        key_maturation_factors = sorted(
            {
                str(event.get("normalized_name") or "")
                for event in events
                if "maturation" in str(event.get("normalized_family") or "").lower()
                or _is_maturation_factor_name(str(event.get("normalized_name") or ""))
            }
        )
        main_risks = []
        if any(module == StrategyModule.UNKNOWN for module in modules):
            main_risks.append("unknown stage biological role")
        if any(event.get("validation_status") == "fallback" for event in events):
            main_risks.append("fallback factor normalization present")
        summary = ProtocolStrategySummary.model_validate(
            {
                "protocol_id": protocol_id,
                "strategy_modules": modules,
                "culture_system": _protocol_culture_system(protocol, annotations_by_protocol.get(protocol_id, [])),
                "key_maturation_factors": key_maturation_factors[:20],
                "main_risks": main_risks,
                "evidence_confidence": Confidence.HIGH if not main_risks else Confidence.MEDIUM,
                "source": "rule",
            }
        )
        summaries.append(summary.model_dump(mode="json"))
    return summaries


def _is_maturation_factor_name(name: str) -> bool:
    normalized = name.lower().replace("-", " ").replace("_", " ")
    tokens = {token.strip("()[];,.:") for token in normalized.split()}
    return bool({"t3", "heparin", "zinc", "znso4", "alk5", "alk5i", "betacellulin"} & tokens)


def _culture_system(stage: dict[str, Any], events: list[dict[str, Any]]) -> str:
    text = " ".join(
        [
            str(stage.get("culture_format") or ""),
            str(stage.get("key_operations") or ""),
            " ".join(str(e.get("normalized_name") or "") for e in events),
        ]
    ).lower()
    if any(token in text for token in ("2d", "adherent", "plate", "monolayer")) and any(
        token in text for token in ("3d", "suspension", "aggregate", "ula")
    ):
        return "2d_to_3d_transition"
    if any(token in text for token in ("suspension", "aggregate", "ula", "shaker")):
        return "3d_or_suspension"
    if any(token in text for token in ("2d", "adherent", "plate", "monolayer")):
        return "2d_adherent"
    return "not_reported"


def _protocol_culture_system(protocol: dict[str, Any], annotations: list[dict[str, Any]]) -> str:
    labels = {str(annotation.get("culture_system") or "") for annotation in annotations}
    protocol_text = str(protocol.get("culture_format") or "").lower()
    if "2d_to_3d_transition" in labels:
        return "2d_to_3d_transition"
    if "3d_or_suspension" in labels or "suspension" in protocol_text:
        return "3d_or_suspension"
    if "2d_adherent" in labels or "adherent" in protocol_text:
        return "2d_adherent"
    return "not_reported"


def _dedupe_modules(modules: Any) -> list[StrategyModule]:
    deduped: list[StrategyModule] = []
    for module in modules:
        current = module if isinstance(module, StrategyModule) else StrategyModule(str(module))
        if current not in deduped:
            deduped.append(current)
    return deduped


def _first_event_quote(events: list[dict[str, Any]]) -> str:
    for event in events:
        quote = str(event.get("evidence_quote") or "")
        if quote:
            return quote
    return ""


def _safe_float(value: Any, default: float | None) -> float | None:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_accepted_protocol(protocol_id: str, accepted_protocol_ids: set[str]) -> bool:
    return bool(protocol_id) and protocol_id in accepted_protocol_ids
