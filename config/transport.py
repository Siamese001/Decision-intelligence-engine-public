"""
config/transport.py

Resilient HTTP transport and TCP socket keepalive configuration.
Enforces aggressive TCP keepalive options (SO_KEEPALIVE, TCP_KEEPALIVE/TCP_KEEPIDLE,
TCP_KEEPINTVL, TCP_KEEPCNT) to detect dead/severed connections in <60 seconds
instead of waiting for the OS default of 2+ hours.
"""
from __future__ import annotations

import concurrent.futures
from contextvars import copy_context
import logging
import socket
import threading
import time
from typing import Any, Callable, List, Optional, Tuple, TypeVar
import weakref

logger = logging.getLogger(__name__)

SocketOption = Tuple[int, int, int]


def build_tcp_keepalive_socket_options(
    keepidle: int = 30,
    keepintvl: int = 10,
    keepcnt: int = 3,
) -> List[SocketOption]:
    """
    Build cross-platform TCP keepalive socket option tuples for httpcore/httpx.

    Args:
        keepidle: Seconds connection must remain idle before sending first keepalive probe (default: 30s).
        keepintvl: Seconds between individual keepalive probes (default: 10s).
        keepcnt: Number of unacknowledged keepalive probes before dropping connection (default: 3).

    Returns:
        List of (level, option, value) tuples suitable for sock.setsockopt(*opt).
    """
    options: List[SocketOption] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]

    # macOS uses TCP_KEEPALIVE (0x10); Linux uses TCP_KEEPIDLE (0x4)
    tcp_keepidle_opt = getattr(socket, "TCP_KEEPALIVE", getattr(socket, "TCP_KEEPIDLE", None))
    if tcp_keepidle_opt is not None:
        options.append((socket.IPPROTO_TCP, tcp_keepidle_opt, keepidle))

    tcp_keepintvl_opt = getattr(socket, "TCP_KEEPINTVL", None)
    if tcp_keepintvl_opt is not None:
        options.append((socket.IPPROTO_TCP, tcp_keepintvl_opt, keepintvl))

    tcp_keepcnt_opt = getattr(socket, "TCP_KEEPCNT", None)
    if tcp_keepcnt_opt is not None:
        options.append((socket.IPPROTO_TCP, tcp_keepcnt_opt, keepcnt))

    return options


