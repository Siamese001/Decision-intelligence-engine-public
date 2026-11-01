"""
Process-Level Network Policy and Zero-Egress Guard.

Enforces hermetic local execution and Law 2 (`INTERNAL_ADVISORY` enclave isolation).
Blocks unauthorized outbound socket connections when zero-egress mode is active.
"""
from __future__ import annotations

import contextlib
import logging
import socket
import threading
from typing import Any, Dict, Iterator, List, Optional, Set

from config.credentials import CredentialManager

logger = logging.getLogger("dual_pipeline.policy.network_policy")

ALLOWED_LOCAL_HOSTS: Set[str] = {
    "localhost",
    "127.0.0.1",
    "::1",
}


class NetworkEgressForbiddenError(RuntimeError):
    """Raised when an outbound network call violates zero-egress policy."""
    pass


class NetworkPolicy:
    """Manages process-wide egress policy and socket interception."""

    def __init__(self, offline_mode_default: bool = False, enforce_zero_egress: bool = False):
        self._offline_mode = (
            offline_mode_default
            or enforce_zero_egress
            or CredentialManager.get_credential("OFFLINE_EXECUTION_MODE", "").lower() in ("1", "true")
        )
        self._lock = threading.RLock()
        self._egress_attempts: List[Dict[str, Any]] = []
        self._socket_interceptor_installed = False
        self._original_connect = socket.socket.connect
        if self._offline_mode:
            self.install_socket_interceptor()

    @contextlib.contextmanager
    def isolated_network_context(self) -> Iterator[NetworkPolicy]:
        """Context manager enforcing zero outbound network egress for this policy instance."""
        prior_state = self.is_offline
        self.set_offline_mode(True)
        try:
            yield self
        finally:
            self.set_offline_mode(prior_state)
            if not prior_state:
                self.uninstall_socket_interceptor()

    @property
    def is_offline(self) -> bool:
        with self._lock:
            return self._offline_mode

    def set_offline_mode(self, enabled: bool) -> None:
        """Enables or disables strict offline mode."""
        with self._lock:
            self._offline_mode = enabled
            if enabled and not self._socket_interceptor_installed:
                self.install_socket_interceptor()
            logger.info(f"[NETWORK_POLICY] Offline mode set to {enabled}.")

    def assert_egress_allowed(self, host: str, port: Optional[int] = None, namespace: str = "CLIENT_SAFE") -> None:
        """
        Validates whether egress to (host, port) is permitted.
        Raises NetworkEgressForbiddenError if offline or if payload is INTERNAL_ADVISORY.
        """
        host_clean = host.lower().strip()

        # Law 2 Invariant: INTERNAL_ADVISORY cannot egress under ANY circumstance
        if namespace == "INTERNAL_ADVISORY":
            self._record_attempt(host, port, namespace, allowed=False, reason="LAW2_INTERNAL_ADVISORY_EGRESS_BLOCKED")
            raise NetworkEgressForbiddenError(
                f"[LAW 2 VIOLATION] Attempted network egress for 'INTERNAL_ADVISORY' payload to {host}:{port}."
            )

        # In offline mode, allow only local loopback interfaces
        if self.is_offline and host_clean not in ALLOWED_LOCAL_HOSTS:
            self._record_attempt(host, port, namespace, allowed=False, reason="OFFLINE_EGRESS_FORBIDDEN")
            raise NetworkEgressForbiddenError(
                f"[ZERO EGRESS] Outbound connection to {host}:{port} is blocked in offline mode."
            )

        self._record_attempt(host, port, namespace, allowed=True, reason="ALLOWED")

    def install_socket_interceptor(self) -> None:
        """Installs a process-wide socket.connect hook to trap outbound connections."""
        with self._lock:
            if self._socket_interceptor_installed:
                return

            original_connect = socket.socket.connect

            def guarded_connect(sock_self, address):
                host = address[0] if isinstance(address, (tuple, list)) and len(address) > 0 else str(address)
                port = address[1] if isinstance(address, (tuple, list)) and len(address) > 1 else None

                # Check policy
                if self.is_offline and str(host).lower() not in ALLOWED_LOCAL_HOSTS:
                    self._record_attempt(str(host), port, "UNKNOWN", allowed=False, reason="SOCKET_CONNECT_BLOCKED")
                    raise NetworkEgressForbiddenError(
                        f"[ZERO EGRESS GUARD] Intercepted unauthorized socket connection to {host}:{port}."
                    )

                return original_connect(sock_self, address)

            socket.socket.connect = guarded_connect
            self._socket_interceptor_installed = True
            logger.info("[NETWORK_POLICY] Zero-egress socket interceptor installed.")

    def uninstall_socket_interceptor(self) -> None:
        """Restores the original socket.connect implementation."""
        with self._lock:
            if self._socket_interceptor_installed:
                socket.socket.connect = self._original_connect
                self._socket_interceptor_installed = False
                logger.info("[NETWORK_POLICY] Zero-egress socket interceptor uninstalled.")

    def _record_attempt(self, host: str, port: Optional[int], namespace: str, allowed: bool, reason: str) -> None:
        import datetime
        record = {
            "host": host,
            "port": port,
            "namespace": namespace,
            "allowed": allowed,
            "reason": reason,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        with self._lock:
            self._egress_attempts.append(record)
            if len(self._egress_attempts) > 500:
                self._egress_attempts = self._egress_attempts[-500:]

    def get_audit_log(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._egress_attempts)

    def reset_audit_log(self) -> None:
        with self._lock:
            self._egress_attempts.clear()


_GLOBAL_POLICY: Optional[NetworkPolicy] = None
_POLICY_LOCK = threading.Lock()


def get_network_policy() -> NetworkPolicy:
    """Returns singleton NetworkPolicy instance."""
    global _GLOBAL_POLICY
    with _POLICY_LOCK:
        if _GLOBAL_POLICY is None:
            _GLOBAL_POLICY = NetworkPolicy()
        return _GLOBAL_POLICY


@contextlib.contextmanager
def enforce_zero_egress() -> Iterator[NetworkPolicy]:
    """Context manager enforcing zero outbound network egress for the duration of the block."""
    policy = get_network_policy()
    prior_state = policy.is_offline
    policy.set_offline_mode(True)
    try:
        yield policy
    finally:
        policy.set_offline_mode(prior_state)
        if not prior_state:
            policy.uninstall_socket_interceptor()


# Governed Aliases
NetworkPolicyGuard = NetworkPolicy
ZeroEgressViolationError = NetworkEgressForbiddenError

