"""Shared timing primitives and correlation; durations are inclusive wall time."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import time
from typing import Any, Iterator, Mapping
from uuid import uuid4

# A fresh mapping is bound per scope; concurrent tasks cannot mutate each other's IDs.
_BINDING: ContextVar[tuple[dict[str, Any], Any]] = ContextVar(
    "ingestion_timing_binding", default=({}, None)
)


def correlation_attributes() -> dict[str, Any]:
    """Only identifiers/digests, never source text or exception messages."""
    return dict(_BINDING.get()[0])


def _utc(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc).isoformat()


class TimedSpan:
    """One interval with monotonic elapsed time and token-restored OTel context."""

    def __init__(self, name: str, attributes: Mapping[str, Any], owner: Any = None, *, inherit: bool = True):
        self.started_ns = time.time_ns()
        self.started_tick = time.perf_counter_ns()
        self.started_at = _utc(self.started_ns)
        self.finished_at: str | None = None
        self.duration_ms: float | None = None
        self.status = "INCOMPLETE"
        self.attributes = {**(correlation_attributes() if inherit else {}), **attributes}
        self._binding_token = _BINDING.set((dict(self.attributes), owner))
        self._otel_token = None
        self.span = None
        try:
            from opentelemetry import context, trace
            from dual_pipeline.otel_engine import get_tracer
            self.span = get_tracer("unified_pipeline").start_span(
                name, start_time=self.started_ns, attributes=self.attributes
            )
            self._otel_token = context.attach(trace.set_span_in_context(self.span))
        except Exception:
            pass

    def update(self, attributes: Mapping[str, Any]) -> None:
        self.attributes.update(attributes)
        if self.span is not None:
            try:
                for key, value in attributes.items():
                    self.span.set_attribute(key, value)
            except Exception:
                pass

    def finish(self, status: str, error_type: str | None = None) -> dict[str, Any]:
        """Close once; repeated finalization does not duplicate elapsed time or detach."""
        if self.finished_at is not None:
            return self.record()
        self.duration_ms = round((time.perf_counter_ns() - self.started_tick) / 1e6, 3)
        finished_ns = time.time_ns()
        self.finished_at, self.status = _utc(finished_ns), status
        try:
            if self.span is not None:
                from opentelemetry.trace import Status, StatusCode
                self.span.set_attribute("duration.monotonic_ms", self.duration_ms)
                self.span.set_attribute("duration.accounting", "inclusive")
                if "pipeline.stage" in self.attributes:
                    self.span.set_attribute("stage.duration_ms", self.duration_ms)
                self.span.set_attribute("terminal_outcome", status)
                if error_type:
                    self.span.set_attribute("error.type", error_type)
                code = StatusCode.OK if status in {"PASS", "COMPLETE"} else StatusCode.ERROR
                self.span.set_status(Status(code))
                self.span.end(end_time=finished_ns)
        except Exception:
            pass
        finally:
            try:
                if self._otel_token is not None:
                    from opentelemetry import context
                    context.detach(self._otel_token)
            finally:
                _BINDING.reset(self._binding_token)
        return self.record()

    def record(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at, "finished_at": self.finished_at,
            "duration_ms": self.duration_ms, "status": self.status,
            "correlation": dict(self.attributes), "duration_clock": "perf_counter_ns",
            "duration_accounting": "inclusive",
            "measurement_state": "FINAL" if self.finished_at is not None else "RUNNING",
        }


from .attempt_ledger import AttemptLedger, get_or_create_attempt_ledger

SPAN_SOURCE_DISCOVERY = "source_discovery"
SPAN_DEPENDENCY_CHECKS = "dependency_checks"
SPAN_CONVERSION = "conversion"
SPAN_SOURCE_ASSEMBLY = "source_assembly"
SPAN_EXTRACTION = "extraction"
SPAN_JUDGE = "judge"
SPAN_MODEL_INITIALIZATION = "model_initialization"
SPAN_GRAPH_TRANSACTION = "graph_transaction"
SPAN_SIDECAR_COPY = "sidecar_copy"
SPAN_CHANGED_RECORD_ENCODING = "changed_record_encoding"
SPAN_VALIDATION = "validation"
SPAN_QUERY_ENCODING = "query_encoding"
SPAN_RETRIEVAL_REPEATS = "retrieval_repeats"
SPAN_ACTIVATION = "activation"

ALL_CHILD_SPAN_NAMES = (
    SPAN_SOURCE_DISCOVERY, SPAN_DEPENDENCY_CHECKS, SPAN_CONVERSION, SPAN_SOURCE_ASSEMBLY,
    SPAN_EXTRACTION, SPAN_JUDGE, SPAN_MODEL_INITIALIZATION, SPAN_GRAPH_TRANSACTION,
    SPAN_SIDECAR_COPY, SPAN_CHANGED_RECORD_ENCODING, SPAN_VALIDATION, SPAN_QUERY_ENCODING,
    SPAN_RETRIEVAL_REPEATS, SPAN_ACTIVATION,
)


def _sum_parent_stages(stage_records: Mapping[str, Any]) -> float:
    total = 0.0
    for s in stage_records.values():
        dur = getattr(s, "duration_ms", None) if not isinstance(s, dict) else s.get("duration_ms")
        if dur is not None:
            total += float(dur)
    return total


def _parse_ts_ms(iso_str: str) -> float:
    return datetime.fromisoformat(iso_str.replace("Z", "+00:00")).timestamp() * 1000.0


def _check_overlap(span_a_name: str, finish_i: str, other: Mapping[str, Any]) -> dict[str, Any] | None:
    start_j = other.get("started_at")
    if not start_j or start_j >= finish_i:
        return None
    overlap_end = min(finish_i, other.get("finished_at") or finish_i)
    overlap_ms = 0.0
    try:
        t0 = _parse_ts_ms(start_j)
        t1 = _parse_ts_ms(overlap_end)
        overlap_ms = round(max(0.0, t1 - t0), 3)
    except Exception:
        pass
    return {
        "span_a": span_a_name,
        "span_b": str(other.get("child_name") or other.get("name")),
        "overlap_start": start_j,
        "overlap_end": overlap_end,
        "overlap_duration_ms": overlap_ms,
    }


def _find_overlaps_for_span(
    name: str, f_i: str, subsequent_spans: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    for other in subsequent_spans:
        ov = _check_overlap(name, f_i, other)
        if ov:
            overlaps.append(ov)
    return overlaps


def _try_parse_interval(s_i: str, f_i: str) -> tuple[float, float] | None:
    try:
        dt_s, dt_f = _parse_ts_ms(s_i), _parse_ts_ms(f_i)
        if dt_f >= dt_s:
            return (dt_s, dt_f)
    except Exception:
        pass
    return None


def _collect_span_metrics(
    sorted_spans: Sequence[Mapping[str, Any]]
) -> tuple[float, list[dict[str, Any]], list[tuple[float, float]], dict[str, list[dict[str, Any]]]]:
    agg_ms = 0.0
    overlapping: list[dict[str, Any]] = []
    intervals: list[tuple[float, float]] = []
    by_name: dict[str, list[dict[str, Any]]] = {}

    for i, c in enumerate(sorted_spans):
        agg_ms += float(c.get("duration_ms") or 0.0)
        name = str(c.get("child_name") or c.get("name") or "unnamed")
        by_name.setdefault(name, []).append(dict(c))
        s_i, f_i = c.get("started_at"), c.get("finished_at")
        if s_i and f_i:
            interval = _try_parse_interval(s_i, f_i)
            if interval:
                intervals.append(interval)
            overlapping.extend(_find_overlaps_for_span(name, f_i, sorted_spans[i + 1:]))
    return agg_ms, overlapping, intervals, by_name


def _compute_critical_path(intervals: list[tuple[float, float]], fallback_ms: float) -> float:
    if not intervals:
        return fallback_ms
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for cur in intervals[1:]:
        prev_s, prev_f = merged[-1]
        if cur[0] <= prev_s or cur[0] <= prev_f:
            merged[-1] = (prev_s, max(prev_f, cur[1]))
        else:
            merged.append(cur)
    return sum(f - s for s, f in merged)


def compute_exclusive_accounting(
    run_span: TimedSpan | None,
    stage_records: Mapping[str, Any],
    child_spans: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Computes exclusive accounting reconciling child spans, parent stages, and request wall time."""
    run_wall_ms = float(run_span.duration_ms) if (run_span and run_span.duration_ms is not None) else 0.0
    parent_stages_sum_ms = _sum_parent_stages(stage_records)

    sorted_spans = sorted(child_spans, key=lambda c: c.get("started_at") or "")
    agg_ms, overlapping_spans, intervals, by_name = _collect_span_metrics(sorted_spans)
    critical_path_ms = _compute_critical_path(intervals, agg_ms)
    aggregate_worker_ms = agg_ms
    unmeasured_residual_ms = max(0.0, parent_stages_sum_ms - critical_path_ms)

    return {
        "measured_pipeline_ms": run_wall_ms,
        "parent_stages_sum_ms": round(parent_stages_sum_ms, 3),
        "critical_path_ms": round(critical_path_ms, 3),
        "child_spans_critical_path_ms": round(critical_path_ms, 3),
        "aggregate_worker_ms": round(aggregate_worker_ms, 3),
        "child_spans_aggregate_worker_ms": round(aggregate_worker_ms, 3),
        "child_span_count": len(child_spans),
        "child_spans_by_name": by_name,
        "unmeasured_residual_ms": round(unmeasured_residual_ms, 3),
        "overlapping_spans": overlapping_spans,
        "has_overlaps": len(overlapping_spans) > 0,
        "duration_accounting": "exclusive; child spans are itemized separately and not added to parent stage totals",
    }


