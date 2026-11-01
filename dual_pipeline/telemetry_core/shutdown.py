"""telemetry_shutdown.py — Bounded shutdown helpers for OpenTelemetry."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def perform_bounded_shutdown(
    get_provider_fn: Callable[[], Any],
    reset_state_fn: Callable[[], None],
    timeout_millis: int = 2000,
) -> bool:
    """Flushes and shuts down the active OpenTelemetry TracerProvider within a bounded timeout."""
    provider = get_provider_fn()
    reset_state_fn()
    if provider is None:
        return True

    timeout_sec = max(0.05, timeout_millis / 1000.0)
    result = {"ok": True}

    def _do_shutdown() -> None:
        try:
            if hasattr(provider, "force_flush"):
                ok = provider.force_flush(timeout_millis=timeout_millis)
                if ok is False:
                    result["ok"] = False
            if hasattr(provider, "shutdown"):
                provider.shutdown()
        except Exception as exc:
            logger.debug("Telemetry shutdown encountered error: %s", exc)
            result["ok"] = False

    worker = threading.Thread(target=_do_shutdown, name="otel_shutdown", daemon=True)
    worker.start()
    worker.join(timeout=timeout_sec)
    if worker.is_alive():
        return False
    return result["ok"]
