"""Bounded request queue and concurrency controller for Decision API (Wave 9).

Provides thread-safe request bounding, burst absorption, and 429 backpressure.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger("dual_pipeline.request_queue")
T = TypeVar("T")


def resolve_max_concurrency(default: Optional[int] = None) -> int:
    """Resolves worker pool concurrency from NEE_QUERY_MAX_WORKERS or CPU count."""
    from config.credentials import CredentialManager

    env_val = CredentialManager.get_credential("NEE_QUERY_MAX_WORKERS")
    if env_val:
        try:
            val = int(env_val)
            if val > 0:
                return val
        except ValueError:
            pass

    if default is not None and default > 0:
        return default

    cpu = os.cpu_count() or 4
    return min(max(cpu, 1), 8)


def resolve_max_queue_size(max_concurrency: int, default: Optional[int] = None) -> int:
    """Resolves maximum queue backlog size from NEE_QUERY_MAX_QUEUE_SIZE or 2x concurrency."""
    from config.credentials import CredentialManager

    env_val = CredentialManager.get_credential("NEE_QUERY_MAX_QUEUE_SIZE")

    if env_val:
        try:
            val = int(env_val)
            if val >= 0:
                return val
        except ValueError:
            pass

    if default is not None and default >= 0:
        return default

    return max_concurrency * 2


class BoundedRequestQueue:
    """
    Thread-safe bounded request queue and execution pool.

    Coordinates a fixed-size worker pool (max_workers) with a bounded
    waiting queue (max_queue_size). When total in-flight requests
    (active + queued) reach max_capacity (max_workers + max_queue_size),
    new requests are rejected immediately with 429 backpressure.
    """

    def __init__(
        self,
        max_workers: int,
        max_queue_size: int = 0,
        thread_name_prefix: str = "nee-decision-api",
    ) -> None:
        if max_workers < 1:
            raise ValueError(f"max_workers must be positive, got {max_workers}")
        if max_queue_size < 0:
            raise ValueError(f"max_queue_size must be non-negative, got {max_queue_size}")

        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.max_capacity = max_workers + max_queue_size

        self._lock = threading.Lock()
        self._active_count = 0
        self._queued_count = 0
        self._rejected_count = 0
        self._completed_count = 0
        self._is_shutting_down = False

        self._capacity_sem = threading.BoundedSemaphore(self.max_capacity)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=thread_name_prefix,
        )

    def submit(self, action: Callable[[], T]) -> Optional[Future[T]]:
        """
        Submits an action for execution if capacity is available.
        Returns Future on success, or None if capacity is exhausted (HTTP 429).
        """
        with self._lock:
            if self._is_shutting_down:
                return None

        # Non-blocking acquisition of capacity (active + queue)
        if not self._capacity_sem.acquire(blocking=False):
            with self._lock:
                self._rejected_count += 1
            logger.warning(
                f"[QUEUE] Capacity exhausted ({self.max_capacity} in-flight). "
                f"Rejecting request (total rejected={self._rejected_count})"
            )
            return None

        with self._lock:
            if self._active_count < self.max_workers:
                self._active_count += 1
            else:
                self._queued_count += 1

        executed_flag = threading.Event()

        def _wrapped() -> T:
            executed_flag.set()
            with self._lock:
                if self._queued_count > 0:
                    self._queued_count -= 1
                    self._active_count += 1
            try:
                return action()
            finally:
                with self._lock:
                    self._active_count = max(0, self._active_count - 1)
                    self._completed_count += 1
                self._capacity_sem.release()

        def _on_done(f: Future[T]) -> None:
            # Handle cancellation before worker picked up the task
            if f.cancelled() and not executed_flag.is_set():
                with self._lock:
                    self._queued_count = max(0, self._queued_count - 1)
                self._capacity_sem.release()

        try:
            future = self._executor.submit(_wrapped)
        except Exception:
            with self._lock:
                if self._active_count > 0:
                    self._active_count -= 1
            self._capacity_sem.release()
            raise

        future.add_done_callback(_on_done)
        return future

    def stats(self) -> Dict[str, Any]:
        """Returns snapshot of current queue and worker utilization."""
        with self._lock:
            return {
                "max_workers": self.max_workers,
                "max_queue_size": self.max_queue_size,
                "max_capacity": self.max_capacity,
                "active_workers": self._active_count,
                "queued_requests": self._queued_count,
                "in_flight": self._active_count + self._queued_count,
                "rejected_total": self._rejected_count,
                "completed_total": self._completed_count,
                "is_shutting_down": self._is_shutting_down,
            }

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        """Signals shutdown and terminates the executor."""
        with self._lock:
            self._is_shutting_down = True
        try:
            self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)
        except TypeError:
            self._executor.shutdown(wait=wait)
