"""
dual_pipeline/cost_tracker.py

Token usage and LLM cost accounting engine with hard budget ceiling enforcement.
Integrates with AstraModelPricing to compute per-call and cumulative USD expenditures
for prompt, cached, completion, and reasoning tokens.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from dual_pipeline.credentials import CredentialManager
from dual_pipeline.token_optimization.schemas import get_model_pricing

logger = logging.getLogger("dual_pipeline.cost_tracker")

from dual_pipeline.l0.request_context import (
    ActionRole,
    BudgetExhaustionError,
    DeadlineExceededError,
    OptionalWorkDisabledError,
    RequestContext,
    ReservationError,
    ReservationLease,
    ReservationState,
    get_clock_epoch_id,
    verify_clock_continuity,
)
from dual_pipeline.l0.reservation_manager import (
    BudgetReservationManager,
    get_reservation_manager,
)


class RunTokenUsageTracker:
    """Thread-safe tracker aggregating token consumption across all LLM operations in a run."""

    def __init__(self, max_tokens: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self.max_tokens = max_tokens
        self.prompt_tokens: int = 0
        self.cached_prompt_tokens: int = 0
        self.cache_write_tokens: int = 0
        self.completion_tokens: int = 0
        self.reasoning_tokens: int = 0
        self.total_tokens: int = 0
        self.call_count: int = 0

    def intercept_token_call(self, estimated_next_call_tokens: int) -> None:
        """Crash the pipeline if total_tokens + estimated_next_call_tokens > max_tokens."""
        with self._lock:
            if self.max_tokens is not None and self.total_tokens + estimated_next_call_tokens > self.max_tokens:
                from dual_pipeline.exceptions import PipelineTokenBudgetExhaustedError
                raise PipelineTokenBudgetExhaustedError(
                    f"Directive token budget exhausted. "
                    f"Total: {self.total_tokens}, Estimated next call: {estimated_next_call_tokens}, Max: {self.max_tokens}"
                )

    def record_usage(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_prompt_tokens: int = 0,
        reasoning_tokens: int = 0,
        cache_write_tokens: int = 0,
        uncached_prompt_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        for v in [prompt_tokens, cached_prompt_tokens, completion_tokens, reasoning_tokens, cache_write_tokens, uncached_prompt_tokens]:
            if v is not None and not isinstance(v, (int, float)):
                raise TypeError(f"Token counts must be numeric, got {type(v)}")

        cpt = int(cached_prompt_tokens) if cached_prompt_tokens is not None else 0
        if uncached_prompt_tokens is not None and prompt_tokens == 0:
            pt = int(uncached_prompt_tokens) + cpt
        else:
            pt = int(prompt_tokens) if prompt_tokens is not None else 0
        ct = int(completion_tokens) if completion_tokens is not None else 0
        rt = int(reasoning_tokens) if reasoning_tokens is not None else 0
        cwt = int(cache_write_tokens) if cache_write_tokens is not None else 0

        with self._lock:
            self.call_count += 1
            self.prompt_tokens += max(0, pt)
            self.cached_prompt_tokens += max(0, cpt)
            self.cache_write_tokens += max(0, cwt)
            self.completion_tokens += max(0, ct)
            self.reasoning_tokens += max(0, rt)
            self.total_tokens += max(0, pt) + max(0, ct)

    @property
    def total_uncached_prompt_tokens(self) -> int:
        with self._lock:
            return max(0, self.prompt_tokens - self.cached_prompt_tokens - self.cache_write_tokens)

    @property
    def total_cached_prompt_tokens(self) -> int:
        with self._lock:
            return self.cached_prompt_tokens

    @property
    def total_cache_write_tokens(self) -> int:
        with self._lock:
            return self.cache_write_tokens

    @property
    def total_completion_tokens(self) -> int:
        with self._lock:
            return self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "call_count": self.call_count,
                "total_prompt_tokens": self.prompt_tokens,
                "cached_prompt_tokens": self.cached_prompt_tokens,
                "cache_write_tokens": self.cache_write_tokens,
                "uncached_prompt_tokens": max(0, self.prompt_tokens - self.cached_prompt_tokens - self.cache_write_tokens),
                "total_completion_tokens": self.completion_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "total_tokens": self.total_tokens,
            }

    def reset(self) -> None:
        with self._lock:
            self.prompt_tokens = 0
            self.cached_prompt_tokens = 0
            self.cache_write_tokens = 0
            self.completion_tokens = 0
            self.reasoning_tokens = 0
            self.total_tokens = 0
            self.call_count = 0
def _to_int(val: Any) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _normalize_call_tokens(
    prompt_tokens: Any,
    cached_prompt_tokens: Any,
    completion_tokens: Any,
    reasoning_tokens: Any,
    cache_write_tokens: Any,
    uncached_prompt_tokens: Optional[Any],
) -> Tuple[int, int, int, int, int, int]:
    cpt = _to_int(cached_prompt_tokens)
    ct = _to_int(completion_tokens)
    rt = _to_int(reasoning_tokens)
    cwt = _to_int(cache_write_tokens)

    if uncached_prompt_tokens is not None:
        uncached = max(0, _to_int(uncached_prompt_tokens))
        pt = uncached + cpt + cwt if _to_int(prompt_tokens) == 0 else _to_int(prompt_tokens)
    else:
        pt = _to_int(prompt_tokens)
        uncached = max(0, pt - cpt - cwt)

    return pt, uncached, cpt, ct, rt, cwt


class RunCostTracker:
    """
    Tracks cumulative USD expenditure across LLM calls using model pricing tables
    and enforces hard budget ceilings.
    """

    def __init__(
        self,
        max_cost_usd: Optional[float] = 50.0,
        budget_ceiling_usd: Optional[float] = None,
        max_call_count: Optional[int] = None,
    ) -> None:
        ceiling = max_cost_usd if max_cost_usd is not None else budget_ceiling_usd
        if ceiling is None:
            ceiling = 50.0
        self.max_cost_usd = float(ceiling)
        self.max_call_count = max_call_count
        self.total_cost_usd: float = 0.0
        self.call_history: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def call_count(self) -> int:
        with self._lock:
            return len(self.call_history)

    @property
    def total_uncached_prompt_tokens(self) -> int:
        with self._lock:
            return sum(c.get("uncached_prompt_tokens", 0) for c in self.call_history)

    @property
    def total_cached_prompt_tokens(self) -> int:
        with self._lock:
            return sum(c.get("cached_prompt_tokens", 0) for c in self.call_history)

    @property
    def total_cache_write_tokens(self) -> int:
        with self._lock:
            return sum(c.get("cache_write_tokens", 0) for c in self.call_history)

    @property
    def total_completion_tokens(self) -> int:
        with self._lock:
            return sum(c.get("completion_tokens", 0) for c in self.call_history)

    @property
    def total_prompt_tokens(self) -> int:
        with self._lock:
            return sum(c.get("prompt_tokens", 0) for c in self.call_history)

    def check_budget_headroom(self, estimated_cost_usd: float = 0.0) -> bool:
        """Returns True if projected expenditure is within authorized budget ceiling."""
        with self._lock:
            if self.max_cost_usd is None:
                return True
            return (self.total_cost_usd + estimated_cost_usd) <= self.max_cost_usd

    def assert_budget_headroom(self, estimated_cost_usd: float = 0.0) -> None:
        """Raises BudgetExhaustionError if projected expenditure would exceed budget ceiling."""
        with self._lock:
            if self.max_cost_usd is not None and (self.total_cost_usd + estimated_cost_usd) > self.max_cost_usd:
                raise BudgetExhaustionError(
                    f"BUDGET_HEADROOM_EXHAUSTED: Projected cost ${self.total_cost_usd + estimated_cost_usd:.4f} "
                    f"exceeds limit of ${self.max_cost_usd:.4f}."
                )

    def _enforce_limits(self) -> None:
        if self.max_cost_usd is not None and self.total_cost_usd > self.max_cost_usd:
            logger.error(
                "Budget exhausted: cumulative cost $%.4f exceeds limit $%.4f",
                self.total_cost_usd,
                self.max_cost_usd,
            )
            raise BudgetExhaustionError(
                f"BUDGET_EXHAUSTED: Cumulative LLM expenditure ${self.total_cost_usd:.4f} "
                f"exceeds authorized ceiling of ${self.max_cost_usd:.4f}."
            )
        if self.max_call_count is not None and len(self.call_history) > self.max_call_count:
            logger.error("Call count limit exhausted: %d calls", len(self.call_history))
            raise BudgetExhaustionError(
                f"CALL_LIMIT_EXHAUSTED: LLM call count {len(self.call_history)} "
                f"exceeds ceiling of {self.max_call_count}."
            )

    def record_call(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_prompt_tokens: int = 0,
        reasoning_tokens: int = 0,
        cache_write_tokens: int = 0,
        uncached_prompt_tokens: Optional[int] = None,
        is_batch: bool = False,
        requested_model: Optional[str] = None,
        resolved_model: Optional[str] = None,
        dispatched_model: Optional[str] = None,
        observed_model: Optional[str] = None,
        evidence_strength: str = "OBSERVED_RESPONSE",
        **kwargs: Any,
    ) -> float:
        """
        Calculates and records the USD cost of an LLM invocation.
        Raises BudgetExhaustionError if cumulative expenditure exceeds max_cost_usd.
        """
        pt, uncached, cpt, ct, rt, cwt = _normalize_call_tokens(
            prompt_tokens,
            cached_prompt_tokens,
            completion_tokens,
            reasoning_tokens,
            cache_write_tokens,
            uncached_prompt_tokens,
        )

        eff_dispatched = dispatched_model or model
        pricing = get_model_pricing(eff_dispatched)
        effective_batch = bool(is_batch or kwargs.get("batch", False))
        call_cost = pricing.calculate_cost(
            uncached_input_tokens=uncached,
            cached_input_tokens=max(0, cpt),
            output_tokens=max(0, ct),
            cache_write_tokens=max(0, cwt),
            is_batch=effective_batch,
        )

        wf = str(kwargs.get("workflow") or "default")
        cached_share = round(cpt / pt, 4) if pt > 0 else 0.0

        with self._lock:
            self.total_cost_usd = round(self.total_cost_usd + call_cost, 6)
            call_entry = {
                "model": model,
                "requested_model": requested_model or model,
                "resolved_model": resolved_model or model,
                "dispatched_model": eff_dispatched,
                "observed_model": observed_model,
                "evidence_strength": evidence_strength,
                "workflow": wf,
                "batch": effective_batch,
                "prompt_tokens": pt,
                "uncached_prompt_tokens": uncached,
                "cached_prompt_tokens": cpt,
                "cached_token_share": cached_share,
                "cache_write_tokens": cwt,
                "completion_tokens": ct,
                "reasoning_tokens": rt,
                "is_batch": is_batch,
                "cost_usd": call_cost,
                "cumulative_cost_usd": self.total_cost_usd,
            }
            self.call_history.append(call_entry)
            self._enforce_limits()

        return call_cost

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "total_cost_usd": self.total_cost_usd,
                "max_cost_usd": self.max_cost_usd,
                "call_count": len(self.call_history),
                "budget_exhausted": (self.max_cost_usd is not None and self.total_cost_usd > self.max_cost_usd),
                "call_history": list(self.call_history),
            }

    def get_cache_metrics_by_workflow(self) -> Dict[str, Dict[str, Any]]:
        """Summarize cache hit rate, token counts, and costs broken down by workflow."""
        with self._lock:
            by_wf: Dict[str, Dict[str, Any]] = {}
            for c in self.call_history:
                wf = c.get("workflow", "default")
                if wf not in by_wf:
                    by_wf[wf] = {
                        "calls": 0,
                        "prompt_tokens": 0,
                        "cached_tokens": 0,
                        "completion_tokens": 0,
                        "total_cost_usd": 0.0,
                    }
                by_wf[wf]["calls"] += 1
                by_wf[wf]["prompt_tokens"] += c.get("prompt_tokens", 0)
                by_wf[wf]["cached_tokens"] += c.get("cached_prompt_tokens", 0)
                by_wf[wf]["completion_tokens"] += c.get("completion_tokens", 0)
                by_wf[wf]["total_cost_usd"] = round(by_wf[wf]["total_cost_usd"] + c.get("cost_usd", 0.0), 6)
            for wf, m in by_wf.items():
                pt = m["prompt_tokens"]
                ct = m["cached_tokens"]
                m["cached_token_share"] = round(ct / pt, 4) if pt > 0 else 0.0
            return by_wf

    def reset(self) -> None:
        with self._lock:
            self.total_cost_usd = 0.0
            self.call_history.clear()


_GLOBAL_TOKEN_TRACKER = RunTokenUsageTracker()
_GLOBAL_COST_TRACKER: Optional[RunCostTracker] = None
_TRACKER_LOCK = threading.Lock()


def get_token_tracker() -> RunTokenUsageTracker:
    return _GLOBAL_TOKEN_TRACKER


def get_cost_tracker(max_cost_usd: Optional[float] = None) -> RunCostTracker:
    global _GLOBAL_COST_TRACKER
    if max_cost_usd is not None:
        with _TRACKER_LOCK:
            if _GLOBAL_COST_TRACKER is None:
                _GLOBAL_COST_TRACKER = RunCostTracker(max_cost_usd=max_cost_usd)
            else:
                _GLOBAL_COST_TRACKER.max_cost_usd = float(max_cost_usd)
    if _GLOBAL_COST_TRACKER is None:
        env_val = CredentialManager.get_credential("NEE_MAX_COST_USD") or CredentialManager.get_credential("NEE_PARAM_MAX_COST_USD")
        ceiling = float(env_val) if env_val else 15.0
        with _TRACKER_LOCK:
            if _GLOBAL_COST_TRACKER is None:
                _GLOBAL_COST_TRACKER = RunCostTracker(max_cost_usd=ceiling)
    return _GLOBAL_COST_TRACKER


def reset_trackers() -> None:
    _GLOBAL_TOKEN_TRACKER.reset()
    if _GLOBAL_COST_TRACKER is not None:
        if _GLOBAL_COST_TRACKER.call_count > 0:
            logger.warning("reset_trackers() called mid-pipeline")
        _GLOBAL_COST_TRACKER.reset()
