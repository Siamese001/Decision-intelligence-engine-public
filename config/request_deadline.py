"""Cooperative monotonic budgets shared by an operation and all of its retries."""
from __future__ import annotations

import math
import os
import socket
import ssl
import threading
import time
import weakref
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Iterator


class RequestDeadlineExceeded(TimeoutError):
    """No further model calls, retries or publication may start in this scope."""


def _close_single_socket(sock: Any) -> None:
    targets = [sock]
    underlying = getattr(sock, "_sock", None)
    if underlying is not None and underlying is not sock:
        targets.append(underlying)
    for target in targets:
        try:
            target.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            target.close()
        except Exception:
            pass


_SUSPEND_DEPTH: ContextVar[int] = ContextVar("request_deadline_suspend_depth", default=0)
_SUSPEND_START: ContextVar[float | None] = ContextVar("request_deadline_suspend_start", default=None)
_SUSPEND_LIMIT: ContextVar[float | None] = ContextVar("request_deadline_suspend_limit", default=None)


def _current_in_flight_suspend(now: float) -> float:
    suspend_start = _SUSPEND_START.get()
    if suspend_start is None:
        return 0.0
    in_flight = max(0.0, now - suspend_start)
    limit = _SUSPEND_LIMIT.get()
    return min(in_flight, limit) if limit is not None else in_flight


@dataclass
class Deadline:
    expires_at: float
    parent: Deadline | None = None
    cancelled: Event = field(default_factory=Event)
    _sockets: weakref.WeakSet[Any] = field(default_factory=weakref.WeakSet)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    suspended_seconds: float = 0.0

    def register_socket(self, sock: Any) -> None:
        with self._lock:
            self._sockets.add(sock)
        if self.parent is not None:
            self.parent.register_socket(sock)

    def unregister_socket(self, sock: Any) -> None:
        with self._lock:
            self._sockets.discard(sock)
        if self.parent is not None:
            self.parent.unregister_socket(sock)

    def abort_sockets(self) -> int:
        """Forcefully shut down and close all active sockets registered to this deadline."""
        with self._lock:
            socks = list(self._sockets)
            self._sockets.clear()
        if self.parent is not None:
            for s in socks:
                self.parent.unregister_socket(s)
        for s in socks:
            _close_single_socket(s)
        return len(socks)

    def cancel(self) -> None:
        """Mark deadline cancelled and immediately abort all registered active sockets."""
        self.cancelled.set()
        self.abort_sockets()

    def extend_by(self, delta_seconds: float) -> None:
        """Shifts deadline expiration outward by delta_seconds (e.g. for interactive HITL wait)."""
        with self._lock:
            self.expires_at += max(0.0, float(delta_seconds))
        if self.parent is not None:
            self.parent.extend_by(delta_seconds)

    def remaining(self) -> float:
        now = time.monotonic()
        remaining = (self.expires_at + _current_in_flight_suspend(now)) - now
        if self.parent is not None:
            remaining = min(remaining, self.parent.remaining())
        if self.cancelled.is_set() or remaining <= 0:
            self.cancel()
            raise RequestDeadlineExceeded("Request deadline exhausted; further work is prohibited")
        return remaining


_CURRENT: ContextVar[Deadline | None] = ContextVar("request_deadline", default=None)

_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_SOCKET_CLOSE = socket.socket.close
_ORIG_SOCKET_SEND = socket.socket.send
_ORIG_SOCKET_SENDALL = socket.socket.sendall
_ORIG_SOCKET_RECV = socket.socket.recv
_ORIG_SOCKET_RECV_INTO = socket.socket.recv_into
_ORIG_SSL_WRAP = ssl.SSLContext.wrap_socket
_ORIG_SSL_SEND = ssl.SSLSocket.send
_ORIG_SSL_SENDALL = ssl.SSLSocket.sendall
_ORIG_SSL_RECV = ssl.SSLSocket.recv
_ORIG_SSL_RECV_INTO = ssl.SSLSocket.recv_into
_ORIG_SSL_READ = ssl.SSLSocket.read
_SOCKET_HOOK_INSTALLED = False
_HOOK_LOCK = threading.Lock()


def _register_current_socket(sock: Any) -> None:
    curr = _CURRENT.get()
    if curr is not None:
        curr.register_socket(sock)


def _unregister_current_socket(sock: Any) -> None:
    curr = _CURRENT.get()
    if curr is not None:
        curr.unregister_socket(sock)


