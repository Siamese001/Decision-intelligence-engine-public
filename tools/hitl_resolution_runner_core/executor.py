"""Execution adapter for HITL resolution wave runner."""
from __future__ import annotations

import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple

from tools.hitl_resolution_runner_core.models import ExecutionResult


def _parse_junit_xml(junit_path: Path) -> Tuple[int, int, int, int, int, int, List[str]]:
    """Extracts test counts and test IDs from JUnit XML report."""
    if not junit_path.is_file():
        return 0, 0, 0, 0, 0, 0, []

    tree = ET.parse(junit_path)
    root = tree.getroot()

    passed = 0
    failed = 0
    errors = 0
    skipped = 0
    xfails = 0
    xpasses = 0
    test_ids = []

    # root can be <testsuites> or <testsuite>
    testcases = root.findall(".//testcase")
    for tc in testcases:
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        t_id = f"{classname}::{name}" if classname else name
        test_ids.append(t_id)

        has_failure = tc.find("failure") is not None
        has_error = tc.find("error") is not None
        has_skipped = tc.find("skipped") is not None

        if has_failure:
            failed += 1
        elif has_error:
            errors += 1
        elif has_skipped:
            # Check for xfail marker in skipped text
            skipped_elem = tc.find("skipped")
            msg = (skipped_elem.get("message") or "") + (skipped_elem.text or "")
            if "xfail" in msg.lower():
                xfails += 1
            else:
                skipped += 1
        else:
            passed += 1

    return passed, failed, errors, skipped, xfails, xpasses, test_ids


def execute_wave_tests(
    test_files: List[str],
    repo_root: Path,
    report_path: Path,
    per_test_timeout: int = 60,
    whole_run_timeout: int = 120,
) -> ExecutionResult:
    """Executes declared wave tests via tools/run_tests_with_progress.py with isolated reporting."""
    python_bin = repo_root / ".venv" / "bin" / "python"
    runner_script = repo_root / "tools" / "run_tests_with_progress.py"

    cmd = [
        str(python_bin),
        str(runner_script),
        "--runner",
        "pytest",
        "--timeout",
        str(per_test_timeout),
    ] + test_files

    # Ensure parent dir of report exists
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        report_path.unlink()

    env = dict(os.environ)
    # Sanitize git subshell variables
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(var, None)

    # Inject clean junit report path
    env["PYTEST_ADDOPTS"] = f"--junitxml={report_path}"

    start_time = time.time()
    try:
        proc = subprocess.run(cmd, timeout=whole_run_timeout, cwd=str(repo_root), env=env, capture_output=True, text=True)
        duration = round(time.time() - start_time, 2)
        exit_code = proc.returncode
        stdout = proc.stdout
        stderr = proc.stderr
    except subprocess.TimeoutExpired as e:
        duration = round(time.time() - start_time, 2)
        return ExecutionResult(
            exit_code=124,
            total_collected=0,
            passed_count=0,
            failed_count=1,
            error_count=0,
            skipped_count=0,
            xfail_count=0,
            xpass_count=0,
            test_ids=[],
            stdout=str(e.stdout or ""),
            stderr=f"TimeoutExpired after {whole_run_timeout}s: {e}",
            duration_seconds=duration,
        )

    passed, failed, errors, skipped, xfails, xpasses, test_ids = _parse_junit_xml(report_path)
    total_collected = len(test_ids)

    return ExecutionResult(
        exit_code=exit_code,
        total_collected=total_collected,
        passed_count=passed,
        failed_count=failed,
        error_count=errors,
        skipped_count=skipped,
        xfail_count=xfails,
        xpass_count=xpasses,
        test_ids=test_ids,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
    )
