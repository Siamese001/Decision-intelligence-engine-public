"""
dual_pipeline/logging_config.py

Structured JSON and human-readable logging configuration for Decision Intelligence Engine.
Supports W3C trace_id and span_id injection, ISO8601 timestamps, run_id context correlation,
and standard CLI flags (--log-format, --log-level).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import sys
from typing import Any, Dict, Optional

from dual_pipeline.credentials import CredentialManager

# Context variable or process-global for active run_id
_CURRENT_RUN_ID: Optional[str] = None


def set_active_run_id(run_id: Optional[str]) -> None:
    """Set process-wide active run_id for structured logging correlation."""
    global _CURRENT_RUN_ID
    _CURRENT_RUN_ID = run_id


def get_active_run_id() -> Optional[str]:
    """Retrieve process-wide active run_id."""
    return _CURRENT_RUN_ID or CredentialManager.get_credential("NEE_RUN_ID")


class StructuredJsonFormatter(logging.Formatter):
    """
    Formats log records into single-line JSON objects with ISO8601 timestamps,
    log levels, active trace_id / span_id (via OpenTelemetry), run_id, and exception tracebacks.
    """

    def __init__(self, run_id: Optional[str] = None) -> None:
        super().__init__()
        self.run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(record.created, tz=datetime.timezone.utc).isoformat()

        # Extract OpenTelemetry trace and span IDs if available
        trace_id: Optional[str] = None
        span_id: Optional[str] = None
        try:
            from dual_pipeline.otel_engine import get_current_trace_and_span_id
            t_id, s_id = get_current_trace_and_span_id()
            trace_id = t_id or None
            span_id = s_id or None
        except Exception:
            pass

        eff_run_id = getattr(record, "run_id", None) or self.run_id or get_active_run_id()

        payload: Dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if eff_run_id:
            payload["run_id"] = eff_run_id
        if trace_id:
            payload["trace_id"] = trace_id
        if span_id:
            payload["span_id"] = span_id

        # Attach exception details if present
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        # Attach extra attributes if passed via extra={}
        standard_attrs = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "run_id",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in standard_attrs and not k.startswith("_")}
        if extras:
            payload["extra"] = extras

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    log_format: str = "text",
    log_level: str = "INFO",
    run_id: Optional[str] = None,
    stream: Optional[Any] = None,
) -> None:
    """
    Configures root and dual_pipeline loggers with the specified format and verbosity.
    
    Args:
        log_format: 'text' or 'json'
        log_level: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'
        run_id: Optional run_id to bind to formatted logs
        stream: Output stream (defaults to sys.stderr)
    """
    if run_id:
        set_active_run_id(run_id)

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)
    out_stream = stream or sys.stderr

    handler = logging.StreamHandler(out_stream)
    if log_format.lower() == "json":
        handler.setFormatter(StructuredJsonFormatter(run_id=run_id))
    else:
        text_fmt = "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
        handler.setFormatter(logging.Formatter(text_fmt))

    root_logger = logging.getLogger()
    # Replace existing StreamHandlers to avoid duplicate log lines
    root_logger.handlers = [h for h in root_logger.handlers if not isinstance(h, logging.StreamHandler)]
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)

    # Ensure dual_pipeline logger propagates at the configured level
    dp_logger = logging.getLogger("dual_pipeline")
    dp_logger.setLevel(numeric_level)
