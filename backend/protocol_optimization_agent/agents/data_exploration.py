"""One bounded DA code task, not a nested autonomous agent loop."""
from __future__ import annotations

import inspect
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from protocol_optimization_agent.analysis.phenotype.sensitivity import (
    sample_input,
    summarize_samples,
    validate_sensitivity,
)
from protocol_optimization_agent.runtime.archive import atomic_json, file_hash
from protocol_optimization_agent.runtime.code_executor import (
    RUNNER,
    CodeBudget,
    IsolationUnavailable,
    execute_code,
    isolation_environment,
)
from protocol_optimization_agent.runtime.context import RunContext, evidence_response_envelope
from protocol_optimization_agent.runtime.data_catalog import catalog_manifest


class SensitivityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    dataset_id: str = Field(min_length=1, max_length=200)
    dataset_version: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=200)
    metric_column: str = Field(min_length=1, max_length=200)
    sample_column: str = Field(min_length=1, max_length=200)
    replicate_column: str = Field(min_length=1, max_length=200)
    study_column: str = Field(min_length=1, max_length=200)
    group_column: str = Field(default="", max_length=200)
    scale: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, max_length=2000)
    seed: int = Field(default=0, ge=0, le=2**32 - 1)
    candidate_code: str = Field(default="", max_length=64000)
    max_repairs: int = Field(default=1, ge=0, le=1)
    budget: CodeBudget = Field(default_factory=CodeBudget)

    @field_validator("scale")
    @classmethod
    def known_scale(cls, value):
        if value.strip().lower() in {"unknown", "source_scale", "unspecified", "未知"}:
            raise ValueError("Declare the actual metric scale, not an unspecified source scale")
        return value


def recipe_code() -> str:
    return ("import json\nimport random\n" + inspect.getsource(summarize_samples) +
            "\npayload = json.load(open('/task/input.json'))\nrandom.seed(payload['seed'])\n"
            "json.dump(summarize_samples(payload), open('/out/result.json', 'w'), allow_nan=False)\n")


