"""Pipeline D: Read-Only Fast Path Context Fusion Bridge.

Consumes ephemeral live STT streaming turns from Pipeline A, applying Law 3
invariants ([UNTRUSTED_FAST_PATH] badge, strict immutability, TTL expiry, and
reconciliation against settled Slow Path graph facts).
"""
from __future__ import annotations

import threading
import time
from typing import List

from dual_pipeline.l0.contracts import FastPathContext
from dual_pipeline.parameters import get_decision_service_parameters

DEFAULT_FAST_PATH_TTL_SEC = float(
    get_decision_service_parameters()["default_fast_path_ttl_sec"]
)  # Ephemeral turns expire after 30 seconds


class FastPathContextBuffer:
    """
    Thread-safe, read-only memory buffer managing ephemeral streaming context for Copilot UI.
    Guarantees that live turns can NEVER be written to the canonical graph directly.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_FAST_PATH_TTL_SEC) -> None:
        self.ttl_seconds: float = ttl_seconds
        self._turns: List[FastPathContext] = []
        self._reconciled_event_ids: set[str] = set()
        self._lock = threading.RLock()

    def ingest_live_turn(
        self,
        event_id: str,
        meeting_id: str,
        speaker: str,
        text: str,
        start_ts: float,
        end_ts: float,
        confidence: float = 0.0,
    ) -> FastPathContext:
        """
        Ingests an incoming live STT turn and tags it with Law 3 untrusted badge.
        """
        with self._lock:
            ctx = FastPathContext(
                event_id=event_id,
                meeting_id=meeting_id,
                speaker=speaker,
                text=text,
                start_ts=start_ts,
                end_ts=end_ts,
                badge="[UNTRUSTED_FAST_PATH]",
                is_ephemeral=True,
                confidence=confidence,
                created_at=time.time(),
            )
            self._turns.append(ctx)
            self._purge_expired_locked()
            return ctx

    def get_active_context(self) -> List[FastPathContext]:
        """
        Returns all active, non-expired, unreconciled live turns for Copilot UI display.
        """
        with self._lock:
            self._purge_expired_locked()
            return [
                t for t in self._turns
                if t.event_id not in self._reconciled_event_ids
            ]

    def reconcile_settled_fact(self, event_id: str) -> None:
        """
        Retires an ephemeral fast-path turn once its underlying evidence is committed
        and verified by the Slow Path knowledge graph.
        """
        with self._lock:
            self._reconciled_event_ids.add(event_id)

    def _purge_expired_locked(self) -> None:
        """Purges turns older than TTL."""
        now = time.time()
        self._turns = [
            t for t in self._turns
            if (now - t.created_at) < self.ttl_seconds
        ]

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()
            self._reconciled_event_ids.clear()
