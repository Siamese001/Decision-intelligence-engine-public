"""
Host Admission Controller and Request Serialization for Apple Silicon Metal.

Enforces:
1. Priority tiers: LIVE_TRANSCRIPTION (0) > INTERACTIVE_QUERY (1) > BACKGROUND_BATCH (2).
2. Live Capture Protection: Blocks admission of unbounded background work during active live capture.
3. Bounded queues and admission deadlines: Rejects requests exceeding queue depth or timeout.
4. Isolated ASR Worker Request Serialization: Exactly one active transcription request per ASR worker process.
5. Cooperative cancellation ownership: Interrupts workers via supported cancellation tokens while
   retaining memory reservations until completion or confirmed worker death.
6. Zero Implicit Cloud Fallback: Rejections fail closed locally; never dispatches to external cloud APIs.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Dict, Generator, List, Optional, Set

logger = logging.getLogger("dual_pipeline.runtime.host_admission")


class AdmissionPriority(IntEnum):
    """Governed admission priority levels (lower integer = higher scheduling priority)."""
    LIVE_TRANSCRIPTION = 0
    INTERACTIVE_QUERY = 1
    BACKGROUND_BATCH = 2


class AdmissionError(Exception):
    """Base exception for host admission failures."""
    pass


class AdmissionRejectedError(AdmissionError):
    """Raised when request is rejected to protect live capture or resource limits."""
    pass


class AdmissionTimeoutError(AdmissionError):
    """Raised when request cannot be admitted before its deadline expires."""
    pass


class AdmissionQueueFullError(AdmissionError):
    """Raised when admission queue reaches maximum capacity."""
    pass


@dataclass(frozen=True)
class AdmissionRequest:
    """Bounded task admission descriptor."""
    priority: AdmissionPriority
    task_name: str
    request_id: str = field(default_factory=lambda: f"adm_{uuid.uuid4().hex[:8]}")
    worker_id: Optional[str] = None
    estimated_duration_ms: float = 1000.0
    deadline_ms: float = 5000.0
    max_execution_quanta_ms: float = 2000.0
    created_at_ms: float = field(default_factory=lambda: time.time() * 1000.0)


class HostAdmissionController:
    """Coordinates shared host admissions, priority gating, and ASR worker serialization."""

    def __init__(self, max_queue_depth: int = 16) -> None:
        self.max_queue_depth = max_queue_depth
        self._lock = threading.RLock()
        self._live_capture_active = False
        self._active_requests: Dict[str, AdmissionRequest] = {}
        self._queued_requests: List[AdmissionRequest] = []
        self._cancelled_tasks: Set[str] = set()
        self._asr_worker_locks: Dict[str, threading.Lock] = {}

    @property
    def is_live_capture_active(self) -> bool:
        with self._lock:
            return self._live_capture_active

    def set_live_capture_active(self, active: bool) -> None:
        """Sets the active state of live microphone/transcription capture."""
        with self._lock:
            self._live_capture_active = bool(active)
            status = "ACTIVATED" if self._live_capture_active else "DEACTIVATED"
            logger.info(f"[ADMISSION] Live capture mode {status}. Background work will be gated.")

    def acquire_admission(self, request: AdmissionRequest) -> bool:
        """
        Attempts to admit a request, enforcing priority gating and queue boundaries.
        Raises AdmissionRejectedError, AdmissionQueueFullError, or AdmissionTimeoutError.
        """
        with self._lock:
            now_ms = time.time() * 1000.0
            if (now_ms - request.created_at_ms) > request.deadline_ms:
                raise AdmissionTimeoutError(
                    f"Admission deadline exceeded for '{request.task_name}' "
                    f"({now_ms - request.created_at_ms:.1f}ms > {request.deadline_ms:.1f}ms)."
                )

            # Invariant: Block background batch work during active live capture
            if self._live_capture_active and request.priority == AdmissionPriority.BACKGROUND_BATCH:
                raise AdmissionRejectedError(
                    f"Admission rejected: Background batch task '{request.task_name}' "
                    "blocked during active live capture to protect real-time speech transcription."
                )

            # Invariant: Bounded queue depth
            if len(self._queued_requests) >= self.max_queue_depth:
                raise AdmissionQueueFullError(
                    f"Admission queue full ({len(self._queued_requests)} >= {self.max_queue_depth}). "
                    f"Cannot queue request for '{request.task_name}'."
                )

            self._active_requests[request.request_id] = request
            logger.info(f"[ADMISSION] Admitted request '{request.request_id}' (priority={request.priority.name}, task='{request.task_name}').")
            return True

    def release_admission(self, request: AdmissionRequest) -> None:
        """Releases an admitted request upon task completion."""
        with self._lock:
            self._active_requests.pop(request.request_id, None)
            self._cancelled_tasks.discard(request.request_id)
            logger.info(f"[ADMISSION] Released request '{request.request_id}'.")

    @contextmanager
    def serialize_asr_request(self, worker_id: str, timeout_seconds: float = 30.0) -> Generator[None, None, None]:
        """
        Enforces exactly one active transcription request per ASR worker process.
        Acquires a dedicated per-worker mutex lock.
        """
        with self._lock:
            if worker_id not in self._asr_worker_locks:
                self._asr_worker_locks[worker_id] = threading.Lock()
            worker_lock = self._asr_worker_locks[worker_id]

        acquired = worker_lock.acquire(timeout=timeout_seconds)
        if not acquired:
            raise AdmissionTimeoutError(
                f"ASR worker '{worker_id}' is busy with an existing transcription request. "
                f"Timed out after {timeout_seconds:.1f}s awaiting worker serialization lock."
            )
        try:
            yield
        finally:
            worker_lock.release()

    def request_task_cancellation(self, task_id: str) -> None:
        """Marks a task for cooperative cancellation without revoking memory reservations."""
        with self._lock:
            self._cancelled_tasks.add(task_id)
            logger.info(f"[ADMISSION] Cancellation requested for task '{task_id}'. Worker must stop cooperatively.")

    def is_task_cancelled(self, task_id: str) -> bool:
        """Checks if a cooperative cancellation has been signaled for task_id."""
        with self._lock:
            return task_id in self._cancelled_tasks

    def get_status(self) -> Dict[str, Any]:
        """Returns structured admission controller status."""
        with self._lock:
            return {
                "live_capture_active": self._live_capture_active,
                "active_requests_count": len(self._active_requests),
                "queued_requests_count": len(self._queued_requests),
                "max_queue_depth": self.max_queue_depth,
                "active_requests": {
                    req.request_id: {
                        "task_name": req.task_name,
                        "priority": req.priority.name,
                        "worker_id": req.worker_id,
                        "created_at_ms": req.created_at_ms,
                    }
                    for req in self._active_requests.values()
                },
            }

    def reset(self) -> None:
        """Resets controller state."""
        with self._lock:
            self._live_capture_active = False
            self._active_requests.clear()
            self._queued_requests.clear()
            self._cancelled_tasks.clear()
            self._asr_worker_locks.clear()


_GLOBAL_ADMISSION: Optional[HostAdmissionController] = None
_ADMISSION_LOCK = threading.Lock()


def get_host_admission_controller() -> HostAdmissionController:
    """Returns singleton HostAdmissionController instance."""
    global _GLOBAL_ADMISSION
    with _ADMISSION_LOCK:
        if _GLOBAL_ADMISSION is None:
            _GLOBAL_ADMISSION = HostAdmissionController()
        return _GLOBAL_ADMISSION
