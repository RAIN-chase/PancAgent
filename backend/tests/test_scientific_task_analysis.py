from __future__ import annotations

import json
from pathlib import Path

import pytest
from protocol_optimization_agent.agents.actions import get_action
from protocol_optimization_agent.agents.data_analysis import DataAnalysisAgent
from protocol_optimization_agent.agents.orchestrator import execute_scientific_action
from protocol_optimization_agent.analysis.optimization.experiment_design import design_experiments
from protocol_optimization_agent.analysis.phenotype.endpoint_comparison import compare_endpoint
from protocol_optimization_agent.analysis.phenotype.h5ad_tools import (
    compute_differential_expression,
    inspect_h5ad_dataset,
)
from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine


def replicated_h5ad(tmp_path: Path) -> Path:
    ad = pytest.importorskip("anndata")
    import numpy as np
    import pandas as pd

    obs, values = [], []
    for group, offset in (("a", 1), ("b", 4)):
        for repeat in range(3):
            for cell in range(2):
                obs.append(
                    {
                        "condition": group,
                        "sample_id": f"{group}{repeat}",
                        "bio": f"{group}{repeat}",
                        "study": "study1",
                        "cell_line": "line1",
                        "culture_batch": "batch1",
                        "sequencing_batch": "seq1",
                    }
                )
                values.append([offset + repeat + cell / 10, 6 - repeat + cell / 10])
    adata = ad.AnnData(
        X=np.array(values),
        obs=pd.DataFrame(obs, index=[f"cell{i}" for i in range(len(obs))]),
        var=pd.DataFrame(index=["INS", "GCG"]),
    )
    path = tmp_path / "replicated.h5ad"
    adata.write_h5ad(path)
    return path


def config() -> dict:
    return {"biological_replicate_column": "bio", "replicate_kind": "biological", "expression_scale": "normalized"}


def test_replicate_de_and_bh_use_biological_units_not_cells(tmp_path):
    from scipy.stats import false_discovery_control, ttest_ind

    path = replicated_h5ad(tmp_path)
    rows = compute_differential_expression(
        {"dataset_id": "d1", "h5ad_path": str(path)}, config(), "condition", "a", "b", ["INS", "GCG"]
    )
    ins = next(row for row in rows if row["gene"] == "INS")
    assert ins["n_replicates_a"] == ins["n_replicates_b"] == 3
    assert ins["n_cells_a"] == ins["n_cells_b"] == 6
    assert ins["p_value"] == pytest.approx(ttest_ind([1.05, 2.05, 3.05], [4.05, 5.05, 6.05], equal_var=False).pvalue)
    assert ins["statistical_scope"] == "exploratory_independent_biological_replicate_means"
    assert ins["multiple_testing"] == "benjamini_hochberg"
    assert [row["adjusted_p_value"] for row in rows] == pytest.approx(
        false_discovery_control([row["p_value"] for row in rows])
    )
    assert ins["n_tests"] == 2
    assert len(json.loads(ins["replicate_results"])) == 6
    inspection = inspect_h5ad_dataset({"h5ad_path": str(path)}, config())
    design = inspection["design_metadata"]
    assert design["fields"]["biological_replicate"]["n_distinct"] == 6
    assert design["fields"]["study"]["values"] == ["study1"]
    assert design["replicate_kind"] == "biological"


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"biological_replicate_column": ""}, "identified_biological_replicates_missing"),
        ({"replicate_kind": "technical"}, "replicate_kind_not_confirmed_biological"),
        ({"expression_scale": "counts"}, "scale_requires_validated_count_or_other_model"),
        ({"study_column": "absent"}, "study_metadata_unknown"),
    ],
)
def test_de_missing_design_or_raw_counts_is_descriptive_only(tmp_path, overrides, reason):
    path = replicated_h5ad(tmp_path)
    rows = compute_differential_expression(
        {"h5ad_path": str(path)}, {**config(), **overrides}, "condition", "a", "b", ["INS"]
    )
    assert rows[0]["p_value"] == rows[0]["adjusted_p_value"] == ""
    assert rows[0]["inference_eligible"] is False
    assert reason in rows[0]["statistical_caveats"]


