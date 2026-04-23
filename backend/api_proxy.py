"""
Transparent proxy for the Anthropic API.

Claude Code is pointed at http://localhost:8000 via ANTHROPIC_BASE_URL.
Every request is forwarded to api.anthropic.com, and a telemetry span
is created from the request/response data.
"""
from __future__ import annotations

import json
import time

import requests as _requests

from telemetry import add_event, start_span

_ANTHROPIC_API = "https://api.anthropic.com"
_STREAM_CHUNK_SIZE = 4096


def _parse_attrs(body: dict) -> dict:
    attrs = {"gen_ai.system": "anthropic"}
    if "model" in body:
        attrs["gen_ai.request.model"] = body["model"]
    if "max_tokens" in body:
        attrs["gen_ai.request.max_tokens"] = body["max_tokens"]
    if "temperature" in body:
        attrs["gen_ai.request.temperature"] = body["temperature"]
    return attrs


def proxy_request(path: str, flask_request) -> tuple:
    """
    Forward flask_request to api.anthropic.com/<path>.
    Returns (response_body_bytes, status_code, headers).
    Creates an OTEL span for the call.
    """
    url = f"{_ANTHROPIC_API}/{path}"

    # forward all headers except Host
    fwd_headers = {
        k: v for k, v in flask_request.headers
        if k.lower() not in ("host", "content-length", "transfer-encoding")
    }

    body_bytes = flask_request.get_data()
    try:
        body = json.loads(body_bytes) if body_bytes else {}
    except Exception:
        body = {}

    is_streaming = body.get("stream", False)
    span_name = f"claude {body.get('model', 'unknown')}"

    with start_span(span_name, _parse_attrs(body)) as span:
        span["attributes"]["proxy.path"] = path
        span["attributes"]["proxy.streaming"] = is_streaming

        # record prompt
        msgs = body.get("messages", [])
        if msgs:
            prompt_text = "\n".join(
                f"[{m.get('role','')}] {m.get('content','')}" for m in msgs
            )
            add_event("prompt", {"gen_ai.prompt": prompt_text[:4096]})

        t0 = time.perf_counter()
        try:
            upstream = _requests.request(
                method=flask_request.method,
                url=url,
                headers=fwd_headers,
                data=body_bytes,
                stream=is_streaming,
                timeout=300,
            )
        except Exception as exc:
            raise RuntimeError(f"Upstream error: {exc}") from exc

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        span["attributes"]["llm.latency_ms"] = latency_ms
        span["attributes"]["http.status_code"] = upstream.status_code

        # collect response
        if is_streaming:
            response_bytes = b"".join(upstream.iter_content(_STREAM_CHUNK_SIZE))
        else:
            response_bytes = upstream.content

        # parse response for usage / completion
        try:
            resp_json = json.loads(response_bytes)
            usage = resp_json.get("usage", {})
            if usage:
                span["attributes"]["gen_ai.usage.input_tokens"]  = usage.get("input_tokens", 0)
                span["attributes"]["gen_ai.usage.output_tokens"] = usage.get("output_tokens", 0)
            content = resp_json.get("content", [])
            completion = content[0].get("text", "") if content else ""
            if completion:
                add_event("completion", {"gen_ai.completion": completion[:4096]})
            stop = resp_json.get("stop_reason")
            if stop:
                span["attributes"]["gen_ai.response.finish_reasons"] = [stop]
        except Exception:
            pass  # streaming responses or non-JSON — best effort

        # strip headers that break Flask's response
        skip = {"content-encoding", "transfer-encoding", "connection", "content-length"}
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in skip
        }

        return response_bytes, upstream.status_code, resp_headers
