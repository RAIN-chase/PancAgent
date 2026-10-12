# Multi-Protocol Comparison and h5ad Directory

## Comparison

The `protocol.compare` action accepts `{"protocol_ids": ["p1", "p2", "p3"]}`
(2 to 100 distinct IDs). Existing explicit two-ID requests still work. The
workbench supports checkbox selection, saved-result matching and a question
draft for new analyses; submitting remains explicit.

For more than two objects the saved result includes a wide `feature_differences`
table, all protocol summaries, full events/steps/semantic annotations and
source references. Semantic alignments use the first selected protocol as an
explicit anchor; they are not all-pair equivalence claims. The feature limit
is reported alongside the total differences. Missing values are never filled
with zero. No phenotype ranking is inferred.

## Server Configuration

### Conversational Discovery

The registered read-only `data.catalog` action lists the datasets authorized for
the current server-bound user and project. Questions such as `当前有哪些数据可用`
or `list available datasets` execute this action without requiring a directory,
manifest or dataset ID. Inventory-only questions use a deterministic plan,
review and saved-JSON table renderer; no model call or upstream PA/DA run is
needed. Explicit expression, DE and maturity requests retain their analysis
routing rather than being replaced by an inventory lookup.

Answers distinguish unconfigured catalog, empty authorized scope, transferring
datasets, ready files, validation failures and catalog read errors. They include
IDs, roles, versions and available cell/gene counts without exposing server file
paths. File readiness does not establish scientific QC. The tool never searches
arbitrary filesystem directories. Subsequent analysis uses the selected
`request.dataset_id` and `request.dataset_version`; multiple plausible choices
or unknown sample mappings require clarification. The directory snapshot and
tool call remain in the normal run archive.

Copy `configs/data_catalog.example.json` to a server-owned configuration file
outside the Git checkout (or an ignored local configuration). Both supplied
Linux paths are recorded in the example, not hard-coded into algorithms.
The role assignments are inferred from filenames and must be confirmed.

Set in the server `.env` for **both** LangGraph and FastAPI:

```dotenv
PROTOCOL_DATA_CATALOG=/absolute/path/to/data_catalog.json
```

Set in `frontend/.env.local`:

```dotenv
PANCAGENT_BACKEND_URL=http://127.0.0.1:8000
```

Start three services in separate server terminals:

```bash
bash scripts/start_linux.sh chat
bash scripts/start_linux.sh api
bash scripts/start_linux.sh frontend-dev
```

The Next.js server proxies `/api/data-catalog` to FastAPI; port 8000 does not
need a browser tunnel. Existing SSH forwarding for 3000/2024 is unchanged.
Restart services after environment changes. Catalog edits are read on refresh.

## Transfer and Publication

Initial entries are `pending` or `transferring`. A file existing at its destination
does not complete a transfer. After a transfer has finished, publish each file:

```bash
python -m protocol_optimization_agent.interfaces.run_archive_cli publish-catalog \
  --catalog /absolute/path/to/data_catalog.json --dataset-id organoids-51-merged
python -m protocol_optimization_agent.interfaces.run_archive_cli publish-catalog \
  --catalog /absolute/path/to/data_catalog.json --dataset-id pancreatic-epi-reference
```

Publication is an operator command: it streams a SHA-256 once, reads h5ad axis
metadata, checks the file did not change, and atomically records version, size,
mtime and `ready`. It does not copy the large matrix or load it into memory.
Listing reads metadata only and rejects changed/missing files. Keep published
files server-owned/read-only; after moving/editing a file, update its catalog
path and republish. This stat check is not a substitute for immutable storage
against a malicious filesystem writer. Existing `register-input` supports
content-addressed read-only storage where stronger guarantees are required.

The merged 51-protocol file is **one dataset**, not 51 invented dataset entries
or 51 independent biological replicates. Inspect its `obs` and supply the real
sample/protocol mapping before protocol-level analyses. Reference availability
does not make unfinished reference-mapping methods available.

After checking actual `obs` columns, configure `cell_type_column`,
`sample_column`, `protocol_column` and (where known) `study_column` /
`biological_replicate_column` using the existing DA manifest field names.
Do not infer sample identities or independent repeats from the file name.

## Public and User Data

Roles are `reference_atlas`, `public_organoid`, `user_scrna`. Public/reference
entries are available to project members, optionally narrowed by `project_ids`.
User entries require `user_id` and `project_id`; authenticated deployments also
require an `artifact_id` whose authorized path matches the server catalog.
Identity comes from the existing backend authentication, never a query user ID.
Do not publish private samples under a public role. The API strips paths and
ownership fields; the browser does not read the filesystem.

Users may explore public datasets without providing a file. Selecting a ready
entry drafts a `data.assess` question with `request.dataset_id` and
`request.dataset_version` (a changed version requires confirmation); DA resolves
the authorized server catalog and runs only the specified task. Scientific
validation (annotations, expression scale, repeats) remains task-specific.

For user data, transfer h5ad to authorized server staging with existing site
transfer facilities, add its server-owned catalog entry, then publish. This
delivery **does not add a resumable browser upload endpoint**. The JSON archive
import remains separate and never accepts h5ad. No source data has been opened
or verified on the remote server from this workstation.
