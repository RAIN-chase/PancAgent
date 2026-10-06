from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_optimization_agent.runtime.context import content_fingerprint

PROTOCOL_ANALYSIS_REQUIRED_OUTPUTS = (
    "protocol_feature_matrix.csv",
    "stage_factor_matrix_or_events_summary.csv",
    "protocol_feature_provenance.json",
)
DATA_ANALYSIS_REQUIRED_OUTPUTS = (
    "data_analysis_protocol_summary.csv",
    "h5ad_summary_by_protocol.jsonl",
    "data_analysis_validation.json",
)


@dataclass(frozen=True)
class UpstreamOutputStatus:
    agent: str
    output_dir: Path
    required_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    empty_files: tuple[str, ...]
    newest_mtime: float | None
    fingerprint: str
    validation_errors: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.missing_files and not self.empty_files and not self.validation_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "output_dir": str(self.output_dir),
            "ready": self.ready,
            "required_files": list(self.required_files),
            "missing_files": list(self.missing_files),
            "empty_files": list(self.empty_files),
            "newest_mtime": self.newest_mtime,
            "fingerprint": self.fingerprint,
            "validation_errors": list(self.validation_errors),
        }


def build_optimization_orchestration_plan(
    protocol_output_dir: Path,
    data_analysis_output_dir: Path,
    *,
    objective: dict[str, Any] | None = None,
    new_protocol_features: dict[str, Any] | None = None,
    force_protocol_rerun: bool = False,
    force_data_rerun: bool = False,
    force_optimization_rerun: bool = False,
) -> dict[str, Any]:
    protocol_status = inspect_upstream_outputs(
        "protocol_analysis_agent",
        protocol_output_dir,
        PROTOCOL_ANALYSIS_REQUIRED_OUTPUTS,
    )
    data_status = inspect_upstream_outputs(
        "data_analysis_agent",
        data_analysis_output_dir,
        DATA_ANALYSIS_REQUIRED_OUTPUTS,
        required_tasks=_objective_tasks(data_analysis_output_dir, objective or {}),
    )
    rerun_protocol = force_protocol_rerun or not protocol_status.ready
    rerun_data = force_data_rerun or not data_status.ready
    can_run_optimization = not rerun_protocol and not rerun_data
    return {
        "schema_version": "optimization-orchestration-plan-v1",
        "cache_key": content_fingerprint(
            {
                **{f"protocol:{name}": str(protocol_output_dir / name) for name in PROTOCOL_ANALYSIS_REQUIRED_OUTPUTS},
                **{f"data:{name}": str(data_analysis_output_dir / name) for name in DATA_ANALYSIS_REQUIRED_OUTPUTS},
            },
            extra={
                "objective": objective or {},
                "new_protocol_features": new_protocol_features or {},
                "output_contract": "protocol-optimization-v1",
            },
        ),
        "upstream_status": {
            "protocol_analysis_agent": protocol_status.to_dict(),
            "data_analysis_agent": data_status.to_dict(),
        },
        "required_upstream_reruns": _required_reruns(rerun_protocol, rerun_data),
        "can_run_protocol_optimizing_agent": can_run_optimization,
        "should_run_protocol_optimizing_agent": can_run_optimization and force_optimization_rerun,
        "policy": "Protocol Optimizing Agent consumes audited PA/DA outputs; raw protocol or h5ad changes require rerunning the corresponding upstream agent.",
    }


def inspect_upstream_outputs(agent: str, output_dir: Path, required_files: tuple[str, ...],
                             *, required_tasks: tuple[str, ...] = ()) -> UpstreamOutputStatus:
    missing = tuple(file_name for file_name in required_files if not (output_dir / file_name).exists())
    empty = tuple(
        file_name
        for file_name in required_files
        if (output_dir / file_name).exists() and (output_dir / file_name).stat().st_size == 0
    )
    mtimes = [
        (output_dir / file_name).stat().st_mtime for file_name in required_files if (output_dir / file_name).exists()
    ]
    fingerprint = content_fingerprint({file_name: str(output_dir / file_name) for file_name in required_files})
    validation_errors = _validation_errors(agent, output_dir, required_tasks)
    return UpstreamOutputStatus(
        agent=agent,
        output_dir=output_dir,
        required_files=required_files,
        missing_files=missing,
        empty_files=empty,
        newest_mtime=max(mtimes) if mtimes else None,
        fingerprint=fingerprint,
        validation_errors=validation_errors,
    )


def _objective_tasks(output_dir: Path, objective: dict[str, Any]) -> tuple[str, ...]:
    endpoints = objective.get("comparison", {}).get("endpoints", [])
    if not isinstance(endpoints, list) or any(not isinstance(endpoint, str) or not endpoint.strip() for endpoint in endpoints):
        raise ValueError("Optimization endpoints must be a list of nonempty names")
    if not endpoints:
        return ("maturity",)
    table_tasks = {"cell_type_composition": "cell_type_composition", "maturity_scores": "maturity",
                   "similarity_scores": "reference_similarity", "marker_scores": "marker_score",
                   "gene_expression_summary": "gene_expression", "differential_expression": "differential_expression"}
    try:
        ir = json.loads((output_dir / "phenotype_ir.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("endpoint_evidence_missing",)
    tasks: set[str] = set()
    for endpoint in endpoints:
        matching = [metric for phenotype in ir.get("phenotypes", []) for metric in phenotype.get("metrics", [])
                    if metric.get("endpoint") == endpoint and metric.get("value") is not None]
        tasks.update(table_tasks.get(metric.get("context", {}).get("source_table"), "endpoint_task_unknown") for metric in matching)
        if not matching:
            tasks.add("endpoint_evidence_missing:" + str(endpoint))
    return tuple(sorted(tasks))


def _validation_errors(agent: str, output_dir: Path, required_tasks: tuple[str, ...]) -> tuple[str, ...]:
    if agent != "data_analysis_agent":
        return ()
    path = output_dir / "data_analysis_validation.json"
    if not path.exists() or path.stat().st_size == 0:
        return ()
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ("data_analysis_validation_unreadable",)
    if (
        report.get("schema_version") != "data-analysis-validation-v2"
        or report.get("technical_qc", {}).get("passed") is not True
        or any(report.get("task_validity", {}).get(task, {}).get("valid") is not True for task in required_tasks)
    ):
        return ("data_analysis_validation_failed",)
    return ()


def _required_reruns(rerun_protocol: bool, rerun_data: bool) -> list[str]:
    reruns: list[str] = []
    if rerun_protocol:
        reruns.append("protocol_analysis_agent")
    if rerun_data:
        reruns.append("data_analysis_agent")
    return reruns
