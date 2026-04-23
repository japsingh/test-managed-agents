"""
Flask backend for the Claude OTEL Monitor.

Endpoints:
  GET  /api/traces           – list of trace summaries
  GET  /api/traces/<id>      – full span list for one trace
  GET  /api/spans            – all raw spans
  GET  /api/metrics          – aggregate metrics
  POST /api/chat             – proxy a chat request to Claude
  GET  /api/events           – SSE stream of new spans (text/event-stream)
"""
from __future__ import annotations

import json
import os
import time

from flask import Flask, Response, jsonify, request, send_from_directory
from dotenv import load_dotenv

load_dotenv()

from telemetry import span_store  # noqa: E402 – must follow load_dotenv
from claude_client import chat  # noqa: E402

app = Flask(__name__, static_folder=None)

# ── CORS (dev convenience) ────────────────────────────────────────────────────

@app.after_request
def _cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response

@app.route("/api/<path:p>", methods=["OPTIONS"])
def _options(p):
    return "", 204


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


# ── SSE stream ────────────────────────────────────────────────────────────────

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
