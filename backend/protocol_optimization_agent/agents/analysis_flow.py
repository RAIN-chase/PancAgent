"""Validated plans and deterministic limits for the conversational analysis loop."""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from protocol_optimization_agent.agents.actions import get_action, validate_action_arguments
from protocol_optimization_agent.runtime.context import content_fingerprint


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    action_id: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def registered_action(self) -> PlanStep:
        action = validate_action_arguments(self.action_id, self.inputs)
        if not set(self.expected_outputs).issubset(action.outputs):
            raise ValueError(f"Undeclared action outputs: {self.action_id}")
        return self


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1)
    target_metrics: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    evidence_gaps: list[str] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def dependency_order(self) -> AnalysisPlan:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen or not set(step.depends_on).issubset(seen):
                raise ValueError("Steps must have unique ids and topologically ordered dependencies")
            seen.add(step.id)
        return self


class ReviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alternative_explanations: list[
        Literal[
            "batch_confounding",
            "stage_difference",
            "association_not_causal",
            "measurement_scale_mismatch",
            "insufficient_replication",
            "unmeasured_cointervention",
            "unknown_mechanism",
        ]
    ] = Field(default_factory=list)
    next_steps: list[PlanStep] = Field(default_factory=list, max_length=4)
    conclusion_refs: list[str] = Field(default_factory=list)


def initialize_budget(request: dict[str, Any]) -> dict[str, Any]:
    limits = {
        "max_tool_calls": 12,
        "max_iterations": 8,
        "max_model_calls": 12,
        "max_seconds": 900,
        "max_model_tokens": 32000,
    }
    defaults = {
        "max_tool_calls": 6,
        "max_iterations": 4,
        "max_model_calls": 6,
        "max_seconds": 300,
        "max_model_tokens": 16000,
    }
    result = {}
    for name, hard_limit in limits.items():
        value = request.get(name, defaults[name])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= hard_limit:
            raise ValueError(f"{name} must be between zero and {hard_limit}")
        if name != "max_seconds" and int(value) != value:
            raise ValueError(f"{name} must be an integer")
        result[name] = value
    cost = request.get("max_model_cost")
    if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float)) or not 0 <= cost <= 100):
        raise ValueError("max_model_cost must be null or between zero and 100")
    return {
        **result,
        "max_model_cost": cost,
        "model_calls": 0,
        "model_tokens": 0,
        "model_cost": 0.0,
        "started_at": time.time(),
        "elapsed_seconds": 0.0,
        "model_usage_known": True,
        "model_cost_known": True,
        "accounting_policy": "Scheduled action calls; nested deterministic operations remain in the run trace."
        " Deadlines are checked between operations, not hard cancellation of an in-flight tool.",
    }


def budget_stop(budget: dict[str, Any], *, model: bool = False, execute: bool = False, iterations: int = 0) -> str:
    if time.time() - budget["started_at"] >= budget["max_seconds"]:
        return "budget_exhausted:time"
    if execute and iterations >= budget["max_tool_calls"]:
        return "budget_exhausted:tool_calls"
    if execute and iterations >= budget["max_iterations"]:
        return "budget_exhausted:iterations"
    if model:
        if budget["model_calls"] >= budget["max_model_calls"]:
            return "budget_exhausted:model_calls"
        if not budget["model_usage_known"]:
            return "budget_exhausted:model_usage_unknown"
        if budget["model_tokens"] >= budget["max_model_tokens"]:
            return "budget_exhausted:model_tokens"
        if budget["max_model_cost"] is not None:
            if not budget["model_cost_known"]:
                return "budget_exhausted:model_cost_unknown"
            if budget["model_cost"] >= budget["max_model_cost"]:
                return "budget_exhausted:model_cost"
    return ""


def parse_object(content: Any) -> dict[str, Any]:
    if isinstance(content, list):
        content = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    text = str(content).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def step_signature(step: dict[str, Any], input_version: str, code_version: str) -> str:
    action = get_action(step["action_id"])
    return content_fingerprint(
        {},
        extra={
            "action_id": action.id,
            "tool_version": action.version,
            "arguments": step["inputs"],
            "input_version": input_version,
            "code_version": code_version,
        },
    )


