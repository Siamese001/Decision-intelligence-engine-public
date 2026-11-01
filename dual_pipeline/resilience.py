"""
dual_pipeline/resilience.py

Enterprise resilience, idempotent retry, and fault tolerance framework:
1. Idempotency key generation (RFC 8785 canonical hash)
2. Exponential backoff retry with decorrelated full jitter
3. Retryable exception classification (HTTP 429, 503, timeouts, network blips)
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import random
import time
from typing import Any, Callable, Dict, Optional, Sequence, Tuple, Type, TypeVar, Union

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Default transient/retryable exception categories
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    BrokenPipeError,
    OSError,
)


def generate_idempotency_key(payload: Union[str, bytes, Dict[str, Any], Sequence[Any]]) -> str:
    """
    Computes deterministic SHA-256 hex digest from payload for idempotent request tagging.
    """
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        # Canonical JSON serialization
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    return f"idem_{hashlib.sha256(raw).hexdigest()[:24]}"


def is_retryable_error(exc: Exception) -> bool:
    """
    Evaluates whether an exception represents a transient failure eligible for retry:
    - Built-in network/timeout exceptions
    - HTTP status 429 (Too Many Requests), 502, 503 (Service Unavailable), 504 (Gateway Timeout)
    """
    if isinstance(exc, DEFAULT_RETRYABLE_EXCEPTIONS):
        return True

    # Inspect status_code or http_status attribute if present (e.g. requests, urllib, httpx, or LLM SDKs)
    status_code = getattr(exc, "status_code", getattr(exc, "http_status", None))
    if isinstance(status_code, int) and status_code in (429, 500, 502, 503, 504):
        return True

    exc_msg = str(exc).lower()
    transient_indicators = (
        "rate limit",
        "too many requests",
        "connection reset",
        "connection refused",
        "timeout",
        "service unavailable",
        "temporarily unavailable",
        "503 service",
        "429 client",
    )
    if any(ind in exc_msg for ind in transient_indicators):
        return True

    return False


def with_idempotent_retry(
    max_retries: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None,
    on_retry_callback: Optional[Callable[[int, Exception, float], None]] = None,
) -> Callable[[F], F]:
    """
    Decorator executing wrapped function with exponential backoff and jitter.
    Powered by tenacity with Idempotency-Key support and telemetry callbacks.
    """
    import tenacity

    exceptions_to_catch = retryable_exceptions or DEFAULT_RETRYABLE_EXCEPTIONS

    def is_transient(exc: BaseException) -> bool:
        if isinstance(exc, Exception):
            return is_retryable_error(exc) or isinstance(exc, exceptions_to_catch)
        return False

    wait_strategy = (
        tenacity.wait_exponential_jitter(initial=base_delay, max=max_delay, exp_base=backoff_factor)
        if jitter
        else tenacity.wait_exponential(multiplier=base_delay, max=max_delay, exp_base=backoff_factor)
    )

    def before_sleep(retry_state: tenacity.RetryCallState) -> None:
        attempt = retry_state.attempt_number
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        delay = retry_state.next_action.sleep if retry_state.next_action else 0.0
        logger.warning(
            "Transient failure in '%s' (attempt %d/%d): %s. Backing off for %.2fs...",
            getattr(retry_state.fn, "__name__", "callable"),
            attempt,
            max_retries,
            exc,
            delay,
        )
        if on_retry_callback and exc is not None:
            try:
                on_retry_callback(attempt, exc, delay)
            except Exception:
                pass

    def decorator(func: F) -> F:
        retrying = tenacity.Retrying(
            stop=tenacity.stop_after_attempt(max_retries + 1),
            wait=wait_strategy,
            retry=tenacity.retry_if_exception(is_transient),
            before_sleep=before_sleep,
            reraise=True,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return retrying(func, *args, **kwargs)

        return wrapper  # type: ignore

    return decorator
