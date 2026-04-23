"""
HTTP/protobuf OTLP TraceService receiver — listens on port 4318.

Configure Claude Code with:
  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
  OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
  OTEL_TRACES_EXPORTER=otlp
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger("monitor")


def start(port: int = 4318, span_store=None) -> bool:
    """
    Start the HTTP/protobuf OTLP server in a background daemon thread.
    Returns True if started successfully, False if dependencies are missing.
    """
    try:
        from opentelemetry.proto.collector.trace.v1 import (
            trace_service_pb2,
        )
        from otlp_proto_parser import parse_resource_spans
    except ImportError as exc:
        log.warning("HTTP/protobuf receiver disabled — missing dependency: %s", exc)
        log.warning("  Fix: pip install opentelemetry-proto")
        return False

    from opentelemetry.proto.collector.trace.v1 import trace_service_pb2 as _pb
    from otlp_proto_parser import parse_resource_spans as _parse

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # silence default access log — Flask handles display

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self):
            if self.path != "/v1/traces":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)

            try:
                req = _pb.ExportTraceServiceRequest()
                req.ParseFromString(body)
                spans = _parse(req.resource_spans)
                log.info("HTTP/protobuf /v1/traces → %d span(s) ingested", len(spans))
                for span in spans:
                    span_store.add(span)
                resp_body = _pb.ExportTraceServiceResponse().SerializeToString()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-protobuf")
                self._cors()
                self.end_headers()
                self.wfile.write(resp_body)
            except Exception as exc:
                log.error("HTTP/protobuf parse error: %s", exc)
                self.send_response(500)
                self.end_headers()

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _run():
        srv = HTTPServer(("0.0.0.0", port), _Handler)
        log.info("HTTP/protobuf OTLP receiver listening on port %d", port)
        srv.serve_forever()

    t = threading.Thread(target=_run, daemon=True, name="http-proto-receiver")
    t.start()
    return True
