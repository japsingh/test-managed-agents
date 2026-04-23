"""
OTLP JSON trace receiver.

Parses the OTLP/HTTP JSON format (application/json) that Claude Code
sends to /v1/traces and converts it into our internal span dicts.

OTLP attribute value types handled:
  stringValue, intValue, doubleValue, boolValue, arrayValue
"""
from __future__ import annotations


def _parse_attr_value(val: dict) -> object:
    if "stringValue" in val:
        return val["stringValue"]
    if "intValue" in val:
        return int(val["intValue"])
    if "doubleValue" in val:
        return float(val["doubleValue"])
    if "boolValue" in val:
        return bool(val["boolValue"])
    if "arrayValue" in val:
        return [
            _parse_attr_value(v)
            for v in val["arrayValue"].get("values", [])
        ]
    return str(val)


def _parse_attrs(attr_list: list) -> dict:
    return {a["key"]: _parse_attr_value(a["value"]) for a in (attr_list or [])}


def _ns(value) -> int:
    """OTLP timestamps are nanoseconds, sometimes sent as strings."""
    return int(value) if value else 0


def _status_code(status: dict) -> str:
    """Map OTLP status code to our OK/ERROR/UNSET string."""
    code = status.get("code", 0) if status else 0
    # OTLP: 0=UNSET, 1=OK, 2=ERROR
    return {0: "UNSET", 1: "OK", 2: "ERROR"}.get(int(code), "UNSET")


def parse_otlp_json(payload: dict) -> list[dict]:
    """
    Parse a full OTLP ExportTraceServiceRequest JSON body.
    Returns a list of internal span dicts ready for the span store.
    """
    spans = []

    for resource_span in payload.get("resourceSpans", []):
        resource_attrs = _parse_attrs(
            resource_span.get("resource", {}).get("attributes", [])
        )

        for scope_span in resource_span.get("scopeSpans", []):
            scope_name = scope_span.get("scope", {}).get("name", "")

            for s in scope_span.get("spans", []):
                trace_id  = s.get("traceId", "")
                span_id   = s.get("spanId", "")
                parent_id = s.get("parentSpanId") or None

                start_ns = _ns(s.get("startTimeUnixNano", 0))
                end_ns   = _ns(s.get("endTimeUnixNano", 0))
                dur_ms   = round((end_ns - start_ns) / 1_000_000, 3)

                attrs = {**resource_attrs, **_parse_attrs(s.get("attributes", []))}

                events = [
                    {
                        "name": e.get("name", ""),
                        "timestamp_ns": _ns(e.get("timeUnixNano", 0)),
                        "attributes": _parse_attrs(e.get("attributes", [])),
                    }
                    for e in s.get("events", [])
                ]

                spans.append({
                    "trace_id":      trace_id,
                    "span_id":       span_id,
                    "parent_span_id": parent_id,
                    "name":          s.get("name", ""),
                    "start_time_ns": start_ns,
                    "end_time_ns":   end_ns,
                    "duration_ms":   dur_ms,
                    "status":        _status_code(s.get("status", {})),
                    "attributes":    attrs,
                    "events":        events,
                    "source":        "claude-code",   # mark as externally received
                    "scope":         scope_name,
                })

    return spans
