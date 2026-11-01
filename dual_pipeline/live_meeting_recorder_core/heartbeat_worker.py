"""Heartbeat worker thread and lifecycle state computation for live meeting recorder."""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.heartbeat_worker")


def _get_capture_state(is_stopped: bool, is_running: bool, has_proc: bool, quarantined: bool) -> str:
    if is_stopped:
        return "STOPPED"
    if is_running and has_proc:
        return "CAPTURING"
    if is_running:
        return "STARTING"
    return "FAILED" if quarantined else "STOPPED"


def _get_asr_state(transcriber: Any, mock: bool, is_stopped: bool, asr_failed: bool = False) -> str:
    if asr_failed or (transcriber and getattr(transcriber, "_terminal_failure", False)):
        return "FAILED"
    if (transcriber and getattr(transcriber, "is_running", False)) or mock:
        return "READY"
    return "STOPPED" if is_stopped else "WARMING"


def _get_context_state(chunks_count: int, last_audible: float) -> str:
    if not chunks_count:
        return "INITIALIZING"
    return "STALE" if (time.monotonic() - last_audible) > 90.0 else "FRESH"


def _get_copilot_state(capture_state: str, asr_state: str, context_state: str) -> str:
    if asr_state == "FAILED":
        return "NOT_READY"
    if capture_state == "CAPTURING" and asr_state == "READY" and context_state in ("INITIALIZING", "FRESH"):
        return "READY"
    return "DEGRADED" if context_state == "STALE" else "NOT_READY"


def compute_recorder_lifecycle_states(recorder: Any) -> Tuple[str, str, str, str]:
    """Computes (capture_state, asr_state, context_state, copilot_state) for heartbeat."""
    is_stopped = getattr(recorder, "_is_stopped", False)
    is_running = getattr(recorder, "running", False)
    cm = getattr(recorder, "capture_manager", None)
    ff_pid = getattr(getattr(cm, "_ffmpeg_proc", None), "pid", None)
    sck_pid = getattr(getattr(cm, "_sck_proc", None), "pid", None)
    mock = getattr(recorder, "mock_mode", False)

    capture_state = _get_capture_state(is_stopped, is_running, bool(ff_pid or sck_pid or mock), getattr(recorder, "_quarantined", False))
    asr_failed = getattr(recorder, "_asr_failed", False)
    asr_state = _get_asr_state(getattr(recorder, "isolated_transcriber", None), mock, is_stopped, asr_failed=asr_failed)
    if hasattr(recorder, "transcribed_chunks"):
        chunks_count = len(getattr(recorder, "transcribed_chunks", set()))
    else:
        chunks_count = len(getattr(recorder, "processed_chunks", set()))
    context_state = _get_context_state(chunks_count, getattr(recorder, "last_audible_monotonic", 0.0))
    copilot_state = _get_copilot_state(capture_state, asr_state, context_state)

    return capture_state, asr_state, context_state, copilot_state


class HeartbeatWorker:
    """Manages periodic emission of session heartbeats in background thread."""

    def __init__(self, recorder: Any, interval_sec: float = 2.0):
        self.recorder = recorder
        self.interval_sec = interval_sec
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        session_id = getattr(self.recorder, "session_id", "session")

        def _loop() -> None:
            while not self._stop_event.is_set() and not getattr(self.recorder, "_is_stopped", False):
                try:
                    self.recorder._emit_heartbeat()
                except Exception as e:
                    logger.debug("Heartbeat worker tick failed: %s", e)
                self._stop_event.wait(timeout=self.interval_sec)

        self._thread = threading.Thread(target=_loop, name=f"heartbeat-{session_id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
