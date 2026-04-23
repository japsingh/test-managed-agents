"""
Converts OTLP protobuf objects (from grpcio or HTTP/protobuf)
into our internal span dict format.
"""
from __future__ import annotations


def _any_value(val):
    kind = val.WhichOneof("value")
    if kind == "string_value": return val.string_value
    if kind == "int_value":    return val.int_value
    if kind == "double_value": return val.double_value
    if kind == "bool_value":   return val.bool_value
    if kind == "array_value":
        return [_any_value(v) for v in val.array_value.values]
    return None


def _attrs(attributes) -> dict:
    return {a.key: _any_value(a.value) for a in attributes}


def _status(status) -> str:
    return {0: "UNSET", 1: "OK", 2: "ERROR"}.get(status.code, "UNSET")


def parse_resource_spans(resource_spans) -> list[dict]:
    """
    Accepts repeated ResourceSpans from an ExportTraceServiceRequest
    and returns a list of internal span dicts.
    """
    spans = []
    for rs in resource_spans:
        resource_attrs = _attrs(rs.resource.attributes)
        for ss in rs.scope_spans:
            scope_name = ss.scope.name
            for s in ss.spans:
                trace_id  = s.trace_id.hex()
                span_id   = s.span_id.hex()
                parent_id = s.parent_span_id.hex() if s.parent_span_id else None
                # OTLP uses all-zero bytes for "no parent"
                if parent_id == "0000000000000000":
                    parent_id = None

                start_ns = s.start_time_unix_nano
                end_ns   = s.end_time_unix_nano
                dur_ms   = round((end_ns - start_ns) / 1_000_000, 3)

                events = [
                    {
                        "name":         e.name,
                        "timestamp_ns": e.time_unix_nano,
                        "attributes":   _attrs(e.attributes),
                    }
                    for e in s.events
                ]

                spans.append({
                    "trace_id":       trace_id,
                    "span_id":        span_id,
                    "parent_span_id": parent_id,
                    "name":           s.name,
                    "start_time_ns":  start_ns,
                    "end_time_ns":    end_ns,
                    "duration_ms":    dur_ms,
                    "status":         _status(s.status),
                    "attributes":     {**resource_attrs, **_attrs(s.attributes)},
                    "events":         events,
                    "source":         "claude-code",
                    "scope":          scope_name,
                })
    return spans
