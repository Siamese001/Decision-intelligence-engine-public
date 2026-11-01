"""NEE Knowledge Graph MCP Server.

Exposes governed read-only knowledge graph queries, intake status,
active pair inspection, and runtime evidence operations as native Model Context
Protocol (MCP) tools over standard I/O.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from mcp.server.mcpserver import MCPServer

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
_bin_name = "Scripts/python.exe" if os.name == "nt" else "bin/python"
_venv_bin = WORKSPACE_ROOT / ".venv" / _bin_name
PYTHON_EXE = str(_venv_bin) if _venv_bin.exists() else sys.executable
NEE_CLI = str(WORKSPACE_ROOT / "nee.py")

server = MCPServer(
    name="nee-knowledge-graph",
    instructions="NEE Knowledge Graph governed server — provides read-only graph query, intake status, active pair, and runtime evidence operations."
)


def _run_cli_command(args: list[str], timeout: int = 120) -> str:
    """Execute a CLI command within the workspace and return formatted output."""
    cmd = [PYTHON_EXE] + args
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    venv_dir = WORKSPACE_ROOT / ".venv"
    if venv_dir.is_dir():
        env["VIRTUAL_ENV"] = str(venv_dir)
        bin_dir = str(venv_dir / ("Scripts" if os.name == "nt" else "bin"))
        env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        if proc.returncode == 0:
            return stdout or "Command succeeded with no output."
        error_detail = f"{stdout}\n{stderr}".strip() if (stdout and stderr) else (stdout or stderr)
        return f"Error (exit code {proc.returncode}):\n{error_detail}"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout} seconds."
    except Exception as exc:
        return f"Error executing command: {exc}"


@server.tool(name="nee_query")
def nee_query(
    question: str,
    materiality: str = "medium",
    query_type: Optional[str] = None,
    allow_dev_bge_m3: bool = True,
    human: bool = True,
) -> str:
    """Query the NEE knowledge graph via the governed read-only query pipeline.

    Args:
        question: Natural language query to evaluate against the knowledge graph.
        materiality: Materiality rating ('low', 'medium', 'high'). Defaults to 'medium'.
        query_type: Query category ('exact_lookup', 'current_state', 'current_state_corroboration',
                    'relationship_explanation', 'cross_workstream_synthesis', 'gap_risk_inconsistency',
                    'opportunity_nba', 'candidate_promotion_review'). Defaults to None (auto-routed).
        allow_dev_bge_m3: Permit BGE-M3 embeddings in development. Defaults to True.
        human: Render executive prose with footnote references. Defaults to True.
    """
    args = [
        NEE_CLI,
        "ask",
        "--question",
        question,
        "--materiality",
        materiality,
    ]
    if query_type and query_type.strip().lower() != "auto":
        args.extend(["--query-type", query_type.strip()])
    if human:
        args.append("--human")
    if allow_dev_bge_m3:
        args.extend(["--embedding-backend", "bge-m3-dev", "--allow-dev-bge-m3"])
    return _run_cli_command(args, timeout=180)


@server.tool(name="nee_intake_status")
def nee_intake_status(run_id: str) -> str:
    """Check the status of an intake execution run by its run ID.

    Args:
        run_id: The run ID of the intake pipeline execution to inspect.
    """
    args = [NEE_CLI, "intake-status", "--run-id", run_id]
    return _run_cli_command(args, timeout=60)


@server.tool(name="nee_intake_directive_status")
def nee_intake_directive_status(directive_id: str) -> str:
    """Check the status of an ingestion directive, including its current lifecycle state and bound packages.

    Args:
        directive_id: The directive ID to inspect.
    """
    args = [NEE_CLI, "intake-directive-status", "--directive-id", directive_id]
    return _run_cli_command(args, timeout=60)


@server.tool(name="nee_runtime_evidence_inventory")
def nee_runtime_evidence_inventory() -> str:
    """Run the runtime evidence inventory, returning active pairs, models, generations, and evaluation bindings."""
    args = ["-m", "dual_pipeline.runtime_evidence", "inventory"]
    return _run_cli_command(args, timeout=60)


@server.tool(name="nee_active_pair")
def nee_active_pair() -> str:
    """Show the currently active graph/index pair, generation ID, graph hash, sidecar hash, and runtime root."""
    try:
        inv_raw = _run_cli_command(["-m", "dual_pipeline.runtime_evidence", "inventory"], timeout=60)
        inv = json.loads(inv_raw)
        pairs = inv.get("runtime_pairs", [])
        if not pairs:
            return "No active graph/index pairs discovered in the runtime inventory."
        # Return summary of discovered runtime pairs
        return json.dumps(pairs, indent=2)
    except Exception as exc:
        return f"Error retrieving active pair: {exc}"


@server.tool(name="nee_semantic_cache_status")
def nee_semantic_cache_status() -> str:
    """Inspect the runtime state and configuration of the NEE semantic cache."""
    args = [NEE_CLI, "semantic-cache-status"]
    return _run_cli_command(args, timeout=60)


@server.tool(name="nee_validate_runtime")
def nee_validate_runtime() -> str:
    """Run runtime and active-pair validation checks against the current environment."""
    args = [NEE_CLI, "validate-runtime"]
    return _run_cli_command(args, timeout=120)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
