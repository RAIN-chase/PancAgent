# PancAgent Research Workbench

## Scope

The default Next.js page is now a research workbench, not a chat landing page.
The existing LangGraph SDK, authentication headers, thread history, messages,
interrupt handling and report contracts are retained. No Agent, scheduler,
database, chart dependency or server-file route was added.

- `frontend/src/components/workbench/index.tsx`: navigation, thread-scoped
  selections, read-only archive inspection, result/evidence/chat coordination.
- `frontend/src/components/workbench/results.tsx`: protocol differences,
  source-ordered event timelines, original steps, semantic alignment and generic
  PA/DA/PO tables. Filtering and pagination affect presentation only.
- `frontend/src/lib/workbench.ts`: saved-message/envelope/report adapters,
  strict JSON-pointer lookup and lossless JSON export.
- `frontend/src/components/thread/index.tsx`: compact conversation composer and
  existing message renderer; only explicit submission creates a run.

## Saved Contracts

| UI content | Saved source |
| --- | --- |
| Protocol names/identities | `result_preview.tool_result.protocol_a/b`, multi-comparison `protocols`, and lookup `results` |
| Feature differences | `result_preview.tool_result.feature_differences` |
| Events, raw steps, alignment | `result_preview.tool_result.event_step_diff` |
| DA values / statistical limitations | envelope `result_preview`, `validation`, `limitations`, `confidence_caveats` |
| PO explanations / hypotheses | envelope `result_preview`; no inferred probability |
| Scientific conclusions / counterevidence | `scientific-analysis-report-v3.conclusions` |
| Measurements | report JSON pointers resolved against loaded tool envelopes |
| Run / input / tool versions / parameters | `run_id` and envelope `result_refs` |
| Conversation context | state `request.session_summary`, explicitly not scientific facts |

The report associated with a selected result must match its run ID. Missing
referenced results display as unavailable, never as zero. Original decimal
strings, units and nulls are preserved; no calculations are performed in the
browser. `0`, missing, `unparseable`, `not_applicable` and `explicit_not_used`
remain distinct. A compiled zero does not establish non-use in source text.
Timelines show source event order and reported days, not inferred durations or
semantic equivalence. PA feature lists and DA previews may be backend-limited;
pagination does not imply the full scientific dataset was transferred.

An evidence drawer identifies the exact result and JSON pointer and displays
result-level source references; these references are not automatically treated
as field-level proof. JSON export serializes the loaded saved object, not a
reconstruction from Markdown. Server absolute paths are never used as browser
download URLs.

## Execution And Recovery

Selecting results, filtering rows, opening evidence and exporting JSON never
submit model requests. Comparing an uncomputed selection or requesting a follow-up
prepares a version-aware question draft; the researcher must explicitly submit.
The current SDK's `reconnectOnMount` rejoins a saved run via session storage,
not a second run submission. Submission is guarded while a request is active.
Cancellation remains disabled: `useStream.stop()` may call the server's cancel
endpoint, so it is not presented as a harmless disconnect operation.

Thread/project scope changes remount transient workbench state, clearing
imports, selected results, evidence and drafts. Conversation visibility remains
a URL preference. The existing backend still authorizes thread access; UI
scope-reset behavior is not an authorization boundary.

## Explicitly Not Implemented

This is the read-only workbench and conversation-integration foundation (plan
stages 2/3), not a full project registry or a new DA/PO visualization engine.

- No global project switching or browser dataset registration. The project
  panel combines conversation summaries with a server-owned metadata catalog;
  see [data catalog and multi-compare](data_catalog_and_multi_compare.md).
- Context is read-only; editing persistent memory remains a later interface.
- No remote arbitrary artifact downloads, h5ad upload, recomputation in charts,
  heatmap previews or unattended experiment approval.
- Local archive inspection accepts supported JSON messages, reports or evidence
  envelopes up to 10 MB. It is explicitly unverified/read-only, does not enter
  server memory and cannot initiate follow-up analysis until returning to the
  live conversation. It does not fetch missing result paths from the server.
- Authentication/production persistence/backup are still server responsibilities.
  The local development server is not a production deployment.

## Verification

```bash
cd frontend
node --test tests/conversation-presentation.test.cjs tests/workbench.test.cjs
# Start the local Next.js dev server separately, then:
WORKBENCH_URL=http://127.0.0.1:3100 node tests/workbench.browser.cjs
```

Use `PLAYWRIGHT_CHANNEL=chrome` if using an installed Chrome instead of the
Playwright browser bundle. Tests use clearly synthetic contract fixtures under
`frontend/tests/fixtures`, never application demonstration data or real h5ad.
Browser tests check desktop/mobile layouts, unchanged downloads, evidence and
counterevidence, presentation-only filtering, scope reset, actual SDK streaming,
follow-up drafts and rejoining a run without resubmission. Provider computation
and multi-user server authorization require separate integration verification.
