# Protocol Input Contract

## Formal Source

The only formal protocol source is:

```text
inputs/Protocols inputs/protocols.jsonl
```

PA CLI and API accept a directory containing this filename. Authenticated APIs
still resolve an authorized directory artifact ID on the server. The fixed
pipeline's default `input_dir` is `inputs/Protocols inputs`, with:

```json
"required_inputs": {
  "protocols_jsonl": "protocols.jsonl",
  "protocol_h5ad_manifest_csv": "protocol_h5ad_manifest.csv"
}
```

The h5ad manifest is a separate DA input; it is not needed to run PA alone.
`protocol_artifacts.jsonl`, `protocol_database.sqlite`, `protocol_factor_events.csv`,
source identity inventories and QC sidecar CSVs are not formal protocol inputs.
They are ignored even when present alongside the formal file. No fallback is
allowed when `protocols.jsonl` is missing, empty or invalid. Legacy fixed-pipeline
configuration keys are rejected with a migration error; update existing configs.

## Record Projection

UTF-8 JSONL contains one object per nonblank line. Each object has a unique,
nonempty string `protocol_id`. Formal records contain flat protocol metadata,
`paper`, `stages`, nested stage `reagents`, and optional `preparation` objects.
The existing PA schema/quality gates remain responsible for stage timing and
evidence checks after projection.

- Protocol metadata is mapped into PA's internal `protocol` object while source
  fields, evidence IDs, manual review metadata and preparation remain available.
- Stage/preparation medium, coating, culture format, operations and nested reagents
  become deterministic factor events. No external event CSV is required.
- Each event retains its source file, zero-based record index, JSON pointer,
  source field and evidence. Missing structural stage IDs receive IDs based on
  protocol ID and collection position; these are identifiers, not inferred timing.
- Nested protocol/stage ownership must match its parent. Invalid collection types,
  duplicate protocol IDs, nonboolean readiness and missing reagent names fail.
- Only explicit scalar numbers are projected as concentration values. Ratios,
  ranges, missing units and missing values remain non-comparable numeric inputs.
  Evidence quotes never supply a missing dose, unit or exposure interval. A reagent's
  unknown exposure does not inherit the enclosing stage's duration.
- `comparison_ready=false` is never upgraded; absent readiness is false. Unknown
  confidence is not mapped to high confidence. Formal source status does not imply
  that a protocol is suitable for quantitative comparison.
- Preparations remain separate from the formal differentiation-stage timeline;
  their projected events carry `stage_role=preparation`, and the standardized
  protocol export retains their original records.

PA still produces its existing matrix, normalized events, provenance, graphs and
audit reports. The extraction workflow remains deferred: this adapter consumes an
already structured export and does not extract protocols from papers.

## Migration

Use the directory rather than passing a legacy database/event CSV:

```powershell
conda run -n protocol_agent python -m protocol_optimization_agent.protocol_analysis_cli --input-dir "inputs/Protocols inputs" --output-dir runs/protocol_analysis_agent_run
```

Use returned output paths because each execution creates an independent archive.
Regenerate PA outputs before comparing the newly published source; selecting an
old PA output collection still queries its historical snapshot, not the new JSONL.
Do not change readiness/quality gates just to make an empty matrix nonempty.

For low-level Python callers, call `load_protocol_analysis_inputs(input_dir)` and
pass its `protocol_records`, `factor_events`, and `source_identity_rows` to
`ProtocolAnalysisAgent.run()`. The obsolete `protocol_database_path` argument was
removed. Input data and prior archived results are not rewritten by this migration.
