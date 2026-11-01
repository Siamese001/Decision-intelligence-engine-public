"""
dual_pipeline/canonical_pipeline/worktree_seeding.py

Encapsulates artifact and deliverable seeding into isolated worktree targets.
Enforces governance invariants against cross-worktree contamination:
1. Deliverables targeted by --regenerate are strictly excluded from seeding.
2. Unapproved or failed decisions (status != PASS/APPROVED) in primary output are not seeded.
3. Once a worktree is initialized, stale decision state from primary output cannot overwrite it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

logger = logging.getLogger("CanonicalAgenticPipeline.worktree_seeding")


def is_source_decision_passed(src_dir: Path) -> bool:
    """Checks whether the source directory has an approved passing final release decision."""
    src_dec_file = src_dir / "final_release_decision.json"
    if not src_dec_file.is_file():
        return False
    try:
        dec_data = json.loads(src_dec_file.read_text(encoding="utf-8"))
        return dec_data.get("status") in ("PASS", "APPROVED")
    except Exception:
        return False


def build_allowed_seeding_patterns(regen_scope: str, force: bool) -> List[str]:
    """Builds deliverable file patterns permitted to be seeded into a worktree."""
    patterns = [
        "01_*", "02_*", "03_*", "metadata.json", "stage_seals.json",
        "final_release_decision.json", "qa_gates_report.json", "llm_judge_evaluation.json",
    ]
    if "01" in regen_scope or "output_1" in regen_scope:
        patterns = [p for p in patterns if not p.startswith("01_")]
    if "02" in regen_scope or "output_2" in regen_scope:
        patterns = [p for p in patterns if not p.startswith("02_")]
    if "03" in regen_scope or "output_3" in regen_scope:
        patterns = [p for p in patterns if not p.startswith("03_")]
    if regen_scope or force:
        blocked = {"final_release_decision.json", "qa_gates_report.json", "llm_judge_evaluation.json"}
        patterns = [p for p in patterns if p not in blocked]
    return patterns


def copy_matching_files(src_dir: Path, dst_dir: Path, pattern: str) -> None:
    """Copies matching files from source to destination if not already present."""
    for f in src_dir.glob(pattern):
        target_f = dst_dir / f.name
        if f.is_file() and not target_f.exists():
            shutil.copy2(f, target_f)


def seed_prior_artifacts_into_worktree(
    src_dir: Path, dst_dir: Path, meta: Optional[Dict[str, Any]] = None
) -> None:
    """Copies prior deliverables and seals from main output container into isolated worktree target."""
    if not src_dir.is_dir() or src_dir.resolve() == dst_dir.resolve():
        return
    dst_dir.mkdir(parents=True, exist_ok=True)

    meta_dict = meta or {}
    regen_scope = str(meta_dict.get("regenerate", ""))
    force = bool(meta_dict.get("force_rerun"))
    has_existing_dst = bool(list(dst_dir.glob("01_*")) or (dst_dir / "metadata.json").is_file())
    src_passed = is_source_decision_passed(src_dir)

    patterns = build_allowed_seeding_patterns(regen_scope, force)
    decision_files = {"final_release_decision.json", "qa_gates_report.json", "llm_judge_evaluation.json"}

    for pat in patterns:
        if pat not in decision_files or src_passed:
            copy_matching_files(src_dir, dst_dir, pat)

    src_att, dst_att = src_dir / "attempts", dst_dir / "attempts"
    can_seed_attempts = not force and not regen_scope and not has_existing_dst and src_passed
    if src_att.is_dir() and not dst_att.exists() and can_seed_attempts:
        shutil.copytree(src_att, dst_att, dirs_exist_ok=True)


def find_worktree_container_parent(path: Path) -> Optional[Path]:
    """Finds the parent directory located directly inside a .worktrees directory."""
    p = path
    while p != p.parent:
        if p.parent.name == ".worktrees":
            return p
        p = p.parent
    return None


def resolve_worktree_dir_path(primary_root: Path, raw_p: Path) -> Path:
    """Resolves an existing worktree directory path, checking raw and feat_ prefix variations."""
    wt_p = (primary_root / raw_p).resolve() if not raw_p.is_absolute() else raw_p.resolve()
    if wt_p.is_dir() or not raw_p.name:
        return wt_p
    cand = (primary_root / ".worktrees" / raw_p.name).resolve()
    if cand.is_dir():
        return cand
    if not raw_p.name.startswith("feat_"):
        prefixed = (primary_root / ".worktrees" / f"feat_{raw_p.name}").resolve()
        if prefixed.is_dir():
            return prefixed
    return wt_p


def handle_existing_worktree_containment(
    base_out: Path, meta_dict: Dict[str, Any], repo_root: Path
) -> Path:
    """Configures metadata when base_out already resides within a worktree."""
    from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
        _provision_git_worktree,
        _resolve_primary_repo_root,
        get_current_git_branch,
        is_authentic_git_worktree,
    )
    from .worktree_inheritance import _resolve_branch_for_worktree, rebind_worktree_metadata_paths

    primary_root = _resolve_primary_repo_root(repo_root or Path.cwd())
    if "worktree_dir" not in meta_dict:
        parent_wt = find_worktree_container_parent(base_out)
        if parent_wt is not None:
            meta_dict["worktree_dir"] = str(parent_wt)

    raw_dir = meta_dict.get("worktree_dir")
    if not raw_dir:
        return base_out

    wt_p = resolve_worktree_dir_path(primary_root, Path(raw_dir))
    meta_dict["worktree_dir"] = str(wt_p)
    if not is_authentic_git_worktree(wt_p):
        branch_name = _resolve_branch_for_worktree(wt_p, meta_dict.get("worktree_branch"))
        _provision_git_worktree(primary_root, wt_p, branch_name)
    b = get_current_git_branch(wt_p)
    if b:
        meta_dict["worktree_branch"] = b
    rebind_worktree_metadata_paths(meta_dict, wt_p, base_out)
    return base_out
