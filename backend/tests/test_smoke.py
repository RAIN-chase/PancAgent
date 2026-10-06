from __future__ import annotations

import json
from pathlib import Path

from protocol_optimization_agent.agents.pipeline import ProtocolOptimizationAgent
from protocol_optimization_agent.runtime.config import ProtocolOptimizationConfig


def test_orchestrator_smoke(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    (input_dir / "protocols.jsonl").write_text(
        json.dumps(
            {
                "protocol_id": "p1",
                "confidence": 0.9,
                "comparison_ready": True,
                "validation_flags": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (input_dir / "protocol_h5ad_manifest.csv").write_text(
        "protocol_id,dataset_id,h5ad_path,condition_id,sample_id,cell_type_col,batch_col\n"
        "p1,d1,missing.h5ad,c1,s1,cell_type,batch_id\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "input_dir": str(input_dir),
                "output_dir": str(output_dir),
                "required_inputs": {
                    "protocols_jsonl": "protocols.jsonl",
                    "protocol_h5ad_manifest_csv": "protocol_h5ad_manifest.csv",
                },
                "quality_filters": {"min_protocol_confidence": 0.65, "require_comparison_ready": True},
                "h5ad": {"annotation_cell_type_column": "cell_type"},
                "optimization_objective": {"primary": "increase_beta_like_maturity"},
            }
        ),
        encoding="utf-8",
    )

    state = ProtocolOptimizationAgent(ProtocolOptimizationConfig.from_file(config_path)).run()

    assert not state.missing_inputs
    assert state.config.output_dir.is_relative_to(output_dir)
    assert (state.config.output_dir / "pipeline_run_log.json").exists()
    assert (state.config.output_dir / "protocol_optimization" / "proposed_improved_protocol.md").exists()
    assert (state.config.output_dir / "final_response.json").exists()
    assert (state.config.output_dir / "source/backend/protocol_optimization_agent/agents/pipeline.py").exists()
    assert [step["step"] for step in state.step_log] == [
        "validate_inputs", "load_protocol_artifacts", "load_h5ad_manifest",
        "analyze_protocol_factors", "analyze_h5ad_profiles", "optimize_protocol_from_agent_outputs",
        "generate_improved_protocol", "qc_final_recommendations", "export_results",
    ]
