# Linux Internal Trial

Status: candidate source and local checks only. Do not tag `v0.1.0-rc.1` before
the intended Linux host passes the clean install and browser acceptance below.
This is a single-user, loopback-only research trial, not a public deployment.

## Compatibility

| Item | Trial requirement | Identity or check |
| --- | --- | --- |
| Software | PancAgent 0.1.0 candidate, exact Git SHA | Record `git rev-parse HEAD` in the host acceptance log |
| Runtime | Linux, Python 3.11, Node 22, pnpm 10.5.1 | Record exact versions and freeze Python dependencies on the target platform |
| Frontend | Repository `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` |
| PA demo | tutorial v1, two synthetic records in `examples/tutorial/protocols.jsonl` | Git-tracked bytes; record SHA-256, expect two protocol IDs and source-linked differences |
| DA demo | tutorial v1, synthetic AnnData 4 cells by 2 genes | Generated locally; published `sha256:` version, two cell types at 0.5 each |
| Formal data | Operator-provided protocol JSONL and published h5ad | Record versions/checksums, protocol/sample mapping, expression layer/scale and applicable obs columns; no universal atlas version is required |

HDF5 bytes may vary with the writer version; the catalog records the actual
generated file's checksum. Synthetic data are for software acceptance only.
Software deployment does not certify reproduction of the full paper data.

The local smoke run recorded the PA fixture SHA-256
`6e9552b5decf246acadeeb733f0f823cc7b3c44bf6b95af90e32c3bf7a6c1cd0` and a
generated h5ad SHA-256 of
`0f6d00bc19ca9b80c35681b703ea62f69d33e85833a7b82ca013593123415466`.
These are checksums of the tested files, not required Linux checksums.

## Install and Initialize

Follow [Linux installation](linux_deployment.md) in a clean environment. Keep
the checkout, persistent workspace, datasets and credentials separate. From
the repository root on Linux, choose a new private directory:

```bash
umask 077
TRIAL="$HOME/pancagent-trial"
mkdir -p "$TRIAL/runs" "$TRIAL/langgraph-state"
cp -n .env.example "$TRIAL/trial.env"
python examples/tutorial/run_protocol_walkthrough.py --output "$TRIAL/runs/pa"
python examples/tutorial/prepare_h5ad_demo.py "$TRIAL/demo-v1"
```

On a fresh checkout, before launching LangGraph, point its local dev state at
the private persistent location with `ln -s "$TRIAL/langgraph-state" .langgraph_api`.
Do not replace an existing state directory in place or import untrusted state
files. Verify that the pinned Linux runtime uses this location and actually
restores the thread. The sealed run archive remains the independent fallback.

Edit `trial.env`: set `PROTOCOL_WORKSPACE_ROOT` to the absolute `runs` path,
`PROTOCOL_ANALYSIS_OUTPUT_DIR` to the printed `OUTPUT_DIR` (not its parent),
and `PROTOCOL_DATA_CATALOG` to the printed catalog path. Set the DA and PO output
directories under the same workspace. Configure a model and its key privately.
Keep `PROTOCOL_ACCESS_MODE=local`, `PROTOCOL_CODE_EXECUTION=disabled`,
`PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR=0`, and `HOST_ADDRESS=127.0.0.1`.

Copy `frontend/.env.local.example` to `frontend/.env.local` and use
`NEXT_PUBLIC_API_URL=http://127.0.0.1:2024` and
`PANCAGENT_BACKEND_URL=http://127.0.0.1:8000`. Do not put keys in frontend env.
Run the preflight before starting services:

```bash
python scripts/deployment_preflight.py --env "$TRIAL/trial.env"
PROTOCOL_ENV_FILE="$TRIAL/trial.env" bash scripts/start_linux.sh chat
PROTOCOL_ENV_FILE="$TRIAL/trial.env" bash scripts/start_linux.sh api
bash scripts/start_linux.sh frontend-build
bash scripts/start_linux.sh frontend-start
```

Run chat, API and frontend-start in separate terminals. Keep all three loopback
only. Forward browser ports over SSH:

```bash
ssh -N -L 3000:127.0.0.1:3000 -L 2024:127.0.0.1:2024 USER@LINUX_HOST
```

Visit `http://127.0.0.1:3000`. The frontend server contacts the API on Linux;
no public API port or tunnel to port 8000 is needed.

## Acceptance Gates

1. Run `pip check`, `python -m pytest -q backend/tests`,
   `bash scripts/tests/test_start_linux.sh`, frozen frontend install and production
   build. After the clean environment passes, capture
   `python -m pip freeze --exclude-editable > constraints-linux-py311.txt`.
   Reinstall using `python -m pip install -c constraints-linux-py311.txt -e '.[h5ad,server,dev]'`
   in a second clean environment and repeat the gates. Record architecture and
   exact interpreter/tool versions alongside the lock. Do not use a Windows freeze.
2. Compare `tutorial_a` and `tutorial_b` in the browser. Open an original step
   and source, draft a follow-up without submitting, then submit once. Verify
   the bound run/result/version/field and refresh without duplicate submission.
3. In Project & Data, select `tutorial-synthetic` and ask for cell composition
   using `cell_type`. Check that both proportions are 0.5. Then copy a small
   authorized personal h5ad to durable storage, add a new `user_scrna` entry to
   the operator-owned catalog with absolute path, `user_id=local_user`,
   `project_id=default`, dataset ID and correct annotations. Run
   `run-archive publish-catalog --catalog /path/catalog.json --dataset-id YOUR_ID`
   only after transfer completes. Verify the ready version and repeat one task.
   The browser does not upload data or accept arbitrary server paths.
4. Record run IDs and verify sealed archives using
   `run-archive verify --run-dir /path/to/run`. Restart services and open the
   same thread. Also test archive recovery independent of conversation storage:
   `run-archive export-preview --run-dir /path/to/run --output /private/recovered.json`,
   transfer privately and open it with the workbench's read-only import button.
   Confirm values, source IDs and version. This preview is not an original
   byte-for-byte artifact export. Never share it publicly.
5. Check missing input and stale catalog versions fail clearly. Confirm no service
   listens on a public interface and code execution stays disabled. Record outcomes
   on the target host before tagging the exact accepted commit.

Multi-user isolation, public serving, full data downloads and Docker are not
trial requirements. Use the same accepted commit on Windows later and repeat
the demos in a clean Windows environment before claiming Windows support.

## Local Candidate Verification (2026-11-10)

- Full backend: 362 passed, 1 skipped, no failures. JUnit output:
  `.tmp/trial-release-final.xml`. Ruff, `pip check` and whitespace checks passed.
- PA and synthetic h5ad publication/composition passed. Archive export preserved
  the source run and rejected a modified result; export also worked on an actual
  sealed test run.
- Frontend: 11 unit tests, Windows production webpack build and desktop/narrow
  browser regression passed. Browser transport was mocked; this is not a live
  model or Linux deployment test.
- Launcher checks passed under Git Bash with stubbed entrypoints. Read-only
  preflight correctly rejected the local Windows host and mismatched Node/pnpm
  versions while validating the demo catalog and mapped columns.
- Target-host SSH authentication was unavailable. Clean Linux install,
  target-platform dependency freeze, live browser workflow and restart
  persistence are still release gates.