class PipelineTiming:
    """Request identity survives retries; each tracker invocation has its own run ID."""

    def __init__(self, pipeline_name: str, metadata: dict[str, Any]):
        self.pipeline_name, self.metadata = pipeline_name, metadata
        self.run_id = uuid4().hex
        self.request_id = str(metadata.get("request_id") or metadata.get("directive_id") or uuid4().hex)
        metadata["request_id"], metadata["run_id"] = self.request_id, self.run_id
        self.input_digest = ""
        self.run_span: TimedSpan | None = None
        self.requested_outcome = "COMPLETE"
        self.stage_spans: dict[str, TimedSpan] = {}
        self.attempts: list[dict[str, Any]] = []
        self.child_spans: dict[str, list[dict[str, Any]]] = {}
        self.all_child_spans: list[dict[str, Any]] = []
        self.attempt_ledger = get_or_create_attempt_ledger(self.request_id)

    def attributes(self) -> dict[str, Any]:
        attrs = {"request_id": self.request_id, "run_id": self.run_id,
                 "pipeline.name": self.pipeline_name}
        bindings = {
            "input_digest": self.input_digest or self.metadata.get("input_hash"),
            "source_digest": self.metadata.get("raw_source_sha256"),
            "source_id": self.metadata.get("source_id"),
            "generation_id": self.metadata.get("generation_id"),
            "run_mode": self.metadata.get("run_mode"),
        }
        attrs.update({key: str(value) for key, value in bindings.items() if value})
        return attrs

    def start_stage(self, stage: str) -> None:
        self.stage_spans[stage] = TimedSpan(
            f"stage.{stage}", {**self.attributes(), "pipeline.stage": stage}, self
        )

    def finish_stage(self, stage: str, status: str, error_type: str | None = None) -> dict[str, Any]:
        interval = self.stage_spans.pop(stage)
        interval.update({**self.attributes(), "stage.status": status})
        record = interval.finish(status, error_type)
        try:
            from dual_pipeline.telemetry_core.metrics import record_stage_duration
            record_stage_duration(stage=stage, duration_seconds=record["duration_ms"] / 1000, status=status)
        except Exception:
            pass
        return record

    @contextmanager
    def child_span(
        self,
        name: str,
        parent: str | None = None,
        attributes: Mapping[str, Any] | None = None,
    ) -> Iterator[TimedSpan]:
        parent_stage = parent or (list(self.stage_spans.keys())[-1] if self.stage_spans else "PIPELINE")
        attrs = {
            **self.attributes(),
            "span.kind": "child",
            "child.name": name,
            "parent.stage": parent_stage,
            **(dict(attributes) if attributes else {}),
        }
        interval = TimedSpan(f"child.{name}", attrs, self, inherit=True)
        try:
            yield interval
        except BaseException as exc:
            interval.finish("FAILED", type(exc).__name__)
            raise
        else:
            if interval.finished_at is None:
                interval.finish("PASS")
        finally:
            record = interval.record()
            record["child_name"] = name
            record["parent_stage"] = parent_stage
            record["duration_accounting"] = "exclusive"
            if name not in self.child_spans:
                self.child_spans[name] = []
            self.child_spans[name].append(record)
            self.all_child_spans.append(record)

    def finish_run(self, status: str) -> None:
        if self.run_span is not None and self.run_span.finished_at is None:
            self.run_span.update(self.attributes())
            self.run_span.finish(status)

    @contextmanager
    def execution_scope(self, tracker: Any) -> Iterator[None]:
        self.run_span = TimedSpan("pipeline.run", self.attributes(), self, inherit=False)
        outcome = "FAILED"
        try:
            yield
        except BaseException as exc:
            tracker.fail_active_stage(exc)
            raise
        else:
            if tracker.current_stage is not None:
                tracker.fail_active_stage(None, status="INCOMPLETE")
            outcome = tracker.execution_outcome(self.requested_outcome)
        finally:
            self.finish_run(outcome)


