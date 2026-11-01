"""Live Meeting Recorder Core: Subprocess and Energy Audio Watchdogs.
Supervises child audio capture processes and monitors live audio stream RMS energy.
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_watchdog")


@dataclass
class FailoverEvent:
    epoch: int
    timestamp: float
    trigger_reason: str
    previous_strategy: str
    fallback_strategy: str
    exit_code: Optional[int]


class AudioSubprocessWatchdog:
    """
    Supervises child audio capture processes with 250ms polling interval.
    Triggers guaranteed failover within 2.0s upon process exit, crash, or dead pipe.
    """

    def __init__(
        self,
        on_failover_callback: Callable[[str, Optional[int]], None],
        poll_interval_sec: float = 0.25,
        max_stall_sec: float = 20.0,
        zero_byte_stall_threshold_sec: Optional[float] = None,
        segment_seconds: Optional[float] = None,
        startup_grace_period_sec: float = 8.0,
    ):
        self.on_failover_callback = on_failover_callback
        self.poll_interval_sec = poll_interval_sec
        self.max_stall_sec = max_stall_sec
        if segment_seconds is None:
            self.segment_seconds = 15.0 if zero_byte_stall_threshold_sec is None else max(0.0, float(zero_byte_stall_threshold_sec))
        else:
            self.segment_seconds = float(segment_seconds)

        if zero_byte_stall_threshold_sec is None:
            self.zero_byte_stall_threshold_sec = max(10.0, float(self.segment_seconds) + 5.0)
        else:
            self.zero_byte_stall_threshold_sec = float(zero_byte_stall_threshold_sec)

        if self.segment_seconds > 1 and self.zero_byte_stall_threshold_sec < self.segment_seconds:
            raise ValueError(
                f"Watchdog zero_byte_stall_threshold_sec ({self.zero_byte_stall_threshold_sec}s) "
                f"must be >= segment_seconds ({self.segment_seconds}s) to prevent false failover storms."
            )

        if zero_byte_stall_threshold_sec is not None and float(self.zero_byte_stall_threshold_sec) < float(startup_grace_period_sec):
            self.startup_grace_period_sec = float(self.zero_byte_stall_threshold_sec) * 0.5
        else:
            self.startup_grace_period_sec = float(startup_grace_period_sec)
        self._registration_time = time.time()
        self._supervised_procs: List[subprocess.Popen] = []
        self._chunks_dir: Optional[Path] = None
        self._zero_byte_chunk_start: Optional[float] = None
        self._zero_byte_chunk_path: Optional[Path] = None
        self._zero_byte_last_mtime: Optional[float] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last_chunk_touch = time.time()
        self.failover_history: List[FailoverEvent] = []
        self.failover_count = 0

    def register_processes(self, procs: List[subprocess.Popen], chunks_dir: Optional[Path] = None) -> None:
        """Updates the list of active child processes to supervise."""
        with self._lock:
            self._supervised_procs = [p for p in procs if p is not None]
            self._chunks_dir = chunks_dir
            self._last_chunk_touch = time.time()
            self._registration_time = time.time()
            self._zero_byte_chunk_start = None
            self._zero_byte_chunk_path = None
            self._zero_byte_last_mtime = None

    def notify_chunk_written(self) -> None:
        """Records that a valid chunk was written, resetting the stall timer."""
        with self._lock:
            self._last_chunk_touch = time.time()
            self._zero_byte_chunk_start = None
            self._zero_byte_chunk_path = None
            self._zero_byte_last_mtime = None

    def start(self) -> None:
        """Starts the background watchdog monitoring thread."""
        self._running = True
        self._thread = threading.Thread(target=self._supervision_loop, daemon=True, name="AudioSubprocessWatchdog")
        self._thread.start()

    def stop(self) -> None:
        """Halts the watchdog monitoring loop."""
        self._running = False
        if self._thread and self._thread.is_alive() and threading.current_thread() != self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None

    def record_failover(
        self,
        trigger_reason: str,
        previous_strategy: str,
        fallback_strategy: str,
        exit_code: Optional[int],
    ) -> FailoverEvent:
        """Appends a failover event to the history ledger."""
        event = FailoverEvent(
            epoch=self.failover_count,
            timestamp=time.time(),
            trigger_reason=trigger_reason,
            previous_strategy=previous_strategy,
            fallback_strategy=fallback_strategy,
            exit_code=exit_code,
        )
        self.failover_history.append(event)
        self.failover_count += 1
        return event

    def _check_zero_byte_starvation(self) -> Tuple[Optional[str], Optional[int]]:
        """Checks for sustained zero-byte chunk starvation."""
        if not self._chunks_dir or not self._chunks_dir.exists():
            return None, None
        now = time.time()
        # Suppress starvation trigger during startup grace period
        if (now - self._registration_time) < self.startup_grace_period_sec:
            return None, None
        chunk_files = sorted(self._chunks_dir.glob("chunk_*.wav"))
        if not chunk_files:
            return None, None
        latest = chunk_files[-1]
        try:
            if not latest.exists() or latest.stat().st_size != 0:
                self._zero_byte_chunk_path = None
                self._zero_byte_chunk_start = None
                self._zero_byte_last_mtime = None
            st = latest.stat()
            mtime = st.st_mtime
            if self._zero_byte_chunk_path != latest:
                self._zero_byte_chunk_path = latest
                self._zero_byte_chunk_start = now
                self._zero_byte_last_mtime = mtime
                return None, None

            # If mtime has advanced significantly while size is still 0, FFmpeg is active
            if self._zero_byte_last_mtime is not None and (mtime - self._zero_byte_last_mtime) > 0.05:
                self._zero_byte_chunk_start = now
                self._zero_byte_last_mtime = mtime
                return None, None

            elapsed = now - (self._zero_byte_chunk_start or now)
            if elapsed >= self.zero_byte_stall_threshold_sec:
                return (
                    f"Zero-byte audio chunk starvation detected ({latest.name} remained 0 bytes > {self.zero_byte_stall_threshold_sec}s)",
                    -998,
                )
        except OSError:
            pass
        return None, None

    def _has_recent_chunk_growth(self, now: float) -> bool:
        """Returns True if any WAV chunk in chunks_dir was modified recently."""
        if not (self._chunks_dir and self._chunks_dir.exists()):
            return False
        max_age = max(3.0, self.poll_interval_sec * 4)
        try:
            for p in self._chunks_dir.glob("*.wav"):
                if p.exists() and (now - p.stat().st_mtime) < max_age:
                    self._last_chunk_touch = now
                    return True
        except Exception:
            pass
        return False

    def _detect_failure(self) -> Tuple[Optional[str], Optional[int]]:
        """Inspects supervised processes, zero-byte chunks, and stall duration under lock."""
        with self._lock:
            for proc in self._supervised_procs:
                res = proc.poll()
                if res is not None:
                    name = proc.args[0] if isinstance(proc.args, list) and proc.args else "subprocess"
                    return f"Process '{name}' exited unexpectedly (exit code {res})", res

            reason, code = self._check_zero_byte_starvation()
            if reason:
                return reason, code

            now = time.time()
            if (now - self._last_chunk_touch) > self.max_stall_sec:
                if not self._has_recent_chunk_growth(now):
                    return f"Audio chunk generation stalled (> {self.max_stall_sec}s)", -999
        return None, None

    def _supervision_loop(self) -> None:
        while self._running:
            time.sleep(self.poll_interval_sec)
            reason, exit_code = self._detect_failure()
            if reason and self._running:
                logger.error("[WATCHDOG_TRIGGER] %s. Initiating failover.", reason)
                try:
                    self.on_failover_callback(reason, exit_code)
                except Exception as e:
                    logger.critical("[WATCHDOG_ERROR] Failover callback failed: %s", e)
                break


class AudioEnergyWatchdog:
    """Monitors live audio stream RMS energy and alerts on prolonged flatlines."""

    def __init__(
        self,
        flatline_threshold_rms: float = 5.0,
        alert_after_chunks: int = 2,
        segment_seconds: int = 15,
        on_alert_callback: Optional[Callable[[str, int, float], None]] = None,
    ) -> None:
        self.flatline_threshold_rms = flatline_threshold_rms
        self.alert_after_chunks = alert_after_chunks
        self.segment_seconds = segment_seconds
        self.on_alert_callback = on_alert_callback

        self.consecutive_silent_chunks: int = 0
        self.total_chunks_monitored: int = 0
        self.total_silent_chunks: int = 0
        self.max_silent_streak: int = 0
        self.alert_count: int = 0
        self.last_alert_chunk_idx: Optional[int] = None

    def _trigger_flatline_alert(self, chunk_idx: int, rms: float, time_str: str) -> str:
        duration_sec = self.consecutive_silent_chunks * self.segment_seconds
        alert_msg = (
            f"⚠️ [CRITICAL AUDIO ALERT at {time_str or f'chunk_{chunk_idx}'}]: "
            f"Flatline silence detected across {self.consecutive_silent_chunks} consecutive chunks "
            f"({duration_sec}s, RMS={rms:.2f}). System audio tap may not be receiving audio! "
            f"Verify headphone routing and macOS Screen Recording permissions."
        )
        self.alert_count += 1
        self.last_alert_chunk_idx = chunk_idx
        logger.warning(alert_msg)
        if self.on_alert_callback:
            try:
                self.on_alert_callback(alert_msg, chunk_idx, rms)
            except Exception as e:
                logger.debug("Audio watchdog callback error: %s", e)
        return alert_msg

    def record_chunk(self, chunk_idx: int, rms: float, time_str: str = "") -> Optional[str]:
        """Evaluates chunk RMS and emits alert if silence persists beyond threshold."""
        self.total_chunks_monitored += 1
        if rms < self.flatline_threshold_rms:
            self.consecutive_silent_chunks += 1
            self.total_silent_chunks += 1
            if self.consecutive_silent_chunks > self.max_silent_streak:
                self.max_silent_streak = self.consecutive_silent_chunks
            if self.consecutive_silent_chunks >= self.alert_after_chunks:
                return self._trigger_flatline_alert(chunk_idx, rms, time_str)
            return None

        if self.consecutive_silent_chunks >= self.alert_after_chunks:
            logger.info(
                "Audio energy restored at chunk %s (RMS=%.2f) after %s silent chunks.",
                chunk_idx, rms, self.consecutive_silent_chunks,
            )
        self.consecutive_silent_chunks = 0
        return None

    def get_stats(self) -> Dict[str, Any]:
        """Returns diagnostic summary of audio energy monitoring."""
        return {
            "total_chunks_monitored": self.total_chunks_monitored,
            "total_silent_chunks": self.total_silent_chunks,
            "consecutive_silent_chunks": self.consecutive_silent_chunks,
            "max_silent_streak": self.max_silent_streak,
            "alert_count": self.alert_count,
            "last_alert_chunk_idx": self.last_alert_chunk_idx,
            "flatline_threshold_rms": self.flatline_threshold_rms,
        }
