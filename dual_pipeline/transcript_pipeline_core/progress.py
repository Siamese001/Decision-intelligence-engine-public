"""
Environment-aware pipeline progress reporting utility.

Provides dual-mode progress tracking:
- TTY Mode: In-place dynamic progress bar with un-truncated stage descriptions.
- Non-TTY Mode: Clean, line-delimited discrete logging with timestamps, percentage,
  and zero carriage return (\\r) horizontal smearing.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Any, Callable, Optional, TextIO

from config.credentials import CredentialManager
from config.transport import set_heartbeat_hook

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]


class PipelineProgressReporter:
    """
    Robust multi-mode progress tracker for long-running pipeline workflows.

    Decouples interactive terminal rendering from non-interactive log capture
    (background tasks, IDE execution logs, CI pipelines, and piped redirection)
    supporting in-band markdown-fenced text blocks.
    """

    def __init__(
        self,
        total: int,
        desc: str = "Pipeline Progress",
        unit: str = "stage",
        file: Optional[TextIO] = None,
        force_mode: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        self.total = max(1, total)
        self.desc = desc
        self.unit = unit
        self.file = file or sys.stderr
        self.progress_callback = progress_callback
        self.start_time: float = time.monotonic()
        self.current_step: int = 0
        self._closed: bool = False

        mode_env = CredentialManager.get_credential("NEE_PROGRESS_MODE", "").strip().lower()
        active_mode = force_mode.lower() if force_mode else mode_env

        if active_mode in ("tty", "interactive"):
            self.is_tty = True
            self.mode = "tty"
        elif active_mode in ("fenced", "markdown"):
            self.is_tty = False
            self.mode = "fenced"
        elif active_mode in ("stream", "plain", "log", "ci"):
            self.is_tty = False
            self.mode = "plain"
        else:
            self.is_tty = getattr(self.file, "isatty", lambda: False)()
            self.mode = "tty" if self.is_tty else "fenced"

        self.pbar: Any = None
        if self.mode == "tty" and self.is_tty and tqdm is not None:
            self.pbar = tqdm(
                total=self.total,
                desc=self.desc,
                unit=self.unit,
                file=self.file,
                dynamic_ncols=True,
                leave=True,
                bar_format="{desc} |{bar:25}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}",
            )

    def __enter__(self) -> PipelineProgressReporter:
        self.start_time = time.monotonic()
        set_heartbeat_hook(self.heartbeat)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        set_heartbeat_hook(None)
        self.close()

    def _format_elapsed(self, seconds: float) -> str:
        """Formats elapsed seconds into MM:SS or HH:MM:SS."""
        secs = int(max(0, seconds))
        mins, s = divmod(secs, 60)
        hrs, m = divmod(mins, 60)
        if hrs > 0:
            return f"{hrs:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def notify(self, step_idx: int, stage_desc: str) -> None:
        """
        Record and display a progress step update.

        In fenced mode: writes a discrete, markdown-fenced text code block.
        In plain mode: writes a line-delimited message ending in \\n.
        In TTY mode: updates in-place tqdm bar without truncating stage info.
        """
        self.current_step = step_idx
        elapsed = time.monotonic() - self.start_time
        elapsed_str = self._format_elapsed(elapsed)
        pct = min(100, max(0, int((step_idx / self.total) * 100)))

        if self.pbar is not None:
            self.pbar.set_postfix_str(stage_desc)
            self.pbar.update(1)
        elif self.mode == "fenced":
            filled = max(0, min(24, int(24 * (pct / 100.0))))
            ascii_bar = "█" * filled + "░" * (24 - filled)
            block = (
                f"\n```text\n"
                f"[{self.desc}] [{step_idx}/{self.total}] |{ascii_bar}| {pct:3d}% [{elapsed_str}]\n"
                f"Active Stage: {stage_desc}\n"
                f"```\n"
            )
            self.file.write(block)
            if hasattr(self.file, "flush"):
                self.file.flush()
        else:
            line = (
                f"[{self.desc}] [{step_idx}/{self.total}] {pct:3d}% "
                f"[{elapsed_str}] {stage_desc}\n"
            )
            self.file.write(line)
            if hasattr(self.file, "flush"):
                self.file.flush()

        if self.progress_callback:
            try:
                self.progress_callback(step_idx, self.total, stage_desc)
            except Exception:
                pass

    def _emit_inband_heartbeat(self, elapsed: float, operation_name: str) -> None:
        """Emits live in-band heartbeat telemetry to prevent UI stagnation during long calls."""
        elapsed_str = self._format_elapsed(elapsed)
        pct = min(100, max(0, int((self.current_step / self.total) * 100)))
        if self.mode == "fenced":
            filled = max(0, min(24, int(24 * (pct / 100.0))))
            ascii_bar = "█" * filled + "░" * (24 - filled)
            block = (
                f"\n```text\n"
                f"[{self.desc}] [{self.current_step}/{self.total}] |{ascii_bar}| {pct:3d}% [{elapsed_str}]\n"
                f"Stage Running: {operation_name} (heartbeat: {elapsed_str})\n"
                f"```\n"
            )
            self.file.write(block)
            if hasattr(self.file, "flush"):
                self.file.flush()
        elif self.pbar is not None:
            self.pbar.set_postfix_str(f"{operation_name} ({elapsed_str})")
        else:
            line = (
                f"[{self.desc}] [{self.current_step}/{self.total}] {pct:3d}% "
                f"[{elapsed_str}] {operation_name} (in progress...)\n"
            )
            self.file.write(line)
            if hasattr(self.file, "flush"):
                self.file.flush()

    def write(self, message: str) -> None:
        """
        Safely write an out-of-band log or informational message
        without colliding with active progress bar lines.
        """
        formatted = message if message.endswith("\n") else f"{message}\n"
        if self.pbar is not None and tqdm is not None:
            tqdm.write(message.rstrip("\n"), file=self.file)
        else:
            self.file.write(formatted)
        if hasattr(self.file, "flush"):
            self.file.flush()

    def heartbeat(self, *args: Any, **kwargs: Any) -> Any:
        """
        Unified heartbeat handler supporting both transport hook callback and context manager:
        - When invoked with (elapsed: float, operation_name: str): emits in-band progress.
        - When invoked with (desc: str, interval: float): returns periodic heartbeat context manager.
        """
        if args and isinstance(args[0], (int, float)):
            elapsed = float(args[0])
            op_name = str(args[1]) if len(args) > 1 else kwargs.get("operation_name", "Operation")
            return self._emit_inband_heartbeat(elapsed, op_name)
        desc = str(args[0]) if args else kwargs.get("desc", "Operation")
        interval = float(args[1]) if len(args) > 1 else kwargs.get("interval", 10.0)
        return self._heartbeat_context(desc, interval)

    def _heartbeat_context(self, desc: str, interval: float = 10.0) -> Any:
        """Context manager that periodically logs active heartbeat during long sub-tasks."""
        reporter = self

        class _HeartbeatContext:
            def __init__(ctx) -> None:
                ctx._desc = desc
                ctx._interval = max(0.01, float(interval))
                ctx._stop_event = threading.Event()
                ctx._thread: Optional[threading.Thread] = None
                ctx._start: float = time.monotonic()

            def _loop(ctx) -> None:
                while not ctx._stop_event.wait(ctx._interval):
                    elapsed = time.monotonic() - ctx._start
                    elapsed_str = reporter._format_elapsed(elapsed)
                    reporter.write(f"⏱️  [{reporter.desc}] {ctx._desc} (elapsed: {elapsed_str}, actively working...)")

            def __enter__(ctx) -> _HeartbeatContext:
                ctx._start = time.monotonic()
                ctx._thread = threading.Thread(target=ctx._loop, daemon=True)
                ctx._thread.start()
                return ctx

            def __exit__(ctx, *args: Any) -> None:
                ctx._stop_event.set()
                if ctx._thread and ctx._thread.is_alive():
                    ctx._thread.join(timeout=1.0)

        return _HeartbeatContext()

    def close(self) -> None:
        """Cleanly close progress tracking and guarantee trailing newline."""
        set_heartbeat_hook(None)
        if self._closed:
            return
        self._closed = True

        if self.pbar is not None:
            try:
                self.pbar.close()
            except Exception:
                pass
            finally:
                self.pbar = None
                # Guarantee newline after in-place bar closes
                try:
                    self.file.write("\n")
                    if hasattr(self.file, "flush"):
                        self.file.flush()
                except Exception:
                    pass
        else:
            try:
                if hasattr(self.file, "flush"):
                    self.file.flush()
            except Exception:
                pass
