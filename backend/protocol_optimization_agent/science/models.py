from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScientificRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)


class ProtocolState(ScientificRecord):
    protocol_id: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    representation_status: Literal["structured_artifacts", "extraction_pending"] = "structured_artifacts"


class PhenotypeMetric(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: str
    family: Literal["cell_identity", "development", "maturity", "quality", "expression", "regulation"]
    endpoint: str
    value: float | None = None
    unit: str = "source_scale"
    status: str
    method: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)


class PhenotypeState(ScientificRecord):
    protocol_id: str
    dataset_id: str
    sample_id: str = ""
    condition_id: str = ""
    metrics: list[PhenotypeMetric] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    validation_status: str = "unknown"
    evidence_ids: list[str] = Field(default_factory=list)


class Evidence(ScientificRecord):
    kind: Literal["protocol", "phenotype", "association", "literature", "experiment_result"]
    source_refs: list[dict[str, Any]] = Field(min_length=1)
    run_id: str
    trace_id: str
    quality: Literal["passed", "failed", "unknown"] = "unknown"
    observations: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_source_identity(self) -> Evidence:
        if any(
            not any(
                ref.get(key) for key in ("path", "artifact_id", "dataset_id", "protocol_id", "doi", "source_doc_id")
            )
            for ref in self.source_refs
        ):
            raise ValueError("Every evidence reference requires a source or artifact identity.")
        return self


class Claim(ScientificRecord):
    statement: str = Field(min_length=1)
    evidence_for: list[str] = Field(default_factory=list)
    evidence_against: list[str] = Field(default_factory=list)
    status: Literal["SUPPORTED", "REFUTED", "UNRESOLVED"] = "UNRESOLVED"
    confidence: Literal["low", "moderate", "high"] = "low"
    scope: dict[str, Any] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)


class Hypothesis(Claim):
    feature: str
    objective: str
    topic_id: str = ""
    test_spec: dict[str, Any] = Field(default_factory=dict)
    protocol_ids: list[str] = Field(default_factory=list)
    rationale_evidence: list[str] = Field(default_factory=list)
    tested_experiment_ids: list[str] = Field(default_factory=list)
    mechanism_explanation: str = "unknown"
    supporting_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    opposing_evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    applicable_conditions: dict[str, Any] = Field(default_factory=dict)
    expected_change: dict[str, Any] = Field(default_factory=dict)
    falsification_conditions: list[str] = Field(default_factory=list)
    competing_explanations: list[dict[str, Any]] = Field(default_factory=list)


class DecisionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    expected_improvement: float = Field(ge=0, le=1)
    information_gain: float = Field(ge=0, le=1)
    experimental_cost: float = Field(ge=0, le=1)
    risk: float = Field(ge=0, le=1)
    source: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    scale: Literal["ordinal_0_1"] = "ordinal_0_1"


class ExperimentCandidate(ScientificRecord):
    hypothesis_ids: list[str] = Field(min_length=1)
    intervention: dict[str, Any]
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list)
    critique: list[str] = Field(default_factory=list)
    scientific_preconditions: dict[str, Any] = Field(default_factory=dict)
    assessment: DecisionAssessment | None = None
    utility: float | None = None
    rank: int | None = None
    eligible: bool = False
    evaluation_id: str = ""
    evaluation_fingerprint: str = ""
    evaluation_version: int = Field(default=1, ge=1)
    evaluated_at: str = ""
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    approved_evaluation_id: str = ""
    status: Literal["PROPOSED", "APPROVED", "REJECTED", "PERFORMED", "OBSERVED", "ASSESSED"] = "PROPOSED"
    reviewer: str = ""
    review_reason: str = ""
    result_evidence_ids: list[str] = Field(default_factory=list)
    result_phenotype_ids: list[str] = Field(default_factory=list)
    feedback_digest: str = ""


class FeedbackAdmission(ScientificRecord):
    experiment_id: str
    feedback_digest: str
    executed_protocol: ProtocolState
    treatment_sample_ids: list[str] = Field(min_length=1)
    control_protocol_id: str = Field(min_length=1)
    control_protocol: ProtocolState | None = None
    control_sample_ids: list[str] = Field(min_length=1)
    phenotypes: list[PhenotypeState] = Field(default_factory=list)
    researcher: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AnalysisDataset(ScientificRecord):
    source_fingerprint: str
    base_source_fingerprint: str = ""
    protocol_rows: list[dict[str, Any]]
    phenotypes: list[PhenotypeState]
    admitted_experiment_ids: list[str] = Field(default_factory=list)
    policy: str = "reviewed_feedback_plus_source_snapshot; not_causal_inference"


class AnalysisResult(ScientificRecord):
    analysis_dataset_id: str
    result_fingerprint: str
    computation_fingerprint: str = ""
    base_source_fingerprint: str
    objective: dict[str, Any]
    protocol_scores: list[dict[str, Any]]
    associations: list[dict[str, Any]]
    admitted_experiment_ids: list[str]
    previous_result_id: str = ""
    change_summary: dict[str, Any] = Field(default_factory=dict)


class ExperimentFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    evidence: Evidence
    outcomes: dict[str, Literal["supports", "refutes", "inconclusive"]]
    interpretation: str = Field(min_length=1)
    researcher: str = Field(min_length=1)
    phenotypes: list[PhenotypeState] = Field(min_length=1)

    @model_validator(mode="after")
    def experimental_evidence_only(self) -> ExperimentFeedback:
        if self.evidence.kind != "experiment_result" or self.evidence.quality != "passed":
            raise ValueError("Feedback requires QC-passed experimental evidence.")
        if not self.outcomes:
            raise ValueError("At least one hypothesis outcome is required.")
        if self.evidence.observations.get("experiment_id") != self.experiment_id:
            raise ValueError("Feedback evidence must identify the performed experiment.")
        return self