def _hooked_socket_connect(sock_self: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        res = _ORIG_SOCKET_CONNECT(sock_self, *args, **kwargs)
    except BlockingIOError:
        _register_current_socket(sock_self)
        raise
    else:
        _register_current_socket(sock_self)
        return res


def _hooked_socket_close(sock_self: Any, *args: Any, **kwargs: Any) -> Any:
    _unregister_current_socket(sock_self)
    return _ORIG_SOCKET_CLOSE(sock_self, *args, **kwargs)


def _hooked_socket_send(sock_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(sock_self)
    return _ORIG_SOCKET_SEND(sock_self, *args, **kwargs)


def _hooked_socket_sendall(sock_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(sock_self)
    return _ORIG_SOCKET_SENDALL(sock_self, *args, **kwargs)


def _hooked_socket_recv(sock_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(sock_self)
    check_deadline()
    return _ORIG_SOCKET_RECV(sock_self, *args, **kwargs)


def _hooked_socket_recv_into(sock_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(sock_self)
    check_deadline()
    return _ORIG_SOCKET_RECV_INTO(sock_self, *args, **kwargs)


def _hooked_ssl_wrap(ssl_self: Any, *args: Any, **kwargs: Any) -> Any:
    res = _ORIG_SSL_WRAP(ssl_self, *args, **kwargs)
    orig_sock = args[0] if args else kwargs.get("sock")
    if orig_sock is not None:
        _unregister_current_socket(orig_sock)
    _register_current_socket(res)
    return res


def _hooked_ssl_send(ssl_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(ssl_self)
    return _ORIG_SSL_SEND(ssl_self, *args, **kwargs)


def _hooked_ssl_sendall(ssl_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(ssl_self)
    return _ORIG_SSL_SENDALL(ssl_self, *args, **kwargs)


def _hooked_ssl_recv(ssl_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(ssl_self)
    check_deadline()
    return _ORIG_SSL_RECV(ssl_self, *args, **kwargs)


def _hooked_ssl_recv_into(ssl_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(ssl_self)
    check_deadline()
    return _ORIG_SSL_RECV_INTO(ssl_self, *args, **kwargs)


def _hooked_ssl_read(ssl_self: Any, *args: Any, **kwargs: Any) -> Any:
    _register_current_socket(ssl_self)
    check_deadline()
    return _ORIG_SSL_READ(ssl_self, *args, **kwargs)


def _install_socket_deadline_hooks() -> None:
    global _SOCKET_HOOK_INSTALLED
    with _HOOK_LOCK:
        if _SOCKET_HOOK_INSTALLED:
            return
        socket.socket.connect = _hooked_socket_connect
        socket.socket.close = _hooked_socket_close
        socket.socket.send = _hooked_socket_send
        socket.socket.sendall = _hooked_socket_sendall
        socket.socket.recv = _hooked_socket_recv
        socket.socket.recv_into = _hooked_socket_recv_into
        ssl.SSLContext.wrap_socket = _hooked_ssl_wrap
        ssl.SSLSocket.send = _hooked_ssl_send
        ssl.SSLSocket.sendall = _hooked_ssl_sendall
        ssl.SSLSocket.recv = _hooked_ssl_recv
        ssl.SSLSocket.recv_into = _hooked_ssl_recv_into
        ssl.SSLSocket.read = _hooked_ssl_read
        _SOCKET_HOOK_INSTALLED = True


_install_socket_deadline_hooks()


def _positive_seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Deadline and transport timeout must be finite positive seconds")
    return float(value)


@contextmanager
def deadline_scope(seconds: float | None) -> Iterator[Deadline | None]:
    """Nested operations can shorten the budget, never reset the parent clock.

    Active outbound sockets are tracked and forcibly aborted when a deadline expires
    or is cancelled. Callers must use remaining_timeout on every request and
    cooperative_sleep on every retry.
    """
    parent = _CURRENT.get()
    if seconds is None:
        check_deadline()
        yield parent
        check_deadline()
        return
    duration = _positive_seconds(seconds)
    if parent is not None:
        duration = min(duration, parent.remaining())
    deadline = Deadline(time.monotonic() + duration, parent)
    token = _CURRENT.set(deadline)
    try:
        yield deadline
        deadline.remaining()
    finally:
        _CURRENT.reset(token)


def check_deadline() -> None:
    deadline = _CURRENT.get()
    if deadline is not None:
        deadline.remaining()


def _resolve_effective_suspend_max(max_suspend_seconds: float | None) -> float | None:
    if max_suspend_seconds is not None:
        return max_suspend_seconds
    if os.environ.get("NEE_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return 0.0
    env_val = os.environ.get("NEE_HITL_MAX_SUSPEND_SECONDS")
    if not env_val:
        return 60.0
    try:
        return max(0.0, float(env_val))
    except ValueError:
        return 60.0


def _apply_suspend_elapsed(elapsed: float) -> None:
    curr = _CURRENT.get()
    seen: set[int] = set()
    while curr is not None and id(curr) not in seen:
        seen.add(id(curr))
        with curr._lock:
            curr.expires_at += elapsed
            curr.suspended_seconds += elapsed
        curr = curr.parent


@contextmanager
def suspend_deadline(max_suspend_seconds: float | None = None) -> Iterator[None]:
    """Pause the active monotonic request deadline while waiting on interactive human input."""
    effective_max = _resolve_effective_suspend_max(max_suspend_seconds)
    depth = _SUSPEND_DEPTH.get()
    depth_token = _SUSPEND_DEPTH.set(depth + 1)
    start_token = None
    limit_token = None
    t0 = time.monotonic()
    if depth == 0:
        start_token = _SUSPEND_START.set(t0)
        limit_token = _SUSPEND_LIMIT.set(effective_max)
    try:
        yield
    finally:
        _SUSPEND_DEPTH.reset(depth_token)
        if depth == 0:
            if start_token is not None:
                _SUSPEND_START.reset(start_token)
            if limit_token is not None:
                _SUSPEND_LIMIT.reset(limit_token)
            elapsed = max(0.0, time.monotonic() - t0)
            if effective_max is not None:
                elapsed = min(elapsed, effective_max)
            _apply_suspend_elapsed(elapsed)


pause_deadline_for_hitl = suspend_deadline


def current_suspended_seconds() -> float:
    """Return total monotonic seconds suspended for interactive human input in the active deadline."""
    curr = _CURRENT.get()
    if curr is None:
        return 0.0
    return curr.suspended_seconds + _current_in_flight_suspend(time.monotonic())


def _resolve_stdin_timeout(timeout_seconds: float) -> float:
    if os.environ.get("NEE_NON_INTERACTIVE", "").lower() in ("1", "true", "yes"):
        return 0.0
    env_timeout = os.environ.get("NEE_HITL_STDIN_TIMEOUT_SECONDS")
    if env_timeout:
        try:
            return max(0.0, float(env_timeout))
        except ValueError:
            pass
    return timeout_seconds


def read_stdin_line_with_timeout(
    timeout_seconds: float = 30.0,
    prompt: str = "",
) -> Optional[str]:
    """Read a single line from sys.stdin with a bounded select timeout."""
    import select
    import sys

    if prompt:
        print(prompt, end="", flush=True)
    if not sys.stdin or sys.stdin.closed:
        return None
    effective_timeout = _resolve_stdin_timeout(timeout_seconds)
    if effective_timeout <= 0:
        return None
    try:
        rlist, _, _ = select.select([sys.stdin], [], [], effective_timeout)
        if rlist:
            line = sys.stdin.readline()
            return line.strip() if line else None
        return None
    except (OSError, ValueError, EOFError, KeyboardInterrupt):
        return None


def remaining_timeout(configured_seconds: float) -> float:
    configured = _positive_seconds(configured_seconds)
    deadline = _CURRENT.get()
    return min(configured, deadline.remaining()) if deadline is not None else configured


def cooperative_sleep(seconds: float) -> None:
    """Backoff consumes the same request budget and cannot authorize a new retry."""
    if seconds < 0 or not math.isfinite(seconds):
        raise ValueError("Backoff must be finite nonnegative seconds")
    deadline = _CURRENT.get()
    if deadline is None:
        time.sleep(seconds)
        return
    deadline.cancelled.wait(min(seconds, deadline.remaining()))
    deadline.remaining()


def without_sdk_retries(client, model: str):
    """Copy declared SDK clients while retaining governed completion wrappers."""
    if not callable(getattr(type(client), "with_options", None)):
        return client
    from config.credentials import _guard_client_completions
    return _guard_client_completions(client.with_options(max_retries=0), model)
