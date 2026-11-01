#!/usr/bin/env python3
"""
tools/reconcile_live_worktrees.py

CLI utility to detect and coalesce bifurcated live worktree branches
(e.g. live_<slug> and live_<slug>_<attendee>), migrating audio and session artifacts
into the deliverable branch, verifying hashes, and pruning the duplicate worktree.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("reconcile_live_worktrees")


def _git_env() -> Dict[str, str]:
    env = os.environ.copy()
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX"):
        env.pop(var, None)
    return env


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _has_deliverables(wt: Path) -> bool:
    output_dir = wt / "output"
    if not output_dir.exists():
        return False
    return bool(list(output_dir.rglob("0[1-3]_*.md")) or list(output_dir.rglob("03_*.json")))


def _has_audio(wt: Path) -> bool:
    output_dir = wt / "output"
    if not output_dir.exists():
        return False
    return bool(list(output_dir.rglob("00_*master.wav")) or list(output_dir.rglob("*.wav")))


def get_worktree_branch(wt_path: Path) -> Optional[str]:
    """Resolves branch name for a git worktree."""
    if not wt_path.exists():
        return None
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(wt_path),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=_git_env(),
    )
    if res.returncode == 0 and res.stdout.strip():
        branch = res.stdout.strip()
        if branch != "HEAD":
            return branch
    return None


def classify_source_and_target(wt_a: Path, wt_b: Path) -> Tuple[Path, Path]:
    """
    Classifies a bifurcated pair into (source_worktree, target_worktree).
    The source contains the audio/artifacts to migrate, while target holds deliverables.
    """
    deliv_a = _has_deliverables(wt_a)
    deliv_b = _has_deliverables(wt_b)
    audio_a = _has_audio(wt_a)
    audio_b = _has_audio(wt_b)

    if audio_a and not deliv_a and deliv_b:
        return wt_a, wt_b
    if audio_b and not deliv_b and deliv_a:
        return wt_b, wt_a

    # Default to longer attendee-specific slug as target
    if len(wt_a.name) <= len(wt_b.name):
        return wt_a, wt_b
    return wt_b, wt_a


def detect_bifurcated_pairs(repo_root: Path) -> List[Tuple[Path, Path]]:
    """Scans .worktrees for bifurcated live worktree pairs."""
    wt_dir = repo_root / ".worktrees"
    if not wt_dir.is_dir():
        return []
    live_wts = sorted([
        d for d in wt_dir.iterdir()
        if d.is_dir() and d.name.startswith("live_") and not d.name.startswith(".")
    ])
    pairs: List[Tuple[Path, Path]] = []
    seen = set()
    for i, a in enumerate(live_wts):
        slug_a = a.name.replace("live_", "")
        for b in live_wts[i + 1:]:
            slug_b = b.name.replace("live_", "")
            if slug_a in slug_b or slug_b in slug_a:
                pair_key = tuple(sorted([str(a.resolve()), str(b.resolve())]))
                if pair_key not in seen:
                    seen.add(pair_key)
                    source, target = classify_source_and_target(a, b)
                    pairs.append((source, target))
    return pairs


def _find_target_meeting_folder(source_meeting_dir: Path, source_wt: Path, target_wt: Path) -> Path:
    """Finds or constructs matching meeting directory in target worktree."""
    rel = source_meeting_dir.relative_to(source_wt / "output")
    # rel is typically <proj>/<date>/<slug>
    parts = list(rel.parts)
    target_output = target_wt / "output"
    if len(parts) >= 2:
        proj, date_str = parts[0], parts[1]
        target_date_dir = target_output / proj / date_str
        if target_date_dir.is_dir():
            target_subs = [d for d in target_date_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
            if target_subs:
                return target_subs[0]
            return target_date_dir / parts[-1]
    return target_output / rel


def migrate_artifacts(source_wt: Path, target_wt: Path) -> List[Dict[str, Any]]:
    """Copies audio, segments, manifests, and cache files from source into target."""
    migrated: List[Dict[str, Any]] = []
    source_output = source_wt / "output"
    if not source_output.is_dir():
        return migrated

    # Collect meeting directories
    meeting_dirs: List[Path] = []
    for p in source_output.rglob("*"):
        if p.is_dir() and any(p.glob("*.wav")):
            meeting_dirs.append(p)
    if not meeting_dirs:
        meeting_dirs = [d for d in source_output.rglob("*") if d.is_dir() and not list(d.glob("*/"))]

    for s_dir in meeting_dirs:
        t_dir = _find_target_meeting_folder(s_dir, source_wt, target_wt)
        t_dir.mkdir(parents=True, exist_ok=True)
        for src_file in s_dir.iterdir():
            if src_file.is_file() and not src_file.name.startswith("."):
                dst_file = t_dir / src_file.name
                src_hash = _sha256(src_file)
                if not dst_file.exists() or _sha256(dst_file) != src_hash:
                    shutil.copy2(src_file, dst_file)
                    dst_hash = _sha256(dst_file)
                    if src_hash != dst_hash:
                        raise ValueError(f"Hash mismatch after copying {src_file.name}: {src_hash} != {dst_hash}")
                    migrated.append({"source": str(src_file), "target": str(dst_file), "sha256": src_hash})

    # Migrate session cache files
    s_cache = source_wt / ".cache"
    if s_cache.is_dir():
        t_cache = target_wt / ".cache"
        t_cache.mkdir(parents=True, exist_ok=True)
        for c_file in s_cache.glob("*"):
            if c_file.is_file() and not (t_cache / c_file.name).exists():
                shutil.copy2(c_file, t_cache / c_file.name)
                migrated.append({"source": str(c_file), "target": str(t_cache / c_file.name), "sha256": _sha256(c_file)})

    return migrated


def commit_migrated_artifacts(target_wt: Path, source_branch: Optional[str]) -> bool:
    """Commits migrated files in target worktree."""
    status = subprocess.run(["git", "status", "--porcelain"], cwd=str(target_wt), capture_output=True, text=True, check=False, timeout=30, env=_git_env())
    if not status.stdout.strip():
        return False
    subprocess.run(["git", "add", "-A"], cwd=str(target_wt), check=True, timeout=30, env=_git_env())
    msg = f"chore(reconcile): coalesce audio and session artifacts from {source_branch or 'bifurcated worktree'}"
    subprocess.run(["git", "commit", "-m", msg], cwd=str(target_wt), check=True, timeout=30, env=_git_env())
    return True


def is_worktree_locked_or_active(wt: Path, repo_root: Path) -> bool:
    """Checks whether a worktree has an active recording session or holds an OS advisory slug flock."""
    wt = Path(wt)
    repo_root = Path(repo_root)
    slug = wt.name.replace("live_", "").replace("_", "-")
    clean_slug = wt.name.replace("live_", "")
    locks_dir = repo_root / ".session_locks"

    lock_candidates = {slug, clean_slug, wt.name}

    # Discover candidate slugs from internal metadata.json or live transcript files
    try:
        output_dir = wt / "output"
        if output_dir.is_dir():
            for meta_path in output_dir.glob("*/*/*/metadata.json"):
                if meta_path.is_file():
                    import json
                    try:
                        meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
                        if meta_data.get("filename_slug"):
                            lock_candidates.add(meta_data["filename_slug"])
                    except Exception:
                        pass
                # Also include directory parent name
                lock_candidates.add(meta_path.parent.name)

            for trans_path in output_dir.glob("*/*/*/*_live_transcript.md"):
                # e.g. 01_10-15-2026_Business_PartnerAdvisoryteam_Daniel_live_transcript.md
                name_parts = trans_path.stem.split("_live_transcript")[0].split("_")
                if len(name_parts) >= 3:
                    # After 01_<date>_
                    cand_from_trans = "_".join(name_parts[2:])
                    if cand_from_trans:
                        lock_candidates.add(cand_from_trans)
    except Exception:
        pass

    for cand in lock_candidates:
        if not cand:
            continue
        for fname in (f"{cand}.flock", f"{cand.replace('-', '_')}.flock", f"{cand.replace('_', '-')}.flock"):
            lock_file = locks_dir / fname
            if lock_file.is_file():
                try:
                    fd = os.open(str(lock_file), os.O_RDWR)
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(fd, fcntl.LOCK_UN)
                    finally:
                        os.close(fd)
                except (BlockingIOError, OSError):
                    return True
    return False


def prune_source_worktree(source_wt: Path, repo_root: Path, source_branch: Optional[str]) -> None:
    """Removes source worktree and deletes its branch cleanly after safely unlinking dependencies."""
    source_wt = Path(source_wt)
    repo_root = Path(repo_root)
    try:
        from tools.pr_governance_core.worktree import safely_unlink_worktree_dependencies
        safely_unlink_worktree_dependencies(source_wt)
    except Exception:
        pass
    subprocess.run(["git", "worktree", "remove", "--force", str(source_wt)], cwd=str(repo_root), check=True, timeout=30, env=_git_env())
    if source_branch:
        subprocess.run(["git", "branch", "-D", source_branch], cwd=str(repo_root), check=False, timeout=30, env=_git_env())


def coalesce_live_worktree_pair(
    source_wt: Path, target_wt: Path, repo_root: Path, dry_run: bool = False
) -> Dict[str, Any]:
    """Coalesces source worktree into target worktree, verifying hashes and pruning source."""
    source_wt = Path(source_wt)
    target_wt = Path(target_wt)
    repo_root = Path(repo_root)
    if is_worktree_locked_or_active(source_wt, repo_root):
        logger.warning(f"Refusing to reconcile active/locked source worktree: {source_wt.name}")
        return {"status": "BLOCKED", "reason": "SOURCE_WORKTREE_ACTIVE", "source": str(source_wt)}
    if is_worktree_locked_or_active(target_wt, repo_root):
        logger.warning(f"Refusing to reconcile active/locked target worktree: {target_wt.name}")
        return {"status": "BLOCKED", "reason": "TARGET_WORKTREE_ACTIVE", "target": str(target_wt)}

    source_branch = get_worktree_branch(source_wt)
    target_branch = get_worktree_branch(target_wt)

    logger.info(f"Coalescing '{source_wt.name}' ({source_branch}) -> '{target_wt.name}' ({target_branch}) [dry_run={dry_run}]")

    migrated = []
    if not dry_run:
        migrated = migrate_artifacts(source_wt, target_wt)
        commit_migrated_artifacts(target_wt, source_branch)
        prune_source_worktree(source_wt, repo_root, source_branch)
    else:
        # Dry run enumeration
        source_output = source_wt / "output"
        if source_output.exists():
            for f in source_output.rglob("*"):
                if f.is_file() and not f.name.startswith("."):
                    migrated.append({"source": str(f), "dry_run": True})

    return {
        "status": "PASS",
        "source_worktree": str(source_wt),
        "target_worktree": str(target_wt),
        "source_branch": source_branch,
        "target_branch": target_branch,
        "migrated_files_count": len(migrated),
        "migrated_files": migrated,
        "pruned": not dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Coalesce bifurcated live worktrees and prune duplicates.")
    parser.add_argument("--scan", action="store_true", help="Scan and list detected bifurcated pairs")
    parser.add_argument("--execute", action="store_true", help="Execute coalescence and safe pruning")
    parser.add_argument("--source", type=Path, default=None, help="Explicit source worktree path or slug")
    parser.add_argument("--target", type=Path, default=None, help="Explicit target worktree path or slug")
    parser.add_argument("--repo-root", type=Path, default=None, help="Repository root (defaults to cwd)")

    args = parser.parse_args()
    from dual_pipeline.core import ROOT
    repo_root = args.repo_root or ROOT

    if args.source and args.target:
        source_wt = (repo_root / args.source if not args.source.is_absolute() else args.source).resolve()
        target_wt = (repo_root / args.target if not args.target.is_absolute() else args.target).resolve()
        pairs = [(source_wt, target_wt)]
    else:
        pairs = detect_bifurcated_pairs(repo_root)

    if not pairs:
        print("No bifurcated live worktree pairs detected.")
        return 0

    print(f"Detected {len(pairs)} bifurcated live worktree pair(s):")
    for src, tgt in pairs:
        print(f"  • Source (migrate & prune): {src.name} -> Target (keep): {tgt.name}")

    if args.scan:
        return 0

    dry_run = not args.execute
    for src, tgt in pairs:
        res = coalesce_live_worktree_pair(src, tgt, repo_root, dry_run=dry_run)
        print(f"[{'DRY-RUN' if dry_run else 'EXECUTED'}] Coalesced {src.name} -> {tgt.name} ({res['migrated_files_count']} file(s)).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
