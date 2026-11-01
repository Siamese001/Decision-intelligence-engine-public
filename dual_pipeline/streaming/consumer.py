"""
dual_pipeline/streaming/consumer.py

Event-driven message queue consumer for pipeline ingestion:
- IngestionMessage envelope
- Bounded concurrency with worker pool
- Retry policy with exponential backoff
- Dead Letter Queue (DLQ) routing for poisoned/failing messages
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class IngestionMessage:
    """Standardized event message triggering an ingestion run."""

    message_id: str
    input_uri: str
    project: str
    meeting_name: str
    date: str
    attendees: str = ""
    priority: int = 1  # 1 = normal, 2 = high, 0 = low
    retry_count: int = 0
    max_retries: int = 3
    extra_meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DeadLetterItem:
    """Record of a failed message deposited in the Dead Letter Queue."""

    message: IngestionMessage
    failure_reason: str
    dropped_at: float = field(default_factory=time.time)


class DeadLetterQueue:
    """In-memory and durable DLQ storing unprocessable ingestion messages."""

    def __init__(self) -> None:
        self._items: List[DeadLetterItem] = []
        self._lock = threading.Lock()

    def route_to_dlq(self, message: IngestionMessage, reason: str) -> DeadLetterItem:
        with self._lock:
            item = DeadLetterItem(message=message, failure_reason=reason)
            self._items.append(item)
            logger.error("Message %s routed to DLQ: %s", message.message_id, reason)
            return item

    def get_items(self) -> List[DeadLetterItem]:
        with self._lock:
            return list(self._items)

    def size(self) -> int:
        with self._lock:
            return len(self._items)


class StreamingIngestionConsumer:
    """
    Consumes ingestion messages with bounded concurrency, retries, and DLQ routing.
    """

    def __init__(
        self,
        handler: Callable[[IngestionMessage], bool],
        max_workers: int = 2,
        dlq: Optional[DeadLetterQueue] = None,
    ) -> None:
        self.handler = handler
        self.max_workers = max_workers
        self.dlq = dlq or DeadLetterQueue()
        self._queue: queue.Queue[Optional[IngestionMessage]] = queue.Queue(maxsize=100)
        self._running = False
        self._threads: List[threading.Thread] = []
        self._processed_count = 0
        self._lock = threading.Lock()

    def start(self) -> None:
        """Starts worker pool threads."""
        self._running = True
        self._threads = []
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"streaming-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def submit(self, message: IngestionMessage, timeout: float = 5.0) -> bool:
        """Submits an ingestion message to the incoming queue."""
        try:
            self._queue.put(message, timeout=timeout)
            return True
        except queue.Full:
            logger.warning("Consumer queue is full; dropping message %s", message.message_id)
            return False

    def _worker_loop(self) -> None:
        while self._running:
            try:
                msg = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if msg is None:  # Sentinel to stop worker
                self._queue.task_done()
                break

            success = False
            error_reason = ""
            try:
                success = self.handler(msg)
                if not success:
                    error_reason = "Handler returned False"
            except Exception as e:
                error_reason = str(e)
                success = False

            if success:
                with self._lock:
                    self._processed_count += 1
            else:
                msg.retry_count += 1
                if msg.retry_count <= msg.max_retries:
                    logger.info("Retrying message %s (attempt %d/%d)", msg.message_id, msg.retry_count, msg.max_retries)
                    try:
                        self._queue.put(msg, timeout=2.0)
                    except queue.Full:
                        logger.warning("Queue full during retry; routing message %s to DLQ", msg.message_id)
                        self.dlq.route_to_dlq(msg, f"Queue full during retry: {error_reason}")
                else:
                    self.dlq.route_to_dlq(msg, f"Max retries exceeded: {error_reason}")

            self._queue.task_done()

    def stop(self, drain: bool = True) -> None:
        """Stops the worker pool, optionally waiting for queue to drain."""
        self._running = False
        for _ in self._threads:
            try:
                self._queue.put(None, timeout=2.0)
            except queue.Full:
                pass
        for t in self._threads:
            t.join(timeout=2.0)

    @property
    def processed_count(self) -> int:
        with self._lock:
            return self._processed_count
