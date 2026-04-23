# Claude OTEL Monitor

A real-time dashboard that captures OpenTelemetry-style traces emitted by
every Anthropic API call and surfaces them as an interactive timeline.

```
┌──────────────┬──────────────────────────────┬──────────────────┐
│  Trace list  │  Span waterfall + attributes  │  Chat with Claude │
│  (live SSE)  │  (click to expand)            │  (triggers calls) │
└──────────────┴──────────────────────────────┴──────────────────┘
          ▲  Metrics bar — calls · tokens · latency · error rate
```

## Features

| Feature | Detail |
|---|---|
| **Live traces** | Server-Sent Events push — new spans appear instantly |
| **Span waterfall** | Proportional timeline bar for every span in a trace |
| **Full attributes** | GenAI semantic-convention attributes (tokens, model, finish reason) |
| **Prompt / completion** | Expandable span events showing exact input and output |
| **Metrics bar** | Aggregates — total calls, input/output tokens, avg latency, error rate |
| **Chat panel** | Built-in chat UI to generate traces without external tooling |
| **Error tracking** | Error spans with `error.type` and `error.message` attributes |

## Quick start

```bash
# 1. Clone
git clone <repo> && cd test-managed-agents

# 2. Set API key
cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...

# 3. Launch (installs deps automatically)
./start-dev.sh
```

Open **http://localhost:3000** — the backend runs on **http://localhost:8000**.

## Docker Compose

```bash
ANTHROPIC_API_KEY=sk-ant-... docker compose up --build
# Frontend → http://localhost:3000
# API      → http://localhost:8000
```

## REST API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/traces` | All traces (summary, newest first) |
| `GET` | `/api/traces/{id}` | Full span list for one trace |
| `GET` | `/api/spans` | Every raw span |
| `GET` | `/api/metrics` | Aggregate metrics |
| `POST` | `/api/chat` | Send a message to Claude |
| `GET` | `/api/events` | SSE stream — live span events |

### POST /api/chat

```json
{
  "messages": [{"role": "user", "content": "Hello"}],
  "model": "claude-opus-4-5",
  "max_tokens": 1024,
  "temperature": null,
  "system": null
}
```

## OTEL span attributes

Each root span carries:

```
gen_ai.system                  = "anthropic"
gen_ai.request.model           = "claude-opus-4-5"
gen_ai.request.max_tokens      = 1024
gen_ai.request.temperature     = 0.7          (if set)
gen_ai.usage.input_tokens      = 42
gen_ai.usage.output_tokens     = 187
gen_ai.response.finish_reasons = ["end_turn"]
gen_ai.response.id             = "msg_…"
llm.latency_ms                 = 1234.56
```

Two span **events** per call:
- `prompt` – rendered conversation (truncated at 4 096 chars)
- `completion` – assistant response text (truncated at 4 096 chars)

Error spans additionally set `error.type` and `error.message`.

## Project structure

```
test-managed-agents/
├── backend/
│   ├── main.py           # Flask app, REST endpoints, SSE stream
│   ├── telemetry.py      # Span store, context manager, in-process exporter
│   ├── claude_client.py  # Instrumented Anthropic HTTP wrapper
│   └── requirements.txt  # flask, requests
├── frontend/
│   ├── src/
│   │   ├── App.js        # Dashboard — metrics, trace list, waterfall, chat
│   │   └── App.css
│   ├── public/index.html
│   ├── package.json
│   └── nginx.conf        # Reverse proxy for the Docker build
├── Dockerfile.backend
├── Dockerfile.frontend
├── docker-compose.yml
├── start-dev.sh          # One-command dev launcher
└── .env.example
```

## Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+ · Flask · `requests` |
| Telemetry | Custom OTEL-compatible span store (stdlib only) |
| Real-time | Server-Sent Events (`text/event-stream`) |
| Frontend | React 18 · plain CSS |
| Container | Docker Compose + Nginx reverse proxy |
