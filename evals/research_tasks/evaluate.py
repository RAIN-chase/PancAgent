"""Grade frozen archived baseline runs; never expose private rubrics to an agent."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from protocol_optimization_agent.runtime.archive import atomic_json, file_hash
from protocol_optimization_agent.science.reporting import pointer_value

BASELINES = {"direct_same_model", "fixed_workflow", "full_agent"}


def validate_cases(suite: dict) -> dict:
    cases = suite["cases"]
    if len({row["id"] for row in cases}) != len(cases):
        raise ValueError("Duplicate case IDs")
    studies = defaultdict(set)
    for row in cases:
        if set(row) != {"id", "split", "study_ids", "input_mode", "question"}:
            raise ValueError("Public cases contain undeclared fields or hidden answers")
        if row["split"] not in {"development", "test", "deployment"} or row["input_mode"] not in {"versioned_data", "no_data"}:
            raise ValueError("Invalid split or input mode")
        if row["split"] != "deployment" and not row["study_ids"]:
            raise ValueError("Research tasks require study grouping")
        studies[row["split"]].update(row["study_ids"])
    if studies["development"] & studies["test"]:
        raise ValueError("Study leakage across development/test")
    return {"n_cases": len(cases), "study_groups": {key: sorted(value) for key, value in studies.items()},
            "status": "question_manifest_valid; frozen_data_and_expert_gold_still_required"}


def evaluate(suite: dict, predictions: list[dict], private_gold: dict, root: Path) -> dict:
    manifest = validate_cases(suite)
    by_case = {row["id"]: row for row in suite["cases"]}
    seen, results, groups = set(), [], defaultdict(list)
    for row in predictions:
        case = by_case[row["case_id"]]
        key = (case["id"], row["baseline"], row["repetition"])
        if row["baseline"] not in BASELINES or key in seen or type(row["repetition"]) is not int or row["repetition"] < 0:
            raise ValueError("Invalid baseline or duplicate repetition")
        seen.add(key)
        if not set(row.get("source_study_ids", [])).issubset(case["study_ids"]):
            raise ValueError("Input study scope exceeds case partition")
        if case["input_mode"] == "no_data" and (row.get("source_study_ids") or row.get("input_artifacts")):
            raise ValueError("No-data control received data")
        for field in ("input_sha256", "config_sha256", "prompt_sha256"):
            if not isinstance(row.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", row[field]):
                raise ValueError("Frozen input/config/prompt fingerprints are required")
        for field in ("elapsed_seconds", "cost_usd", "input_tokens", "output_tokens"):
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError("Cost/latency/usage must be nonnegative or null, never inferred zero")
        errors, values = [], {}
        for binding in row.get("quantities", []):
            try:
                if type(binding["result_index"]) is not int or binding["result_index"] < 0:
                    raise ValueError("Result indices must be nonnegative integers")
                ref = row["results"][binding["result_index"]]
                path = root / ref["path"]
                if not path.resolve().is_relative_to(root.resolve()) or file_hash(path) != ref["sha256"]:
                    raise ValueError("Result path/hash invalid")
                value = pointer_value(json.loads(path.read_text(encoding="utf-8")), binding["field_path"])
                if binding["key"] in values:
                    raise ValueError("Duplicate quantitative key")
                values[binding["key"]] = value
            except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
                errors.append(str(exc))
        gold = private_gold.get(case["id"], {})
        expected = gold.get("expected_numbers", {})
        numeric = None
        if expected:
            checks = []
            for name, target in expected.items():
                actual = values.get(name)
                checks.append(isinstance(actual, (int, float)) and not isinstance(actual, bool) and math.isfinite(actual)
                              and math.isclose(actual, target, rel_tol=gold.get("rtol", 1e-8), abs_tol=gold.get("atol", 1e-8)))
            numeric = all(checks) and not errors
        rating = gold.get("blind_ratings", {}).get(f"{row['baseline']}:{row['repetition']}", {})
        record = {"case_id": case["id"], "split": case["split"], "baseline": row["baseline"], "repetition": row["repetition"],
            "execution_success": row["execution_status"] == "succeeded" if row.get("execution_status") else None, "numerical_correctness": numeric,
            "source_binding_integrity": not errors if row.get("quantities") else None,
            "source_entailment": rating.get("source_entailment"), "method_correctness": rating.get("method_correctness"),
            "scientific_applicability": rating.get("scientific_applicability"), "question_sufficiency": rating.get("question_sufficiency"),
            "decision_correctness": row.get("decision") == gold["expected_decision"] if "expected_decision" in gold else None,
            "elapsed_seconds": row.get("elapsed_seconds"), "cost_usd": row.get("cost_usd"),
            "input_tokens": row.get("input_tokens"), "output_tokens": row.get("output_tokens"),
            "binding_errors": errors, "values": values, "grading_status": "expert_gold_available" if gold else "unscored_no_expert_gold"}
        results.append(record)
        groups[case["id"]].append((row, record))
    comparisons = []
    for case_id, entries in groups.items():
        inputs = {(row["input_sha256"], row["config_sha256"], json.dumps(row.get("budget"), sort_keys=True)) for row, _ in entries}
        models = {json.dumps(row.get("model"), sort_keys=True) for row, _ in entries if row["baseline"] != "fixed_workflow"}
        baselines = {row["baseline"] for row, _ in entries}
        repetitions = {}
        for baseline in baselines:
            items = [record for row, record in entries if row["baseline"] == baseline]
            signatures = {json.dumps(record["values"], sort_keys=True) for record in items}
            decisions = {row.get("decision") for row, _ in entries if row["baseline"] == baseline}
            repetitions[baseline] = {"n_runs": len(items), "numeric_variation_detected": len(signatures) > 1 if len(items) > 1 and all(item["values"] for item in items) else None,
                                     "decision_variation_detected": len(decisions) > 1 if len(items) > 1 else None}
        comparisons.append({"case_id": case_id, "all_baselines_present": baselines == BASELINES,
            "same_frozen_inputs_and_budget": len(inputs) == 1 and all(isinstance(row.get("budget"), dict) and row["budget"] for row, _ in entries), "same_direct_agent_model": len(models) == 1 and all(row.get("model") for row, _ in entries if row["baseline"] != "fixed_workflow"),
            "repetitions": repetitions})
    return {"schema_version": "research-evaluation-v1", "suite": manifest, "results": results, "comparisons": comparisons,
            "missing_cases": sorted(set(by_case) - set(groups)), "suite_sha256": hashlib.sha256(json.dumps(suite, sort_keys=True).encode()).hexdigest(),
            "policy": "No synthetic performance numbers. Null is unmeasured. Hash integrity is not citation entailment. Deployment checks are separate from scientific test tasks. Study scopes require operator-reviewed frozen manifests."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--private-gold", type=Path)
    parser.add_argument("--artifacts-root", type=Path)
    parser.add_argument("--output", type=Path, default=Path("runs/research_evaluation/report.json"))
    args = parser.parse_args()
    suite = json.loads(args.cases.read_text(encoding="utf-8"))
    if args.predictions:
        if not args.private_gold or not args.artifacts_root:
            parser.error("Predictions require separate private gold and an authorized artifacts root")
        predictions = [json.loads(line) for line in args.predictions.read_text(encoding="utf-8").splitlines() if line.strip()]
        result = evaluate(suite, predictions, json.loads(args.private_gold.read_text(encoding="utf-8")), args.artifacts_root)
    else:
        result = validate_cases(suite)
    atomic_json(args.output, result)
    print(json.dumps({"output": str(args.output), "n_cases": len(suite["cases"]), "runs_scored": len(result.get("results", []))}))


if __name__ == "__main__":
    main()
