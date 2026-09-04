from __future__ import annotations

import csv
import json
from pathlib import Path

from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent


def test_protocol_optimizing_agent_exports_hypothesis_framework_outputs(tmp_path: Path) -> None:
    protocol_dir = tmp_path / "protocol_outputs"
    data_dir = tmp_path / "data_outputs"
    output_dir = tmp_path / "optimization_outputs"
    protocol_dir.mkdir()
    data_dir.mkdir()

    (protocol_dir / "protocol_feature_matrix.csv").write_text(
        "protocol_id,paper_id,reagent_factor_x,semantic_beta_cell_maturation,graph_n_stages\n"
        "p1,a,0,0,4\n"
        "p2,b,1,1,5\n"
        "p3,c,1,1,6\n",
        encoding="utf-8",
    )
    (protocol_dir / "stage_factor_matrix_or_events_summary.csv").write_text(
        "protocol_id,stage_id,factor_name,evidence_quote\np2,s1,factor_x,factor x was added\n",
        encoding="utf-8",
    )
    (protocol_dir / "protocol_feature_provenance.json").write_text(
        json.dumps({"reagent_factor_x": {"evidence_quote": "factor x was added during maturation"}}),
        encoding="utf-8",
    )

    (data_dir / "maturity_scores.csv").write_text(
        "protocol_id,dataset_id,lineage,maturity_score,status\n"
        "p1,d1,beta,0.2,numeric\n"
        "p2,d2,beta,0.8,numeric\n"
        "p3,d3,beta,0.9,numeric\n",
        encoding="utf-8",
    )
    (data_dir / "similarity_scores.csv").write_text(
        "protocol_id,dataset_id,cosine_similarity,status\np1,d1,0.3,numeric\np2,d2,0.7,numeric\np3,d3,0.85,numeric\n",
        encoding="utf-8",
    )
    for filename in [
        "cell_type_composition.csv",
        "marker_scores.csv",
        "gene_program_scores.csv",
        "regulatory_program_scores.csv",
        "trajectory_scores.csv",
    ]:
        (data_dir / filename).write_text("protocol_id,dataset_id,status\n", encoding="utf-8")
    (data_dir / "data_analysis_protocol_summary.csv").write_text(
        "protocol_id,n_datasets\np1,1\np2,1\np3,1\n",
        encoding="utf-8",
    )
    (data_dir / "h5ad_summary_by_protocol.jsonl").write_text(
        '{"protocol_id":"p1","dataset_id":"d1"}\n',
        encoding="utf-8",
    )

    outputs = ProtocolOptimizingAgent(
        output_dir=output_dir,
        objective={"primary": "increase_beta_like_maturity"},
    ).run(
        protocol_dir,
        data_dir,
        new_protocol_features={"reagent_factor_x": 1, "semantic_beta_cell_maturation": 1, "graph_n_stages": 5},
    )

    expected = {
        "optimization_input_gate_report",
        "phenotype_targets",
        "protocol_phenotype_summary",
        "protocol_phenotype_associations",
        "optimization_candidate_features",
        "counterfactual_protocol_predictions",
        "proposed_improved_protocol_md",
        "proposed_improved_protocol_json",
        "factor_recommendation_table",
        "evidence_and_risk_report",
        "experimental_validation_plan_md",
        "experimental_validation_plan_json",
    }
    assert expected.issubset(outputs)
    assert all(path.exists() for path in outputs.values())

    associations = list(csv.DictReader(outputs["protocol_phenotype_associations"].open(encoding="utf-8")))
    assert associations
    assert associations[0]["causal_interpretation"] == "association_only"

    candidates = list(csv.DictReader(outputs["optimization_candidate_features"].open(encoding="utf-8")))
    assert candidates[0]["causal_interpretation"] == "hypothesis_requires_experimental_validation"

    proposal = json.loads(outputs["proposed_improved_protocol_json"].read_text(encoding="utf-8"))
    assert proposal["schema_version"] == "optimized-protocol-proposal-v1"
    assert proposal["recommendation_policy"] == "associations_are_hypotheses_not_causal_proof"
    assert proposal["recommendations"][0]["requires_experimental_validation"] == "true"

    prediction = json.loads(outputs["counterfactual_protocol_predictions"].read_text(encoding="utf-8"))
    assert prediction["status"] == "similarity_scored"
    assert prediction["nearest_protocols"]
