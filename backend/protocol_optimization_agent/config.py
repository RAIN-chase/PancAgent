from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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
    def from_file(cls, path: str | Path) -> "ProtocolOptimizationConfig":
        config_path = Path(path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        base = Path.cwd()

        def resolve_dir(value: str) -> Path:
            candidate = Path(value)
            if candidate.is_absolute():
                return candidate
            return (base / candidate).resolve()

        return cls(
            input_dir=resolve_dir(data["input_dir"]),
            output_dir=resolve_dir(data["output_dir"]),
            required_inputs=dict(data["required_inputs"]),
            quality_filters=dict(data.get("quality_filters", {})),
            llm=dict(data.get("llm", {"provider": "offline"})),
            h5ad=dict(data.get("h5ad", {})),
            optimization_objective=dict(data.get("optimization_objective", {})),
        )

    def input_path(self, key: str) -> Path:
        return self.input_dir / self.required_inputs[key]
