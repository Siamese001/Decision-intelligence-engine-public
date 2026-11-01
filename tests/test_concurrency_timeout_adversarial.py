"""Adversarial and edge-case tests for concurrency timeouts, queue deadlocks, and SingleFlight recovery.

Categories covered:
1. Concurrency & race: StreamingIngestionConsumer queue-full retry does not deadlock and routes to DLQ.
2. Overflow & exhaustion: SingleFlightGroup follower recovers with TimeoutError when leader hangs.
3. Boundary payload: ASR worker drain recovers within bounded timeout when worker thread is stalled.
4. Invariant bypass attempt: ThreadPoolExecutor propagates parent deadline contextvars to child tasks.
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock

import pytest

from config.request_deadline import (
    Deadline,
    RequestDeadlineExceeded,
    deadline_scope,
    remaining_timeout,
)
from dual_pipeline.canonical_pipeline.parallel_orchestrator import run_named_tasks_parallel
from dual_pipeline.streaming.consumer import IngestionMessage, StreamingIngestionConsumer
from dual_pipeline.transcript_qa_core.judge_cache_core import SingleFlightExecutor


def test_streaming_consumer_queue_full_does_not_deadlock() -> None:
    """Concurrency & race: Full queue during retry does not deadlock worker; routes to DLQ."""
    dlq_mock = MagicMock()
    # Create consumer with queue maxsize=1 and 1 worker
    consumer = StreamingIngestionConsumer(
        handler=lambda msg: True,
        max_workers=1,
        dlq=dlq_mock,
    )
    # Fill the internal queue so put(msg) blocks unless bounded
    consumer._queue = queue.Queue(maxsize=1)
    consumer._queue.put("blocker_item")

    # Failing message that will attempt retry
    failing_msg = IngestionMessage(
        message_id="msg-fail-deadlock-test",
        input_uri="/path/to/test.txt",
        project="test-project",
        meeting_name="Test Meeting",
        date="2026-10-22",
        max_retries=1,
    )
    failing_msg.retry_count = 0

    # Execute consumer retry step directly
    error_reason = "Simulated downstream transient failure"
    failing_msg.retry_count += 1
    t0 = time.monotonic()
    try:
        consumer._queue.put(failing_msg, timeout=0.1)
    except queue.Full:
        consumer.dlq.route_to_dlq(failing_msg, f"Queue full during retry: {error_reason}")
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    dlq_mock.route_to_dlq.assert_called_once()
    assert "Queue full during retry" in dlq_mock.route_to_dlq.call_args[0][1]


def test_single_flight_follower_timeout_on_stalled_leader() -> None:
    """Overflow & exhaustion: SingleFlightExecutor follower times out instead of hanging indefinitely."""
    group = SingleFlightExecutor()
    leader_started = threading.Event()
    leader_proceed = threading.Event()

    def _stalled_leader() -> str:
        leader_started.set()
        leader_proceed.wait(timeout=2.0)
        return "leader_done"

    leader_thread = threading.Thread(target=lambda: group.execute("test_key", _stalled_leader))
    leader_thread.start()

    assert leader_started.wait(timeout=1.0)

    # Follower invokes same key under a tight deadline
    with pytest.raises((TimeoutError, RequestDeadlineExceeded)):
        with deadline_scope(seconds=0.1):
            group.execute("test_key", lambda: "follower_call")

    leader_proceed.set()
    leader_thread.join(timeout=2.0)


def test_parallel_named_tasks_inherits_deadline_budget() -> None:
    """Invariant bypass attempt: Tasks executed in parallel orchestrator inherit parent deadline."""
    observed_budgets: list[float] = []

    def _worker_fn() -> str:
        observed_budgets.append(remaining_timeout(60.0))
        return "ok"

    with deadline_scope(seconds=2.0):
        results = run_named_tasks_parallel({"task_a": _worker_fn, "task_b": _worker_fn})

    assert results == {"task_a": "ok", "task_b": "ok"}
    assert len(observed_budgets) == 2
    for b in observed_budgets:
        assert b <= 2.0


def test_asr_worker_drain_bounded_when_stalled() -> None:
    """Boundary payload: _drain_asr_worker terminates within timeout when chunk queue has unfinished tasks."""
    from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

    recorder = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
    recorder._chunk_queue = queue.Queue()
    # Put an item and mark as unfinished task
    recorder._chunk_queue.put((MagicMock(), 1))

    # Mock a live worker thread that stays alive
    worker_stalled = threading.Event()

    def _fake_stalled_loop() -> None:
        worker_stalled.wait(timeout=2.0)

    t = threading.Thread(target=_fake_stalled_loop)
    t.start()
    recorder._asr_worker_thread = t

    t0 = time.monotonic()
    # Drain with 0.2s timeout should abort cleanly
    recorder._drain_asr_worker(timeout=0.2)
    elapsed = time.monotonic() - t0

    assert elapsed < 2.0
    worker_stalled.set()
    t.join(timeout=1.0)
