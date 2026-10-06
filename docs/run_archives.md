# Run Archives

## Coverage and Ownership

The conversational Orchestrator, standalone `protocol_chat` graph, fixed pipeline,
three agents' `run()` methods, PA and protocol-query CLIs, and protocol analysis/query/chat API use the
shared `RunArchive` journal. Scientific approvals and feedback remain in the
existing project SQLite world model; archives reference their exact evaluation
and analysis versions rather than replacing that state.

Every execution has a unique run ID. A conversation/thread ID is independent.
Submitting a previously used run ID creates a child run in conversational/tool
entrypoints. Completed archives reject further journal changes. Pipeline and
standalone agent runs always create fresh IDs. Within an active run, agent working
files may be refreshed, but each completed agent/tool call has a separate immutable
artifact copy and intermediate result; historical call outputs remain available.
Selected cached collections also have immutable input artifact copies, independent
of the mutable working copy. Reusing a plot filename retains each completed SVG
version. Collection directories containing h5ad are rejected: submit datasets
through their manifest/artifact IDs instead of mixing them into PA/DA/PO outputs.

Local runs use `<workspace>/users/<user>/runs/<run>`. Authenticated runs use
`<workspace>/users/<user>/projects/<project>/runs/<run>`. For the fixed pipeline and
direct agent calls, the old configured output directory is now a **storage root**.
Use paths returned by `run()`, API responses, or CLI output, not an assumed fixed
filename under that root. Update `PROTOCOL_ANALYSIS_OUTPUT_DIR` to a concrete PA
output directory when selecting a collection to compare.
`protocol-query` keeps its JSON response on stdout and prints the run location on
stderr; queries and plots operate on a private copy, not the selected source.
PA file entrypoints consume only `protocols.jsonl`; the PA API snapshots that file,
not unrelated sidecar files in its directory. The archived JSONL is the authority
for deterministically projected internal events. See [protocol_input_contract.md](protocol_input_contract.md).

## Files

```text
<run>/
  run_journal.sqlite          # Authoritative, transactionally serialized events
  run_manifest.json          # Identity, parent/thread, lifecycle, tasks, artifacts
  config.json                # Initial and effective per-call configuration
  input_manifest.json        # Input identity, hashes, snapshots, cache sources
  environment.json           # Python, package versions, Git HEAD, actual source hashes
  source/                    # Working Python source, including uncommitted changes
  model_manifest.json        # Model parameters, provider model when reported, usage
  prompts.jsonl              # Actual messages, prompt hash, response, latency/error
  tool_calls.jsonl            # Call IDs, parents, parameter refs, duration/status
  parameters/<call>.json      # Complete redacted parameters, not truncated previews
  intermediate/<call>.json    # Internal stage/tool results
  state_objects/<sha>.json    # Deduplicated graph-state fields referenced by stages
  inputs/<sha256>/            # Small structured input and FeatureSpec snapshots
  protocol_analysis/          # PA working outputs or snapshot of selected source
  data_analysis/              # DA working outputs or snapshot of selected source
  protocol_optimization/      # PO working outputs or snapshot of selected source
  artifacts/<call>/           # Versioned copies of completed call outputs
  evidence.json               # Evidence references and artifact/result versions
  final_response.json         # Actual returned final answer or structured result
  final_report.md             # Human-readable rendering of that saved result
  audit_report.json           # Archival checks, errors and execution state
  errors.jsonl
  bundle_manifest.json        # SHA-256 inventory of sealed bundle files
```

Agent-native CSV/JSON reports retain their existing names. Standalone agents use
their class name for the working-output subdirectory. `_logs/tool_calls.jsonl` is
a compatibility view of the root tool log, not another independent event stream.

