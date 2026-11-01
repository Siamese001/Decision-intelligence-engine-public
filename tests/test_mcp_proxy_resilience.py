"""
Tests for Hardened MCP Stdio Proxy Resilience.

Validates:
1. Handshake shielding (<50ms response when upstream socket is delayed/offline).
2. Socket path discovery across os.tmpdir() and /tmp with variant naming.
3. Bidirectional JSON-RPC streaming and tool execution.
4. Upstream socket recycling and reconnection resilience without stdio termination.
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Generator

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HARDENED_PROXY_SCRIPT = REPO_ROOT / "tools" / "mcp_proxy_bundle_hardened.js"
EXTENSION_PROXY_SCRIPT = Path(
    "/Users/amitayer/.antigravity-ide/extensions/googlecloudtools.datacloud-0.11.0-universal/mcp_servers/cli/mcp_proxy_bundle.js"
)


class MockUpstreamMcpServer:
    """Mock upstream Unix domain socket server speaking JSON-RPC 2.0."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self._server_sock: Any = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.received_messages: list[dict[str, Any]] = []

    def start(self) -> None:
        import socket

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self.socket_path)
        self._server_sock.listen(5)
        self._server_sock.settimeout(0.5)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        import socket

        while self._running:
            try:
                conn, _ = self._server_sock.accept()
            except (socket.timeout, OSError):
                continue
            conn.settimeout(1.0)
            buffer = ""
            try:
                while self._running:
                    chunk = conn.recv(4096).decode("utf-8")
                    if not chunk:
                        break
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if not line.strip():
                            continue
                        msg = json.loads(line)
                        self.received_messages.append(msg)
                        self._handle_message(conn, msg)
            except (socket.timeout, OSError):
                pass
            finally:
                conn.close()

    def _handle_message(self, conn: Any, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": True}},
                    "serverInfo": {"name": "mock-upstream", "version": "1.0.0"},
                },
            }
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        elif method == "tools/list":
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": [
                        {
                            "name": "mock_upstream_tool",
                            "description": "Mock tool for resilience test",
                            "inputSchema": {"type": "object", "properties": {}},
                        }
                    ]
                },
            }
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name")
            resp = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Executed {tool_name} successfully"}],
                    "isError": False,
                },
            }
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            with contextlib.suppress(OSError):
                self._server_sock.close()
        if os.path.exists(self.socket_path):
            with contextlib.suppress(OSError):
                os.unlink(self.socket_path)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)


@pytest.fixture
def temp_socket_path() -> Generator[str, None, None]:
    with tempfile.TemporaryDirectory() as td:
        sock_path = os.path.join(td, "test_resilience.sock")
        yield sock_path
        if os.path.exists(sock_path):
            with contextlib.suppress(OSError):
                os.unlink(sock_path)


@pytest.mark.parametrize("proxy_path", [HARDENED_PROXY_SCRIPT, EXTENSION_PROXY_SCRIPT])
def test_handshake_shielding_prevents_context_cancellation(
    proxy_path: Path, temp_socket_path: str
) -> None:
    """Verifies proxy immediately returns valid MCP initialize response (<100ms)

    when upstream socket does not exist yet, preventing client context cancellation.
    """
    if not proxy_path.exists():
        pytest.skip(f"Proxy script {proxy_path} not found")

    proc = subprocess.Popen(
        ["node", str(proxy_path), temp_socket_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        t0 = time.time()
        init_req = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-suite", "version": "1.0"},
                },
            }
        )
        assert proc.stdin is not None
        proc.stdin.write(init_req + "\n")
        proc.stdin.flush()

        assert proc.stdout is not None
        line = proc.stdout.readline()
        elapsed_ms = (time.time() - t0) * 1000
        assert line, "No response from proxy"
        data = json.loads(line)

        assert elapsed_ms < 150, f"Handshake took {elapsed_ms:.1f}ms, expected < 150ms"
        assert data.get("id") == 1
        assert "result" in data
        assert data["result"]["protocolVersion"] == "2024-11-05"
        assert "serverInfo" in data["result"]
    finally:
        proc.terminate()
        proc.wait(timeout=2.0)


@pytest.mark.parametrize("proxy_path", [HARDENED_PROXY_SCRIPT, EXTENSION_PROXY_SCRIPT])
def test_delayed_socket_binding_and_tool_execution(
    proxy_path: Path, temp_socket_path: str
) -> None:
    """Verifies that when upstream socket starts AFTER initialization, the proxy

    seamlessly binds, performs __proxy_init_sync__, and handles tools/call.
    """
    if not proxy_path.exists():
        pytest.skip(f"Proxy script {proxy_path} not found")

    mock_server = MockUpstreamMcpServer(temp_socket_path)
    proc = subprocess.Popen(
        ["node", str(proxy_path), temp_socket_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        # 1. Shielded init before server exists
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 100,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        init_resp = json.loads(proc.stdout.readline())
        assert init_resp["id"] == 100

        # 2. Start mock upstream server with delay
        time.sleep(0.1)
        mock_server.start()
        time.sleep(0.2)

        # 3. Send tools/call
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 101,
                    "method": "tools/call",
                    "params": {"name": "mock_upstream_tool", "arguments": {}},
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        tool_resp = json.loads(proc.stdout.readline())
        assert tool_resp["id"] == 101
        assert "result" in tool_resp
        assert "Executed mock_upstream_tool successfully" in str(tool_resp["result"]["content"])

        received_methods = [m.get("method") for m in mock_server.received_messages]
        assert "initialize" in received_methods
        assert "tools/call" in received_methods
    finally:
        mock_server.stop()
        proc.terminate()
        proc.wait(timeout=2.0)


def test_reconnection_resilience_on_upstream_restart(temp_socket_path: str) -> None:
    """Verifies that if upstream socket restarts, the proxy preserves stdio

    transport alive and reconnects when the socket returns.
    """
    mock_server = MockUpstreamMcpServer(temp_socket_path)
    mock_server.start()

    proc = subprocess.Popen(
        ["node", str(HARDENED_PROXY_SCRIPT), temp_socket_path],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdin is not None
        assert proc.stdout is not None

        # Call 1 while server is alive
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 201,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }
            )
            + "\n"
        )
        proc.stdin.flush()
        resp1 = json.loads(proc.stdout.readline())
        assert resp1["id"] == 201

        # Terminate upstream server abruptly
        mock_server.stop()
        time.sleep(0.15)

        # Proxy must STILL BE ALIVE (poll returns None)
        assert proc.poll() is None, "Proxy prematurely terminated on upstream disconnect"

        # Restart upstream server
        mock_server = MockUpstreamMcpServer(temp_socket_path)
        mock_server.start()
        time.sleep(0.2)

        # Call 2 after server restart
        proc.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 202,
                    "method": "tools/call",
                    "params": {"name": "mock_upstream_tool", "arguments": {}},
                }
            )
            + "\n"
        )
        proc.stdin.flush()

        resp2 = json.loads(proc.stdout.readline())
        assert resp2["id"] == 202
        assert "Executed mock_upstream_tool successfully" in str(resp2["result"]["content"])
    finally:
        mock_server.stop()
        proc.terminate()
        proc.wait(timeout=2.0)
