# Goal-Reviewed Orchestrator

The conversational graph remains `configure -> plan -> execute -> review -> execute or finalize`.
PA, DA and PO remain domain executors; the Orchestrator alone owns bounded planning.
Action Registry remains the only executable capability list. Quantitative algorithms are unchanged.

## Plan And Question Acceptance

Plans are structured JSON, not model tool calls. The plan records the original question,
`goal_type`, `objects`, `target_metrics`, steps, unresolved evidence and clarification questions.
Initial plans cannot supply their own acceptance verdict or final conclusions.

Review separately checks task execution and scientific goal coverage:

- Protocol comparison requires matrix and original-step differences, not just retrieval.
- Endpoint analysis requires validated numerical results covering requested objects/metrics.
- Endpoint comparison requires an explicit endpoint and at least two comparable cohorts.
  The existing endpoint tool checks measurement scale, method, sampling/culture context and sample identity.
  Passing these checks permits descriptive comparison, not unconditional superiority or causality.
- Hypothesis generation requires actual hypotheses; unavailable or empty outputs do not count.
- Any failed, blocked or clarification-dependent planned subquestion remains explicit and prevents `completed`.

An endpoint-free "which protocol is better?" receives a clarification request even if retrieval succeeds.
Acceptance is computed from saved results, never from model assertions or request parameters alone.
Automatic question classification is a conservative routing safeguard, not a semantic proof that an arbitrary
free-text question has been fully understood. Explicit objects and endpoints remain essential.

## Result Dependencies

Bindings automatically create step dependencies. Sources are current-run result references with verified
file hashes and JSON pointers; paths cannot escape the run archive. Resolved inputs are validated against
the registered action immediately before execution. Bindings cannot overwrite explicit scientific parameters.

Example step after `lookup_a`:

```json
{
  "id": "compare",
  "action_id": "protocol.compare",
  "inputs": {"protocol_id_b": "p2"},
  "input_bindings": [{
    "source_step": "lookup_a",
    "field_path": "/result_preview/results/0/protocol_id",
    "input_path": "/protocol_id_a"
  }],
  "expected_outputs": ["feature_diff", "event_step_diff"]
}
```

The upstream lookup must resolve the intended protocol; selecting an ambiguous first hit is not a scientific
identity check. Binding provenance and resolved parameters are saved in `step_results.json` and tool logs.
Call signatures include the action/code/input versions and bound source hashes.

## Review And Scientific Conclusions

Each proposed conclusion contains `claim`, `explanation`, `evidence_level`, `supporting_refs`,
`opposing_refs`, `limitations`, `objects` and `metrics`. References identify saved results or historical
scientific objects and field paths. Code rejects unknown IDs, invalid fields, unsupported scopes,
unusable positive support, numerical literals in model-authored prose and promotion of hypotheses/DE
exploration to facts. Incomplete goal coverage downgrades final conclusions to `undetermined`.

Planning automatically receives up to eight lexically relevant hypotheses with versions, test specifications,
support and opposition. `history_context.json` preserves that snapshot. Relevant historical counterevidence
is retained in exploratory/hypothesis conclusions, not silently discarded or treated as automatic refutation.
Lexical retrieval is not exhaustive semantic recall; historical evidence cannot establish effects in a new context.
Citation validation establishes source binding, not biological entailment. Explanations remain subject to scientific review.

## Local Failures And Budgets

- One model-format repair per run, shared by plan and review, counts against model/time/token/cost budgets.
- Exact duplicate calls reuse the verified saved reference and do not consume another tool execution.
- Repeated scientific evidence does not trigger another model review by itself.
- Failed steps block only their dependent steps; independent steps continue within budget.
- Missing scientific parameters return `needs_clarification`; the model must not fabricate them.
- Budget stops preserve partial results and a structured report. Deadlines are checked between operations;
  they do not forcibly cancel an in-flight analysis.

## Saved Reports

`scientific-analysis-report-v3` retains acceptance, conclusions, opposing evidence, unresolved subquestions,
all quantitative bindings and result references. Presentation selects up to twenty-five question-relevant
numeric fields, explicitly states the displayed/retained counts and links the complete `final_report.json`.
All displayed values are read from hash-verified saved JSON. A filesystem link is a local artifact entry,
not a public download endpoint; remote distribution still requires the authorized artifact interface.

Core implementation: `agents/orchestrator.py`, `agents/analysis_flow.py`, `agents/actions.py`,
`science/reporting.py`, `science/state.py`.
Regressions: `backend/tests/test_orchestrator_goals.py` and `backend/tests/test_analysis_flow.py`.

## Verification: 2026-10-07

- Full backend sweep: 258 passed, no skips, one existing Starlette/httpx deprecation warning.
- Final goal/loop regression pass: 40 passed, including the additional unexecuted-subquestion budget case.
- Ruff checks passed across `backend`; mypy passed for the five changed implementation modules.
- Frozen scientific baseline unchanged: matrices, values, units, missingness, source evidence, queries and Phenotype IR.
- New tests explicitly isolate PA/DA/PO directories instead of copying real local cached outputs.
  Early sweeps hit temporary-disk exhaustion; completed superseded workspace test directories were removed,
  and the successful full sweep used an E-drive temporary directory.
- Models in these tests are offline fixtures. Hosted-LLM end-to-end behavior, large-h5ad performance,
  mechanism entailment and intervention effects were not validated by this refactor.
