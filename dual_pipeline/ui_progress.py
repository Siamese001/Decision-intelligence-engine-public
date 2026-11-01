"""
UI Progress Tracking Utility for Decision Intelligence Engine.

Provides unified progress tracking, percentage calculation, ETA estimation,
and Streamlit progress bar bindings for long-running pipelines, evaluations,
test suites, and copilot context fusion.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional


class UIProgressTracker:
    """
    Stateful progress tracker with exact percentage, elapsed time, and ETA calculations.
    """

    def __init__(
        self,
        total_steps: int,
        title: str = "Pipeline Run",
        callback: Optional[Callable[[int, int, float, str, Dict[str, Any]], None]] = None,
    ) -> None:
        self.total_steps = max(1, total_steps)
        self.title = title
        self.callback = callback
        self.current_step = 0
        self.stage_description = "Initialized"
        self.start_time = time.monotonic()
        self.last_update_time = self.start_time
        self.step_history: list[tuple[str, float]] = []

    @property
    def percentage(self) -> float:
        """Returns percentage complete between 0.0 and 100.0."""
        return min(100.0, max(0.0, round((self.current_step / self.total_steps) * 100.0, 1)))

    @property
    def fraction(self) -> float:
        """Returns fraction complete between 0.0 and 1.0."""
        return min(1.0, max(0.0, self.current_step / self.total_steps))

    @property
    def elapsed_seconds(self) -> float:
        """Total elapsed seconds since tracker initialization."""
        return time.monotonic() - self.start_time

    @property
    def eta_seconds(self) -> Optional[float]:
        """Estimated seconds remaining based on average time per step."""
        if self.current_step <= 0:
            return None
        elapsed = self.elapsed_seconds
        avg_time = elapsed / self.current_step
        remaining_steps = self.total_steps - self.current_step
        return max(0.0, avg_time * remaining_steps)

    def format_status(self) -> str:
        """Formats a human-readable status string with stage, percentage, and timing."""
        eta_str = f" • ETA: {self.eta_seconds:.1f}s" if self.eta_seconds is not None and self.fraction < 1.0 else ""
        return (
            f"[{self.current_step}/{self.total_steps}] {self.percentage:.1f}% • "
            f"{self.stage_description} ({self.elapsed_seconds:.1f}s{eta_str})"
        )

    def update(
        self,
        step: Optional[int] = None,
        stage_description: Optional[str] = None,
        increment: int = 0,
        extra_metrics: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Updates the tracker state and dispatches callback if registered.
        Returns current percentage complete.
        """
        now = time.monotonic()
        if step is not None:
            self.current_step = min(self.total_steps, max(0, step))
        elif increment > 0:
            self.current_step = min(self.total_steps, self.current_step + increment)

        if stage_description is not None:
            self.stage_description = stage_description

        step_elapsed = now - self.last_update_time
        self.step_history.append((self.stage_description, step_elapsed))
        self.last_update_time = now

        metrics = extra_metrics or {}
        metrics["elapsed_seconds"] = round(self.elapsed_seconds, 2)
        metrics["eta_seconds"] = round(self.eta_seconds, 2) if self.eta_seconds is not None else None

        if self.callback:
            self.callback(
                self.current_step,
                self.total_steps,
                self.percentage,
                self.stage_description,
                metrics,
            )

        return self.percentage

    def finish(self, final_message: str = "Completed successfully") -> str:
        """Marks tracker as finished at 100% and returns summary message."""
        self.current_step = self.total_steps
        self.stage_description = final_message
        self.update(self.total_steps, final_message)
        return (
            f"✅ {self.title} completed in {self.elapsed_seconds:.2f}s "
            f"({self.total_steps}/{self.total_steps} stages)"
        )


class StreamlitProgressCallback:
    """
    Bridge connecting pipeline executions directly to Streamlit progress widgets.
    """

    def __init__(
        self,
        progress_bar: Any,
        status_container: Optional[Any] = None,
        title_prefix: str = "Pipeline Stage",
    ) -> None:
        self.progress_bar = progress_bar
        self.status_container = status_container
        self.title_prefix = title_prefix
        self.start_time = time.monotonic()

    def __call__(
        self,
        current_step: int,
        total_steps: int,
        description: str,
        extra_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        total = max(1, total_steps)
        current = min(total, max(0, current_step))
        fraction = current / total
        pct_int = int(round(fraction * 100))
        elapsed = time.monotonic() - self.start_time

        display_text = f"{self.title_prefix} {current}/{total} ({pct_int}%): {description} [{elapsed:.1f}s]"

        try:
            if self.progress_bar is not None:
                self.progress_bar.progress(fraction, text=display_text)
        except Exception:
            pass

        try:
            if self.status_container is not None:
                extra_info = ""
                if extra_metrics:
                    parts = [f"{k}: {v}" for k, v in extra_metrics.items() if k not in ("elapsed_seconds", "eta_seconds")]
                    if parts:
                        extra_info = f" ({', '.join(parts)})"
                self.status_container.write(f"⚡ `[{current}/{total}]` **{pct_int}%** &mdash; {description}{extra_info}")
        except Exception:
            pass
