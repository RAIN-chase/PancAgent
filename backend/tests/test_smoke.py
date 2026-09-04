from __future__ import annotations

import json
from pathlib import Path

from protocol_optimization_agent.config import ProtocolOptimizationConfig
from protocol_optimization_agent.orchestrator import ProtocolOptimizationAgent


def test_orchestrator_smoke(tmp_path: Path) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()

    (input_dir / "protocol_artifacts.jsonl").write_text(
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
    (input_dir / "protocol_factor_events.csv").write_text(
        "protocol_id,stage_id,factor,concentration,unit\np1,s1,Activin A,100,ng/ml\n",
        encoding="utf-8",
    )
    (input_dir / "protocol_database.sqlite").write_bytes(b"")
    (input_dir / "lineage_candidate_edges.csv").write_text("source,target\n", encoding="utf-8")
    (input_dir / "protocol_evidence_coverage.csv").write_text("protocol_id,coverage\np1,1.0\n", encoding="utf-8")
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
                    "protocol_artifacts_jsonl": "protocol_artifacts.jsonl",
                    "protocol_factor_events_csv": "protocol_factor_events.csv",
                    "protocol_database_sqlite": "protocol_database.sqlite",
                    "lineage_candidate_edges_csv": "lineage_candidate_edges.csv",
                    "protocol_evidence_coverage_csv": "protocol_evidence_coverage.csv",
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
    assert (output_dir / "pipeline_run_log.json").exists()
    assert (output_dir / "protocol_optimization" / "proposed_improved_protocol.md").exists()