def build_resilient_http_client(
    timeout: float = 300.0,
    max_retries: int = 1,
    keepidle: int = 30,
    keepintvl: int = 10,
    keepcnt: int = 3,
    verify: bool = True,
) -> Optional[Any]:
    """
    Construct a synchronous httpx.Client with TCP keepalive socket options.
    Returns None if httpx is not installed.
    """
    try:
        import httpx
        socket_opts = build_tcp_keepalive_socket_options(
            keepidle=keepidle,
            keepintvl=keepintvl,
            keepcnt=keepcnt,
        )
        transport = httpx.HTTPTransport(
            verify=verify,
            retries=max_retries,
            socket_options=socket_opts,
        )
        return httpx.Client(
            transport=transport,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("Failed to initialize resilient httpx client with socket keepalive: %s", exc)
        return None


def build_resilient_async_http_client(
    timeout: float = 300.0,
    max_retries: int = 1,
    keepidle: int = 30,
    keepintvl: int = 10,
    keepcnt: int = 3,
    verify: bool = True,
) -> Optional[Any]:
    """
    Construct an asynchronous httpx.AsyncClient with TCP keepalive socket options.
    Returns None if httpx is not installed.
    """
    try:
        import httpx
        socket_opts = build_tcp_keepalive_socket_options(
            keepidle=keepidle,
            keepintvl=keepintvl,
            keepcnt=keepcnt,
        )
        transport = httpx.AsyncHTTPTransport(
            verify=verify,
            retries=max_retries,
            socket_options=socket_opts,
        )
        return httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("Failed to initialize resilient async httpx client with socket keepalive: %s", exc)
        return None


T = TypeVar("T")


_HEARTBEAT_HOOK: Optional[Callable[[float, str], None]] = None


def set_heartbeat_hook(hook: Optional[Callable[[float, str], None]]) -> None:
    """Registers a global callback invoked on each heartbeat interval during execution."""
    global _HEARTBEAT_HOOK
    _HEARTBEAT_HOOK = hook


class SiblingPassAbortedError(RuntimeError):
    """Raised when an operation aborts early due to sibling task failure."""
    pass


def _is_aborted(abort_event: Optional[Any]) -> bool:
    return abort_event is not None and getattr(abort_event, "is_set", lambda: False)()


def _emit_heartbeat_if_due(
    now_mono: float,
    last_heartbeat: float,
    start_time: float,
    heartbeat_interval: float,
    operation_name: str,
    deadline_seconds: float,
) -> float:
    if (now_mono - last_heartbeat) < heartbeat_interval:
        return last_heartbeat
    current_elapsed = now_mono - start_time
    logger.info(
        "[%s] In progress... (elapsed: %0.1fs / deadline: %0.1fs)",
        operation_name,
        current_elapsed,
        deadline_seconds,
    )
    if _HEARTBEAT_HOOK is not None:
        try:
            _HEARTBEAT_HOOK(current_elapsed, operation_name)
        except Exception:
            pass
    return now_mono


def _check_execution_deadline(start_time: float, deadline_seconds: float, operation_name: str) -> float:
    from config.request_deadline import RequestDeadlineExceeded, remaining_timeout
    if (time.monotonic() - start_time) >= deadline_seconds:
        raise TimeoutError(f"{operation_name} exceeded hard deadline of {deadline_seconds}s")
    try:
        return remaining_timeout(deadline_seconds)
    except RequestDeadlineExceeded as rde:
        raise TimeoutError(f"{operation_name} exceeded hard deadline of {deadline_seconds}s") from rde


class BoundedThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """ThreadPoolExecutor that marks worker threads daemon and prevents hanging on exit."""

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_, q=self._work_queue):
            q.put(None)

        num_threads = len(self._threads)
        if num_threads < self._max_workers:
            thread_name = "%s_%d" % (self._thread_name_prefix or self, num_threads)
            t = threading.Thread(
                name=thread_name,
                target=concurrent.futures.thread._worker,
                args=(weakref.ref(self, weakref_cb), self._work_queue, self._initializer, self._initargs),
                daemon=True,
            )
            t.start()
            self._threads.add(t)
            concurrent.futures.thread._threads_queues[t] = self._work_queue

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        if exc_type is not None:
            self.shutdown(wait=False, cancel_futures=True)
            return False
        self.shutdown(wait=True, cancel_futures=True)
        return False


def _drain_worker(
    future: concurrent.futures.Future[Any],
    executor: concurrent.futures.ThreadPoolExecutor,
    timeout: float = 0.2,
) -> None:
    """Wait briefly for an interrupted worker thread to release resources and exit."""
    if not future.done():
        try:
            future.result(timeout=timeout)
        except Exception:
            pass
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass


def _abort_and_drain(
    deadline: Any,
    future: concurrent.futures.Future[Any],
    executor: concurrent.futures.ThreadPoolExecutor,
    drain_timeout: float,
) -> None:
    if deadline is not None:
        deadline.cancel()
    _drain_worker(future, executor, timeout=drain_timeout)


