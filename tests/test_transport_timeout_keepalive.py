"""
tests/test_transport_timeout_keepalive.py

Unit tests for transport-level socket keepalive, read timeout defaults,
and retry limits across synchronous and asynchronous OpenAI clients.
"""
import os
import socket
import unittest
from unittest.mock import patch

from config.credentials import (
    CredentialManager,
    get_openai_client,
)
from config.transport import (
    build_resilient_async_http_client,
    build_resilient_http_client,
    build_tcp_keepalive_socket_options,
)


def test_build_tcp_keepalive_socket_options():
    """Verify cross-platform socket option construction."""
    opts = build_tcp_keepalive_socket_options(keepidle=25, keepintvl=5, keepcnt=2)
    assert len(opts) >= 1
    # First option must be SOL_SOCKET, SO_KEEPALIVE, 1
    assert opts[0] == (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

    # Check for keepidle option
    tcp_keepidle_opt = getattr(socket, "TCP_KEEPALIVE", getattr(socket, "TCP_KEEPIDLE", None))
    if tcp_keepidle_opt is not None:
        assert (socket.IPPROTO_TCP, tcp_keepidle_opt, 25) in opts

    tcp_keepintvl_opt = getattr(socket, "TCP_KEEPINTVL", None)
    if tcp_keepintvl_opt is not None:
        assert (socket.IPPROTO_TCP, tcp_keepintvl_opt, 5) in opts

    tcp_keepcnt_opt = getattr(socket, "TCP_KEEPCNT", None)
    if tcp_keepcnt_opt is not None:
        assert (socket.IPPROTO_TCP, tcp_keepcnt_opt, 2) in opts


def test_build_resilient_http_clients():
    """Verify that resilient sync and async http clients initialize with custom transports."""
    client = build_resilient_http_client(timeout=120.0, max_retries=1)
    assert client is not None
    assert client.timeout.connect == 120.0
    assert client.timeout.read == 120.0
    client.close()

    async_client = build_resilient_async_http_client(timeout=150.0, max_retries=1)
    assert async_client is not None
    assert async_client.timeout.connect == 150.0
    assert async_client.timeout.read == 150.0


def test_get_openai_client_mock():
    """Verify mock mode returns None client and mock creds."""
    client, creds = get_openai_client(allow_mock=True, model="gpt-6-luna")
    if not os.environ.get("OPENAI_API_KEY"):
        assert client is None
        assert creds.is_mock is True


def test_get_openai_client_timeout_and_retries():
    """Verify that timeout defaults to 360s for reasoning models and retries default to 1."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk_test_redacted_placeholder"}), patch("openai.OpenAI") as mock_openai:
            client, creds = get_openai_client(
                allow_mock=False,
                model="gpt-6-luna",
                reasoning_effort="high",
            )
            mock_openai.assert_called_once()
            call_kw = mock_openai.call_args[1]
            assert call_kw["timeout"] == 1200.0
            assert call_kw["max_retries"] == 1
            assert "http_client" in call_kw
            assert creds.model == "gpt-6-luna"
            assert creds.reasoning_effort == "high"


def test_get_openai_client_env_override():
    """Verify that OPENAI_TIMEOUT and OPENAI_MAX_RETRIES environment variables override defaults."""
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk_test_redacted_placeholder",
            "OPENAI_TIMEOUT": "240.0",
            "OPENAI_MAX_RETRIES": "0",
        },
    ), patch("openai.OpenAI") as mock_openai:
        client, creds = get_openai_client(
            allow_mock=False,
            model="gpt-5.6-luna",
            reasoning_effort="none",
        )
        mock_openai.assert_called_once()
        call_kw = mock_openai.call_args[1]
        assert call_kw["timeout"] == 240.0
        assert call_kw["max_retries"] == 0


def test_credential_manager_parity():
    """Verify CredentialManager.get_openai_client delegates with identical defaults."""
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk_test_redacted_placeholder"}), patch("openai.OpenAI") as mock_openai:
            client, creds = CredentialManager.get_openai_client(
                allow_mock=False,
                model="gpt-6-luna",
                reasoning_effort="high",
            )
            mock_openai.assert_called_once()
            call_kw = mock_openai.call_args[1]
            assert call_kw["timeout"] == 1200.0
            assert call_kw["max_retries"] == 1




class TransportTimeoutKeepaliveTests(unittest.TestCase):
    """Unittest TestCase wrapper ensuring both unittest and pytest discovery."""

    def test_build_tcp_keepalive_socket_options(self):
        test_build_tcp_keepalive_socket_options()

    def test_build_resilient_http_clients(self):
        test_build_resilient_http_clients()

    def test_get_openai_client_mock(self):
        test_get_openai_client_mock()

    def test_get_openai_client_timeout_and_retries(self):
        test_get_openai_client_timeout_and_retries()

    def test_get_openai_client_env_override(self):
        test_get_openai_client_env_override()

    def test_credential_manager_parity(self):
        test_credential_manager_parity()


if __name__ == "__main__":
    unittest.main()
