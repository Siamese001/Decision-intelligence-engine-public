"""
PipelineSpanRecorder context manager backed by real OpenTelemetry spans.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger("dual_pipeline.span_recorder")


class PipelineSpanRecorder:
    """
    Manages active OpenTelemetry spans for pipeline stages and records duration metrics.
    """

    def __init__(self, service_name: str = "unified_pipeline") -> None:
        self.service_name = service_name

    @contextmanager
    def span(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        from opentelemetry.trace import Status, StatusCode

        from dual_pipeline.otel_engine import get_tracer

        tracer = get_tracer(self.service_name)
        start_time = time.perf_counter()
        with tracer.start_as_current_span(name) as otel_span:
            if attributes:
                for k, v in attributes.items():
                    otel_span.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v))
            try:
                yield otel_span
                otel_span.set_status(Status(StatusCode.OK))
                status_str = "PASS"
            except Exception as exc:
                otel_span.record_exception(exc)
                otel_span.set_status(Status(StatusCode.ERROR, str(exc)))
                status_str = "FAIL"
                raise
            finally:
                duration_s = time.perf_counter() - start_time
                try:
                    from dual_pipeline.telemetry_core.metrics import record_stage_duration
                    record_stage_duration(stage=name, duration_seconds=duration_s, status=status_str)
                except Exception:
                    pass
