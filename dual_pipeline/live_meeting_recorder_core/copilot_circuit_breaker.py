"""
copilot_circuit_breaker.py — Real-time telemetry, failure detection, and circuit breaker for Copilot graph cache.

Enforces:
1. Continuous monitoring of cache hits, misses, and disk fallbacks within a 5-minute sliding window.
2. Automated circuit breaker tripping when fallback rate exceeds 50% or cross-engagement queries return silently empty.
3. OpenTelemetry span and event emission (cache_miss, fallback_to_disk, circuit_breaker_tripped).
4. Graceful fail-closed / alert postures for degraded states.
"""
from __future__ import annotations

import enum
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitBreakerStatus(str, enum.Enum):
    """Lifecycle states of the Copilot cache circuit breaker."""
    CLOSED = "CLOSED"         # Normal operation: cache healthy
    HALF_OPEN = "HALF_OPEN"   # Probing recovery after trip
    OPEN = "OPEN"             # Tripped: excessive fallbacks or silent empty results


class CircuitTripReason(str, enum.Enum):
    """Reason for circuit breaker tripping."""
    HIGH_FALLBACK_RATE = "HIGH_FALLBACK_RATE"
    SILENT_EMPTY_CROSS_ENGAGEMENT = "SILENT_EMPTY_CROSS_ENGAGEMENT"
    TIMEOUT_BREACH = "TIMEOUT_BREACH"
    MANUAL_TRIP = "MANUAL_TRIP"


@dataclass
class CircuitMetricsSnapshot:
    """Immutable snapshot of circuit telemetry."""
    status: CircuitBreakerStatus
    window_seconds: float
    hits: int
    misses: int
    fallbacks: int
    total_operations: int
    fallback_rate: float
    consecutive_empty_cross_engagements: int
    is_tripped: bool
    trip_reason: Optional[str]
    last_tripped_at: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "window_seconds": self.window_seconds,
            "hits": self.hits,
            "misses": self.misses,
            "fallbacks": self.fallbacks,
            "total_operations": self.total_operations,
            "fallback_rate": round(self.fallback_rate, 4),
            "consecutive_empty_cross_engagements": self.consecutive_empty_cross_engagements,
            "is_tripped": self.is_tripped,
            "trip_reason": self.trip_reason,
            "last_tripped_at": self.last_tripped_at,
        }