The journal uses SQLite `BEGIN IMMEDIATE` to serialize concurrent updates. JSON
and JSONL are atomically replaced views; the journal is authoritative if a process
stops during export. Large stage results/parameters are saved separately so event
records do not repeatedly duplicate full matrices. API keys and credential fields
are redacted; known credential environment values are also masked in text/errors.
Do not put credentials in evidence quotes, questions or source code. Deploy pinned
source and restart workers after updates; do not edit Python files during active runs.
Graph-stage parameters/results use `graph-state-snapshot-v1` references to immutable
per-field JSON objects; unchanged matrices/events across stages occupy one object
per run. All actual values are retained, not replaced by truncated summaries.

Persisting candidates from a sealed standalone PO run creates a child ingestion
run. Its export comes from the scientific database, with evaluation IDs recorded
in the archive. Use the returned `output_dir`; the original optimization report
remains a historical computation snapshot and is never rewritten by ingestion.

## Large Input Registration

Unregistered files receive a streaming full-content SHA-256, including files over
100 MiB. Directory identities include every file's content hash, not a truncated
metadata sample. h5ad is referenced, not copied into every run. Referenced inputs
are checked for size/mtime/ctime changes at completion; a change marks the run
partial and adds an error. Keep the original version or register it before use.

For large h5ad, register **once on the trusted server**:

```bash
python -m protocol_optimization_agent.interfaces.run_archive_cli register-input \
  --source /srv/incoming/sample.h5ad \
  --storage /srv/research/users/alice/projects/study/datasets \
  --registry /srv/research/dataset_registry
export PROTOCOL_DATASET_REGISTRY=/srv/research/dataset_registry
```

PowerShell uses `$env:PROTOCOL_DATASET_REGISTRY = 'E:/research/dataset_registry'`.
Registration copies into a content-addressed version and makes that copy read-only;
the original incoming file is untouched. The CLI prints the version, checksum and
actual stored path. Use that path in a local manifest. In authenticated mode,
register it as a read-only authorized artifact in the server auth configuration
and submit its artifact ID, not a client filesystem path.

Only server-owned registry receipts plus unchanged, read-only stored files enable
reuse of ingestion hashes. Writable/changed registered files are rejected. Do not
let clients write the registry or chmod/versioned datasets; filesystem ACLs and
ownership are part of this contract. A privileged operator can bypass read-only
permissions, so periodic full re-verification/backups remain necessary. Existing
receipt reuse is not a claim of cryptographic tamper-proof storage.

## Historical Verification

```bash
python -m protocol_optimization_agent.interfaces.run_archive_cli verify --run-dir /srv/research/users/alice/projects/study/runs/run_ID
```

The verifier checks archived outputs and the sealed bundle (including prompt,
parameter, intermediate, source, and final-result files) without calling an LLM
or rerunning analysis. Nonzero exit indicates a missing/unsealed/changed artifact.
To explain a recommendation: read `final_response.json`, locate its evidence in
`evidence.json`, open the referenced intermediate/artifact snapshot, then follow
call IDs to parameters, input manifests, and actual source/model records.
The final result also includes `archive_provenance` with run status, actual tool
evidence, result references and persisted candidate evaluation IDs. This is
run-level linkage, not automatic verification that every natural-language sentence
is supported by a particular citation.

## Limits

- Archive checks are not a substitute for scientific comparability or statistical
  validation. Existing PA release gates, DA validation, and candidate approvals
  still govern those questions. DE remains exploratory unless upgraded separately.
- Model aliases may change remotely; requested model and provider-reported model
  are recorded, but absent provider revision/usage/cost are unknown (`null`), not zero.
- Internal graph stages and implemented h5ad functions are traced. External tool
  internals/subprocesses are not automatically instrumented; wrapper inputs and
  returned artifacts are the boundary.
- Abrupt power loss/SIGKILL cannot run a finalizer. A remaining `running` manifest
  without a sealed bundle is an incomplete run, not a successful one. Investigate
  the journal before retrying as a new child run; do not overwrite the old directory.
- SQLite/filesystem archives are retained locally and consume disk. Apply server
  retention, backup and access policy; do not commit `runs/` or dataset registries.
- Live model billing, actual production HTTP streaming and huge-file throughput
  require deployment validation; mocked/offline tests do not demonstrate those.
