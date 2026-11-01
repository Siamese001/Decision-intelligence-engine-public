"""
Fail-Closed Fallback Circuit Breaker Gateway.

Governs execution routing between local Metal open-weights models and external
cloud fallback APIs (gpt-5.6-luna / gpt-5.6-luna). Default posture is fail-closed
(cloud fallback disabled by default).
"""
from __future__ import annotations

import enum
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List, Optional

logger = logging.getLogger("dual_pipeline.policy.circuit_breaker")


class BreakerState(str, enum.Enum):
    CLOSED = "CLOSED"         # Normal operation: local models executing
    OPEN = "OPEN"             # Tripped: local failures exceeded threshold
    HALF_OPEN = "HALF_OPEN"   # Probing: testing whether local execution has recovered


class LocalExecutionHaltedError(RuntimeError):
    """Raised when local execution fails and cloud fallback is not authorized."""
    pass


@dataclass(frozen=True)
class CircuitAuditEvent:
    """Immutable audit record of circuit breaker state transitions and fallback attempts."""
    timestamp: str
    event_type: str
    from_state: str
    to_state: str
    failure_reason: str
    model_key: str
    cloud_fallback_invoked: bool


class CircuitBreakerGateway:
    """Manages failure thresholds and fail-closed cloud fallback gating."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_success_threshold: int = 2,
        allow_cloud_fallback: bool = False,
        fallback_model: Optional[str] = None,
    ):
        from dual_pipeline.model_registry import ModelRegistry, ModelTier
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_success_threshold = max(1, recovery_success_threshold)
        self.allow_cloud_fallback = allow_cloud_fallback
        self.fallback_model = fallback_model or ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_REASONING)[0]
        self._state = BreakerState.CLOSED
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._lock = threading.RLock()
        self._audit_log: List[CircuitAuditEvent] = []

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state

    @property
    def consecutive_failures(self) -> int:
        with self._lock:
            return self._consecutive_failures

    def record_success(self, model_key: str = "local_model") -> None:
        """Records a successful local execution."""
        with self._lock:
            self._consecutive_failures = 0
            if self._state == BreakerState.HALF_OPEN:
                self._consecutive_successes += 1
                if self._consecutive_successes >= self.recovery_success_threshold:
                    self._transition(
                        to_state=BreakerState.CLOSED,
                        reason="RECOVERY_THRESHOLD_MET",
                        model_key=model_key,
                        cloud_fallback=False,
                    )
            elif self._state == BreakerState.CLOSED:
                self._consecutive_successes += 1

    def record_failure(self, model_key: str, reason: str) -> None:
        """
        Records a local execution or schema failure.
        Trips breaker to OPEN if failure threshold is reached.
        """
        with self._lock:
            self._consecutive_failures += 1
            self._consecutive_successes = 0
            logger.warning(
                f"[CIRCUIT_BREAKER] Failure {self._consecutive_failures}/{self.failure_threshold} "
                f"on '{model_key}': {reason}"
            )

            if self._consecutive_failures >= self.failure_threshold and self._state != BreakerState.OPEN:
                self._transition(
                    to_state=BreakerState.OPEN,
                    reason=f"EXCEEDED_FAILURE_THRESHOLD: {reason}",
                    model_key=model_key,
                    cloud_fallback=self.allow_cloud_fallback,
                )

    def route_execution(
        self,
        local_fn: Callable[[], Any],
        cloud_fallback_fn: Optional[Callable[[], Any]] = None,
        model_key: str = "local_model",
    ) -> Any:
        """
        Executes local_fn. If the circuit is OPEN and cloud fallback is authorized,
        routes to cloud_fallback_fn; otherwise fails closed with LocalExecutionHaltedError.
        """
        with self._lock:
            current_state = self._state
            fallback_permitted = self.allow_cloud_fallback

        if current_state == BreakerState.OPEN:
            if not fallback_permitted or cloud_fallback_fn is None:
                raise LocalExecutionHaltedError(
                    f"[CIRCUIT BREAKER: OPEN] Local execution for '{model_key}' has halted "
                    f"due to repeated failures, and cloud fallback is disabled by policy."
                )
            logger.info(f"[CIRCUIT BREAKER] Routing to cloud fallback ({self.fallback_model}) for '{model_key}'.")
            return cloud_fallback_fn()

        try:
            result = local_fn()
            self.record_success(model_key)
            return result
        except Exception as e:
            self.record_failure(model_key, str(e))
            if self._state == BreakerState.OPEN and fallback_permitted and cloud_fallback_fn is not None:
                logger.warning("[CIRCUIT BREAKER] Tripped to OPEN during call. Diverting to cloud fallback.")
                return cloud_fallback_fn()
            raise

    def set_cloud_fallback_authorization(self, authorized: bool) -> None:
        """Enables or disables cloud fallback authorization."""
        with self._lock:
            self.allow_cloud_fallback = authorized
            logger.info(f"[CIRCUIT_BREAKER] Cloud fallback authorization set to {authorized}.")

    def trip(self, reason: str = "MANUAL_TRIP", model_key: str = "manual") -> None:
        """Manually trips the circuit breaker to OPEN."""
        with self._lock:
            self._transition(BreakerState.OPEN, reason, model_key, self.allow_cloud_fallback)

    def reset(self) -> None:
        """Manually resets the circuit breaker to CLOSED."""
        with self._lock:
            self._transition(BreakerState.CLOSED, "MANUAL_RESET", "system", False)
            self._consecutive_failures = 0
            self._consecutive_successes = 0

    def _transition(self, to_state: BreakerState, reason: str, model_key: str, cloud_fallback: bool) -> None:
        event = CircuitAuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type="STATE_TRANSITION",
            from_state=self._state.value,
            to_state=to_state.value,
            failure_reason=reason,
            model_key=model_key,
            cloud_fallback_invoked=cloud_fallback,
        )
        self._state = to_state
        self._audit_log.append(event)
        logger.info(f"[CIRCUIT_BREAKER] Transition: {event.from_state} -> {event.to_state} ({reason})")

    def get_audit_log(self) -> List[CircuitAuditEvent]:
        with self._lock:
            return list(self._audit_log)


_GLOBAL_BREAKER: Optional[CircuitBreakerGateway] = None
_BREAKER_LOCK = threading.Lock()


def get_circuit_breaker() -> CircuitBreakerGateway:
    """Returns singleton CircuitBreakerGateway instance."""
    global _GLOBAL_BREAKER
    with _BREAKER_LOCK:
        if _GLOBAL_BREAKER is None:
            _GLOBAL_BREAKER = CircuitBreakerGateway()
        return _GLOBAL_BREAKER