SUCCESS_STATUSES = {"ok", "ok_cached_result", "optimization_ran", "ok_cached_optimization_outputs"}


def inspect_result(
    result: dict[str, Any], action_id: str, expected_outputs: list[str] | None = None
) -> tuple[bool, list[str], str]:
    status = str(result.get("status") or "")
    gaps = list(result.get("evidence_gaps", []))
    preview = result.get("result_preview", result)
    if isinstance(preview, dict):
        gaps.extend(preview.get("evidence_gaps", []))
    if status not in SUCCESS_STATUSES:
        gaps.append("action_result:" + (status or "missing_status"))
    if action_id.startswith("phenotype."):
        operation = get_action(action_id).operation
        validity = result.get("validation", {}).get("task_validity", {}).get(operation)
        if not validity or not validity.get("valid"):
            gaps.append("requested_task_validation_failed")
        if not isinstance(preview, list) or not preview or any(row.get("status") != "ok" for row in preview):
            gaps.append("requested_numeric_result_incomplete")
    if action_id == "data.assess" and not result.get("validation", {}).get("technical_qc", {}).get("passed"):
        gaps.append("technical_qc_missing_or_failed")
    protocol_result = preview if isinstance(preview, dict) else {}
    payload = protocol_result.get("tool_result", {})
    next_experiments = protocol_result.get("next_experiment_candidates", {})
    logical_outputs = {
        "protocol_candidates": "results" in protocol_result
        or bool(protocol_result.get("resolved_protocols"))
        or protocol_result.get("intent") == "protocol_collection_summary",
        "feature_diff": "feature_differences" in payload,
        "factor_diff": "factor_diff" in payload,
        "stage_diff": "stage_diff" in payload,
        "event_step_diff": "event_step_diff" in payload,
        "numeric_results": "result_preview" in result,
        "phenotype_ir": "phenotype_ir" in result,
        "versioned_world_model": "schema_version" in protocol_result,
        "hypotheses": "hypotheses" in result
        or "hypotheses" in next_experiments
        or "hypothesis" in protocol_result.get("records", {}),
        "conflicts": "conflicts" in protocol_result,
        "history": "history" in protocol_result,
        "critiques": "candidates" in next_experiments,
        "ranking": "ranking_status" in next_experiments,
        "researcher_review_required": next_experiments.get("automatic_execution_allowed") is False,
    }
    for name in expected_outputs or []:
        if name not in result and name not in protocol_result and not logical_outputs.get(name):
            gaps.append("expected_output_missing:" + name)
    if (
        action_id == "protocol.lookup"
        and protocol_result.get("intent") == "find_protocols"
        and not protocol_result.get("results")
    ):
        gaps.append("requested_protocol_not_found")
    if action_id == "protocol.compare" or protocol_result.get("intent") == "compare_protocols":
        details = protocol_result.get("tool_result", {}).get("event_step_diff", {})
        if details.get("detail_status") != "available":
            gaps.append("original_protocol_details_incomplete")

    # Context/run bookkeeping is not new scientific evidence.
    def scientific(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: scientific(item)
                for key, item in value.items()
                if key
                not in {
                    "run_id",
                    "trace_id",
                    "output_dir",
                    "output_files",
                    "scientific_state",
                    "evaluated_at",
                    "timestamp_utc",
                    "retrieved_at_utc",
                    "match_score",
                    "matched_tokens",
                }
            }
        if isinstance(value, list):
            return [scientific(item) for item in value]
        return value

    evidence = protocol_result.get("results", []) if protocol_result.get("intent") == "find_protocols" else preview
    if protocol_result.get("intent") == "find_protocols":
        evidence = [
            {key: value for key, value in row.items() if key not in {"match_score", "matched_tokens"}}
            for row in evidence
        ]
        evidence.sort(key=lambda row: str(row.get("protocol_id", "")))
    digest = content_fingerprint({}, extra=scientific(evidence))
    return not gaps, sorted(set(map(str, gaps))), digest
