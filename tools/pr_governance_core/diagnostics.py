"""
tools/pr_governance_core/diagnostics.py

Failed-stage diagnostics extraction and durable logging.
Retains failure IDs, bounded error tail, full-log path, and log digest.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


def extract_failure_ids(stage_idx: int, stage_name: str, output: str) -> List[str]:
    """Extracts failure identifiers from stage name and output."""
    ids: Set[str] = set()

    # 1. Deterministic stage-based ID
    clean_name = re.sub(r"[^A-Za-z0-9_]+", "_", stage_name).strip("_").upper()
    ids.add(f"STAGE_{stage_idx}_{clean_name}")

    # 2. Pytest test failure node IDs
    for match in re.finditer(r"(?:FAILED|ERROR)\s+([a-zA-Z0-9_\-\./]+::[a-zA-Z0-9_\[\]\-\./]+)", output):
        ids.add(match.group(1))

    # 3. Linter rule IDs and error codes
    for match in re.finditer(r"\b(INV-[A-Z0-9_-]+|TID\d{3}|[EFWC]\d{3,4}|C901)\b", output):
        ids.add(match.group(1))

    # 4. AST decoupling / budget violations
    for match in re.finditer(r"\b(BUDGET_OVERSIZED|COMPLEXITY_OVER_LIMIT|AST_IMPORT_VIOLATION)\b", output):
        ids.add(match.group(1))

    return sorted(list(ids))


def compute_bounded_error_tail(output: str, max_lines: int = 30, max_chars: int = 2000) -> str:
    """Retains the trailing error lines where tracebacks and failure summaries reside."""
    if not output:
        return ""
    lines = output.strip().splitlines()
    tail_lines = lines[-max_lines:] if len(lines) > max_lines else lines
    tail = "\n".join(tail_lines)
    if len(tail) > max_chars:
        tail = "..." + tail[-max_chars:]
    return tail


def write_full_stage_log(
    stage_idx: int,
    stage_name: str,
    output: str,
    repo_root: Path,
    branch_slug: Optional[str] = None,
) -> Tuple[Path, str]:
    """Writes full stage output to reports/readiness/logs/<slug>/stage_XX.log and returns (abs_path, sha256)."""
    slug = branch_slug or "current"
    log_dir = repo_root / "reports" / "readiness" / "logs" / slug
    log_dir.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", stage_name).strip("_").lower()[:40]
    log_file = (log_dir / f"stage_{stage_idx:02d}_{safe_name}.log").resolve()
    log_file.write_text(output, encoding="utf-8")
    digest = hashlib.sha256(output.encode("utf-8")).hexdigest()
    return log_file, digest


def format_failed_stage_diagnostics(
    stage_idx: int,
    stage_name: str,
    cmd: List[str],
    output: str,
    repo_root: Path,
    branch_slug: Optional[str] = None,
) -> Dict[str, Any]:
    """Produces the structured diagnostics payload for a failed readiness stage."""
    failure_ids = extract_failure_ids(stage_idx, stage_name, output)
    error_tail = compute_bounded_error_tail(output)
    abs_log_path, digest = write_full_stage_log(stage_idx, stage_name, output, repo_root, branch_slug)
    return {
        "failure_ids": failure_ids,
        "error_tail": error_tail,
        "full_log_path": str(abs_log_path.resolve()),
        "log_digest": digest,
    }
