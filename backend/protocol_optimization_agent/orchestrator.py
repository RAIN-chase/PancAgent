from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.protocol_analysis import ProtocolAnalysisAgent
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent
from protocol_optimization_agent.config import ProtocolOptimizationConfig
from protocol_optimization_agent.io import ensure_dir, read_csv_dicts, read_jsonl, write_json
from protocol_optimization_agent.llm_client import build_llm_client
from protocol_optimization_agent.state import PipelineState


class ProtocolOptimizationAgent:
    """Lightweight DAG orchestrator for the fixed protocol optimization workflow."""

    def __init__(self, config: ProtocolOptimizationConfig) -> None:
        self.config = config

    def run(self) -> PipelineState:
        state = PipelineState(config=self.config, run_id=_run_id())
        ensure_dir(self.config.output_dir)

        self.validate_inputs(state)
        self.load_protocol_artifacts(state)
        self.load_h5ad_manifest(state)
        self.analyze_protocol_factors(state)
        self.analyze_h5ad_profiles(state)
        self.optimize_protocol_from_agent_outputs(state)
        self.generate_improved_protocol(state)
        self.qc_final_recommendations(state)
        self.export_results(state)
        return state

    def validate_inputs(self, state: PipelineState) -> None:
        for key, filename in self.config.required_inputs.items():
            path = self.config.input_dir / filename
            state.input_paths[key] = path
            if not path.exists():
                state.missing_inputs.append(key)
        status = "warning" if state.missing_inputs else "ok"
        state.log_step("validate_inputs", status, missing_inputs=state.missing_inputs)

    def load_protocol_artifacts(self, state: PipelineState) -> None:
        state.protocol_records = read_jsonl(state.input_paths["protocol_artifacts_jsonl"])
        state.factor_events = read_csv_dicts(state.input_paths["protocol_factor_events_csv"])
        state.log_step(
            "load_protocol_artifacts",
            "ok",
            n_protocol_records=len(state.protocol_records),
            n_factor_events=len(state.factor_events),
        )

    def load_h5ad_manifest(self, state: PipelineState) -> None:
        state.h5ad_manifest = read_csv_dicts(state.input_paths["protocol_h5ad_manifest_csv"])
        state.log_step("load_h5ad_manifest", "ok", n_manifest_rows=len(state.h5ad_manifest))

    def analyze_protocol_factors(self, state: PipelineState) -> None:
        agent = ProtocolAnalysisAgent(
            self.config.output_dir / "protocol_analysis",
            self.config.quality_filters,
            llm_client=build_llm_client(self.config.llm),
        )
        state.protocol_outputs = agent.run(state.protocol_records, state.factor_events)
        state.log_step("analyze_protocol_factors", "ok", outputs=_stringify_paths(state.protocol_outputs))

    def analyze_h5ad_profiles(self, state: PipelineState) -> None:
        agent = DataAnalysisAgent(self.config.output_dir / "data_analysis", self.config.h5ad)
        state.h5ad_outputs = agent.run(state.h5ad_manifest)
        state.log_step("analyze_h5ad_profiles", "ok", outputs=_stringify_paths(state.h5ad_outputs))

    def optimize_protocol_from_agent_outputs(self, state: PipelineState) -> None:
        agent = ProtocolOptimizingAgent(
            self.config.output_dir / "protocol_optimization",
            objective=self.config.optimization_objective,
        )
        protocol_dir = state.protocol_outputs["protocol_feature_matrix"].parent
        h5ad_dir = state.h5ad_outputs["h5ad_summary_by_protocol"].parent
        state.optimization_outputs = agent.run(protocol_dir, h5ad_dir)
        state.log_step(
            "optimize_protocol_from_agent_outputs", "ok", outputs=_stringify_paths(state.optimization_outputs)
        )

    def generate_improved_protocol(self, state: PipelineState) -> None:
        state.log_step(
            "generate_improved_protocol",
            "ok",
            markdown=str(state.optimization_outputs["proposed_improved_protocol_md"]),
            json=str(state.optimization_outputs["proposed_improved_protocol_json"]),
        )

    def qc_final_recommendations(self, state: PipelineState) -> None:
        if state.missing_inputs:
            state.warnings.append("Some required inputs are missing; outputs may contain placeholders.")
        state.log_step("qc_final_recommendations", "ok", warnings=state.warnings)

    def export_results(self, state: PipelineState) -> None:
        state.log_step("export_results", "ok")
        write_json(
            self.config.output_dir / "pipeline_run_log.json",
            {
                "run_id": state.run_id,
                "input_dir": str(self.config.input_dir),
                "output_dir": str(self.config.output_dir),
                "missing_inputs": state.missing_inputs,
                "warnings": state.warnings,
                "llm": _redact_llm_config(self.config.llm),
                "steps": state.step_log,
            },
        )


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _stringify_paths(paths: dict[str, Path]) -> dict[str, str]:
    return {key: str(value) for key, value in paths.items()}


def _redact_llm_config(config: dict[str, object]) -> dict[str, object]:
    redacted = dict(config)
    if "api_key" in redacted:
        redacted["api_key"] = "***"
    return redacted
