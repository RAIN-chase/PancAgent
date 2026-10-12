# Conversation Entry And Report Presentation

Orchestrator distinguishes ordinary conversation from scientific execution in
the existing configure/plan/finalize nodes; no separate Agent is introduced.

- Exact greetings, acknowledgements and capability questions return text without
  model/tool calls or domain-output snapshots. Capability descriptions use the
  available Action Registry entries. Mixed greetings plus scientific requests
  continue through scientific planning, not the greeting shortcut.
- Vague requests ask for the objects/data and endpoint. A model-generated plan
  may have no steps only when it includes explicit clarification questions.
  Standalone `scientific.state` is rejected as a filler for non-state questions.
  Relevant history is already supplied to planning through scoped memory.
- Ordinary replies and pure clarification create no `analysis_report` message,
  scientific observations or interpretations. Original messages, conversation
  summary revisions and a sealed `conversation_response.json` remain archived.
- A subsequent scientific question in the same thread still creates a child run
  and executes registered actions, retaining the conversation summary.

Review schema/reference validation failure uses `analysis_error:invalid_review`,
not scientific evidence insufficiency. The report retains valid tool results
and explicitly labels the failed automated review; fabricated values still do
not enter saved conclusions. Existing plan validation and tool error contracts
remain unchanged.

Scientific-state counters and pagination settings are not report measurements.
The complete saved tool JSON remains available for audit. Reports no longer link
server filesystem paths as HTTP downloads; the existing analysis-panel button
downloads the structured report carried by the final message. An authorized
server artifact API is still needed to download other archived files remotely.
Raw tool-call/result displays default to hidden, but can be enabled with the
existing toggle or `hideToolCalls=false` URL parameter. Zero-step conversation
and clarification do not render an Analysis panel.

## Checks

```bash
python -m pytest -q backend/tests/test_conversation_entry.py
cd frontend
node --test tests/conversation-presentation.test.cjs
```

Backend tests cover greeting/capability/vague turns, zero-step clarification,
filler rejection, checkpoint follow-up and metadata exclusion. Frontend tests
exercise server-rendered panel visibility, report-download availability and
consistent opt-in tool display. Real Linux/provider/UI streaming validation
remains a separate deployment check. Restart the backend with `--no-reload`
and rebuild the frontend to apply the changes; the server does not automatically
pick up modifications when automatic reloading is disabled.