def test_batch_confounding_and_paired_design_block_independent_test(tmp_path):
    anndata = pytest.importorskip("anndata")

    path = replicated_h5ad(tmp_path)
    adata = anndata.read_h5ad(path)
    adata.obs["sequencing_batch"] = adata.obs["condition"].astype(str)
    adata.write_h5ad(path)
    rows = compute_differential_expression({"h5ad_path": str(path)}, config(), "condition", "a", "b", ["INS"])
    assert "sequencing_batch_confounded_with_group" in rows[0]["statistical_caveats"]
    assert rows[0]["p_value"] == ""
    adata.obs["sequencing_batch"] = "seq1"
    adata.obs["bio"] = [f"r{i // 2 % 3}" for i in range(12)]
    adata.write_h5ad(path)
    rows = compute_differential_expression({"h5ad_path": str(path)}, config(), "condition", "a", "b", ["INS"])
    assert "paired_or_overlapping_replicates_require_paired_model" in rows[0]["statistical_caveats"]


def test_composition_validity_does_not_depend_on_maturity_or_other_gene_task(tmp_path):
    path = replicated_h5ad(tmp_path)
    outputs = DataAnalysisAgent(
        tmp_path / "out",
        h5ad_config={
            "annotation_cell_type_column": "condition",
            "requested_tasks": ["cell_type_composition"],
            "query_genes": ["MISSING"],
        },
    ).run([{"protocol_id": "p1", "dataset_id": "d1", "h5ad_path": str(path)}])
    report = json.loads(outputs["data_analysis_validation"].read_text())
    assert report["technical_qc"]["passed"] is True
    assert report["task_validity"]["cell_type_composition"]["valid"] is True
    assert report["task_validity"]["gene_expression"]["valid"] is False
    assert report["task_validity"]["maturity"]["valid"] is False
    assert report["task_validity"]["maturity"]["valid"] is False


def test_complete_protocol_events_and_steps_survive_comparison(tmp_path):
    (tmp_path / "protocol_feature_matrix.csv").write_text(
        "protocol_id,protocol_name,factor_x_presence\np1,A,1\np2,B,0\n"
    )
    events = [
        {"protocol_id": "p1", "normalized_family": "x", "evidence_quote": f"event{index}", "day_start": index}
        for index in range(65)
    ]
    (tmp_path / "normalized_factor_events.jsonl").write_text("\n".join(map(json.dumps, events)))
    (tmp_path / "standardized_protocol_representations.jsonl").write_text(
        json.dumps({"protocol_id": "p1", "stages": [{"note": "original"}]})
    )
    result = ProtocolQueryEngine(tmp_path).compare_protocols("p1", "p2", max_features=1)
    assert len(result["event_step_diff"]["protocol_a_events"]) == 65
    assert len(result["event_step_diff"]["source_refs"]) == 65
    assert result["event_step_diff"]["protocol_a_steps"][0]["stages"][0]["note"] == "original"
    assert "explicit_not_used" in result["event_step_diff"]["absence_policy"]


def test_unranked_falsifiable_hypotheses_preserve_opposing_evidence():
    edit = {
        "candidate_feature": "x",
        "evidence_refs": [{"protocol_id": "p1"}],
        "opposing_evidence_refs": [{"study_id": "against1"}],
    }
    first = design_experiments([edit], {"primary": "INS"}, {"scientific_ready": False})
    assert first["ranking_status"] == "unranked_hypothesis_set"
    assert first["candidates"][0]["rank"] is None
    hypothesis = first["hypotheses"][0]
    assert hypothesis["falsification_conditions"] and hypothesis["alternative_explanations"]
    hypothesis["evidence_against"] = ["evidence_against1"]
    hypothesis["status"] = "REFUTED"
    second = design_experiments(
        [{**edit, "opposing_evidence_refs": []}], {"primary": "INS"}, {"scientific_ready": False}, [hypothesis]
    )
    assert second["hypotheses"][0]["opposing_evidence_refs"] == [{"study_id": "against1"}]
    assert second["hypotheses"][0]["evidence_against"] == ["evidence_against1"]
    assert second["hypotheses"][0]["status"] == "REFUTED"
    assert second["selected_for_review"] == []
    unranked = design_experiments(
        [edit, {**edit, "candidate_feature": "y"}],
        {
            "primary": "INS",
            "experiment_design": {
                "assessments": {
                    "x": {
                        "expected_improvement": 1,
                        "information_gain": 1,
                        "experimental_cost": 0,
                        "risk": 0,
                        "rationale": "ordinal assessment",
                        "source": "researcher",
                    }
                }
            },
        },
        {"scientific_ready": False},
    )
    assert all(candidate["rank"] is None for candidate in unranked["candidates"])
    assert [candidate["id"] for candidate in unranked["candidates"]] == sorted(
        candidate["id"] for candidate in unranked["candidates"]
    )


