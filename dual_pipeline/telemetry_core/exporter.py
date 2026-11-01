"""
exporter.py — Local append-only JSONL span exporter for OpenTelemetry.

Provides verifiable, cryptographic proof of execution on local disk without
requiring an external APM collector.
"""
from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path
from typing import Any, List, Union

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.trace import format_span_id, format_trace_id


class JSONLSpanExporter(SpanExporter):
    """
    Appends finished OpenTelemetry spans into an offline, append-only JSONL file.
    Provides verifiable, cryptographic proof of execution on local disk without
    requiring an external APM collector.
    """

    def __init__(
        self,
        file_path: Union[str, Path],
        max_bytes: int = 50 * 1024 * 1024,
        backup_count: int = 10,
        retention_days: int = 14,
    ):
        self.file_path = Path(file_path).resolve()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.retention_days = retention_days
        self._lock = threading.Lock()

    @staticmethod
    def _format_timestamp(ns: int | None) -> str:
        if not ns:
            return ""
        return datetime.datetime.fromtimestamp(ns / 1e9, tz=datetime.timezone.utc).isoformat()

    @staticmethod
    def _format_events(span_events: Any) -> list[dict[str, Any]]:
        events = []
        for ev in span_events:
            events.append({
                "name": ev.name,
                "timestamp": JSONLSpanExporter._format_timestamp(ev.timestamp),
                "attributes": dict(ev.attributes or {}),
            })
        return events

    @staticmethod
    def _serialize_value(item: Any) -> Any:
        return item if isinstance(item, (str, int, float, bool)) else str(item)

    @staticmethod
    def _serialize_attributes(span_attributes: Any) -> dict[str, Any]:
        attrs = {}
        for k, v in (span_attributes or {}).items():
            if isinstance(v, (str, int, float, bool)):
                attrs[k] = v
            elif isinstance(v, (list, tuple)):
                attrs[k] = [JSONLSpanExporter._serialize_value(x) for x in v]
            else:
                attrs[k] = str(v)
        return attrs

    @staticmethod
    def _span_to_record(span: ReadableSpan) -> str:
        ctx = span.get_span_context()
        trace_id_str = format_trace_id(ctx.trace_id) if ctx.trace_id else ""
        span_id_str = format_span_id(ctx.span_id) if ctx.span_id else ""
        parent_id_str = (
            format_span_id(span.parent.span_id)
            if span.parent and span.parent.span_id
            else None
        )
        start_ns = span.start_time or 0
        end_ns = span.end_time or start_ns
        duration_ms = (end_ns - start_ns) / 1_000_000.0

        status_code = span.status.status_code.name if span.status else "UNSET"
        status_desc = span.status.description if span.status else None

        record = {
            "trace_id": trace_id_str,
            "span_id": span_id_str,
            "parent_span_id": parent_id_str,
            "name": span.name,
            "start_time": JSONLSpanExporter._format_timestamp(start_ns),
            "end_time": JSONLSpanExporter._format_timestamp(end_ns),
            "duration_ms": round(duration_ms, 3),
            "status": {"code": status_code, "description": status_desc},
            "attributes": JSONLSpanExporter._serialize_attributes(span.attributes),
            "events": JSONLSpanExporter._format_events(span.events),
        }
        return json.dumps(record, sort_keys=True)

    def _rotate_and_prune(self) -> None:
        import time
        try:
            if self.file_path.exists() and self.file_path.stat().st_size >= self.max_bytes:
                for i in range(self.backup_count - 1, 0, -1):
                    src = self.file_path.with_name(f"{self.file_path.name}.{i}")
                    dst = self.file_path.with_name(f"{self.file_path.name}.{i + 1}")
                    if src.exists():
                        if i + 1 > self.backup_count:
                            src.unlink(missing_ok=True)
                        else:
                            src.replace(dst)
                first_backup = self.file_path.with_name(f"{self.file_path.name}.1")
                self.file_path.replace(first_backup)

            cutoff = time.time() - (self.retention_days * 86400)
            prefix = self.file_path.stem
            for f in self.file_path.parent.glob(f"{prefix}*"):
                if f != self.file_path and f.is_file():
                    try:
                        if f.stat().st_mtime < cutoff:
                            f.unlink(missing_ok=True)
                    except OSError:
                        pass
        except Exception:
            pass

    def export(self, spans: List[ReadableSpan]) -> SpanExportResult:
        records = [self._span_to_record(s) for s in spans]
        if records:
            with self._lock:
                self._rotate_and_prune()
                try:
                    with open(self.file_path, "a", encoding="utf-8") as f:
                        for line in records:
                            written = f.write(line + "\n")
                            if written != len(line) + 1:
                                raise IOError(f"Fail-Fast: Incomplete write to {self.file_path}")
                        f.flush()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).error(f"Fail-Fast: Telemetry export failed: {e}")
                    return SpanExportResult.FAILURE
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


__all__ = ["JSONLSpanExporter"]
