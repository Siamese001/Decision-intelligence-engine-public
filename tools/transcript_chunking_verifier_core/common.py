"""Common utilities and test runner for transcript chunking wave verification."""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logger = logging.getLogger("verify_transcript_chunking")


def sha256_file(path: Path) -> str:
    """Computes SHA-256 hex digest for a file."""
    if not path.exists():
        return "FILE_NOT_FOUND"
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_info() -> Dict[str, Any]:
    """Extracts git commit and working tree status."""
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
        )
        is_dirty = len(status.strip()) > 0
        diff_hash = hashlib.sha256(status.encode("utf-8")).hexdigest()
        return {
            "git_head": head,
            "is_dirty": is_dirty,
            "status_digest": diff_hash,
        }
    except Exception as e:
        return {"git_head": "UNKNOWN", "is_dirty": True, "error": str(e)}


def run_tests_via_progress_runner(test_files: List[str], timeout: int = 60) -> Tuple[int, str]:
    """Runs declared test files via tools/run_tests_with_progress.py."""
    runner_script = ROOT_DIR / "tools/run_tests_with_progress.py"
    cmd = [
        sys.executable,
        str(runner_script),
        "--runner",
        "pytest",
        "--timeout",
        str(timeout),
    ] + test_files

    logger.info("Executing tests via progress runner: %s", " ".join(cmd))
    res = subprocess.run(cmd, cwd=str(ROOT_DIR), text=True, capture_output=True)
    combined = res.stdout + "\n" + res.stderr
    return res.returncode, combined
