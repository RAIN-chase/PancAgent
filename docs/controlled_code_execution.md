# Controlled Scientific Code Execution

## Delivered Boundary

Orchestrator retains its existing Plan/Execute/Review loop. No autonomous Code Agent or nested planner was added. Existing PA, DA and PO tools and numerical contracts remain unchanged.

This first increment provides:

- `runtime/code_executor.py`: one shared, fail-closed rootless Linux Docker backend.
- `agents/data_exploration.py`: a single opt-in DA action, `data.exploratory_sensitivity`.
- `analysis/phenotype/sensitivity.py`: bounded metadata extraction and a trusted reference calculation.
- Saved plans, inputs, candidate scripts, scans, environment/image identity, seeds, diagnostics, repair attempts, validation and results in each run's `code_tasks/` directory.
- Persistent exploratory evidence classification, rejection of fact promotion, and a phenotype-workspace result table referencing the saved JSON.

PA and PO do not receive arbitrary Python tools. PA continues proposing source-supported FeatureSpec definitions and using its deterministic compiler. PO continues using registered candidate/constraint methods, delegates metrics to DA, and cannot replace upstream artifacts. Their future code tasks require separate input contracts, domain validators and tests before registration. Protocol extraction remains deferred.

## First DA Task

The supported task is **leave-one-declared-biological-replicate-out mean sensitivity for an existing numeric h5ad `obs` metric**. It is not a gene-program scorer, differential-expression engine, predictive model or new maturity definition. Use the formal actions for existing calculations first.

The versioned input is resolved through the existing authorized dataset catalog. Only requested `obs` columns are read, in chunks; `X` is never read. The current metadata ceiling is two million cells and one thousand biological replicates. Larger inputs require an explicitly registered subset. Metric values are averaged within sample, sample means receive equal weight within biological replicate, and biological replicates receive equal weight in the group mean. Cell counts are never substituted for independent sample counts.

Missing/nonfinite metric values are counted and omitted; samples with no finite values fail validation. Unknown sample/study/replicate identifiers, inconsistent mappings, paired designs spanning groups, unknown scale and fewer than two declared replicates per group stop the task. Independence and metric provenance remain declared assumptions, not experimentally established facts. No p-values, calibrated confidence intervals, causal claims, cross-study stability guarantees or batch correction are provided. Single-study support is flagged in the report.

Example action inputs (column names must actually exist):

```json
{
  "request": {
    "dataset_id": "organoids-51-merged",
    "dataset_version": "sha256:REPLACE_WITH_PUBLISHED_VERSION",
    "endpoint": "existing_beta_score",
    "metric_column": "existing_beta_score",
    "sample_column": "sample_id",
    "replicate_column": "biological_replicate_id",
    "study_column": "study_id",
    "group_column": "protocol_id",
    "scale": "Scanpy module score on declared log-normalized expression",
    "rationale": "Evaluate replicate omission sensitivity; existing scoring action does not provide this diagnostic",
    "seed": 0,
    "max_repairs": 1,
    "budget": {"timeout_seconds": 30, "memory_mb": 256, "cpus": 1.0}
  }
}
```

The Orchestrator may supply `request.candidate_code` for this task. With no candidate, DA generates the approved recipe. The script reads `/task/input.json` and writes `/out/result.json`, matching `sample-sensitivity-v1` (see the trusted `summarize_samples` function). Candidate outputs must match trusted host-side recomputation, including every group, omission, count and finite numeric value. This narrow fallback deliberately does **not** accept arbitrary new methods or output schemas.

One repair at most replaces a failed candidate with the approved recipe. Timeout or cleanup failure stops immediately. Failed numerical output is retained only for diagnostics, never as accepted scientific evidence. Scripts are never executed, imported or evaluated on the host. This is a controlled calculation extension, not unrestricted self-improving code.

## Linux Provisioning

