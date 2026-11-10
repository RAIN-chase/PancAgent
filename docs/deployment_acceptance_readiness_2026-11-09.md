# Deployment acceptance readiness — 2026-11-09

## Local checks completed

- Backend: 354 passed, 1 skipped, 4 Windows socket-dependent tests deselected, 2 dataset-publication tests failed before the atomic-rename fix. The full `test_run_archive.py` file passed afterward (18 tests). A complete post-fix backend run is still required on Linux.
- Backend Ruff passed. The changed Orchestrator passed targeted Mypy; repository-wide Mypy still has unrelated errors and is not the documented pre-commit gate.
- Frontend: 10 unit tests passed, TypeScript passed, ESLint had no errors (16 warnings), and `next build --webpack` succeeded. Default Turbopack could not spawn a CSS worker inside the Windows sandbox.
- `pip check` passed in the `protocol_agent` environment.

## Acceptance on the intended Linux host

From a fresh Python 3.11 environment and installed frontend dependencies:

```bash
python -m pip check
python -m pytest -q backend/tests
bash scripts/tests/test_start_linux.sh
cd frontend && pnpm install --frozen-lockfile && pnpm build
```

Pass criteria: no unexpected skips or failures, frontend production build completes without network-dependent font fetching, and the Linux launcher checks pass. Then run one small authorized PA/DA/PO workflow, verify its saved result references and hashes, restart the service, and confirm conversation recovery in a fresh run. For shared access, exercise authenticated identity, project permissions, and artifact denial cases. If controlled code execution is enabled, perform the real rootless Linux isolation acceptance in `controlled_code_execution.md`.

Scientific performance claims additionally require the frozen study inputs, private gold, independent expert ratings, and paper-reference reproduction listed in `research_quality_validation.md`. The 24-question manifest check alone does not satisfy that gate.
