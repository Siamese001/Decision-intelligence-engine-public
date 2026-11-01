"""Verification utility for hermetic MCP server executables and offline readiness.

Audits configured MCP servers in mcp_config.json to confirm that executable
commands resolve locally on disk and run without network dependency.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def resolve_config_path(override_path: str | None = None) -> Path:
    """Resolve the active mcp_config.json file path."""
    if override_path:
        return Path(override_path).expanduser().resolve()

    primary = Path.home() / ".gemini" / "config" / "mcp_config.json"
    if primary.exists():
        return primary

    fallback = Path.home() / ".config" / "antigravity" / "mcp_config.json"
    return fallback


def check_command_binary(command_str: str) -> tuple[bool, str]:
    """Verify that a command executable exists locally on disk or in PATH."""
    cmd_path = Path(command_str).expanduser()
    if cmd_path.is_absolute() or "/" in command_str:
        if cmd_path.exists() and os.access(cmd_path, os.X_OK):
            return True, f"Found local executable: {cmd_path}"
        return False, f"Command path does not exist or is not executable: {cmd_path}"

    resolved = shutil.which(command_str)
    if resolved:
        return True, f"Found executable in PATH: {resolved}"
    return False, f"Command not found in PATH: {command_str}"


def audit_server_config(name: str, server_def: dict[str, object]) -> tuple[bool, str]:
    """Audit a single MCP server configuration entry."""
    command = server_def.get("command")
    if not isinstance(command, str) or not command.strip():
        return False, f"Server '{name}' missing valid 'command' string."

    binary_ok, reason = check_command_binary(command)
    if not binary_ok:
        return False, f"Server '{name}': {reason}"

    args = server_def.get("args", [])
    if isinstance(args, list) and args and isinstance(args[0], str) and args[0].endswith(".py"):
        script_path = Path(args[0]).expanduser()
        if not script_path.exists():
            return False, f"Server '{name}' script path does not exist: {script_path}"

    return True, f"Server '{name}': OK ({command})"


def verify_mcp_config_file(config_file: Path) -> tuple[bool, list[str]]:
    """Audit all MCP servers defined in the specified configuration file."""
    if not config_file.exists():
        return False, [f"Config file not found: {config_file}"]

    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"Failed to read/parse {config_file}: {exc}"]

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return False, [f"'mcpServers' object missing or invalid in {config_file}"]

    results: list[str] = []
    all_ok = True
    for s_name, s_def in servers.items():
        if isinstance(s_def, dict):
            ok, msg = audit_server_config(s_name, s_def)
            results.append(msg)
            if not ok:
                all_ok = False
        else:
            all_ok = False
            results.append(f"Server '{s_name}' definition is not an object.")

    return all_ok, results


def run_offline_help_smoke(command: str) -> tuple[bool, str]:
    """Execute a local binary with --help to confirm clean execution."""
    env = os.environ.copy()
    env["UV_OFFLINE"] = "1"
    try:
        proc = subprocess.run(
            [command, "--help"],
            timeout=10,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if proc.returncode == 0:
            return True, f"Offline smoke passed for {command}"
        return False, f"Exit code {proc.returncode}: {proc.stderr[:100]}"
    except Exception as exc:
        return False, f"Execution failed for {command}: {exc}"


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for MCP server verification."""
    parser = argparse.ArgumentParser(description="Verify MCP server executables.")
    parser.add_argument("--config", help="Path to mcp_config.json", default=None)
    parser.add_argument("--smoke", help="Run offline help check on binary", default=None)
    args = parser.parse_args(argv)

    if args.smoke:
        ok, msg = run_offline_help_smoke(args.smoke)
        print(f"[{'PASS' if ok else 'FAIL'}] {msg}")
        return 0 if ok else 1

    cfg_path = resolve_config_path(args.config)
    success, messages = verify_mcp_config_file(cfg_path)
    for line in messages:
        print(f"  {line}")

    if success:
        print(f"\n[PASS] All MCP servers in {cfg_path} verified successfully.")
        return 0

    print(f"\n[FAIL] MCP verification failures detected in {cfg_path}.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
