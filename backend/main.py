"""
Flask backend for the Claude OTEL Monitor.

Endpoints:
  GET  /api/traces           – list of trace summaries
  GET  /api/traces/<id>      – full span list for one trace
  GET  /api/spans            – all raw spans
  GET  /api/metrics          – aggregate metrics
  POST /api/chat             – proxy a chat request to Claude
  GET  /api/events           – SSE stream of new spans (text/event-stream)

OTLP receiver (compatible with Claude Code):
  POST /v1/traces            – OTLP JSON trace ingest
  GET  /debug/requests       – last 50 raw incoming requests (for debugging)
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import deque

from flask import Flask, Response, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv()

from telemetry import span_store  # noqa: E402
from claude_client import chat    # noqa: E402
from otlp_receiver import parse_otlp_json  # noqa: E402
from api_proxy import proxy_request  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("monitor")

app = Flask(__name__, static_folder=None)

# ── Request log (ring buffer, shown at /debug/requests) ───────────────────────

_request_log: deque[dict] = deque(maxlen=50)


@app.before_request
def _log_request():
    entry = {
        "time":         time.strftime("%H:%M:%S"),
        "method":       request.method,
        "path":         request.path,
        "content_type": request.content_type,
        "body_bytes":   request.content_length or 0,
    }
    _request_log.append(entry)
    log.info("%s %s  ct=%s  len=%s",
             entry["method"], entry["path"],
             entry["content_type"], entry["body_bytes"])


# ── CORS ──────────────────────────────────────────────────────────────────────

@app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response


@app.route("/api/<path:p>", methods=["OPTIONS"])
@app.route("/v1/<path:p>",  methods=["OPTIONS"])
def _options(p):
    return "", 204


# ── Anthropic API proxy ───────────────────────────────────────────────────────
# Set ANTHROPIC_BASE_URL=http://localhost:8000 in Claude Code to route all
# API calls through here. Each call gets a telemetry span automatically.

@app.route("/v1/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def anthropic_proxy(path: str):
    # /v1/traces is our own OTLP endpoint — don't proxy it
    if path == "traces":
        return otlp_traces()
    try:
        body, status, headers = proxy_request(f"v1/{path}", request)
        return body, status, dict(headers)
    except Exception as exc:
        log.error("Proxy error: %s", exc)
        return jsonify({"error": str(exc)}), 502


# ── REST endpoints ────────────────────────────────────────────────────────────

@app.get("/api/traces")
def get_traces():
    return jsonify(span_store.traces())


@app.get("/api/traces/<trace_id>")
def get_trace(trace_id: str):
    spans = span_store.by_trace(trace_id)
    if not spans:
        return jsonify({"error": "not found"}), 404
    return jsonify({"trace_id": trace_id, "spans": spans})


@app.get("/api/spans")
def get_spans():
    return jsonify(span_store.all_spans())


@app.get("/api/metrics")
def get_metrics():
    return jsonify(span_store.metrics())


# ── Debug endpoint ────────────────────────────────────────────────────────────

@app.get("/debug/requests")
def debug_requests():
    """Shows the last 50 incoming requests — useful to verify Claude Code is sending data."""
    return jsonify(list(_request_log))


@app.post("/debug/echo")
def debug_echo():
    """Echoes back exactly what was received — useful to inspect raw payloads."""
    return jsonify({
        "content_type": request.content_type,
        "body_text":    request.get_data(as_text=True)[:4096],
    })


# ── Chat proxy ────────────────────────────────────────────────────────────────

@app.post("/api/chat")
def post_chat():
    body = request.get_json(force=True)
    try:
        result = chat(
            messages=body["messages"],
            model=body.get("model", "claude-opus-4-5"),
            max_tokens=body.get("max_tokens", 1024),
            temperature=body.get("temperature"),
            system=body.get("system"),
        )
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


# ── OTLP receiver ─────────────────────────────────────────────────────────────

@app.post("/v1/traces")
def otlp_traces():
    """
    Accepts OTLP/HTTP JSON traces.
    If protobuf arrives instead, logs a clear message so the user knows
    to set OTEL_EXPORTER_OTLP_PROTOCOL=http/json.
    """
    ct = request.content_type or ""

    if "protobuf" in ct or "octet-stream" in ct:
        log.warning(
            "Received protobuf on /v1/traces — set "
            "OTEL_EXPORTER_OTLP_PROTOCOL=http/json and retry"
        )
        return jsonify({"error": "protobuf not supported, use http/json"}), 415

    body = request.get_json(force=True, silent=True)
    if not body:
        raw = request.get_data(as_text=True)[:200]
        log.warning("Could not parse /v1/traces body: %s", raw)
        return jsonify({"error": "invalid JSON body"}), 400

    spans = parse_otlp_json(body)
    log.info("OTLP /v1/traces → %d span(s) ingested", len(spans))
    for span in spans:
        span_store.add(span)

    return jsonify({"partialSuccess": {}}), 200


# ── SSE stream ────────────────────────────────────────────────────────────────

def _sse_stream():
    q = span_store.subscribe()
    init_payload = json.dumps({
        "type":    "init",
        "traces":  span_store.traces(),
        "metrics": span_store.metrics(),
    })
    yield f"data: {init_payload}\n\n"
    try:
        while True:
            try:
                span = q.get(timeout=15)
                yield f"data: {json.dumps({'type': 'span', 'span': span})}\n\n"
            except Exception:
                yield ": heartbeat\n\n"
    finally:
        span_store.unsubscribe(q)


@app.get("/api/events")
def sse_events():
    return Response(
        _sse_stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Static frontend ───────────────────────────────────────────────────────────

_build_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "build")


@app.get("/")
@app.get("/<path:path>")
def serve_frontend(path: str = ""):
    if os.path.isdir(_build_dir):
        target = os.path.join(_build_dir, path)
        if path and os.path.isfile(target):
            return send_from_directory(_build_dir, path)
        return send_from_directory(_build_dir, "index.html")
    return "<h2>Frontend not built. Run <code>npm run build</code> in frontend/</h2>", 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("Starting Claude OTEL Monitor on http://0.0.0.0:8000")
    log.info("API proxy ready   — set ANTHROPIC_BASE_URL=http://localhost:8000")
    log.info("OTLP receiver     — POST http://localhost:8000/v1/traces")
    log.info("Debug log         — GET  http://localhost:8000/debug/requests")
    app.run(host="0.0.0.0", port=8000, threaded=True)


def _sse_stream():
    """Generator that yields SSE-formatted messages as new spans arrive."""
    q = span_store.subscribe()
    # send current state on connect
    init_payload = json.dumps({
        "type": "init",
        "traces": span_store.traces(),
        "metrics": span_store.metrics(),
    })
    yield f"data: {init_payload}\n\n"

    try:
        while True:
            try:
                span = q.get(timeout=15)  # 15 s heartbeat
                payload = json.dumps({"type": "span", "span": span})
                yield f"data: {payload}\n\n"
            except Exception:
                # timeout → send a heartbeat comment to keep connection alive
                yield ": heartbeat\n\n"
    finally:
        span_store.unsubscribe(q)


@app.get("/api/events")
def sse_events():
    return Response(
        _sse_stream(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Static frontend (production build) ───────────────────────────────────────

_build_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "build")


@app.get("/")
@app.get("/<path:path>")
def serve_frontend(path: str = ""):
    if os.path.isdir(_build_dir):
        target = os.path.join(_build_dir, path)
        if path and os.path.isfile(target):
            return send_from_directory(_build_dir, path)
        return send_from_directory(_build_dir, "index.html")
    return "<h2>Frontend not built. Run <code>npm run build</code> in frontend/</h2>", 200


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
