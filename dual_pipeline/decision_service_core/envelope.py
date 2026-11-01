"""Envelope creation and validation for decision service responses."""

from __future__ import annotations

import time
from typing import Any, Mapping

from ..contracts import create_artifact
from ..core import digest, stable_id
from ..decision_models import (
    DecisionTerminalState,
    ResponseTerminalState,
    validate_response_envelope,
)
from .handlers import _citations


def build_response_envelope(
    *,
    request_id: str,
    run_id: str | None,
    destination: str,
    handler: str,
    terminal_state: ResponseTerminalState | DecisionTerminalState | str,
    result: Any,
    retrieval_call_count: int,
    provider_call_count: int,
    started: float,
    error: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state = str(terminal_state)
    if hasattr(terminal_state, "value"):
        state = str(terminal_state.value)
    envelope_fields: dict[str, Any] = {
        "request_id": request_id,
        "run_id": run_id,
        "destination": destination,
        "handler": handler,
        "terminal_state": state,
        "result": result,
        "citations": _citations(result),
        "retrieval_call_count": retrieval_call_count,
        "provider_call_count": provider_call_count,
        "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "error": dict(error) if error is not None else None,
        "response_digest": "",
    }
    envelope_fields["response_digest"] = digest(
        {
            key: value
            for key, value in envelope_fields.items()
            if key != "response_digest"
        }
    )
    envelope = create_artifact(
        "decision-response-envelope",
        stable_id(
            "decision-response-envelope",
            request_id,
            run_id,
            state,
            length=26,
        ),
        **envelope_fields,
    )
    return validate_response_envelope(envelope)


__all__ = ["build_response_envelope"]
