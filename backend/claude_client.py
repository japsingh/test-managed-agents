"""
Thin wrapper around the Anthropic Messages API (direct HTTP via requests)
that emits OpenTelemetry-compatible spans for every call.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from telemetry import add_event, start_span

_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


def _api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    return key


def chat(
    messages: list[dict],
    model: str = "claude-opus-4-5",
    max_tokens: int = 1024,
    temperature: float | None = None,
    system: str | None = None,
) -> dict:
    """
    Call the Anthropic Messages API with full OTEL instrumentation.
    Returns a dict with the response data.
    """
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if system:
        payload["system"] = system

    span_name = f"claude {model}"
    span_attrs = {
        "gen_ai.system": "anthropic",
        "gen_ai.request.model": model,
        "gen_ai.request.max_tokens": max_tokens,
    }
    if temperature is not None:
        span_attrs["gen_ai.request.temperature"] = temperature

    with start_span(span_name, span_attrs) as span:
        # record prompt
        prompt_text = "\n".join(f"[{m['role']}] {m['content']}" for m in messages)
        add_event("prompt", {"gen_ai.prompt": prompt_text[:4096]})

        t0 = time.perf_counter()
        try:
            resp = requests.post(
                _API_URL,
                json=payload,
                headers={
                    "x-api-key": _api_key(),
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                timeout=120,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RuntimeError(f"Anthropic API error {resp.status_code}: {resp.text}") from exc

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        data = resp.json()

        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        stop_reason = data.get("stop_reason", "")
        content_blocks = data.get("content", [])
        completion_text = content_blocks[0].get("text", "") if content_blocks else ""

        # record attributes
        span["attributes"]["gen_ai.usage.input_tokens"] = input_tokens
        span["attributes"]["gen_ai.usage.output_tokens"] = output_tokens
        span["attributes"]["gen_ai.response.finish_reasons"] = [stop_reason]
        span["attributes"]["llm.latency_ms"] = latency_ms
        span["attributes"]["gen_ai.response.id"] = data.get("id", "")

        add_event("completion", {"gen_ai.completion": completion_text[:4096]})

        return {
            "id": data.get("id", ""),
            "model": data.get("model", model),
            "content": completion_text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "stop_reason": stop_reason,
            "latency_ms": latency_ms,
        }
