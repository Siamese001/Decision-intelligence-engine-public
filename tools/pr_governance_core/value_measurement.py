"""Validate branch-bound before/after benefit receipts for PR queue Value."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from tools.pr_governance_core.common import slugify_branch


@dataclass(frozen=True)
class MeasuredBenefit:
    statement: str
    receipt_path: Path
    category: str


def _finite_number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _diff_tree_hash(root: Path, base: str, head: str) -> Optional[str]:
    """Compute git diff tree hash for base..head."""
    try:
        res = subprocess.run(
            ["git", "diff-tree", "-r", "--no-commit-id", base, head],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            return hashlib.sha256(res.stdout.encode("utf-8")).hexdigest()
    except Exception:
        pass
    return None


def load_measured_benefit(
    branch: str, branch_sha: str, base_sha: str, repo_root: Optional[Path],
    worktree_path: Optional[str],
) -> Optional[MeasuredBenefit]:
    """Accept only a passing, same-revision quantitative comparison."""
    if len(branch_sha) != 40 or len(base_sha) != 40:
        return None
    roots = [Path(worktree_path)] if worktree_path else []
    if repo_root:
        roots.append(repo_root)
    name = f"benefit_measurement_{slugify_branch(branch)}_receipt.json"
    for root in roots:
        path = root / "reports" / name
        try:
            usable = path.is_file() and not path.is_symlink() and path.stat().st_size <= 1_000_000
        except OSError:
            usable = False
        if not usable:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or any(data.get(key) != expected for key, expected in (
            ("receipt_type", "pr_queue_benefit_measurement"), ("terminal_outcome", "PASS"),
            ("branch", branch), ("branch_sha", branch_sha),
        )):
            continue

        receipt_base_sha = str(data.get("base_sha", ""))
        base_matches = (receipt_base_sha == base_sha)
        if not base_matches and len(receipt_base_sha) == 40 and len(base_sha) == 40:
            receipt_diff_hash = data.get("diff_tree_hash")
            current_diff_hash = _diff_tree_hash(root, base_sha, branch_sha)
            if receipt_diff_hash and current_diff_hash and receipt_diff_hash == current_diff_hash:
                base_matches = True
            elif current_diff_hash:
                receipt_calculated_diff = _diff_tree_hash(root, receipt_base_sha, branch_sha)
                if receipt_calculated_diff and receipt_calculated_diff == current_diff_hash:
                    base_matches = True
        if not base_matches:
            continue

        before = _finite_number(data.get("baseline_value"))
        after = _finite_number(data.get("observed_value"))
        direction = data.get("benefit_direction")
        metric, unit, scope = (str(data.get(key, "")).strip() for key in ("metric_name", "unit", "scope"))
        category = str(data.get("benefit_category", "UNKNOWN"))
        if before is None or after is None or not all((metric, unit, scope)):
            continue
        if direction not in {"increase", "decrease"} or (after - before) * (1 if direction == "increase" else -1) <= 0:
            continue
        if category not in {
            "COST_REDUCTION",
            "LATENCY_IMPROVEMENT",
            "GRAPH_INTEGRITY",
            "INTEGRITY_PROTECTION",
            "FEATURE_AND_DELIVERABLE",
            "FEATURE_CAPABILITY",
            "CLIENT_DELIVERABLE",
            "HOUSEKEEPING",
        }:
            continue
        statement = f"{metric} improved from {before:g} to {after:g} {unit} for {scope}."
        return MeasuredBenefit(statement, path.resolve(), category)
    return None
