"""End-to-End test for concurrency timeout propagation and deadlock elimination.

Verifies:
1. End-to-end parallel L2 fork timeout propagation and prompt deadline inheritance.
2. Live meeting recorder ASR queue drain clean shutdown without deadlock.
"""

from __future__ import annotations

import queue
import threading
import time
from unittest.mock import MagicMock

import pytest

from config.request_deadline import (
    RequestDeadlineExceeded,
    deadline_scope,
    remaining_timeout,
)
from dual_pipeline.canonical_pipeline.child_dispatch import run_parallel_l2_fork


@pytest.mark.timeout(30)
def test_parallel_l2_fork_timeout_propagation_e2e() -> None:
    """E2E flow: Parallel L2 fork inherits request deadline budget across worker threads."""
    observed_budgets: list[float] = []

    mock_pipeline = MagicMock()

    def _slow_recap(*args: object, **kwargs: object) -> str:
        observed_budgets.append(remaining_timeout(60.0))
        return "# Executive Recap\nValid output"

    def _slow_graph(*args: object, **kwargs: object) -> str:
        observed_budgets.append(remaining_timeout(60.0))
        return "{}"

    mock_pipeline.l2_recap.execute = _slow_recap
    mock_pipeline.l2_graph.execute = _slow_graph

    recorder = MagicMock()
    recorder.span = MagicMock(return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()))

    meta: dict[str, object] = {
        "meeting_name": "Fork E2E Test",
        "date": "10/22/2026",
        "project": "core",
        "raw_transcript": "Speaker 1: Hello team.\nSpeaker 2: Hi there.",
    }
    pa_context: dict[str, object] = {"metadata": {}}
    artifacts: dict[str, object] = {}

    with deadline_scope(seconds=3.0):
        recap, graph = run_parallel_l2_fork(
            pipeline=mock_pipeline,
            input_s2_s3="Speaker 1: Hello team.\nSpeaker 2: Hi there.",
            pa_context=pa_context,
            meta=meta,
            rerun_s2=True,
            rerun_s3=True,
            recorder=recorder,
            notify_step=lambda s, msg: None,
            attempt=1,
            current_recap="",
            current_graph="",
            artifacts=artifacts,
        )

    assert "Executive Recap" in recap
    assert len(observed_budgets) == 2
    for b in observed_budgets:
        assert b <= 3.0


@pytest.mark.timeout(30)
def test_live_meeting_recorder_clean_drain_e2e() -> None:
    """E2E flow: ASR worker queue drains and joins within bounded time upon stop()."""
    from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

    recorder = LiveMeetingRecorder.__new__(LiveMeetingRecorder)
    recorder._chunk_queue = queue.Queue()
    recorder.processed_chunks = set()
    recorder.transcribed_chunks = set()
    recorder._consecutive_asr_failures = 0
    recorder._asr_failed = False
    recorder.process_chunk = MagicMock()

    stop_event = threading.Event()

    def _worker_loop() -> None:
        while not stop_event.is_set():
            try:
                item = recorder._chunk_queue.get(timeout=0.05)
                if item is None:
                    recorder._chunk_queue.task_done()
                    break
                recorder._chunk_queue.task_done()
            except queue.Empty:
                continue

    worker_t = threading.Thread(target=_worker_loop)
    worker_t.start()
    recorder._asr_worker_thread = worker_t

    # Add 2 items
    recorder._chunk_queue.put((MagicMock(), 1))
    recorder._chunk_queue.put((MagicMock(), 2))

    t0 = time.monotonic()
    recorder._drain_asr_worker(timeout=2.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 3.0
    assert not worker_t.is_alive()
