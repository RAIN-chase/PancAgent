from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FactorType(str, Enum):
    REAGENT = "reagent"
    MEDIUM_BASE = "medium_base"
    MATRIX_OR_COATING = "matrix_or_coating"
    CULTURE_FORMAT = "culture_format"
    OPERATION = "operation"
    MECHANICAL_CONDITION = "mechanical_condition"
    AGGREGATION = "aggregation"
    SORTING = "sorting"
    OXYGEN_CONDITION = "oxygen_condition"
    TRANSPLANTATION = "transplantation"
    CELL_STATE_TRANSITION = "cell_state_transition"
    QC_MARKER = "qc_marker"
    ASSAY = "assay"
    OTHER = "other"


class ActionType(str, Enum):
    ADD_REAGENT = "add_reagent"
    SET_MEDIUM = "set_medium"
    SET_MATRIX_OR_COATING = "set_matrix_or_coating"
    SET_CULTURE_FORMAT = "set_culture_format"
    CULTURE = "culture"
    WASH = "wash"
    DISSOCIATE = "dissociate"
    TRANSFER_OR_SEED = "transfer_or_seed"
    AGGREGATE = "aggregate"
    CELL_STATE_TRANSITION = "cell_state_transition"
    ASSAY = "assay"
    OPERATION = "operation"
    PROTOCOL_ACTION = "protocol_action"


class StrategyModule(str, Enum):
    DEFINITIVE_ENDODERM_INDUCTION = "definitive_endoderm_induction"
    PRIMITIVE_GUT_TUBE_PATTERNING = "primitive_gut_tube_patterning"
    POSTERIOR_FOREGUT_PATTERNING = "posterior_foregut_patterning"
    PANCREATIC_PROGENITOR_SPECIFICATION = "pancreatic_progenitor_specification"
    ENDOCRINE_INDUCTION = "endocrine_induction"
    BETA_CELL_MATURATION = "beta_cell_maturation"
    STRESS_REDUCTION_SURVIVAL = "stress_reduction_survival"
    AGGREGATION_MATRIX_REMODELING = "aggregation_matrix_remodeling"
    QC_OR_ASSAY = "qc_or_assay"
    UNKNOWN = "unknown"


class EvidenceSpan(BaseModel):
    model_config = ConfigDict(extra="allow")

    quote: str = ""
    source_doc_id: str = ""
    source_location: str = ""
    entity_id: str = ""
    entity_type: str = ""


class StageRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    stage_id: str
    protocol_id: str | None = None
    stage_index: int | None = None
    stage_name: str = ""
    day_start: float | None = None
    day_end: float | None = None
    duration_days: float | None = None
    input_cell_state: str | None = None
    output_cell_state: str | None = None
    key_operations: str | None = None
    evidence_quote: str = ""

    @field_validator("stage_id")
    @classmethod
    def stage_id_required(cls, value: str) -> str:
        if not value:
            raise ValueError("stage_id is required")
        return value

    @model_validator(mode="after")
    def require_sort_or_time_signal(self) -> "StageRecord":
        if self.stage_index is None and self.day_start is None and self.day_end is None and self.duration_days is None:
            raise ValueError("stage must have stage_index or at least one temporal field")
        return self


class FactorEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    protocol_id: str
    stage_id: str | None = None
    stage_index: int | None = None
    day_start: float | None = None
    day_end: float | None = None
    duration_days: float | None = None
    factor_type: FactorType
    factor_name: str = ""
    standardized_name: str = ""
    concentration_value: float | None = None
    concentration_unit: str | None = None
    unit: str | None = None
    parse_status: str = ""
    concentration_parse_status: str = ""
    evidence_quote: str = ""
    validation_status: str = ""

    @field_validator("protocol_id")
    @classmethod
    def protocol_id_required(cls, value: str) -> str:
        if not value:
            raise ValueError("protocol_id is required")
        return value

    @model_validator(mode="after")
    def require_key_factor_evidence(self) -> "FactorEvent":
        if self.factor_type in {
            FactorType.REAGENT,
            FactorType.MEDIUM_BASE,
            FactorType.MATRIX_OR_COATING,
            FactorType.CULTURE_FORMAT,
            FactorType.OPERATION,
            FactorType.MECHANICAL_CONDITION,
            FactorType.AGGREGATION,
        } and not self.evidence_quote:
            raise ValueError("key factor event requires evidence_quote")
        return self


class ProtocolRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    protocol_id: str
    paper_id: str | None = None
    comparison_ready: bool = True
    protocol: dict[str, Any] = Field(default_factory=dict)
    stages: list[dict[str, Any]] = Field(default_factory=list)
    validation_flags: list[Any] = Field(default_factory=list)

    @field_validator("protocol_id")
    @classmethod
    def protocol_id_required(cls, value: str) -> str:
        if not value:
            raise ValueError("protocol_id is required")
        return value


class NormalizedFactor(BaseModel):
    protocol_id: str
    stage_id: str | None = None
    original_name: str
    normalized_name: str
    normalized_family: str
    factor_type: FactorType
    pathway: str | None = None
    action_type: ActionType
    day_start: float | None = None
    day_end: float | None = None
    duration_days: float | None = None
    concentration_value: float | None = None
    concentration_unit: str | None = None
    evidence_quote: str
    confidence: Confidence
    normalization_source: Literal["rule", "llm", "fallback"]
    validation_status: Literal["valid", "fallback", "invalid"] = "valid"
    validation_notes: list[str] = Field(default_factory=list)


class TemporalAnnotation(BaseModel):
    protocol_id: str
    stage_id: str
    day_start: float | None = None
    day_end: float | None = None
    duration_days: float | None = None
    temporal_confidence: Confidence
    evidence_quote: str
    source: Literal["rule", "llm", "fallback"]
    validation_notes: list[str] = Field(default_factory=list)


class SemanticStageAnnotation(BaseModel):
    protocol_id: str
    stage_id: str
    stage_index: int | None = None
    biological_role: StrategyModule
    strategy_modules: list[StrategyModule]
    key_factors: list[str] = Field(default_factory=list)
    culture_system: str | None = None
    evidence_quote: str
    confidence: Confidence
    source: Literal["rule", "llm", "fallback"]
    validation_notes: list[str] = Field(default_factory=list)


class ProtocolStrategySummary(BaseModel):
    protocol_id: str
    strategy_modules: list[StrategyModule]
    culture_system: str
    key_maturation_factors: list[str] = Field(default_factory=list)
    main_risks: list[str] = Field(default_factory=list)
    evidence_confidence: Confidence
    source: Literal["rule", "llm", "fallback"]


class AuditFinding(BaseModel):
    severity: Literal["low", "medium", "high"]
    issue_type: str
    protocol_id: str
    stage_id: str | None = None
    factor_name: str | None = None
    message: str
    evidence_quote: str = ""
    check_source: Literal["rule", "llm", "schema"]


class FeatureMatrixAuditFinding(BaseModel):
    severity: Literal["low", "medium", "high"]
    issue_type: str
    protocol_id: str
    feature: str
    message: str
    current_value: Any = None
    evidence_quote: str = ""
    check_source: Literal["rule", "llm"]
    confidence: Confidence = Confidence.LOW


class FeatureMatrixRepairCandidate(BaseModel):
    protocol_id: str
    feature: str
    candidate_event_ids: list[str] = Field(default_factory=list)
    candidate_stage_ids: list[str] = Field(default_factory=list)
    parsed_fields: dict[str, Any] = Field(default_factory=dict)
    evidence_quote: str
    confidence: Confidence
    repair_source: Literal["llm"]
    validation_status: Literal["valid", "rejected"] = "valid"
    validation_notes: list[str] = Field(default_factory=list)


class FeatureSpec(BaseModel):
    feature_name: str
    feature_type: Literal["binary", "count", "numeric_max", "numeric_min", "numeric_sum", "categorical", "text_flag"]
    level: Literal["protocol", "stage", "factor", "operation"]
    biological_category: str
    rationale: str
    trigger_patterns: list[str]
    value_rule: str
    source_fields: list[str]
    required_evidence: bool = True
    confidence: Confidence
    proposed_by: Literal["rule", "llm", "human"]
    approved_status: Literal["approved", "candidate", "rejected"] = "candidate"

    @field_validator("feature_name")
    @classmethod
    def feature_name_must_be_machine_safe(cls, value: str) -> str:
        if not value:
            raise ValueError("feature_name is required")
        if not value.replace("_", "").isalnum() or value[0].isdigit():
            raise ValueError("feature_name must be snake_case-like and cannot start with a digit")
        return value

    @model_validator(mode="after")
    def require_trigger_patterns(self) -> "FeatureSpec":
        if not self.trigger_patterns:
            raise ValueError("trigger_patterns are required")
        if not self.source_fields:
            raise ValueError("source_fields are required")
        return self


class FeatureSpecSet(BaseModel):
    protocol_id: str
    proposed_features: list[FeatureSpec]
