"""tools/pr_governance_core/plan_ownership.py

Plan ownership discovery and status resolution for PR Queue Prioritization.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.pr_governance_core.common import slugify_branch


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _verified_wave_receipt(
    branch: str, wave: int, head_sha: str, plan_sha: str, repo_root: Path, wt_path: Optional[Path], show_fn: Optional[Any]
) -> Optional[str]:
    """Accept completion only from a passing receipt bound to this branch, HEAD, and plan bytes."""
    candidates = [
        f"reports/waves/wave{wave}.json",
        f"reports/wave{wave}_receipt.json",
        f"reports/wave{wave}_runtime_receipt.json",
        f"reports/implementation/wave{wave}.json",
    ]
    for rel in candidates:
        raw = None
        if wt_path and wt_path.exists() and (wt_path / rel).is_file():
            raw = (wt_path / rel).read_text(encoding="utf-8", errors="replace")
        if not raw and show_fn:
            try:
                raw = show_fn(branch, rel)
            except TypeError:
                raw = show_fn(rel)
        if not raw:
            continue
        try:
            receipt = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        bound_branch = receipt.get("branch") or receipt.get("branch_name")
        bound_head = receipt.get("head_sha") or receipt.get("head_commit_sha")
        bound_plan = receipt.get("plan_sha256") or receipt.get("plan_hash")
        if (
            receipt.get("terminal_outcome") == "PASS"
            and str(receipt.get("wave")) == str(wave)
            and bound_branch == branch
            and bound_head == head_sha
            and bound_plan == plan_sha
        ):
            return f"{rel}#{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"
    return None


def _read_git_status_plans(wt_path: Path) -> List[Tuple[str, Path, str, str]]:
    discovered = []
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(wt_path), capture_output=True, text=True, check=False, timeout=5,
        )
        if res.returncode != 0:
            return discovered
        for line in res.stdout.splitlines():
            rel = line[3:].strip()
            if "plan" in rel.lower() and rel.endswith(".md"):
                p = wt_path / rel
                if p.is_file():
                    discovered.append((rel, p, p.read_text(encoding="utf-8", errors="replace"), "untracked"))
    except Exception:
        pass
    return discovered


def _find_matching_glob_plan(wt_path: Path, tokens: List[str]) -> Optional[Tuple[str, Path, str, str]]:
    for p in wt_path.glob("plans/**/implementation_plan.md"):
        p_str = str(p).lower()
        if any(t in p_str for t in tokens):
            return (str(p.relative_to(wt_path)), p, p.read_text(encoding="utf-8", errors="replace"), "untracked")
    return None


def _find_untracked_plans(wt_path: Path, slug: str) -> List[Tuple[str, Path, str, str]]:
    discovered = _read_git_status_plans(wt_path)
    if discovered:
        return discovered
    scoped = wt_path / f"plans/{slug}/implementation_plan.md"
    if scoped.is_file():
        return [(f"plans/{slug}/implementation_plan.md", scoped, scoped.read_text(encoding="utf-8", errors="replace"), "untracked")]
    tokens = [t for t in slug.split("_") if len(t) > 2 and t not in ("docs", "feat", "chore", "fix", "plan")]
    glob_match = _find_matching_glob_plan(wt_path, tokens)
    if glob_match:
        return [glob_match]
    return []


def _show_git_path(branch: str, repo_root: Path, rel: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "show", f"{branch}:{rel}"],
            cwd=str(repo_root), capture_output=True, text=True, check=False, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    except Exception:
        pass
    return None


def _find_committed_candidate_plans(
    branch: str,
    repo_root: Path,
    candidates: List[str],
    show_fn: Optional[Any],
) -> List[Tuple[str, Path, str, str]]:
    for rel in candidates:
        text = None
        if show_fn:
            try:
                text = show_fn(branch, rel)
            except TypeError:
                text = show_fn(rel)
        if not text:
            text = _show_git_path(branch, repo_root, rel)
        if text:
            return [(rel, repo_root / rel, text, "committed")]
    return []


def _find_committed_ls_tree_plan(
    branch: str,
    repo_root: Path,
    tokens: List[str],
) -> List[Tuple[str, Path, str, str]]:
    try:
        ls_proc = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", branch],
            cwd=str(repo_root), capture_output=True, text=True, check=False, timeout=5,
        )
        if ls_proc.returncode != 0:
            return []
        for line in ls_proc.stdout.splitlines():
            if "plans/" in line and line.endswith("implementation_plan.md"):
                if any(t in line.lower() for t in tokens):
                    text = _show_git_path(branch, repo_root, line)
                    if text:
                        return [(line, repo_root / line, text, "committed")]
    except Exception:
        pass
    return []


def _find_committed_plans(
    branch: str,
    repo_root: Path,
    slug: str,
    show_fn: Optional[Any],
) -> List[Tuple[str, Path, str, str]]:
    candidates = [
        f"plans/{slug}/implementation_plan.md",
        f"plans/{branch.replace('/', '_')}/implementation_plan.md",
    ]
    discovered = _find_committed_candidate_plans(branch, repo_root, candidates, show_fn)
    if discovered:
        return discovered
    tokens = [t for t in slug.split("_") if len(t) > 2 and t not in ("docs", "feat", "chore", "fix", "plan")]
    return _find_committed_ls_tree_plan(branch, repo_root, tokens)


def discover_plan_ownership(
    branch: str,
    repo_root: Path,
    wt_path: Optional[Path],
    show_fn: Optional[Any] = None,
) -> List[Any]:
    """Inspects branch-local committed, modified, and untracked plan documents."""
    from tools.pr_governance_core.queue_snapshot import PlanOwnershipRecord
    from tools.pr_governance_core.wave_status import parse_plan_text

    slug = slugify_branch(branch)
    discovered: List[Tuple[str, Path, str, str]] = []

    if wt_path and wt_path.exists():
        discovered.extend(_find_untracked_plans(wt_path, slug))
    if not discovered:
        discovered.extend(_find_committed_plans(branch, repo_root, slug, show_fn))

    records: List[PlanOwnershipRecord] = []
    for plan_id, path, text, src_type in discovered:
        s_hash = _compute_sha256(text)
        status_map = parse_plan_text(text)
        for wave_id, status in status_map.items():
            head_proc = subprocess.run(["git", "rev-parse", f"refs/heads/{branch}"], cwd=repo_root, capture_output=True, text=True, check=False, timeout=5)
            head_sha = head_proc.stdout.strip() if head_proc.returncode == 0 else ""
            receipt_binding = _verified_wave_receipt(branch, wave_id, head_sha, s_hash, repo_root, wt_path, show_fn)
            rc_bindings = [receipt_binding] if receipt_binding else []
            verified_status = (
                "open" if status == "open"
                else ("verified_done" if rc_bindings else "unverified_done")
            )
            records.append(
                PlanOwnershipRecord(
                    branch_id=branch,
                    plan_id=plan_id,
                    plan_path=str(path),
                    wave_id=wave_id,
                    source_hash=s_hash,
                    source_type=src_type,
                    declared_status=status,
                    verified_status=verified_status,
                    receipt_bindings=rc_bindings,
                )
            )
    return records
