"""Adversarial and boundary test suite for hermetic MCP server execution.

Covers:
1. Boundary payloads: empty configs, missing executable fallback, empty args.
2. Malformed input: truncated JSON, invalid server structures.
3. Invariant bypass attempts: offline execution with zero network access.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from tools.verify_mcp_servers import (
    audit_server_config,
    check_command_binary,
    run_offline_help_smoke,
    verify_mcp_config_file,
)


@pytest.mark.unit
@pytest.mark.adversarial
def test_check_command_binary_nonexistent() -> None:
    """Boundary test: non-existent command path returns clean failure without crash."""
    ok, msg = check_command_binary("/nonexistent/bin/fake_mcp_server_12345")
    assert not ok
    assert "does not exist" in msg or "not found" in msg


@pytest.mark.unit
@pytest.mark.adversarial
def test_check_command_binary_empty_string() -> None:
    """Boundary test: empty string command returns clean failure."""
    ok, msg = check_command_binary("")
    assert not ok
    assert "not found" in msg or "does not exist" in msg


@pytest.mark.unit
@pytest.mark.adversarial
def test_audit_server_config_malformed_structures() -> None:
    """Malformed input test: missing command or invalid command types fail closed."""
    ok1, msg1 = audit_server_config("bad1", {})
    assert not ok1
    assert "missing valid 'command'" in msg1

    ok2, msg2 = audit_server_config("bad2", {"command": 12345})  # type: ignore[dict-item]
    assert not ok2
    assert "missing valid 'command'" in msg2

    ok3, msg3 = audit_server_config(
        "bad3",
        {"command": sys.executable, "args": ["/nonexistent/script_path_987.py"]},
    )
    assert not ok3
    assert "script path does not exist" in msg3


@pytest.mark.unit
@pytest.mark.adversarial
def test_verify_mcp_config_file_corrupted_json(tmp_path: Path) -> None:
    """Malformed input test: truncated/corrupted JSON file returns error cleanly."""
    corrupted_file = tmp_path / "corrupted_mcp.json"
    corrupted_file.write_text("{\"mcpServers\": { unclosed", encoding="utf-8")

    ok, errors = verify_mcp_config_file(corrupted_file)
    assert not ok
    assert any("Failed to read/parse" in err for err in errors)


@pytest.mark.unit
@pytest.mark.adversarial
def test_verify_mcp_config_file_boundary_empty(tmp_path: Path) -> None:
    """Boundary test: config file with empty servers dictionary passes without error."""
    empty_file = tmp_path / "empty_mcp.json"
    empty_file.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    ok, messages = verify_mcp_config_file(empty_file)
    assert ok
    assert len(messages) == 0


@pytest.mark.unit
@pytest.mark.adversarial
def test_mcp_server_fetch_offline_execution() -> None:
    """Invariant bypass test: mcp-server-fetch executes offline with UV_OFFLINE=1."""
    venv_bin = Path(sys.executable).parent / "mcp-server-fetch"
    if not venv_bin.exists():
        pytest.skip("mcp-server-fetch not installed in active environment")

    ok, msg = run_offline_help_smoke(str(venv_bin))
    assert ok, f"Offline execution failed: {msg}"
    assert "Offline smoke passed" in msg
