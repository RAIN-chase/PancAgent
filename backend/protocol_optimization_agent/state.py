from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from protocol_optimization_agent.config import ProtocolOptimizationConfig


@dataclass
class PipelineState:
    config: ProtocolOptimizationConfig
    run_id: str
    input_paths: dict[str, Path] = field(default_factory=dict)
    missing_inputs: list[str] = field(default_factory=list)
    protocol_records: list[dict[str, Any]] = field(default_factory=list)
    factor_events: list[dict[str, Any]] = field(default_factory=list)
    h5ad_manifest: list[dict[str, Any]] = field(default_factory=list)
    protocol_outputs: dict[str, Path] = field(default_factory=dict)
    h5ad_outputs: dict[str, Path] = field(default_factory=dict)
    integration_outputs: dict[str, Path] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    step_log: list[dict[str, Any]] = field(default_factory=list)

    def log_step(self, step: str, status: str, **details: Any) -> None:
        self.step_log.append({"step": step, "status": status, "details": details})