def execute_sensitivity(arguments: dict, context: RunContext) -> dict:
    action = "data.exploratory_sensitivity"
    artifacts = []
    def response(status, *, preview=None, gaps=None, limitations=None, outputs=None, validation=None, evidence=None):
        return evidence_response_envelope(context=context, subagent="DA", status=status,
            question=arguments.get("question", action), selected_operation=action,
            result_preview=preview or {}, output_files=outputs or {}, used_tools=["isolated_sample_sensitivity"], evidence_refs=evidence or [],
            limitations=limitations or [], confidence_caveats=["Exploratory sensitivity, not a causal effect, p-value, confidence interval or protocol ranking.",
                "Biological independence, scale and metric provenance are user-declared; metadata consistency is checked, not experimentally proven.",
                "Single-study support and batch confounding must be evaluated separately; this task does not correct batch effects."],
            validation=validation or {}, extra={"evidence_gaps": gaps or [], "evidence_level": "exploratory",
                "execution_artifacts": artifacts,
                "promotion_status": "unreviewed_script; human tests and scientific review required; never auto-registered"})
    try:
        spec = SensitivityRequest.model_validate(arguments.get("request", {}))
    except ValidationError:
        return response("needs_clarification", gaps=["explicit_dataset_version_sample_design_endpoint_scale_and_rationale_required"],
                        limitations=["Supply valid SensitivityRequest fields; unknown sample relationships or scales are not inferred."])
    # Existing registered gene-expression, gene-program and maturity tools remain the first choice for those tasks.
    row = catalog_manifest(spec.dataset_id, context.user_id, context.project_id, spec.dataset_version)[0]
    job = context.run_root() / "code_tasks" / ("sensitivity_" + uuid4().hex)
    job.mkdir(parents=True, exist_ok=False)
    atomic_json(job / "plan.json", {"task": action, "domain": "DA", "method": "leave_one_replicate_out_mean",
        "request": spec.model_dump(exclude={"candidate_code"}), "input": {"dataset_id": spec.dataset_id, "version": spec.dataset_version},
        "allowed_outputs": ["result.json"], "allowed_methods": ["leave_one_replicate_out_mean"],
        "acceptance": ["explicit_sample_design", "finite_values", "trusted_numeric_recomputation", "unchanged_input"],
        "code_policy": {"DA": "this validated sensitivity method only", "PA": "declarative FeatureSpec/compiler only",
                        "PO": "registered candidate tools; delegate upstream metrics to DA"}})
    outputs = {"code_task_plan": str(job / "plan.json")}
    try:
        environment = isolation_environment()
    except IsolationUnavailable as exc:
        atomic_json(job / "diagnostic.json", {"status": "isolation_unavailable", "message": str(exc)})
        return response("isolation_unavailable", gaps=["required_isolation_backend_unavailable"], outputs=outputs,
                        limitations=[str(exc), "No candidate code was executed on the host."])
    path = Path(row["h5ad_path"])
    def unchanged():
        stat = path.stat()
        if stat.st_size != row["size_bytes"] or stat.st_mtime_ns != row["mtime_ns"]:
            raise ValueError("Published input changed during analysis; republish and rerun")
    try:
        unchanged()
        payload = sample_input(path, spec.model_dump())
        unchanged()
        expected = summarize_samples(payload)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        atomic_json(job / "diagnostic.json", {"status": "scientific_precheck_failed", "message": str(exc)})
        return response("evidence_insufficient", gaps=["sample_design_or_metric_precheck_failed"],
                        limitations=[str(exc)], outputs=outputs)
    atomic_json(job / "sample_input.json", payload)
    input_ref = {"evidence_type": "declared_sample_metric_snapshot", "dataset_id": spec.dataset_id,
                 "dataset_version": spec.dataset_version, "metric_column": spec.metric_column,
                 "size_bytes": row["size_bytes"], "mtime_ns": row["mtime_ns"],
                 "path": str(job / "sample_input.json"), "sha256": file_hash(job / "sample_input.json")}
    atomic_json(job / "input_manifest.json", input_ref)
    atomic_json(job / "environment.json", environment)
    atomic_json(job / "expected.json", expected)
    attempts = []
    started = time.monotonic()
    candidate = spec.candidate_code or recipe_code()
    result = None
    for attempt in range(spec.max_repairs + 1):
        remaining = spec.budget.timeout_seconds - (time.monotonic() - started)
        if remaining < 1:
            break
        task = job / f"attempt_{attempt}"
        task.mkdir()
        # Only these two data files are ever mounted, not the run archive, h5ad, codebase or credentials.
        (task / "candidate.py").write_text(candidate, encoding="utf-8")
        (task / "runner.py").write_text(RUNNER, encoding="utf-8")
        atomic_json(task / "input.json", payload)
        # Rootless containers run as nobody; grant read/traverse, never host-write permission.
        task.chmod(0o755)
        for file in task.iterdir():
            file.chmod(0o444)
        try:
            budget = spec.budget.model_copy(update={"timeout_seconds": max(1, int(remaining))})
            result = execute_code(task, budget, environment)
            validate_sensitivity(result, expected)
            unchanged()
            atomic_json(task / "validated_result.json", result)
            attempts.append({"attempt": attempt, "status": "validated", "script_sha256": file_hash(task / "candidate.py")})
            break
        except (OSError, ValueError, SyntaxError, subprocess.SubprocessError, IsolationUnavailable) as exc:
            result = None
            attempts.append({"attempt": attempt, "status": "failed", "diagnostic": str(exc),
                             "script_sha256": file_hash(task / "candidate.py")})
            if isinstance(exc, (TimeoutError, IsolationUnavailable)):
                break
            # One bounded repair uses the approved recipe, never an unconstrained nested model loop.
            candidate = recipe_code()
    atomic_json(job / "attempts.json", attempts)
    artifacts.extend({**attempt, "script": (job / f"attempt_{attempt['attempt']}" / "candidate.py").read_text(encoding="utf-8"),
                      "environment": environment, "read_only": True} for attempt in attempts)
    outputs.update({"sample_input": str(job / "sample_input.json"), "environment": str(job / "environment.json"),
                    "attempts": str(job / "attempts.json")})
    if result is None:
        return response("analysis_failed", gaps=["isolated_execution_or_numeric_validation_failed"], outputs=outputs,
                        limitations=["Repair or time budget exhausted; failed candidate output is not scientific evidence."])
    final = {**result, "dataset_id": spec.dataset_id, "dataset_version": spec.dataset_version,
             "endpoint": spec.endpoint, "scale": spec.scale, "aggregation": payload["aggregation"],
             "n_cells": payload["n_cells"], "n_samples": payload["n_samples"],
             "n_missing_metric": payload["n_missing_metric"], "n_studies": len({row["study_id"] for row in payload["replicates"]}),
             "seed": spec.seed, "evidence_level": "exploratory"}
    atomic_json(job / "result.json", final)
    outputs["sensitivity_result"] = str(job / "result.json")
    return response("ok", preview=final, outputs=outputs, validation={"scientific_checks": {
        "numeric_recomputed": True, "sample_metadata_consistent": True, "input_version_unchanged": True},
        "inference": "descriptive_declared_biological_replicates_only"},
        evidence=[{**input_ref, "study_id": study} for study in sorted({row["study_id"] for row in payload["replicates"]})])
