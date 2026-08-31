# ProtocolOptimizationAgent Frontend

This folder is reserved for the UI layer only.

Frontend responsibilities:

- Collect user input and display protocol-analysis results.
- Call the backend HTTP API.
- Never run LangGraph or agent logic directly.
- Never read Agent1 artifacts, h5ad files, or generated backend outputs directly.

Backend boundary:

- `POST /protocol-analysis/run`
- `POST /protocol-query/find`
- `POST /protocol-query/compare`
- `POST /protocol-query/chat`
- `GET /health`

Start the current static frontend shell from the repository root:

```powershell
.\scripts\start_frontend_static.ps1 -Port 5173
```

For `agent-chat-ui`, connect it to the LangGraph Server instead:

```text
http://127.0.0.1:2024
graph: protocol_chat
```
