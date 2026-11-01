"""
dual_pipeline/notifications/webhook_dispatcher.py

HMAC-SHA256 signed webhook dispatcher for pipeline lifecycle events.
Dispatches signed HTTP POST notifications on directive completion, failure,
or HITL review requirements with exponential backoff and retry policies.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class WebhookPayload:
    """Standardized payload envelope for pipeline lifecycle notifications."""

    event_id: str
    event_type: str  # e.g., 'directive.completed', 'directive.failed', 'hitl.required'
    directive_id: str
    run_id: str
    timestamp: float = field(default_factory=time.time)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_canonical_json(self) -> str:
        """Serializes payload to RFC 8785 canonical JSON (deterministic key sorting)."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


class WebhookSigner:
    """Computes and validates HMAC-SHA256 signatures for webhook payloads."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("Webhook secret cannot be empty")
        self._secret = secret.encode("utf-8")

    def sign(self, payload_bytes: bytes, timestamp: int) -> str:
        """
        Signs the payload bytes prefixed with timestamp to prevent replay attacks:
        signature = HMAC_SHA256(secret, f"t={timestamp}.{payload}")
        """
        to_sign = f"t={timestamp}.".encode("utf-8") + payload_bytes
        return hmac.new(self._secret, to_sign, hashlib.sha256).hexdigest()

    def verify(self, payload_bytes: bytes, signature: str, timestamp: int, max_age_seconds: int = 300) -> bool:
        """Verifies signature authenticity and checks timestamp staleness."""
        now = int(time.time())
        if abs(now - timestamp) > max_age_seconds:
            return False
        expected_sig = self.sign(payload_bytes, timestamp)
        return hmac.compare_digest(expected_sig, signature)


@dataclass
class WebhookDeliveryReceipt:
    """Immutable receipt recording a webhook dispatch attempt."""

    delivery_id: str
    event_id: str
    target_url: str
    status_code: Optional[int]
    success: bool
    attempt_count: int
    duration_ms: float
    error_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


class WebhookDispatcher:
    """Dispatches HMAC-signed webhooks with retry and exponential backoff."""

    def __init__(
        self,
        endpoint_url: str,
        secret: str,
        max_retries: int = 3,
        initial_backoff_sec: float = 0.5,
        backoff_multiplier: float = 2.0,
        timeout_sec: float = 10.0,
        http_poster: Optional[Callable[[str, bytes, Dict[str, str], float], tuple[int, bytes]]] = None,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.signer = WebhookSigner(secret)
        self.max_retries = max_retries
        self.initial_backoff_sec = initial_backoff_sec
        self.backoff_multiplier = backoff_multiplier
        self.timeout_sec = timeout_sec
        self._http_poster = http_poster or self._default_http_post

    @staticmethod
    def _default_http_post(
        url: str,
        data: bytes,
        headers: Dict[str, str],
        timeout: float,
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, response.read()

    def dispatch(self, payload: WebhookPayload) -> WebhookDeliveryReceipt:
        """Dispatches payload with retries and exponential backoff."""
        payload_json = payload.to_canonical_json()
        payload_bytes = payload_json.encode("utf-8")
        timestamp = int(time.time())
        signature = self.signer.sign(payload_bytes, timestamp)

        headers = {
            "Content-Type": "application/json",
            "X-NEE-Event-Type": payload.event_type,
            "X-NEE-Event-Id": payload.event_id,
            "X-NEE-Timestamp": str(timestamp),
            "X-NEE-Signature": f"v1={signature}",
            "User-Agent": "NEE-Webhook-Dispatcher/1.0",
        }

        delivery_id = f"dlv_{payload.event_id}_{timestamp}"
        backoff = self.initial_backoff_sec
        last_error = None
        status_code = None

        t0 = time.perf_counter()
        for attempt in range(1, self.max_retries + 1):
            try:
                status_code, _ = self._http_poster(self.endpoint_url, payload_bytes, headers, self.timeout_sec)
                if 200 <= status_code < 300:
                    duration_ms = (time.perf_counter() - t0) * 1000.0
                    return WebhookDeliveryReceipt(
                        delivery_id=delivery_id,
                        event_id=payload.event_id,
                        target_url=self.endpoint_url,
                        status_code=status_code,
                        success=True,
                        attempt_count=attempt,
                        duration_ms=round(duration_ms, 2),
                    )
                last_error = f"HTTP {status_code}"
            except Exception as e:
                last_error = str(e)

            if attempt < self.max_retries:
                time.sleep(backoff)
                backoff *= self.backoff_multiplier

        duration_ms = (time.perf_counter() - t0) * 1000.0
        return WebhookDeliveryReceipt(
            delivery_id=delivery_id,
            event_id=payload.event_id,
            target_url=self.endpoint_url,
            status_code=status_code,
            success=False,
            attempt_count=self.max_retries,
            duration_ms=round(duration_ms, 2),
            error_message=last_error,
        )