def _poll_execution_loop(
    future: concurrent.futures.Future[Any],
    start_time: float,
    deadline_seconds: float,
    heartbeat_interval: float,
    operation_name: str,
    abort_event: Optional[Any],
) -> T:
    last_heartbeat = start_time
    while True:  # timeout bounded by deadline and remaining_timeout
        if _is_aborted(abort_event):
            raise SiblingPassAbortedError(
                f"{operation_name} aborted because a sibling task failed terminally.")
        rem = _check_execution_deadline(start_time, deadline_seconds, operation_name)
        wait_step = min(heartbeat_interval, max(0.01, rem))
        try:
            result = future.result(timeout=wait_step)
            _check_execution_deadline(start_time, deadline_seconds, operation_name)
            if _is_aborted(abort_event):
                raise SiblingPassAbortedError(
                    f"{operation_name} aborted because a sibling task failed terminally.")
            return result
        except concurrent.futures.TimeoutError:
            if future.done():
                raise
            last_heartbeat = _emit_heartbeat_if_due(
                time.monotonic(), last_heartbeat, start_time, heartbeat_interval,
                operation_name, deadline_seconds,
            )


def execute_with_deadline_and_heartbeat(
    fn: Callable[..., T],
    *args: Any,
    deadline_seconds: float = 360.0,
    heartbeat_interval: float = 15.0,
    operation_name: str = "External Operation",
    abort_event: Optional[Any] = None,
    **kwargs: Any,
) -> T:
    """
    Execute callable `fn(*args, **kwargs)` with an absolute deadline and live heartbeat telemetry.
    Propagates the request deadline into the worker. Timeout stops waiting and
    cooperatively prevents new retries; an in-flight socket must honor its timeout.

    Args:
        fn: Target callable to execute.
        *args: Positional arguments for `fn`.
        deadline_seconds: Hard ceiling in seconds before raising TimeoutError (default: 360s).
        heartbeat_interval: Seconds between live heartbeat progress logs (default: 15s).
        operation_name: High-signal label for log output (e.g. 'L2-Stage03 Graph Extraction').
        abort_event: Optional threading.Event to monitor for sibling aborts.
        **kwargs: Keyword arguments for `fn`.

    Returns:
        Result of `fn(*args, **kwargs)`.

    Raises:
        TimeoutError: If execution exceeds `deadline_seconds`.
        SiblingPassAbortedError: If abort_event is set by a sibling task.
        Exception: Any exception raised by `fn`.
    """
    from config.request_deadline import RequestDeadlineExceeded, deadline_scope
    with deadline_scope(deadline_seconds) as deadline:
        start_time = time.monotonic()
        executor = BoundedThreadPoolExecutor(max_workers=1, thread_name_prefix="transport_worker")
        future = executor.submit(copy_context().run, fn, *args, **kwargs)
        drain_timeout = min(0.2, max(0.05, deadline_seconds))
        try:
            return _poll_execution_loop(
                future, start_time, deadline_seconds, heartbeat_interval,
                operation_name, abort_event,
            )
        except RequestDeadlineExceeded as rde:
            _abort_and_drain(deadline, future, executor, drain_timeout)
            raise TimeoutError(f"{operation_name} exceeded hard deadline of {deadline_seconds}s") from rde
        except BaseException:
            _abort_and_drain(deadline, future, executor, drain_timeout)
            raise
        finally:
            if not future.done():
                _drain_worker(future, executor, timeout=0.05)


def urllib_json_request(
    url: str,
    *,
    method: str = "POST",
    headers: Optional[dict[str, str]] = None,
    payload_bytes: Optional[bytes] = None,
    timeout: float = 30.0,
) -> Tuple[int, dict[str, Any]]:
    """Execute a deadline-bounded HTTP JSON request via standard library urllib."""
    import json
    import urllib.request
    from config.request_deadline import remaining_timeout

    eff_timeout = remaining_timeout(timeout)
    req = urllib.request.Request(
        url,
        data=payload_bytes,
        method=method,
        headers=headers or {},
    )
    with urllib.request.urlopen(req, timeout=eff_timeout) as resp:
        raw = resp.read().decode("utf-8")
        data = json.loads(raw) if raw.strip() else {}
        return int(getattr(resp, "status", 200)), data if isinstance(data, dict) else {}


