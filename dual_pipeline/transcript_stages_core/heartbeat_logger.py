"""
Non-blocking background heartbeat logger for LLM inference calls.
Emits active in-band progress ticks at periodic intervals to eliminate silent freezes.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, List, Optional, TextIO


class BackgroundHeartbeatLogger:
    """
    Non-blocking background heartbeat logger.
    Emits elapsed ticks every interval seconds to stdout/stream during long-running operations.
    """

    def __init__(
        self,
        operation_name: str = "LLM Inference",
        interval: float = 10.0,
        stream: Optional[TextIO] = None,
    ) -> None:
        self.operation_name = operation_name
        self.interval = max(0.01, float(interval))
        self.stream = stream or sys.stdout
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._start_time: float = 0.0
        self.emitted_ticks: List[float] = []

    def _format_elapsed(self, seconds: float) -> str:
        secs = int(max(0, seconds))
        mins, s = divmod(secs, 60)
        hrs, m = divmod(mins, 60)
        if hrs > 0:
            return f"{hrs:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def emit_tick(self, elapsed: float) -> str:
        """Format, write, and flush a single discrete heartbeat progress tick."""
        elapsed_str = self._format_elapsed(elapsed)
        line = f"[{self.operation_name}] Active inference in progress... (elapsed: {elapsed_str})\n"
        self.emitted_ticks.append(elapsed)
        try:
            self.stream.write(line)
            if hasattr(self.stream, "flush"):
                self.stream.flush()
        except Exception:
            pass
        return line

    def _run(self) -> None:
        while not self._stop_event.wait(self.interval):
            elapsed = time.monotonic() - self._start_time
            self.emit_tick(elapsed)

    def start(self) -> None:
        """Start the background heartbeat thread."""
        self._start_time = time.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"heartbeat-{self.operation_name}",
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the background heartbeat thread and join cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def __enter__(self) -> BackgroundHeartbeatLogger:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