class CopilotCacheCircuitBreaker:
    """
    Sliding-window circuit breaker for Copilot cache & retrieval pipeline.
    Trips to OPEN if:
      - Fallback rate > 50% over sliding window (default 300s) with >= min_requests
      - Consecutive empty cross-engagement results >= threshold (default 3)
    """

    def __init__(
        self,
        window_seconds: float = 300.0,
        fallback_rate_threshold: float = 0.50,
        min_requests_threshold: int = 5,
        silent_empty_cross_engagement_threshold: int = 3,
    ) -> None:
        self.window_seconds: float = max(1.0, float(window_seconds))
        self.fallback_rate_threshold: float = float(fallback_rate_threshold)
        self.min_requests_threshold: int = max(1, int(min_requests_threshold))
        self.silent_empty_threshold: int = max(1, int(silent_empty_cross_engagement_threshold))

        self._lock = threading.RLock()
        self._status: CircuitBreakerStatus = CircuitBreakerStatus.CLOSED
        self._trip_reason: Optional[CircuitTripReason] = None
        self._last_tripped_at: Optional[float] = None

        # Deques of epoch timestamps
        self._hit_timestamps: deque[float] = deque()
        self._miss_timestamps: deque[float] = deque()
        self._fallback_timestamps: deque[float] = deque()

        self._consecutive_empty_cross_engagements: int = 0
        self._consecutive_timeout_breaches: int = 0
        self.recovery_timeout_seconds: float = 30.0

    @property
    def status(self) -> CircuitBreakerStatus:
        with self._lock:
            self._evaluate_state_locked()
            return self._status

    @property
    def is_tripped(self) -> bool:
        return self.status == CircuitBreakerStatus.OPEN

    @property
    def trip_reason(self) -> Optional[str]:
        with self._lock:
            return self._trip_reason.value if self._trip_reason else None

    def _purge_old_events_locked(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._hit_timestamps and self._hit_timestamps[0] < cutoff:
            self._hit_timestamps.popleft()
        while self._miss_timestamps and self._miss_timestamps[0] < cutoff:
            self._miss_timestamps.popleft()
        while self._fallback_timestamps and self._fallback_timestamps[0] < cutoff:
            self._fallback_timestamps.popleft()

    def _evaluate_state_locked(self) -> None:
        now = time.time()
        self._purge_old_events_locked(now)

        if self._status == CircuitBreakerStatus.OPEN:
            if self._last_tripped_at and (now - self._last_tripped_at) >= self.recovery_timeout_seconds:
                self._status = CircuitBreakerStatus.HALF_OPEN
            return

        total = len(self._hit_timestamps) + len(self._miss_timestamps) + len(self._fallback_timestamps)
        fallbacks = len(self._fallback_timestamps)
        rate = (fallbacks / total) if total > 0 else 0.0

        if total >= self.min_requests_threshold and rate > self.fallback_rate_threshold:
            self._trip_locked(CircuitTripReason.HIGH_FALLBACK_RATE, now)
        elif self._consecutive_empty_cross_engagements >= self.silent_empty_threshold:
            self._trip_locked(CircuitTripReason.SILENT_EMPTY_CROSS_ENGAGEMENT, now)

    def _trip_locked(self, reason: CircuitTripReason, now: float) -> None:
        self._status = CircuitBreakerStatus.OPEN
        self._trip_reason = reason
        self._last_tripped_at = now
        logger.warning(
            "CopilotCacheCircuitBreaker TRIPPED to OPEN. Reason: %s (hits=%d, misses=%d, fallbacks=%d)",
            reason.value,
            len(self._hit_timestamps),
            len(self._miss_timestamps),
            len(self._fallback_timestamps),
        )
        self.emit_otel_event(
            "circuit_breaker_tripped",
            {
                "circuit.status": self._status.value,
                "circuit.trip_reason": reason.value,
                "circuit.fallbacks": len(self._fallback_timestamps),
                "circuit.timestamp": now,
            },
        )

    def record_hit(self, node_id: Optional[str] = None) -> None:
        """Records a successful L1 cache hit without spawning a synthetic OTEL span when uninstrumented."""
        now = time.time()
        with self._lock:
            self._hit_timestamps.append(now)
            self._consecutive_timeout_breaches = 0
            if self._status == CircuitBreakerStatus.HALF_OPEN:
                self._status = CircuitBreakerStatus.CLOSED
                self._trip_reason = None
            self._evaluate_state_locked()

    def record_latency_ms(self, elapsed_ms: float, budget_ms: float = 15.0) -> None:
        """Records retrieval/verification latency and trips TIMEOUT_BREACH after 3 consecutive breaches."""
        now = time.time()
        with self._lock:
            if elapsed_ms > budget_ms:
                self._consecutive_timeout_breaches += 1
                if self._consecutive_timeout_breaches >= 3 and self._status != CircuitBreakerStatus.OPEN:
                    self._trip_locked(CircuitTripReason.TIMEOUT_BREACH, now)
            else:
                self._consecutive_timeout_breaches = 0

    def record_miss(self, node_id: Optional[str] = None) -> None:
        """Records a cache miss."""
        now = time.time()
        with self._lock:
            self._miss_timestamps.append(now)
            self._evaluate_state_locked()
        self.emit_otel_event("cache_miss", {"cache.node_id": node_id or "unspecified"})

    def record_fallback(self, reason: str = "fallback_to_disk", node_id: Optional[str] = None) -> None:
        """Records an intentional or forced fallback to L2 disk / topology retrieval."""
        now = time.time()
        with self._lock:
            self._fallback_timestamps.append(now)
            self._evaluate_state_locked()
        self.emit_otel_event(
            "fallback_to_disk",
            {"cache.reason": reason, "cache.node_id": node_id or "unspecified"},
        )

    def record_cross_engagement_result(self, result_count: int, query: str = "") -> None:
        """Records cross-engagement query outcome. Consecutive 0-result returns trigger tripping."""
        with self._lock:
            if result_count <= 0:
                self._consecutive_empty_cross_engagements += 1
                logger.info(
                    "Cross-engagement query returned 0 results (consecutive=%d/%d). Query: '%s'",
                    self._consecutive_empty_cross_engagements,
                    self.silent_empty_threshold,
                    query,
                )
            else:
                self._consecutive_empty_cross_engagements = 0
            self._evaluate_state_locked()

    def emit_otel_event(self, event_name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        """Emits an OpenTelemetry span or event with standardized semantics."""
        try:
            from opentelemetry import trace
            tracer = trace.get_tracer("dual_pipeline.live_copilot.cache")
            current_span = trace.get_current_span()
            clean_attrs = attributes or {}
            if current_span and current_span.is_recording():
                current_span.add_event(event_name, attributes=clean_attrs)
            else:
                with tracer.start_as_current_span(f"copilot_cache.{event_name}") as span:
                    for k, v in clean_attrs.items():
                        span.set_attribute(str(k), v)
        except Exception as exc:
            logger.debug("Failed to emit OTEL event '%s': %s", event_name, exc)

    def get_snapshot(self) -> CircuitMetricsSnapshot:
        """Returns point-in-time metrics snapshot."""
        with self._lock:
            now = time.time()
            self._purge_old_events_locked(now)
            hits = len(self._hit_timestamps)
            misses = len(self._miss_timestamps)
            fallbacks = len(self._fallback_timestamps)
            total = hits + misses + fallbacks
            rate = (fallbacks / total) if total > 0 else 0.0
            return CircuitMetricsSnapshot(
                status=self._status,
                window_seconds=self.window_seconds,
                hits=hits,
                misses=misses,
                fallbacks=fallbacks,
                total_operations=total,
                fallback_rate=rate,
                consecutive_empty_cross_engagements=self._consecutive_empty_cross_engagements,
                is_tripped=(self._status == CircuitBreakerStatus.OPEN),
                trip_reason=self.trip_reason,
                last_tripped_at=self._last_tripped_at,
            )

    def reset(self) -> None:
        """Resets circuit breaker state to CLOSED and clears history."""
        with self._lock:
            self._status = CircuitBreakerStatus.CLOSED
            self._trip_reason = None
            self._last_tripped_at = None
            self._hit_timestamps.clear()
            self._miss_timestamps.clear()
            self._fallback_timestamps.clear()
            self._consecutive_empty_cross_engagements = 0


# Global singleton registry
_GLOBAL_CIRCUIT_BREAKER: Optional[CopilotCacheCircuitBreaker] = None
_GLOBAL_LOCK = threading.Lock()


def get_copilot_circuit_breaker() -> CopilotCacheCircuitBreaker:
    """Returns the shared CopilotCacheCircuitBreaker singleton."""
    global _GLOBAL_CIRCUIT_BREAKER
    with _GLOBAL_LOCK:
        if _GLOBAL_CIRCUIT_BREAKER is None:
            _GLOBAL_CIRCUIT_BREAKER = CopilotCacheCircuitBreaker()
        return _GLOBAL_CIRCUIT_BREAKER


def reset_copilot_circuit_breaker() -> None:
    """Resets the shared CopilotCacheCircuitBreaker singleton."""
    global _GLOBAL_CIRCUIT_BREAKER
    with _GLOBAL_LOCK:
        if _GLOBAL_CIRCUIT_BREAKER is not None:
            _GLOBAL_CIRCUIT_BREAKER.reset()