@contextmanager
def timed_child_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
    parent: str | None = None,
) -> Iterator[TimedSpan]:
    """Times a child operation under the active PipelineTiming context."""
    attrs, owner = _BINDING.get()
    if owner is not None and hasattr(owner, "child_span"):
        with owner.child_span(name, parent=parent, attributes=attributes) as span:
            yield span
    else:
        extra = dict(attributes) if attributes else {}
        span = TimedSpan(f"child.{name}", {**attrs, "span.kind": "child", "child.name": name, **extra}, owner)
        try:
            yield span
        except BaseException as exc:
            span.finish("FAILED", type(exc).__name__)
            raise
        else:
            if span.finished_at is None:
                span.finish("PASS")


@contextmanager
def attempt_scope(
    attempt: int,
    parent_attempt: int | None = None,
    input_digest: str = "",
    model_digest: str = "",
    code_digest: str = "",
    policy_digest: str = "",
    resume_decision: Mapping[str, Any] | None = None,
) -> Iterator[TimedSpan]:
    """Called inside L2; exception and non-passing attempts remain inspectable."""
    attrs, owner = _BINDING.get()
    interval = TimedSpan("pipeline.attempt", {**attrs, "attempt": attempt}, owner)
    ledger = getattr(owner, "attempt_ledger", None)
    if ledger is not None:
        ledger.start_attempt(
            attempt_id=attempt,
            run_id=attrs.get("run_id", ""),
            parent_attempt_id=parent_attempt,
            input_digest=input_digest or attrs.get("input_digest", ""),
            model_digest=model_digest,
            code_digest=code_digest,
            policy_digest=policy_digest,
        )
    outcome = "INCOMPLETE"
    retry_cause: str | None = None
    try:
        yield interval
    except BaseException as exc:
        outcome = "FAILED"
        retry_cause = type(exc).__name__
        interval.finish("FAILED", retry_cause)
        raise
    else:
        outcome = "PASS"
        if interval.finished_at is None:
            interval.finish("PASS")
    finally:
        interval.finish("INCOMPLETE")
        record = interval.record()
        if owner is not None:
            owner.attempts.append(record)
            if ledger is not None:
                ledger.finish_attempt(
                    attempt_id=attempt,
                    status=interval.status,
                    terminal_outcome="COMPLETE" if outcome == "PASS" else "FAILED",
                    retry_cause=retry_cause,
                    resume_decision=resume_decision,
                )
            directory = getattr(owner, "evidence_directory", None)
            if directory is not None:
                directory.mkdir(parents=True, exist_ok=True)
                from dual_pipeline.execution_evidence import write_immutable_evidence
                write_immutable_evidence(directory / f"attempt-{attempt}.json", record)
                if ledger is not None:
                    ledger.export_to_json(directory / "attempt_ledger.json")

