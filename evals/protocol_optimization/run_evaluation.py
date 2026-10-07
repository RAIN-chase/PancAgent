"""Offline scientific-boundary regression, not biological efficacy validation."""
from __future__ import annotations

import argparse
from pathlib import Path

from protocol_optimization_agent.analysis.optimization.assessment import assess_similar_protocol_reference
from protocol_optimization_agent.analysis.optimization.association import _pearson, leave_study_out
from protocol_optimization_agent.analysis.optimization.mechanisms import interpret_mechanisms
from protocol_optimization_agent.runtime.io import read_json, write_json
from protocol_optimization_agent.runtime.llm_client import OfflineLLMClient


def evaluate() -> dict:
    cases = read_json(Path(__file__).with_name("cases.json"))
    results = []
    for case in cases:
        points = [{"protocol_id": f"p{i}", "x": x, "y": y, "study_ids": case["studies"][i]}
                  for i, (x, y) in enumerate(case["values"])]
        sensitivity = leave_study_out(points)
        reference = assess_similar_protocol_reference({
            "new_protocol_features": {"reagent_x": 2},
            "protocol_feature_rows": [{"protocol_id": p["protocol_id"], "reagent_x": p["x"]} for p in points],
            "protocol_phenotype_rows": [], "endpoint_rows": []})["reference_report"]
        package = {"id": "synthetic-evidence", "observations": [], "limitations": ["Synthetic observations only."]}
        candidates = [{"feature": "reagent_x", "endpoint_associations": []}]
        no_mechanism = interpret_mechanisms(candidates, package, {}, [], OfflineLLMClient())
        supplied_mechanism = interpret_mechanisms(candidates, package, {}, [{
            "feature": "reagent_x", "url": "https://example.invalid/synthetic-opposition",
            "quote": "Synthetic regression excerpt, not a biological finding.", "stance": "opposes"}], OfflineLLMClient())
        no_leakage = all(not (set(fold["excluded_protocol_ids"]) & set(fold["retained_protocol_ids"]))
                         for fold in sensitivity["folds"])
        results.append({"id": case["id"], "fixture_kind": case["fixture_kind"],
            "simple_association_baseline_r": _pearson([p["x"] for p in points], [p["y"] for p in points]),
            "study_sensitivity": sensitivity, "expected": case["expected"], "no_fold_leakage": no_leakage,
            "similar_reference_baseline": {"status": reference["status"],
                "candidate_outcome_estimate": reference["candidate_outcome_estimate"]},
            "mechanism_ablation": {"no_sources_status": no_mechanism["interpretations"][0]["status"],
                "supplied_source_status": supplied_mechanism["interpretations"][0]["status"],
                "opposition_retained": bool(supplied_mechanism["interpretations"][0]["opposing_evidence_refs"]),
                "numeric_prediction": supplied_mechanism["interpretations"][0]["numeric_predictions"]},
            "passed": sensitivity["status"] == case["expected"] and no_leakage
                and reference["candidate_outcome_estimate"] is None
                and bool(supplied_mechanism["interpretations"][0]["opposing_evidence_refs"])})
    return {"schema_version": "po-boundary-evaluation-v1", "n_cases": len(results),
        "n_passed": sum(row["passed"] for row in results), "results": results,
        "scope": "Synthetic boundary and ablation regression; no measured ranking gain, causal effect, predictive accuracy or novel-protocol efficacy."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate()
    write_json(args.output, report)
    print(f"{report['n_passed']}/{report['n_cases']} synthetic cases passed")
    raise SystemExit(0 if report["n_passed"] == report["n_cases"] else 1)