@pytest.mark.parametrize(
    "action_id,arguments,key",
    [
        ("protocol.assess", {}, "protocol_assessments"),
        ("data.assess", {}, "validation"),
        ("protocol.phenotype_compare", {"protocol_ids": ["p1", "p2"], "endpoint": "INS"}, "evidence_gaps"),
        ("hypothesis.generate", {"edits": [{"candidate_feature": "x"}], "objective": {"primary": "INS"}}, "hypotheses"),
    ],
)
def test_new_available_actions_have_executable_validated_implementations(tmp_path, action_id, arguments, key):
    assert get_action(action_id).availability == "available"
    result = execute_scientific_action.invoke(
        {
            "action_id": action_id,
            "arguments": arguments,
            "protocol_output_dir": str(tmp_path / "pa"),
            "data_analysis_output_dir": str(tmp_path / "da"),
            "optimization_output_dir": str(tmp_path / "po"),
            "max_features": 10,
            "workspace_root": str(tmp_path),
        }
    )
    assert key in result if key == "validation" else key in result["result_preview"]


def test_endpoint_cohort_requires_explicit_scale_context_and_target_values():
    context = {
        "expression_scale": "normalized",
        "normalization": "library_size",
        "method_version": "1",
        "cell_population": "beta",
        "sampling_condition": "day30",
        "cell_line": "line1",
        "stage": "mature",
        "biological_replicate_id": "r1",
    }
    phenotypes = [
        {
            "id": f"ph{index}",
            "protocol_id": f"p{index}",
            "sample_id": f"s{index}",
            "metadata": context,
            "metrics": [
                {
                    "id": f"m{index}",
                    "endpoint": "INS",
                    "value": index / 10,
                    "status": "ok",
                    "unit": "normalized_counts",
                    "method": "mean",
                    "context": context,
                }
            ],
        }
        for index in (1, 2)
    ]
    ir = {"phenotypes": phenotypes}
    result = compare_endpoint(ir, ["p1", "p2"], "INS")
    assert result["status"] == "ok" and result["ranking"] is None
    assert [cohort["sample_mean"] for cohort in result["cohorts"]] == [0.1, 0.2]
    assert all(cohort["n_identified_biological_replicates"] == 1 for cohort in result["cohorts"])
    phenotypes[1]["metrics"][0]["unit"] = "source_scale"
    result = compare_endpoint(ir, ["p1", "p2"], "INS")
    assert result["status"] == "partial" and result["cohorts"] == []
    assert "endpoint_scale_or_context_unknown:m2" in result["evidence_gaps"]
    assert compare_endpoint(ir, ["p1", "p2", "p3"], "INS")["status"] == "partial"
    for phenotype in phenotypes:
        phenotype["metrics"][0]["unit"] = "fraction"
        phenotype["metrics"][0]["context"] = {
            key: value for key, value in context.items() if key not in {"expression_scale", "normalization"}
        }
        phenotype["metadata"] = phenotype["metrics"][0]["context"]
    assert compare_endpoint(ir, ["p1", "p2"], "INS")["status"] == "ok"
    phenotypes[1]["sample_id"] = "unknown"
    assert "sample_identity_missing_or_duplicated:p2" in compare_endpoint(ir, ["p1", "p2"], "INS")["evidence_gaps"]
