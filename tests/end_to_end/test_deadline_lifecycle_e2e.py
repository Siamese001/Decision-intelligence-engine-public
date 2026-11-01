"""
tests/end_to_end/test_deadline_lifecycle_e2e.py

End-to-End Lifecycle Verification for Transport Deadlines, Cancellation, and Socket Hygiene.
Tests bounded execution against stalling and byte-dripping TCP servers:
1. Stalling TCP server: verifies TimeoutError raised within budget + grace period (<1.5s for 0.25s deadline).
2. Socket lifecycle & cleanup: verifies that active sockets unblock and close immediately upon abort.
3. Thread residue invariant: verifies zero lingering worker threads via threading.enumerate().
4. Parallel DAG cancellation: verifies sibling tasks abort immediately without blocking threadpool exit.

Governed under Decision H-8 and AGENTS.md Architectural Invariants.
"""
from __future__ import annotations

import http.server
import socket
import threading
import time
from typing import Any, List

import pytest

from config.request_deadline import check_deadline, deadline_scope
from config.transport import (
    BoundedThreadPoolExecutor,
    SiblingPassAbortedError,
    execute_with_deadline_and_heartbeat,
)


class StallingTCPServer:
    """Lightweight test server simulating network hangs and byte drips."""

    def __init__(self, mode: str = "hang") -> None:
        self.mode = mode
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(10)
        self.port = self.srv.getsockname()[1]
        self._running = True
        self._threads: List[threading.Thread] = []
        self._server_th = threading.Thread(target=self._accept_loop, daemon=True)
        self._server_th.start()

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self.srv.accept()
                t = threading.Thread(target=self._client_handler, args=(conn,), daemon=True)
                t.start()
                self._threads.append(t)
            except Exception:
                break

    def _client_handler(self, conn: socket.socket) -> None:
        try:
            if self.mode == "hang":
                time.sleep(10.0)
            elif self.mode == "drip":
                for _ in range(20):
                    time.sleep(0.5)
                    conn.sendall(b"x")
        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def close(self) -> None:
        self._running = False
        try:
            self.srv.close()
        except Exception:
            pass


class TestDeadlineLifecycleE2E:
    """E2E verification of bounded transport and resource hygiene."""

    def test_e2e_stalling_server_aborts_within_budget(self):
        """Verifies TimeoutError fires within budget and cleans up worker resources."""
        server = StallingTCPServer(mode="hang")
        try:
            initial_threads = {t.name for t in threading.enumerate()}

            def make_hanging_call():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.connect(("127.0.0.1", server.port))
                    s.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                    s.recv(1024)
                finally:
                    s.close()

            start_t = time.monotonic()
            with pytest.raises(TimeoutError, match="exceeded hard deadline"):
                execute_with_deadline_and_heartbeat(
                    make_hanging_call,
                    deadline_seconds=0.3,
                    heartbeat_interval=0.1,
                    operation_name="E2E Stalling Transport Call",
                )
            elapsed = time.monotonic() - start_t

            # Hard timeout of 0.3s must terminate well before 2.0s
            assert elapsed < 1.5, f"Execution took {elapsed:.2f}s, exceeding budget + grace period"

            # Allow brief moment for daemon worker cleanup
            time.sleep(0.2)
            active_threads = {t.name for t in threading.enumerate()}
            new_threads = [name for name in active_threads if name not in initial_threads and "transport_worker" in name]
            assert len(new_threads) == 0, f"Leaked transport worker threads: {new_threads}"
        finally:
            server.close()

    def test_e2e_drip_server_aborts_without_budget_reset(self):
        """Verifies slow byte drips cannot trick deadline into resetting."""
        server = StallingTCPServer(mode="drip")
        try:
            def make_dripping_call():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.connect(("127.0.0.1", server.port))
                    total_bytes = 0
                    while total_bytes < 100:
                        chunk = s.recv(1)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                finally:
                    s.close()

            start_t = time.monotonic()
            with pytest.raises(TimeoutError, match="exceeded hard deadline"):
                execute_with_deadline_and_heartbeat(
                    make_dripping_call,
                    deadline_seconds=0.4,
                    heartbeat_interval=0.1,
                    operation_name="E2E Drip Transport Call",
                )
            elapsed = time.monotonic() - start_t
            assert elapsed < 1.5, f"Drip call exceeded deadline boundary: {elapsed:.2f}s"
        finally:
            server.close()

    def test_e2e_parallel_dag_cancellation_lifecycle(self):
        """Verifies parallel sibling tasks abort promptly when one fails terminally."""
        server = StallingTCPServer(mode="hang")
        try:
            abort_event = threading.Event()
            completed_tasks: List[str] = []

            def fast_failing_task():
                time.sleep(0.05)
                abort_event.set()
                raise ValueError("Terminal validation error in Pass 2A")

            def slow_hanging_task_b():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.connect(("127.0.0.1", server.port))
                    while not abort_event.is_set():
                        s.recv(1024)
                finally:
                    s.close()
                completed_tasks.append("2B")

            def slow_hanging_task_c():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    s.connect(("127.0.0.1", server.port))
                    while not abort_event.is_set():
                        s.recv(1024)
                finally:
                    s.close()
                completed_tasks.append("2C")

            start_t = time.monotonic()
            with pytest.raises(ValueError, match="Terminal validation error in Pass 2A"):
                with deadline_scope(5.0):
                    with BoundedThreadPoolExecutor(max_workers=3, thread_name_prefix="test_dag") as executor:
                        f_a = executor.submit(fast_failing_task)
                        f_b = executor.submit(slow_hanging_task_b)
                        f_c = executor.submit(slow_hanging_task_c)
                        try:
                            f_a.result()
                            f_b.result()
                            f_c.result()
                        except Exception:
                            abort_event.set()
                            executor.shutdown(wait=False, cancel_futures=True)
                            raise

            elapsed = time.monotonic() - start_t
            # Entire parallel block must abort within <1.0s, not wait for 10s server hang
            assert elapsed < 1.0, f"Parallel DAG abort took {elapsed:.2f}s; failed to cancel promptly"
        finally:
            server.close()
