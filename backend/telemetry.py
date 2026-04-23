"""
Lightweight OpenTelemetry-compatible span store.

Produces span dicts that follow the GenAI semantic-convention draft,
using only the Python standard library.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from typing import Any

MAX_SPANS = 2_000  # ring-buffer size


# ── Span context / current-span stack (thread-local) ─────────────────────────

class _SpanContext(threading.local):
    def __init__(self):
        super().__init__()
        self.stack: list[dict] = []


_ctx = _SpanContext()


def _current_span() -> dict | None:
    return _ctx.stack[-1] if _ctx.stack else None


# ── In-memory span store ──────────────────────────────────────────────────────

class SpanStore:
    def __init__(self, maxlen: int = MAX_SPANS):
        self._lock = threading.Lock()
        self._spans: deque[dict] = deque(maxlen=maxlen)
        self._listeners: list[queue.SimpleQueue] = []

    # ── write ─────────────────────────────────────────────────────────────────

    def add(self, span: dict) -> None:
        with self._lock:
            self._spans.append(span)
            for q in list(self._listeners):
                q.put(span)

    def subscribe(self) -> queue.SimpleQueue:
        """Return a queue that receives every new span."""
        q: queue.SimpleQueue = queue.SimpleQueue()
        with self._lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: queue.SimpleQueue) -> None:
        with self._lock:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass

    # ── read ──────────────────────────────────────────────────────────────────

    def all_spans(self) -> list[dict]:
        with self._lock:
            return list(self._spans)

    def by_trace(self, trace_id: str) -> list[dict]:
        with self._lock:
            return [s for s in self._spans if s["trace_id"] == trace_id]

    def traces(self) -> list[dict]:
        """One summary row per trace, newest first."""
        spans = self.all_spans()
        by_trace: dict[str, list[dict]] = {}
        for s in spans:
            by_trace.setdefault(s["trace_id"], []).append(s)

        result = []
        for tid, slist in by_trace.items():
            root = next((s for s in slist if s["parent_span_id"] is None), slist[0])
            duration = sum(s["duration_ms"] for s in slist if s["parent_span_id"] is None)
            has_error = any(s["status"] == "ERROR" for s in slist)
            result.append({
                "trace_id": tid,
                "name": root["name"],
                "start_time": root["start_time_ns"],
                "duration_ms": duration,
                "span_count": len(slist),
                "status": "ERROR" if has_error else "OK",
                "model": root["attributes"].get("gen_ai.request.model"),
                "input_tokens": root["attributes"].get("gen_ai.usage.input_tokens"),
                "output_tokens": root["attributes"].get("gen_ai.usage.output_tokens"),
            })
        result.sort(key=lambda x: x["start_time"], reverse=True)
        return result

    def metrics(self) -> dict:
        spans = self.all_spans()
        roots = [s for s in spans if s["parent_span_id"] is None]
        if not roots:
            return {
                "total_calls": 0, "total_input_tokens": 0,
                "total_output_tokens": 0, "avg_latency_ms": 0, "error_rate": 0,
            }
        n = len(roots)
        errors = sum(1 for s in roots if s["status"] == "ERROR")
        in_tok = sum(s["attributes"].get("gen_ai.usage.input_tokens", 0) for s in roots)
        out_tok = sum(s["attributes"].get("gen_ai.usage.output_tokens", 0) for s in roots)
        avg_lat = sum(s["duration_ms"] for s in roots) / n
        return {
            "total_calls": n,
            "total_input_tokens": int(in_tok),
            "total_output_tokens": int(out_tok),
            "avg_latency_ms": round(avg_lat, 2),
            "error_rate": round(errors / n * 100, 1),
        }


span_store = SpanStore()


# ── Span builder / context manager ───────────────────────────────────────────

@contextmanager
def start_span(name: str, attributes: dict[str, Any] | None = None):
    """
    Context manager that creates a span, pushes it to the stack, and
    finalises + exports it on exit.
    """
    parent = _current_span()
    trace_id = parent["trace_id"] if parent else uuid.uuid4().hex
    span_id = uuid.uuid4().hex[:16]

    span: dict = {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent["span_id"] if parent else None,
        "name": name,
        "start_time_ns": time.time_ns(),
        "end_time_ns": None,
        "duration_ms": 0.0,
        "status": "UNSET",
        "attributes": dict(attributes or {}),
        "events": [],
    }

    _ctx.stack.append(span)
    try:
        yield span
        span["status"] = "OK"
    except Exception as exc:
        span["status"] = "ERROR"
        span["attributes"]["error.type"] = type(exc).__name__
        span["attributes"]["error.message"] = str(exc)
        raise
    finally:
        _ctx.stack.pop()
        span["end_time_ns"] = time.time_ns()
        span["duration_ms"] = round(
            (span["end_time_ns"] - span["start_time_ns"]) / 1_000_000, 3
        )
        span_store.add(span)


def add_event(name: str, attributes: dict[str, Any] | None = None) -> None:
    span = _current_span()
    if span is not None:
        span["events"].append({
            "name": name,
            "timestamp_ns": time.time_ns(),
            "attributes": dict(attributes or {}),
        })
