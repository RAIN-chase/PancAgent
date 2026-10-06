# Authenticated Deployment

Updated 2026-10-01. `PROTOCOL_ACCESS_MODE=local` remains an explicitly trusted single-operator mode, not a multi-user security boundary. Do not expose that profile to untrusted users.

## Server Configuration

Update the installed project with `pip install -e .`; the custom auth adapter declares its directly used `langgraph-sdk` dependency (validated against SDK 0.4.2). Install the existing `dev` extras for the LangGraph CLI when needed.

An administrator provisions an untracked `configs/auth.local.json` using `configs/auth.example.json` as the shape. Paths must be absolute and artifacts must remain beneath the configured user/project root. Users cannot register arbitrary artifact paths through requests. Large inputs may be mounted beneath that root by the administrator; links resolving outside it are refused.

Generate a separate opaque random token per user and store only its SHA-256 hash in the server configuration:

```bash
python -c "import secrets,hashlib; token=secrets.token_urlsafe(32); print(token); print(hashlib.sha256(token.encode()).hexdigest())"
```

Deliver the token privately. Never commit it, add it to `NEXT_PUBLIC_*`, log it or place it in a URL. Protect the configuration file with server OS permissions. Removing/changing a user's hash revokes that token; memberships use explicit `read`, `write`, and `review` permissions. This is a small server-managed credential backend, not OAuth/SSO or a user-registration service. TLS, reverse-proxy rate limiting, credential rotation and operational backups remain deployment responsibilities.

Set these server environment values, including the same values in the untracked `.env` when LangGraph loads it:

```text
PROTOCOL_ACCESS_MODE=authenticated
PROTOCOL_AUTH_CONFIG=/srv/protocol-agent/configs/auth.local.json
PROTOCOL_RESEARCH_PROJECT_ID=pancreas
```

Missing configuration/identity or an unknown access mode fails closed. Authenticated mode ignores shared output defaults and the local `PROTOCOL_ALLOW_EXTERNAL_OUTPUT_DIR` bypass. Runtime folders are under `users/{identity}/projects/{project_id}/runs/{run_id}`. Scientific databases remain per-user/per-project under `users/{identity}/research/{project_id}`; project membership is checked before access.

## Entry Points

```bash
langgraph dev --config langgraph.authenticated.json --host 127.0.0.1 --port 2024
protocol-api --host 127.0.0.1 --port 8000
```

On Windows the existing `scripts/start_langgraph_server.ps1` accepts `-ConfigPath langgraph.authenticated.json`; it selects that file automatically when the shell access mode is authenticated. `langgraph dev` is a development/testing server, not the complete production hosting setup. The deployment runtime must support LangGraph custom auth. Use TLS at the reverse proxy, forward per-user Authorization headers, and do not substitute one shared upstream credential for all clients.

The LangGraph adapter follows the installed SDK's [custom Auth contract](https://reference.langchain.com/python/langgraph-sdk/auth/Auth): it authenticates Bearer credentials, stamps/filter-checks thread ownership, permits assistant discovery/read and denies unhandled resource operations. Both graphs require the server-injected `langgraph_auth_user` context; missing it is rejected, including when accidentally using the non-auth configuration in authenticated mode. Python graph invocation and its reserved configuration are trusted server interfaces, not public JSON authentication APIs. Never let a gateway accept arbitrary client `configurable.langgraph_auth_user` as trusted identity.

Example graph input:

```json
{
  "project_id": "pancreas",
  "protocol_output_artifact_id": "pa_outputs",
  "data_analysis_output_artifact_id": "da_outputs",
  "messages": [{"role": "user", "content": "Compare p1 and p2"}]
}
```

The protocol-only graph uses `output_artifact_id`. DA manifest rows use `artifact_id` instead of `h5ad_path`. Identity/workspace are server-owned; client paths, external tool paths, scripts and LLM credential/endpoint overrides are disabled. Graph output artifacts require read/write access because tools can write visualizations and ingest scientific state. Registered input artifacts need only read access. Server-admin paths are rechecked after symlink resolution; do not grant users OS write access to credential/configuration directories.

The standalone API checks credentials on all routes except `/health`. PA run requests use `input_artifact_id` and `output_artifact_id`. Query requests use `project_id`, `output_artifact_id`, and query parameters. Supplying raw `input_dir`/`output_dir` is rejected in authenticated mode. Unknown fields, including client-declared `user_id`, are rejected. Review/performed/feedback/admission endpoints under `/scientific/experiments/` additionally require `review` permission, and reviewer identity comes from the verified credential. Feedback/admission payload actors must match that identity.

## Frontend And CLI

For direct access to the guarded LangGraph URL, set frontend `NEXT_PUBLIC_AUTH_SCHEME=bearer`, `NEXT_PUBLIC_ASSISTANT_ID=orchestrator`, and `NEXT_PUBLIC_API_URL` to the guarded service. Restart Next.js. Enter a per-user token through the existing connection form; streaming, status checks and thread clients now send Bearer headers. Tokens use the existing browser credential storage; use only a trusted frontend origin. No OAuth login/session management or dedicated approval UI is added here. The generic shared-key Next.js proxy is not the configured multi-user gateway.

For CLI commands, set `PROTOCOL_AUTH_TOKEN` in the process environment (never a public frontend variable or command-line token). CLI verifies it, binds user identity and checks project/researcher permissions. `--user-id` cannot impersonate another user. Researcher arguments and feedback JSON must identify the authenticated user. CLI input files and output directories in authenticated mode must be inside the authorized project.

Legacy direct analysis commands/Python agent classes remain trusted OS-operator interfaces. Do not expose their arbitrary filesystem arguments as public endpoints or delegate them under one shared privileged OS account; public multi-user requests must use the guarded service adapters above.

Required checks before multi-user release include real HTTP auth-hook/thread ownership tests against the deployed LangGraph runtime, reverse-proxy header forwarding/TLS, concurrent stream cancellation, token revocation and backup restoration. Unit/API tests cover policy, both graph runtime guards, path access and identity-bound approvals, but do not replace deployment verification.
