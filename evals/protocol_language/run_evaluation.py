"""Score the small, explicitly labeled PA language regression set offline."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from protocol_optimization_agent.analysis.protocol.step_semantics import annotate_steps
from protocol_optimization_agent.runtime.io import read_json, write_json

CASE = Path(__file__).resolve().parent


def evaluate() -> dict:
    cases = read_json(CASE / "cases.json")
    results = []
    for case in cases:
        text = case["text"]
        source = {"protocol_id": case["id"], "stages": [
            {"stage_id": "s", "stage_index": 1, "key_operations": text, "evidence_quote": text}]}
        steps, audit = annotate_steps([source], {case["id"]})
        actions = steps[0]["actions"]
        labels = [action["operation"] for action in actions]
        expected, actual = Counter(case["operations"]), Counter(labels)
        guards = {guard["kind"] for guard in steps[0]["constraints"]}
        expected_guards = set(case["guards"])
        results.append({"case_id": case["id"], "fixture_kind": case["fixture_kind"],
            "expected_actions": sum(expected.values()), "predicted_actions": sum(actual.values()),
            "matched_action_labels": sum((expected & actual).values()),
            "exact_operation_order": labels == case["operations"],
            "expected_guard_kinds": len(expected_guards), "predicted_guard_kinds": len(guards),
            "matched_guard_kinds": len(guards & expected_guards),
            "source_retained": "".join(action["text"] for action in actions) == text,
            "evidence_supported_actions": audit["n_evidence_supported_actions"]})
    totals = {key: sum(row[key] for row in results) for key in (
        "expected_actions", "predicted_actions", "matched_action_labels", "expected_guard_kinds",
        "predicted_guard_kinds", "matched_guard_kinds", "evidence_supported_actions")}
    return {"scope": "fixture_regression_not_corpus_accuracy", "n_cases": len(results),
        "totals": totals, "metrics": {
            "action_label_precision": totals["matched_action_labels"] / max(totals["predicted_actions"], 1),
            "action_label_recall": totals["matched_action_labels"] / max(totals["expected_actions"], 1),
            "guard_kind_precision": totals["matched_guard_kinds"] / max(totals["predicted_guard_kinds"], 1),
            "guard_kind_recall": totals["matched_guard_kinds"] / max(totals["expected_guard_kinds"], 1),
            "exact_operation_order_rate": sum(row["exact_operation_order"] for row in results) / max(len(results), 1),
            "source_retention_rate": sum(row["source_retained"] for row in results) / max(len(results), 1),
            "excerpt_support_rate": totals["evidence_supported_actions"] / max(totals["predicted_actions"], 1)},
        "not_measured": ["expert adjudicated completeness", "parameter attachment accuracy",
                         "implicit dependencies", "biological equivalence", "online model accuracy"],
        "cases": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=CASE.parents[1] / "runs/evals_protocol_language/report.json")
    args = parser.parse_args()
    report = evaluate()
    write_json(args.output, report)
    print(report["metrics"])
    print(f"Saved {args.output}")
