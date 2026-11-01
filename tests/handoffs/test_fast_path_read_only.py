"""Test suite for Law 3 (Fast Path Read-Only Context Fusion & Expiration)."""
from __future__ import annotations

import time

from dual_pipeline.decision.fast_path_context import FastPathContextBuffer


def test_fast_path_read_only_badge_and_reconciliation():
    """Verifies that live turns carry untrusted badges and are reconciled when facts settle."""
    buf = FastPathContextBuffer(ttl_seconds=10.0)

    # Ingest provisional turn
    turn1 = buf.ingest_live_turn(
        event_id="turn_live_1",
        meeting_id="meet_99",
        speaker="Speaker 1",
        text="We might be launching in November.",
        start_ts=1.0,
        end_ts=3.0,
        confidence=0.45,
    )
    assert "[UNTRUSTED_FAST_PATH]" in turn1.badge
    assert turn1.is_ephemeral is True

    # Active context contains the turn
    active = buf.get_active_context()
    assert len(active) == 1
    assert active[0].event_id == "turn_live_1"

    # Reconcile turn when Slow Path commits evidence
    buf.reconcile_settled_fact("turn_live_1")
    active_after = buf.get_active_context()
    assert len(active_after) == 0


def test_fast_path_ttl_expiration():
    """Verifies that expired live turns are automatically purged."""
    buf = FastPathContextBuffer(ttl_seconds=0.1)  # 100ms TTL
    buf.ingest_live_turn(
        event_id="turn_short_lived",
        meeting_id="meet_99",
        speaker="Speaker",
        text="Temporary remark.",
        start_ts=0.0,
        end_ts=1.0,
    )

    # Immediately active
    assert len(buf.get_active_context()) == 1

    # Sleep past TTL
    time.sleep(0.15)
    assert len(buf.get_active_context()) == 0
