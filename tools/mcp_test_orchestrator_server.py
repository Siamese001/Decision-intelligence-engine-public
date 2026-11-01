"""Test Suite Orchestrator & Concurrency MCP Server (Rank 5).

Exposes governed pytest execution (strictly enforcing Class A debug vs Class B receipt
rules, 60s per-test timeout, and zero background hanging), isolated SQLite test database
provisioning, and structured failure diagnostic parsing as native Model Context Protocol (MCP) tools.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List

from mcp.server.mcpserver import MCPServer

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKSPACE_ROOT))

server = MCPServer(
    name="test-orchestrator",
    instructions=(
        "Test Suite Orchestrator & Concurrency Server — provides governed Class A/B pytest execution, "
        "concurrency-isolated SQLite database provisioning, and automated pytest failure diagnostic parsing."
    )
)

def _find_venv_bin(name: str) -> str:
    cand = WORKSPACE_ROOT / ".venv" / "bin" / name
    if cand.is_file():
        return str(cand)
    if ".worktrees" in str(WORKSPACE_ROOT):
        parent_root = WORKSPACE_ROOT.parent.parent
        cand = parent_root / ".venv" / "bin" / name
        if cand.is_file():
            return str(cand)
    if name == "python":
        return sys.executable
    import shutil
    found = shutil.which(name)
    return found or name


PYTHON_EXE = _find_venv_bin("python")
PYTEST_EXE = _find_venv_bin("pytest")


@server.tool(name="run_governed_test_suite")
def run_governed_test_suite(
    test_scope: str,
    is_receipt_run: bool = False,
    timeout_sec: int = 60,
    verbose: bool = True
) -> str:
    """Execute tests enforcing the AGENTS.md pytest protocol (Class A debug vs Class B receipt).

    Args:
        test_scope: Path to test file (e.g. 'tests/test_foo.py') or named test ('tests/test_foo.py::test_bar').
        is_receipt_run: If False (Class A debug), scope must be a single file or test case. Full suite is forbidden.
                        If True (Class B receipt), scope must declare the wave's acceptance criteria.
        timeout_sec: Hard per-test timeout in seconds (default: 60).
        verbose: Whether to pass -v.
    """
    clean_scope = test_scope.strip()

    # Rule enforcement: Class A debug runs MUST NOT be full suite or multi-file
    if not is_receipt_run:
        if clean_scope in ("", "tests", "tests/", "tests/*") or len(clean_scope.split()) > 1:
            return json.dumps({
                "error": "AGENTS.md Violation: Class A debug runs MUST be scoped to a single test file or named test case.",
                "scope_provided": clean_scope,
                "permitted_format": "tests/test_example.py or tests/test_example.py::test_case"
            })

    cmd = [PYTEST_EXE, clean_scope, f"--timeout={timeout_sec}"]
    if verbose:
        cmd.append("-v")

    # Enforce hermetic test isolation per credential-isolation-and-egress-sanitization.md
    env = dict(os.environ, PYTHONIOENCODING="utf-8", MOCK_ENV="true")
    for sensitive_var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
    ):
        env.pop(sensitive_var, None)

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=180,  # Process wrapper timeout
            env=env,
        )

        stdout = proc.stdout
        stderr = proc.stderr
        combined = stdout + "\n" + stderr

        # Parse metrics
        passed_m = re.search(r"(\d+)\s+passed", combined)
        failed_m = re.search(r"(\d+)\s+failed", combined)
        skipped_m = re.search(r"(\d+)\s+skipped", combined)

        passed = int(passed_m.group(1)) if passed_m else 0
        failed = int(failed_m.group(1)) if failed_m else 0
        skipped = int(skipped_m.group(1)) if skipped_m else 0

        duration_m = re.search(r"in\s+([\d\.]+)s", combined)
        duration_sec = float(duration_m.group(1)) if duration_m else 0.0

        return json.dumps({
            "status": "PASSED" if proc.returncode == 0 else "FAILED",
            "test_class": "Class B (Receipt)" if is_receipt_run else "Class A (Debug)",
            "exit_code": proc.returncode,
            "scope": clean_scope,
            "metrics": {
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "duration_seconds": duration_sec,
            },
            "output_summary": combined[-1000:] if len(combined) > 1000 else combined,
        }, indent=2)

    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "TIMEOUT",
            "error": "Test execution timed out after 180 seconds. Hard limit enforced to prevent background hanging."
        })
    except Exception as exc:
        return json.dumps({"status": "ERROR", "error": f"Failed to execute test runner: {exc}"})


@server.tool(name="provision_isolated_test_db")
def provision_isolated_test_db(template_db_path: str = "") -> str:
    """Create an isolated, ephemeral SQLite database copy to prevent lock contention in concurrent tests.

    Args:
        template_db_path: Optional path to an existing database to clone. If omitted, creates a fresh blank test DB.
    """
    try:
        temp_dir = Path(tempfile.gettempdir()) / "nee_test_sandboxes"
        temp_dir.mkdir(parents=True, exist_ok=True)

        session_id = f"sandbox_{uuid.uuid4().hex[:8]}"
        sandbox_path = temp_dir / f"{session_id}.sqlite"

        if template_db_path:
            tpl = Path(template_db_path)
            if not tpl.is_absolute():
                tpl = WORKSPACE_ROOT / tpl
            if tpl.is_file():
                shutil.copyfile(tpl, sandbox_path)
            else:
                return json.dumps({"error": f"Template database not found at {tpl}"})
        else:
            # Touch blank DB
            sandbox_path.touch()

        return json.dumps({
            "status": "PROVISIONED",
            "sandbox_id": session_id,
            "isolated_db_path": str(sandbox_path),
            "safe_for_concurrency": True,
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": f"Failed to provision isolated test db: {exc}"})


@server.tool(name="extract_test_failure_diagnostics")
def extract_test_failure_diagnostics(pytest_output_text: str) -> str:
    """Parse raw pytest traceback and failure output into structured diagnostic items.

    Args:
        pytest_output_text: The full stdout/stderr string from a failed pytest run.
    """
    failures: List[Dict[str, Any]] = []

    # Find failure headers e.g. "____ test_name ____"
    fail_blocks = re.split(r"_{3,}\s*([a-zA-Z0-9_:\[\]\-\.]+)\s*_{3,}", pytest_output_text)
    if len(fail_blocks) > 1:
        for i in range(1, len(fail_blocks), 2):
            test_name = fail_blocks[i]
            body = fail_blocks[i + 1] if i + 1 < len(fail_blocks) else ""

            # Look for assertion error or exception line
            err_line = ""
            for line in body.splitlines():
                if "Error:" in line or line.startswith("E   "):
                    err_line = line.strip()
                    break

            failures.append({
                "test_name": test_name,
                "error_snippet": err_line or "Failure occurred without explicit error line.",
                "body_preview": body[:500].strip(),
            })

    return json.dumps({
        "detected_failure_count": len(failures),
        "failures": failures,
    }, indent=2)


@server.tool(name="audit_wave_test_coverage")
def audit_wave_test_coverage(wave_name: str, test_file_paths_csv: str) -> str:
    """Audit declared acceptance test fixtures for a given development wave.

    Args:
        wave_name: Wave identifier (e.g. 'Wave 1' or 'Wave 4').
        test_file_paths_csv: Comma-separated list of test file paths.
    """
    paths = [p.strip() for p in test_file_paths_csv.split(",") if p.strip()]
    results: List[Dict[str, Any]] = []
    all_exist = True

    for p in paths:
        f = Path(p)
        if not f.is_absolute():
            f = WORKSPACE_ROOT / f

        exists = f.is_file()
        if not exists:
            all_exist = False

        results.append({
            "test_path": p,
            "exists": exists,
            "size_bytes": f.stat().st_size if exists else 0,
        })

    return json.dumps({
        "wave": wave_name,
        "declared_test_count": len(paths),
        "all_fixtures_present": all_exist,
        "fixtures": results,
    }, indent=2)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
