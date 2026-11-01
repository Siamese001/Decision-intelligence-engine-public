"""
tests/adversarial/test_copilot_concurrency_adversarial.py

Adversarial and edge-case testing for Live Copilot concurrency, request snapshots,
and deliberation queue coalescing under AGENTS.md Invariant 9.
"""
from __future__ import annotations

import concurrent.futures
import time

from dual_pipeline.live_meeting_recorder_core.copilot_lifecycle import (
    CopilotDeliberationQueue,
    CopilotLifecycleOrchestrator,
    CopilotLifecycleState,
    CopilotRequestSnapshot,
)


def test_concurrency_lock_and_snapshot():
    """Verify thread-safe snapshot validation and concurrent orchestrator request tracking."""
    orch = CopilotLifecycleOrchestrator(session_id="test_sess_concurrency")
    orch._state = CopilotLifecycleState.READY

    num_threads = 12
    req_ids = [f"req_concurrent_{i}" for i in range(num_threads)]

    # Concurrent begin_request
    def run_request_lifecycle(req_id: str):
        orch.begin_request(req_id)
        time.sleep(0.01)
        orch.end_request(req_id)

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(run_request_lifecycle, rid) for rid in req_ids]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert orch.state == CopilotLifecycleState.READY
    assert len(orch._active_requests) == 0

    # Snapshot deadline and generation invalidation tests
    now = time.time()
    valid_snap = CopilotRequestSnapshot(
        session_id="s1",
        generation_id="gen_v1",
        created_at=now,
        deadline_seconds=5.0,
    )
    assert valid_snap.is_valid(current_generation_id="gen_v1", now=now + 1.0)

    # Expiration past deadline
    expired_snap = CopilotRequestSnapshot(
        session_id="s1",
        generation_id="gen_v1",
        created_at=now - 10.0,
        deadline_seconds=5.0,
    )
    assert not expired_snap.is_valid(current_generation_id="gen_v1", now=now)
    assert expired_snap.state == "EXPIRED"

    # Generation mismatch invalidation
    mismatched_snap = CopilotRequestSnapshot(
        session_id="s1",
        generation_id="gen_v1",
        created_at=now,
        deadline_seconds=5.0,
    )
    assert not mismatched_snap.is_valid(current_generation_id="gen_v2", now=now + 1.0)
    assert mismatched_snap.state == "SUPPRESSED_STALE"


def test_coalescing_queue():
    """Verify newest-request coalescing behavior under rapid sequential and concurrent arrival."""
    # When disabled, submissions are rejected
    disabled_queue = CopilotDeliberationQueue(enabled=False)
    snap0 = CopilotRequestSnapshot(session_id="s0")
    assert not disabled_queue.submit_deliberation(snap0, None)

    queue = CopilotDeliberationQueue(enabled=True)
    snap1 = CopilotRequestSnapshot(session_id="s1", text="turn 1")
    snap2 = CopilotRequestSnapshot(session_id="s1", text="turn 2")

    assert queue.submit_deliberation(snap1, None)
    assert snap1.state == "ACTIVE"

    # Newer submission coalesces and suppresses older pending request
    assert queue.submit_deliberation(snap2, None)
    assert snap1.state == "SUPPRESSED_STALE"
    assert snap1.suppression_reason == "coalesced_by_newer_request"
    assert snap2.state == "ACTIVE"

    # Pop returns the newest pending request
    popped = queue.pop_pending()
    assert popped is not None
    active_snap, _ = popped
    assert active_snap.text == "turn 2"
    assert queue.pop_pending() is None

    # Clear queue suppresses any pending item
    snap3 = CopilotRequestSnapshot(session_id="s1", text="turn 3")
    queue.submit_deliberation(snap3, None)
    queue.clear()
    assert snap3.state == "SUPPRESSED_STALE"
    assert snap3.suppression_reason == "queue_cleared"
    assert queue.pop_pending() is None
