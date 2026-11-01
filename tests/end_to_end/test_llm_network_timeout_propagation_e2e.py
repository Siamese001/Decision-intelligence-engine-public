"""End-to-End test for LLM network timeout propagation and socket abortion.

Verifies:
1. End-to-end request deadline propagation across guarded OpenAI completions.
2. Immediate socket abortion and RequestDeadlineExceeded error on deadline exhaustion.
"""

from __future__ import annotations

import socket
import time
from unittest.mock import MagicMock

import pytest

from config.credentials import _guard_client_completions
from config.request_deadline import (
    Deadline,
    RequestDeadlineExceeded,
    deadline_scope,
)


@pytest.mark.timeout(30)
def test_socket_abort_on_deadline_exhaustion_e2e() -> None:
    """E2E flow: registered active sockets are aborted on deadline exhaustion."""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client_sock.connect(("127.0.0.1", port))
    conn, _ = server_sock.accept()

    try:
        deadline = Deadline(time.monotonic() + 0.1)
        deadline.register_socket(client_sock)

        # Before expiry, socket is open
        assert not client_sock._closed

        # Wait for deadline to expire and check remaining
        time.sleep(0.12)
        with pytest.raises(RequestDeadlineExceeded):
            deadline.remaining()

        # Socket should now be aborted and closed
        assert client_sock._closed
    finally:
        server_sock.close()
        conn.close()
        client_sock.close()


@pytest.mark.timeout(30)
def test_guarded_client_e2e_request_deadline_propagation() -> None:
    """E2E flow: LLM client call obeys active deadline_scope and aborts on expiry."""
    mock_client = MagicMock()

    def _slow_create(*args: object, **kwargs: object) -> object:
        # Simulate network delay exceeding request deadline
        time.sleep(0.3)
        return MagicMock(choices=[MagicMock(message=MagicMock(content="{}"))])

    mock_client.chat.completions.create = _slow_create
    guarded = _guard_client_completions(mock_client, default_model="gpt-5.6-terra")

    # Within deadline_scope of 0.1s, the slow completion should trigger deadline exhaustion
    with pytest.raises(RequestDeadlineExceeded):
        with deadline_scope(seconds=0.1):
            guarded.chat.completions.create(messages=[{"role": "user", "content": "test query"}])
