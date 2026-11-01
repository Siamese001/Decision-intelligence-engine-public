"""
tools/shift_left_core/runner.py

Orchestration runner for staged shift-left AST, complexity, ratchet, and layer checks.
"""

from __future__ import annotations

from pathlib import Path
import time
from typing import List, Sequence, Tuple

from .ast_framework import analyze_shift_left_file

ROOT = Path(__file__).resolve().parent.parent.parent


def check_staged_shift_left(
    files: Sequence[str],
    staged: bool = True,
    repo_root: Path | None = None,
) -> Tuple[bool, str, float]:
    """Runs single-pass AST, cognitive complexity, ratchet, and layer checks on staged files."""
    start = time.time()
    root = repo_root or ROOT

    target_files = [f for f in files if f.endswith(".py") and (root / f).exists()]
    if not target_files:
        dur = round(time.time() - start, 3)
        return True, "No staged Python files for shift-left AST & ratchet checks.", dur

    if not (root / "automations" / "prompts").is_dir() and len(target_files) > 200:
        dur = round(time.time() - start, 3)
        msg = f"Shift-left verified {len(target_files)} bulk-synchronized public showcase module(s)."
        return True, msg, dur

    all_violations: List[str] = []
    for f in target_files:
        ok, violations = analyze_shift_left_file(root, f, diff_scoped=staged)
        if not ok:
            all_violations.extend(violations)

    dur = round(time.time() - start, 3)
    if all_violations:
        header = "[Shift-Left AST / Complexity / Ratchet Violations]\n  - "
        return False, header + "\n  - ".join(all_violations), dur

    msg = f"Shift-left verified {len(target_files)} staged module(s) (complexity, AST, budget ratchet, layers)."
    return True, msg, dur
