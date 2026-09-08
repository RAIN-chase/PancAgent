from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.protocol_optimizing import ProtocolOptimizingAgent


def test_protocol_optimizing_agent_exports_hypothesis_framework_outputs(tmp_path: Path) -> None:
    protocol_dir = tmp_path / "protocol_outputs"
    data_dir = tmp_path / "data_outputs"
    output_dir = tmp_path / "optimization_outputs"
    protocol_dir.mkdir()
    data_dir.mkdir()

    (protocol_dir / "protocol_feature_matrix.csv").write_text(
        "protocol_id,paper_id,reagent_factor_x,semantic_beta_cell_maturation,graph_n_stages,confidence_score\n"
        "p1,a,0,0,4,0.9\n"
        "p2,b,1,1,5,0.8\n"
        "p3,c,1,1,6,0.7\n"
        "p4,d,0,1,5,0.6\n"
        "p5,e,1,1,5,0.5\n",
        encoding="utf-8",
    )
    (protocol_dir / "stage_factor_matrix_or_events_summary.csv").write_text(
        "protocol_id,stage,stage_index,day_start,day_end,n_factor_events,factors\n"
        "p2,maturation,5,20,30,1,factor_x\n",
        encoding="utf-8",
    )
    (protocol_dir / "filtered_protocol_factor_events.csv").write_text(
        "event_id,protocol_id,stage_id,day_start,day_end,factor_type,factor_name,concentration_value,concentration_unit,evidence_quote\n"
        "e2,p2,maturation,20,30,reagent,factor_x,10,ng/ml,factor x was added\n"
        "e3,p3,maturation,20,30,reagent,factor_x,10,ng/ml,factor x was added\n"
        "e5,p5,maturation,20,30,reagent,factor_x,10,ng/ml,factor x was added\n",
        encoding="utf-8",
    )
    (protocol_dir / "protocol_feature_provenance.json").write_text(
        json.dumps(
            {
                "protocols": {
                    "p2": {
                        "reagent_factor_x": [
                            {
                                "event_id": "e2",
                                "stage_id": "maturation",
                                "quote": "factor x was added during maturation",
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    (data_dir / "maturity_scores.csv").write_text(
        "protocol_id,dataset_id,lineage,maturity_score,status\n"
        "p1,d1,beta,0.2,numeric\n"
        "p2,d2,beta,0.8,numeric\n"
        "p3,d3,beta,0.9,numeric\n"
        "p4,d4,beta,0.4,numeric\n"
        "p5,d5,beta,0.85,numeric\n",
        encoding="utf-8",
    )
    (data_dir / "similarity_scores.csv").write_text(
        "protocol_id,dataset_id,cosine_similarity,status\n"
        "p1,d1,0.3,numeric\n"
        "p2,d2,0.7,numeric\n"
        "p3,d3,0.85,numeric\n"
        "p4,d4,0.45,numeric\n"
        "p5,d5,0.8,numeric\n",
        encoding="utf-8",
    )
    (data_dir / "marker_scores.csv").write_text(
        "protocol_id,dataset_id,marker_set,score,status\n"
        "p1,d1,stress,0.9,numeric\n"
        "p2,d2,stress,0.2,numeric\n"
        "p3,d3,stress,0.1,numeric\n"
        "p4,d4,stress,0.7,numeric\n"
        "p5,d5,stress,0.15,numeric\n",
        encoding="utf-8",
    )
    for filename in [
        "cell_type_composition.csv",
        "gene_program_scores.csv",
        "regulatory_program_scores.csv",
        "trajectory_scores.csv",
    ]:
        (data_dir / filename).write_text("protocol_id,dataset_id,status\n", encoding="utf-8")
    (data_dir / "data_analysis_protocol_summary.csv").write_text(
        "protocol_id,n_datasets\np1,1\np2,1\np3,1\np4,1\np5,1\n",
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
        "optimized_protocol_edits",
        "experimental_validation_plan_md",
        "experimental_validation_plan_json",
    }
    assert expected.issubset(outputs)
    assert all(path.exists() for path in outputs.values())

    associations = list(csv.DictReader(outputs["protocol_phenotype_associations"].open(encoding="utf-8")))
    assert associations
    assert associations[0]["causal_interpretation"] == "association_only"
    assert associations[0]["target_endpoint"] == "objective_weighted_score"

    summaries = list(csv.DictReader(outputs["protocol_phenotype_summary"].open(encoding="utf-8")))
    assert "objective_weighted_score" in summaries[0]

    candidates = list(csv.DictReader(outputs["optimization_candidate_features"].open(encoding="utf-8")))
    candidate_features = {row["feature"] for row in candidates}
    assert "confidence_score" not in candidate_features
    assert candidates[0]["causal_interpretation"] == "hypothesis_requires_experimental_validation"

    proposal = json.loads(outputs["proposed_improved_protocol_json"].read_text(encoding="utf-8"))
    assert proposal["schema_version"] == "optimized-protocol-proposal-v1"
    assert proposal["recommendation_policy"] == "associations_are_hypotheses_not_causal_proof"
    assert proposal["recommendations"][0]["requires_experimental_validation"] == "true"
    assert proposal["protocol_edits"][0]["edit_type"] == "study_variable_before_intervention"
    assert proposal["protocol_edits"][0]["target_stage"] == "maturation"
    assert proposal["protocol_edits"][0]["evidence_refs"]
    assert proposal["protocol_edits"][0]["negative_controls"]

    prediction = json.loads(outputs["counterfactual_protocol_predictions"].read_text(encoding="utf-8"))
    assert prediction["status"] == "similar_protocol_reference_scored"
    assert prediction["prediction_policy"] == "similar-protocol reference only; not a causal or supervised prediction model"
    assert prediction["feature_coverage"]["mean"] == pytest.approx(1.0)
    assert prediction["out_of_distribution"]["status"] == "in_distribution"
    assert prediction["nearest_protocols"]

    phenotype_targets = list(csv.DictReader(outputs["phenotype_targets"].open(encoding="utf-8")))
    stress_target = next(
        row
        for row in phenotype_targets
        if row["protocol_id"] == "p1" and row["endpoint"] == "marker_scores:stress"
    )
    assert stress_target["direction"] == "lower_is_better"
    assert stress_target["normalization"].startswith("minmax_by_endpoint:")
    assert float(stress_target["raw_score"]) == pytest.approx(0.9)
    assert float(stress_target["objective_score"]) == pytest.approx(0.0)
