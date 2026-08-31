# Contribution Rules

These rules are mandatory for every commit in this repository.

## Pre-Commit Checks

Before every commit, the contributor must check and record that:

1. Code correctness has been checked.
   - Run the available automated tests for the changed area.
   - At minimum for the current project state, run:
     ```powershell
     conda run -n protocol_agent python -m pytest
     ```
   - If tests cannot be run, document the exact reason before committing.

2. Inline comments are appropriate and maintainable.
   - Comments must explain non-obvious intent, constraints, or domain assumptions.
   - Comments must not restate the code mechanically.
   - Stale, misleading, commented-out, or placeholder comments must be removed.

3. Generated/runtime artifacts are not committed.
   - Do not commit `runs/`, `.langgraph_api/`, `.env`, or large local input datasets under `inputs/`.
   - Commit reusable code, schemas, configs, docs, and small examples only.

4. LLM-related outputs remain auditable.
   - LLMs may propose structures or evidence pointers.
   - Final matrix values must be computed by deterministic code from structured protocol artifacts.
   - New FeatureSpec columns must pass schema, source-evidence, and compiler checks.

## Commit Message

Use a concise commit message that states the functional change, for example:

```text
Add LangGraph protocol chat server
```
