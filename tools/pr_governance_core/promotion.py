"""
tools/pr_governance_core/promotion.py

Unified PR promotion workflow tracking the full 10-phase promotion path:
1. instruction_intake
2. preparation
3. commit_hooks
4. rebase
5. readiness
6. review
7. lock_wait
8. merge
9. verification
10. cleanup
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Optional, Tuple

from .latency import PromotionTimeline


def check_diff_syntax(diff_names: list[str], target_cwd: Path) -> Tuple[bool, Optional[str]]:
    """Performs early deterministic syntax prechecks on modified Python and JSON files."""
    for name in diff_names:
        file_path = target_cwd / name
        if not file_path.is_file():
            continue
        if name.endswith(".py"):
            try:
                ast.parse(file_path.read_bytes(), filename=str(file_path))
            except SyntaxError as e:
                return False, f"Python syntax error in {name}:{e.lineno}: {e.msg}"
        elif name.endswith(".json"):
            if name.endswith(".transcript_cache.json"):
                continue
            try:
                json.loads(file_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                return False, f"JSON syntax error in {name}:{e.lineno}: {e.msg}"
    return True, None


def promote_branch(
    branch: str,
    title: str,
    args: Optional[argparse.Namespace] = None,
    repo_root: Optional[Path] = None,
    timeline: Optional[PromotionTimeline] = None,
) -> int:
    """Canonical PR promotion adapter delegating directly to governed cmd_merge."""
    from .lifecycle import cmd_merge

    _ = (repo_root, timeline)
    merge_args = args or argparse.Namespace(
        branch=branch,
        title=title,
        fail_fast=False,
        skip_readiness_run=False,
        recheck_failed_only=False,
        full=False,
        delete_branch=True,
        retry=0,
        llm_summary=False,
    )
    return cmd_merge(merge_args)


def cmd_promote(args: argparse.Namespace) -> int:
    """CLI handler for `pr_governance.py promote <branch> --title <title>` (thin alias to cmd_merge)."""
    from .lifecycle import cmd_merge

    return cmd_merge(args)


def cmd_submit(args: argparse.Namespace) -> int:
    """CLI handler for `pr_governance.py submit <branch> --title <title>` (thin alias to cmd_merge)."""
    from .lifecycle import cmd_merge

    return cmd_merge(args)
