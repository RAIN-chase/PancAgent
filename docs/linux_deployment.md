# Linux deployment

The analysis code is shared between Windows and Linux. Do not copy a Windows
Conda environment, `node_modules`, or historical runtime database into the new
installation. Linux end-to-end validation is required before using real workloads.

## Install

Use Python 3.11 for the LangGraph local server and Node.js 22 with pnpm 10.5.1
for the current Next.js frontend. Clone the repository, then run from its root:

```bash
conda create -n protocol_agent python=3.11 -y
conda activate protocol_agent
python -m pip install -e ".[h5ad,server,dev]"
python -m pip check
cp -n .env.example .env
cp -n configs/default_config.json configs/server.local.json
cp -n frontend/.env.local.example frontend/.env.local
cd frontend
pnpm install --frozen-lockfile
```

Edit these local files before starting. Do not commit credentials. The server
extra installs the local LangGraph CLI and dotenv loader; h5ad installs the
current phenotype analysis dependencies. Optional advanced analysis tools are
not needed for initial deployment. Editable installation also retains access
to the tracked gene-program CSV files outside the Python package.

## Configure data and runtime

- In `configs/server.local.json`, set Linux `input_dir` and `output_dir`, and
  select a model available to your provider. Batch LLM settings are independent
  of the `PROTOCOL_CHAT_*` conversation settings in `.env`.
- Upload `protocols.jsonl`, `protocol_h5ad_manifest.csv`, and actual h5ad files
  separately. The default input folder does not currently include the required
  manifest. Use Linux absolute paths in the manifest's `h5ad_path` column.
- Verify sample/protocol identifiers and the configured AnnData `obs` columns.
  Paths are case-sensitive on Linux. Relative configuration paths resolve from
  the repository root when launched through the script.
- Set `PROTOCOL_WORKSPACE_ROOT` to a writable, persistent directory, for example
  `/srv/protocol-agent/runtime`. Configure PA, DA and PO output locations to the
  actual generated artifacts; copying empty directories is not sufficient.
- If relocating gene-program inputs, set `PROTOCOL_MATURITY_PROGRAM_DIR` to their
  directory. Keep input datasets separate from writable output directories.
- Start with a new Linux runtime. Old memory and archived artifacts can contain
  Windows absolute paths. Historical migration requires path/provenance checks;
  do not rewrite immutable archived files indiscriminately.

The launcher loads `.env` for chat, API and batch via python-dotenv, without
evaluating it as shell code. Exported shell variables take precedence in the
launcher. Set `PROTOCOL_ENV_FILE` to use another dotenv file. LangGraph also
reads the `.env` configured in its JSON file: keep that file consistent or update
the LangGraph configuration when using an alternative env file.
Frontend environment configuration is loaded by Next.js from `frontend/`.
Public variables must be configured before building; changing them requires a
new build. No API key belongs in a `NEXT_PUBLIC_*` variable.

## Start a private research deployment

Activate `protocol_agent` in each backend terminal. From the repository root:

```bash
bash scripts/start_linux.sh chat
```

In another terminal, build and start the frontend:

```bash
bash scripts/start_linux.sh frontend-build
bash scripts/start_linux.sh frontend-start
```

Defaults bind to `127.0.0.1`: chat on port 2024, frontend on port 3000.
Set `HOST_ADDRESS` or `PORT` explicitly to override them. The script does not
overwrite environment files or select another port silently. Stop with Ctrl+C.

For a single trusted operator, keep `NEXT_PUBLIC_API_URL=http://localhost:2024`
and run this on your own computer, replacing the SSH destination:

```bash
ssh -L 3000:127.0.0.1:3000 -L 2024:127.0.0.1:2024 user@server
```

Open `http://localhost:3000`. For direct remote access, use the server URL
instead of browser localhost. For frontend development, use `frontend-dev`;
set `NEXT_ALLOWED_DEV_ORIGINS` to permitted hostnames/IPs when needed.

Optional direct API and batch entrypoints:

```bash
bash scripts/start_linux.sh api
bash scripts/start_linux.sh batch --config configs/server.local.json
```

The frontend conversation uses LangGraph, not the optional port-8000 API.
These commands do not install dependencies or fabricate missing inputs.

## Authentication and production boundary

Do not expose the local profile publicly. For authenticated access, configure
`PROTOCOL_ACCESS_MODE=authenticated`, `PROTOCOL_AUTH_CONFIG`, per-user tokens and
project/artifact permissions following [authenticated_deployment.md](authenticated_deployment.md).
The launcher automatically selects `langgraph.authenticated.json` when the
loaded access mode is authenticated. Chat rejects extra CLI arguments; host and
port are configured through environment variables. `LANGGRAPH_CONFIG` can select
a custom configuration only when the access mode is not authenticated.

`langgraph dev` remains a development/testing server, even with authentication.
Production requires an appropriate persistent Agent Server deployment,
HTTPS/reverse proxy with streaming support, and operational backup policies.
A service manager alone does not convert the dev server into production.

## Verify and update

Before real analysis, run:

```bash
python -m pytest -q backend/tests
bash scripts/tests/test_start_linux.sh
```

Then test one small PA/DA/PO workload, frontend conversation, and memory recovery
after restart. Record the deployed commit with `git rev-parse HEAD`. Validation
of launcher syntax does not verify model connectivity or scientific outputs.

For updates: finish active analyses, stop services, check `git status`, and run
`git pull --ff-only`. Reinstall dependencies if changed, run the checks, rebuild
the frontend if changed, then restart. Back up runtime databases and archives
before schema changes. Keep server-only configuration in ignored local files.
Do not modify tracked application code directly on the server.