Execution defaults to disabled. The operator must provision a local **rootless Docker Engine on Linux with cgroup v2 delegation, seccomp and active CPU, memory and PID limits**. Rootful Docker, missing resource support/seccomp, unavailable daemon/image and non-pinned tags are rejected. No host Python fallback exists.

Build the trusted dependency-free runtime once, outside agent execution:

```bash
docker build -t pancagent-code:stdlib-v1 docker/code-executor
docker image inspect pancagent-code:stdlib-v1 --format '{{.Id}}'
docker info --format '{{json .SecurityOptions}}'
```

Set the server `.env` using the complete image ID returned above, then restart the backend:

```dotenv
PROTOCOL_CODE_EXECUTION=rootless-docker
PROTOCOL_CODE_IMAGE=sha256:REPLACE_WITH_64_HEX_CHARACTERS
```

Runtime never pulls images or installs dependencies. The trusted image removes pip/setuptools/wheel. Candidate imports are limited to selected standard-library modules; AST scanning is a diagnostic/policy aid, **not a security sandbox**. Image build/tag changes require operator review and a new pinned ID. The image must contain no credentials or private data; credential-named image environment entries are rejected.

Every job has disabled networking, a non-root container user, dropped capabilities, no-new-privileges, read-only root and task mount, no Docker socket, no host output mount, bounded CPU/memory/swap/PIDs/file descriptors and a total execution-attempt wall-time budget. Only aggregated input JSON, runner, candidate and scan are visible, not the h5ad, source tree, `.env` or other runs. Output resides on bounded tmpfs while the container remains alive. Logs and files have size limits. Only checked regular-file tar members are read into saved host files; archives are never extracted. Cleanup forcibly removes the named container and must be confirmed. If the service itself crashes, the container's initial process has a fixed lifetime; production operators should additionally monitor stale containers and total concurrent resource use.

Unconfirmed cleanup also quarantines the current backend process against further launches until an operator checks containers and restarts it. This process-local guard is not a cross-worker scheduler or distributed cleanup service.

Rootless containers share a host kernel; this is not a claim of VM-grade isolation against kernel vulnerabilities. For hostile public multi-tenancy, provision and verify a stronger sandbox/runtime and a server-side concurrency supervisor before enabling this feature. Do not expose Docker administration endpoints to users. Configuration follows the official [container run security/resource options](https://docs.docker.com/engine/containers/run/) and [rootless resource-limit requirements](https://docs.docker.com/engine/security/rootless/).

## Reproducibility and Promotion

Each run preserves the declared dataset version, file size/mtime, metadata snapshot hash, script hash, exact image ID, engine security environment, seed, parameters, diagnostics and versioned output. Published source files must remain immutable; stat checks do not defend against a malicious filesystem writer who preserves size/mtime. This first method is deterministic and has no stochastic estimator, even though the seed is retained for audit.

Passing once only establishes agreement with this task's reference calculation. The result remains `exploratory`; persistent memory retains that classification. No output is copied into PA matrices, formal Phenotype IR, production code or the Action Registry. Candidate code cannot approve itself.

Promotion is a human-reviewed repository change: preserve script/input/image hashes, add cross-dataset and edge-case tests, independently reproduce results, review scientific assumptions and limitations, assign domain ownership, input/output schema and method/action versions, and approve resource/security requirements before registering a formal action. PA/PO promotion also requires source coverage or decision-constraint checks respectively. There is no dynamic script loading or automatic promotion endpoint.

## Verification

Unit/integration-logic tests use a mocked container transport and never run candidate code on the host:

```bash
python -m pytest backend/tests/test_code_execution.py -q
```

On the provisioned Linux server, explicitly enable the real-container isolation smoke test:

```bash
PROTOCOL_RUN_CODE_INTEGRATION=1 python -m pytest backend/tests/test_code_execution.py -q
```

That smoke test checks read-only task/root filesystems, disabled external networking and absence of a host test secret. Run it before enabling code mode for users. In the current Windows workstation environment Docker Engine is not running, so the real-container test cannot be certified here and is skipped by default. The container-free tests do not prove deployment isolation.
