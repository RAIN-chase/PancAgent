from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from protocol_optimization_agent.analysis.protocol.input_loader import PROTOCOL_INPUT_FILENAME


@dataclass(frozen=True)
class ProtocolOptimizationConfig:
    input_dir: Path
    output_dir: Path
    required_inputs: dict[str, str]
    quality_filters: dict[str, Any]
    llm: dict[str, Any]
    h5ad: dict[str, Any]
    optimization_objective: dict[str, Any]

    @classmethod
    def from_file(cls, path: str | Path) -> ProtocolOptimizationConfig:
        config_path = Path(path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        required_inputs = dict(data["required_inputs"])
        if required_inputs.get("protocols_jsonl") != PROTOCOL_INPUT_FILENAME or any(
            key in required_inputs for key in ("protocol_artifacts_jsonl", "protocol_factor_events_csv", "protocol_database_sqlite",
                                               "lineage_candidate_edges_csv", "protocol_evidence_coverage_csv")
        ):
            raise ValueError('Protocol inputs must use "protocols_jsonl": "protocols.jsonl" only; update the legacy configuration.')
        base = Path.cwd()

        def resolve_dir(value: str) -> Path:
            candidate = Path(value)
            if candidate.is_absolute():
                return candidate
            return (base / candidate).resolve()

        return cls(
            input_dir=resolve_dir(data["input_dir"]),
            output_dir=resolve_dir(data["output_dir"]),
            required_inputs=required_inputs,
            quality_filters=dict(data.get("quality_filters", {})),
            llm=dict(data.get("llm", {"provider": "offline"})),
            h5ad=dict(data.get("h5ad", {})),
            optimization_objective=dict(data.get("optimization_objective", {})),
        )

    def input_path(self, key: str) -> Path:
        return self.input_dir / self.required_inputs[key]
