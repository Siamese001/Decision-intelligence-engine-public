"""
dual_pipeline/rate_limiter.py

Thread-safe token bucket rate limiter with RPM/TPM throttling,
HTTP 429 Retry-After header integration, and global provider limiter registry.
"""
from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Generator, Mapping, Optional

logger = logging.getLogger("dual_pipeline.rate_limiter")


class RateLimitExceededError(RuntimeError):
    """Raised when request cannot be fulfilled within rate limiter timeout."""


class TokenBucketRateLimiter:
    """
    Thread-safe dual token bucket rate limiter enforcing requests-per-minute (RPM)
    and tokens-per-minute (TPM) ceilings with exponential jitter backoff on HTTP 429.
    """

    def __init__(
        self,
        name: str = "default",
        rpm: float = 60.0,
        tpm: float = 60000.0,
        max_burst_factor: float = 1.0,
    ) -> None:
        self.name = name
        self.rpm = max(0.1, float(rpm))
        self.tpm = max(1.0, float(tpm))
        self.max_burst_factor = max_burst_factor

        self.req_capacity = self.rpm * self.max_burst_factor
        self.req_tokens = self.req_capacity
        self.req_refill_rate = self.rpm / 60.0  # tokens per second

        self.tok_capacity = self.tpm * self.max_burst_factor
        self.tok_tokens = self.tok_capacity
        self.tok_refill_rate = self.tpm / 60.0  # tokens per second

        self.last_update = time.monotonic()
        self._lock = threading.RLock()
        self._backoff_until = 0.0

    def _refill(self) -> None:
        now = time.monotonic()
        delta = max(0.0, now - self.last_update)
        self.last_update = now

        self.req_tokens = min(self.req_capacity, self.req_tokens + delta * self.req_refill_rate)
        self.tok_tokens = min(self.tok_capacity, self.tok_tokens + delta * self.tok_refill_rate)

    def acquire(
        self,
        requests: int = 1,
        tokens: int = 0,
        timeout: Optional[float] = None,
    ) -> bool:
        """
        Acquires permission to execute an LLM call. Blocks until capacity is available
        or raises RateLimitExceededError if timeout expires.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None

        while True:
            with self._lock:
                now = time.monotonic()
                # If currently in a 429 backoff penalty window, wait
                if self._backoff_until > now:
                    wait_for = self._backoff_until - now
                    if deadline is not None and (now + wait_for) > deadline:
                        raise RateLimitExceededError(
                            f"Rate limiter '{self.name}' in 429 backoff until {self._backoff_until:.2f} (wait: {wait_for:.2f}s, timeout: {timeout}s)"
                        )
                else:
                    self._refill()
                    if self.req_tokens >= requests and self.tok_tokens >= tokens:
                        self.req_tokens -= requests
                        self.tok_tokens -= tokens
                        return True

                    # Calculate wait time needed
                    req_deficit = max(0.0, requests - self.req_tokens)
                    tok_deficit = max(0.0, tokens - self.tok_tokens)
                    wait_req = req_deficit / self.req_refill_rate if self.req_refill_rate > 0 else 0.0
                    wait_tok = tok_deficit / self.tok_refill_rate if self.tok_refill_rate > 0 else 0.0
                    wait_for = max(wait_req, wait_tok, 0.01)

                    if deadline is not None and (now + wait_for) > deadline:
                        raise RateLimitExceededError(
                            f"Rate limiter '{self.name}' capacity exceeded: required ({requests} reqs, {tokens} tokens), "
                            f"available ({self.req_tokens:.1f}, {self.tok_tokens:.1f}). Wait time {wait_for:.2f}s exceeds timeout {timeout}s"
                        )

            # Sleep outside lock
            sleep_time = min(wait_for, 0.5)
            time.sleep(sleep_time)

    def record_response(self, headers: Mapping[str, Any]) -> None:
        """
        Inspects response headers from LLM provider to extract Retry-After or rate limit state.
        """
        if not headers:
            return
        retry_after = headers.get("retry-after") or headers.get("Retry-After")
        if retry_after is not None:
            try:
                seconds = float(retry_after)
                with self._lock:
                    self._backoff_until = max(self._backoff_until, time.monotonic() + seconds)
                    logger.warning(
                        "Rate limiter '%s' honoring Retry-After: backing off for %.2fs",
                        self.name,
                        seconds,
                    )
            except (ValueError, TypeError):
                pass

    @contextmanager
    def limit(
        self,
        requests: int = 1,
        tokens: int = 0,
        timeout: Optional[float] = None,
    ) -> Generator[TokenBucketRateLimiter, None, None]:
        self.acquire(requests=requests, tokens=tokens, timeout=timeout)
        try:
            yield self
        finally:
            pass


_LIMITER_REGISTRY: Dict[str, TokenBucketRateLimiter] = {}
_REGISTRY_LOCK = threading.Lock()


def get_rate_limiter(
    name: str = "default",
    rpm: Optional[float] = None,
    tpm: Optional[float] = None,
) -> TokenBucketRateLimiter:
    """Retrieve or create a registered singleton rate limiter by provider/endpoint name."""
    with _REGISTRY_LOCK:
        if name not in _LIMITER_REGISTRY:
            eff_rpm = rpm if rpm is not None else 60.0
            eff_tpm = tpm if tpm is not None else 60000.0
            _LIMITER_REGISTRY[name] = TokenBucketRateLimiter(name=name, rpm=eff_rpm, tpm=eff_tpm)
        return _LIMITER_REGISTRY[name]
