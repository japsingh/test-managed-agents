"""
gRPC OTLP TraceService receiver — listens on port 4317.

Configure Claude Code with:
  OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
  OTEL_EXPORTER_OTLP_PROTOCOL=grpc
  OTEL_TRACES_EXPORTER=otlp
"""
from __future__ import annotations

import logging
import threading
from concurrent import futures

log = logging.getLogger("monitor")

_server = None


def start(port: int = 4317, span_store=None) -> bool:
    """
    Start the gRPC OTLP server in a background daemon thread.
    Returns True if started successfully, False if dependencies are missing.
    """
    global _server
    try:
        import grpc
        from opentelemetry.proto.collector.trace.v1 import (
            trace_service_pb2,
            trace_service_pb2_grpc,
        )
        from otlp_proto_parser import parse_resource_spans
    except ImportError as exc:
        log.warning("gRPC receiver disabled — missing dependency: %s", exc)
        log.warning("  Fix: pip install grpcio opentelemetry-proto")
        return False

    class _TraceServicer(trace_service_pb2_grpc.TraceServiceServicer):
        def Export(self, request, context):
            spans = parse_resource_spans(request.resource_spans)
            log.info("gRPC /v1/traces → %d span(s) ingested", len(spans))
            for span in spans:
                span_store.add(span)
            return trace_service_pb2.ExportTraceServiceResponse()

    def _run():
        global _server
        _server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
        trace_service_pb2_grpc.add_TraceServiceServicer_to_server(
            _TraceServicer(), _server
        )
        _server.add_insecure_port(f"[::]:{port}")
        _server.start()
        log.info("gRPC OTLP receiver listening on port %d", port)
        _server.wait_for_termination()

    t = threading.Thread(target=_run, daemon=True, name="grpc-receiver")
    t.start()
    return True


def stop():
    global _server
    if _server:
        _server.stop(grace=2)
