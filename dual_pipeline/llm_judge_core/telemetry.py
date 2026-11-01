"""dual_pipeline/llm_judge_core/telemetry.py

Unified telemetry, cost tracking, and monotonic dispatch profiling for the LLM judge stack.
Ensures every judge invocation measures monotonic queue, provider, retry/backoff,
local evaluation, model initialization, locks, and operator wait separately while
strictly isolating credentials and excluding raw transcript/prompt content.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import re
import time
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger(__name__)

# Active dispatch context for nesting and caller propagation
_CURRENT_DISPATCH: contextvars.ContextVar[Optional[JudgeDispatchTelemetry]] = contextvars.ContextVar(
    "_CURRENT_DISPATCH", default=None
)

_SECRET_RE = re.compile(r"(?:sk-[a-zA-Z0-9_\-]{20,}|bearer\s+[a-zA-Z0-9_\-\.]{20,}|AKIA[0-9A-Z]{16})", re.IGNORECASE)


def _sanitize_error_text(error_msg: Optional[str]) -> Optional[str]:
    """Sanitize error messages to eliminate secrets and limit length."""
    if not error_msg:
        return None
    cleaned = _SECRET_RE.sub("[REDACTED_SECRET]", str(error_msg))
    # Exclude raw content: strip newlines and truncate to 200 characters
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > 200:
        cleaned = cleaned[:197] + "..."
    return cleaned


@dataclass
class JudgeDispatchTelemetry:
    """Monotonic phase timings and metadata for an LLM judge invocation."""

    operation_name: str
    effective_evaluator: str
    effective_model: str
    status: str = "COMPLETED"
    monotonic_start: float = field(default_factory=time.perf_counter)
    monotonic_end: float = 0.0

    # Separated monotonic durations in seconds
    queue_duration_s: float = 0.0
    provider_duration_s: float = 0.0
    retry_backoff_duration_s: float = 0.0
    local_eval_duration_s: float = 0.0
    model_init_duration_s: float = 0.0
    lock_wait_duration_s: float = 0.0
    operator_wait_duration_s: float = 0.0
    total_duration_s: float = 0.0

    # Identity bindings (excluding raw content)
    source_id: Optional[str] = None
    run_id: Optional[str] = None
    attempt_id: Optional[str] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None

    # Token counters
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    def record_phase(self, phase: str, duration_seconds: float) -> None:
        """Record a monotonic duration for a specific execution phase."""
        dur = max(0.0, float(duration_seconds))
        if phase == "queue":
            self.queue_duration_s += dur
        elif phase == "provider":
            self.provider_duration_s += dur
        elif phase == "retry_backoff":
            self.retry_backoff_duration_s += dur
        elif phase == "local_eval":
            self.local_eval_duration_s += dur
        elif phase == "model_init":
            self.model_init_duration_s += dur
        elif phase == "lock_wait":
            self.lock_wait_duration_s += dur
        elif phase == "operator_wait":
            self.operator_wait_duration_s += dur

    def to_dict(self) -> Dict[str, Any]:
        """Export serialized dictionary with safe non-content metrics."""
        return {
            "operation_name": self.operation_name,
            "effective_evaluator": self.effective_evaluator,
            "effective_model": self.effective_model,
            "status": self.status,
            "total_duration_s": round(self.total_duration_s, 6),
            "phases": {
                "queue_s": round(self.queue_duration_s, 6),
                "provider_s": round(self.provider_duration_s, 6),
                "retry_backoff_s": round(self.retry_backoff_duration_s, 6),
                "local_eval_s": round(self.local_eval_duration_s, 6),
                "model_init_s": round(self.model_init_duration_s, 6),
                "lock_wait_s": round(self.lock_wait_duration_s, 6),
                "operator_wait_s": round(self.operator_wait_duration_s, 6),
            },
            "source_id": self.source_id,
            "run_id": self.run_id,
            "attempt_id": self.attempt_id,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "cached": self.cached_tokens,
                "reasoning": self.reasoning_tokens,
            },
        }


@contextmanager
def judge_dispatch_scope(
    operation_name: str,
    effective_evaluator: str,
    effective_model: str,
    source_id: Optional[str] = None,
    run_id: Optional[str] = None,
    attempt_id: Optional[str] = None,
) -> Generator[JudgeDispatchTelemetry, None, None]:
    """Context manager measuring monotonic duration across judge dispatch phases."""
    telemetry = JudgeDispatchTelemetry(
        operation_name=operation_name,
        effective_evaluator=effective_evaluator,
        effective_model=effective_model,
        source_id=source_id,
        run_id=run_id,
        attempt_id=attempt_id,
    )
    token = _CURRENT_DISPATCH.set(telemetry)
    try:
        yield telemetry
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            telemetry.status = "CANCELLED"
        else:
            telemetry.status = "FAILED"
        telemetry.error_type = exc.__class__.__name__
        telemetry.error_message = _sanitize_error_text(str(exc))
        raise
    finally:
        telemetry.monotonic_end = time.perf_counter()
        elapsed = telemetry.monotonic_end - telemetry.monotonic_start
        sub_sum = (
            telemetry.queue_duration_s
            + telemetry.retry_backoff_duration_s
            + telemetry.local_eval_duration_s
            + telemetry.model_init_duration_s
            + telemetry.lock_wait_duration_s
            + telemetry.operator_wait_duration_s
            + telemetry.provider_duration_s
        )
        telemetry.total_duration_s = max(0.001, elapsed, sub_sum)

        # Default provider duration if no sub-phase was explicitly tracked
        if telemetry.provider_duration_s <= 0.0 and telemetry.total_duration_s > 0.0:
            other_sub_sum = (
                telemetry.queue_duration_s
                + telemetry.retry_backoff_duration_s
                + telemetry.local_eval_duration_s
                + telemetry.model_init_duration_s
                + telemetry.lock_wait_duration_s
                + telemetry.operator_wait_duration_s
            )
            telemetry.provider_duration_s = max(0.0, telemetry.total_duration_s - other_sub_sum)

        _record_scope_metrics(telemetry)
        _CURRENT_DISPATCH.reset(token)


def _record_scope_metrics(telemetry: JudgeDispatchTelemetry) -> None:
    """Record metrics from a completed or failed judge dispatch scope."""
    try:
        from dual_pipeline.telemetry_core.metrics import (
            increment_counter,
            record_histogram,
            record_stage_duration,
        )

        record_stage_duration(
            f"judge.{telemetry.effective_evaluator}",
            telemetry.total_duration_s,
            status=telemetry.status,
        )
        labels = {
            "evaluator": telemetry.effective_evaluator,
            "model": telemetry.effective_model,
            "status": telemetry.status,
        }
        increment_counter("judge.invocations.total", 1.0, labels)
        if telemetry.queue_duration_s > 0:
            record_histogram("judge.queue.duration_seconds", telemetry.queue_duration_s, labels)
        if telemetry.provider_duration_s > 0:
            record_histogram("judge.provider.duration_seconds", telemetry.provider_duration_s, labels)
        if telemetry.retry_backoff_duration_s > 0:
            record_histogram("judge.retry_backoff.duration_seconds", telemetry.retry_backoff_duration_s, labels)
        if telemetry.local_eval_duration_s > 0:
            record_histogram("judge.local_eval.duration_seconds", telemetry.local_eval_duration_s, labels)
        if telemetry.model_init_duration_s > 0:
            record_histogram("judge.model_init.duration_seconds", telemetry.model_init_duration_s, labels)
        if telemetry.lock_wait_duration_s > 0:
            record_histogram("judge.lock_wait.duration_seconds", telemetry.lock_wait_duration_s, labels)
        if telemetry.operator_wait_duration_s > 0:
            record_histogram("judge.operator_wait.duration_seconds", telemetry.operator_wait_duration_s, labels)
    except Exception as ex:
        logger.debug("Failed to flush dispatch metrics: %s", ex)


def _extract_usage_tokens(usage: Any) -> tuple[int, int, int, int, int]:
    if isinstance(usage, dict):
        pt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or usage.get("prompt_token_count") or 0)
        ct = int(usage.get("completion_tokens") or usage.get("output_tokens") or usage.get("candidates_token_count") or 0)
        details = usage.get("prompt_tokens_details") or {}
        cpt = int(details.get("cached_tokens") or usage.get("cached_content_token_count") or usage.get("cache_read_input_tokens") or 0)
        cdetails = usage.get("completion_tokens_details") or {}
        rt = int(cdetails.get("reasoning_tokens") or usage.get("thinking_tokens") or 0)
        cwt = int(details.get("cache_creation_input_tokens") or details.get("cache_write_tokens") or usage.get("cache_creation_input_tokens") or 0)
        return pt, ct, cpt, rt, cwt

    pt = int(usage.prompt_tokens) if isinstance(getattr(usage, "prompt_tokens", None), (int, float)) else 0
    ct = int(usage.completion_tokens) if isinstance(getattr(usage, "completion_tokens", None), (int, float)) else 0
    details = getattr(usage, "prompt_tokens_details", None)
    cpt = int(details.cached_tokens) if (details and isinstance(getattr(details, "cached_tokens", None), (int, float))) else 0
    cdetails = getattr(usage, "completion_tokens_details", None)
    rt = int(cdetails.reasoning_tokens) if (cdetails and isinstance(getattr(cdetails, "reasoning_tokens", None), (int, float))) else 0
    cwt = int(getattr(details, "cache_creation_input_tokens", 0) or getattr(details, "cache_write_tokens", 0) or 0)
    return pt, ct, cpt, rt, cwt


def _resolve_judge_duration(duration_seconds: float, resp: Any) -> float:
    dur = float(duration_seconds)
    if dur > 0.0:
        return dur
    active_scope = _CURRENT_DISPATCH.get()
    if active_scope:
        return max(0.001, time.perf_counter() - active_scope.monotonic_start)
    elapsed_sec = getattr(resp, "_elapsed_seconds", None)
    if isinstance(elapsed_sec, (int, float)) and elapsed_sec > 0:
        return float(elapsed_sec)
    resp_ms = getattr(resp, "response_ms", None)
    if isinstance(resp_ms, (int, float)) and resp_ms > 0:
        return float(resp_ms) / 1000.0
    return 0.001


def _update_active_dispatch_tokens(pt: int, ct: int, cpt: int, rt: int) -> None:
    active_scope = _CURRENT_DISPATCH.get()
    if not active_scope:
        return
    active_scope.prompt_tokens += pt
    active_scope.completion_tokens += ct
    active_scope.cached_tokens += cpt
    active_scope.reasoning_tokens += rt


def _emit_telemetry_metrics(
    model: str,
    dur: float,
    pt: int,
    ct: int,
    cpt: int,
    cwt: int,
    rt: int,
    operation_name: str,
) -> None:
    try:
        from dual_pipeline.cost_tracker import get_cost_tracker, get_token_tracker
        from dual_pipeline.telemetry_core.metrics import record_llm_call_metrics

        get_token_tracker().record_usage(
            prompt_tokens=pt,
            completion_tokens=ct,
            cached_prompt_tokens=cpt,
            cache_write_tokens=cwt,
            reasoning_tokens=rt,
        )
        get_cost_tracker().record_call(
            model=model,
            prompt_tokens=pt,
            completion_tokens=ct,
            cached_prompt_tokens=cpt,
            cache_write_tokens=cwt,
            reasoning_tokens=rt,
            batch=False,
        )
        record_llm_call_metrics(
            model=model,
            duration_seconds=dur,
            prompt_tokens=pt,
            completion_tokens=ct,
            reasoning_tokens=rt,
        )
    except Exception as ex:
        logger.warning("Failed to record %s LLM telemetry: %s", operation_name, ex)


def record_judge_llm_usage(
    resp: Any,
    model: str,
    duration_seconds: float = 0.0,
    operation_name: str = "LLM Judge",
) -> None:
    """Record token usage, cost, and telemetry metrics from an LLM Judge response."""
    if not resp:
        return
    usage = getattr(resp, "usage", None)
    if not usage:
        return

    pt, ct, cpt, rt, cwt = _extract_usage_tokens(usage)
    dur = _resolve_judge_duration(duration_seconds, resp)
    _update_active_dispatch_tokens(pt, ct, cpt, rt)
    _emit_telemetry_metrics(model, dur, pt, ct, cpt, cwt, rt, operation_name)

