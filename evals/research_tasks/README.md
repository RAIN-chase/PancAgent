# Research Task Pilot

`cases.json` contains 24 question drafts using current protocol IDs. Research
development/test partitions are grouped by study; deployment checks are separate.
Questions contain no gold answers. R08/R20/R24 are no-data controls. Dataset
availability, reference numbers and expert answer rubrics still need freezing.

## Validate Questions

From the repository root in the installed backend environment:

```bash
python evals/research_tasks/evaluate.py --output runs/research_evaluation/manifest_check.json
```

This validates the manifest only; it does not run the Agent or claim task accuracy.

## Baseline Protocol

Run `direct_same_model`, `fixed_workflow` and `full_agent` separately. Use the same
model revision for direct/Agent comparisons, identical authorized input snapshots,
configuration and budgets; preserve each actual prompt hash. Repeat each task at
least three times. Record unavailable usage/cost as null, not zero. Fixed workflows
must use the same deterministic computations rather than separately reimplemented
statistics. No-data runs must receive no study files, persisted evidence or
cross-run memory. Reset test memory for each repetition unless memory itself is
the explicit evaluated condition. Public questions may be provided to models;
private gold and blind ratings must never enter model context.

The runner/operator must authorize and verify frozen study manifests. Declaring a
study ID here does not prove that an h5ad excludes held-out studies.

For scheduler comparisons, run the same `full_agent` cases with parallel
read-only dispatch enabled and disabled, holding the model revision, prompt,
input snapshots, budgets and memory reset policy fixed. Repeat each condition
at least three times. Compare task acceptance, saved result hashes, evidence
gaps, tool/model calls, elapsed time and reported cost. Treat a faster run with
different scientific conclusions or missing evidence as a regression. The
current 24 drafts and manifest check do not establish these outcomes until
study inputs and independent gold are frozen.

## Saved Prediction Contract

One JSON object per line, paths relative to an authorized artifact root:

```json
{
  "case_id": "R01", "baseline": "full_agent", "repetition": 0,
  "source_study_ids": ["nair_2019"], "input_artifacts": ["input_manifest.json"],
  "input_sha256": "<64 lowercase hex characters>",
  "config_sha256": "<64 lowercase hex characters>",
  "prompt_sha256": "<64 lowercase hex characters>",
  "model": {"provider": "...", "name": "...", "revision": "..."},
  "budget": {"tool_calls": 6, "seconds": 120},
  "execution_status": "succeeded", "decision": "retrieve",
  "elapsed_seconds": null, "cost_usd": null,
  "input_tokens": null, "output_tokens": null,
  "results": [{"path": "run/result.json", "sha256": "<actual file hash>"}],
  "quantities": [{"key": "endpoint_mean", "result_index": 0, "field_path": "/mean"}]
}
```

Use decisions `retrieve`, `compute`, `clarify`, or `refuse` consistently in private
gold. Quantities are read from saved JSON; do not copy model-written numbers into
the grading file. Questions with no valid quantitative expectation may omit them.

Private gold is a separate, access-controlled JSON object keyed by case ID:

```json
{
  "R01": {
    "expected_decision": "retrieve",
    "expected_numbers": {}, "atol": 1e-8, "rtol": 1e-8,
    "blind_ratings": {
      "full_agent:0": {
        "source_entailment": null, "method_correctness": null,
        "scientific_applicability": null, "question_sufficiency": null
      }
    }
  }
}
```

Fill ratings only after independent adjudication. A matching file hash establishes
binding integrity, not citation entailment. Do not publish private gold until the
evaluation protocol allows it.

```bash
python evals/research_tasks/evaluate.py --predictions /authorized/predictions.jsonl --private-gold /restricted/gold.json --artifacts-root /authorized/artifacts --output runs/research_evaluation/report.json
```

The report keeps numerical, evidence, method, applicability and answer dimensions
separate, flags missing baselines/cases and unequal inputs/models, and reports
repeat numeric/decision variation. Null means unmeasured; missing gold remains
unscored. This is an offline grader, not an online benchmark execution harness.
