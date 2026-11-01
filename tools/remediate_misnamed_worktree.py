#!/usr/bin/env python3
"""
tools/remediate_misnamed_worktree.py

Governed utility to migrate misnamed worktrees (e.g. live_* to ingest_doc_*).
Ensures safe branch renaming, worktree directory migration, metadata synchronization,
and dependency re-linking without data loss (Wave 3).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.canonical_pipeline.worktree_naming import (
    CONVENTIONAL_BRANCH_RE,
    derive_worktree_identity,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    _resolve_primary_repo_root,
    git_worktree_provision_lock,
    is_authentic_git_worktree,
    run_git,
)
from tools.pr_governance_core.worktree import link_worktree_dependencies

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("remediate_misnamed_worktree")

PRIMARY_ROOT = _resolve_primary_repo_root(ROOT)


def _get_untracked_prefixes(source_dir: Path) -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(source_dir),
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if proc.returncode == 0:
            return [line[3:].strip() for line in proc.stdout.splitlines() if len(line) > 3]
    except Exception:
        pass
    return []


def _extract_metadata_from_worktree(source_dir: Path) -> Dict[str, Any]:
    """Extracts project, date, clean_slug, and title from metadata or artifacts."""
    untracked = _get_untracked_prefixes(source_dir)
    wt_tokens = set(re.findall(r"[a-z0-9]+", source_dir.name.lower())) - {
        "live", "feat", "meeting", "transcript", "ingest", "doc"
    }

    scored_candidates: List[Tuple[int, Path]] = []
    for meta_file in source_dir.rglob("metadata.json"):
        try:
            rel = str(meta_file.relative_to(source_dir))
            is_untracked = any(rel.startswith(up) for up in untracked)
            meta_tokens = set(re.findall(r"[a-z0-9]+", rel.lower()))
            overlap = len(wt_tokens & meta_tokens)
            has_tr = any(meta_file.parent.glob("01_*_live_transcript.md"))
            score = (10 if is_untracked else 0) + (5 if has_tr else 0) + overlap
            scored_candidates.append((score, meta_file))
        except Exception:
            continue

    meta: Dict[str, Any] = {}
    if scored_candidates:
        scored_candidates.sort(key=lambda c: c[0], reverse=True)
        best_file = scored_candidates[0][1]
        try:
            meta = json.loads(best_file.read_text(encoding="utf-8"))
            if not meta.get("clean_slug"):
                meta["clean_slug"] = best_file.parent.name
        except Exception:
            pass

    if not meta.get("date") or not meta.get("clean_slug"):
        for tr in source_dir.rglob("01_*_live_transcript.md"):
            m = re.search(r"01_(\d{2}-\d{2}-\d{4})_(.+?)_live_transcript\.md", tr.name)
            if m:
                meta.setdefault("date", m.group(1))
                meta.setdefault("clean_slug", m.group(2))
                break

    # Normalize project
    proj = meta.get("project") or meta.get("project_name") or ""
    if proj.lower().startswith("project_"):
        meta["project"] = proj[len("project_"):].upper()
    elif not proj:
        for candidate in ("LENS", "JARVIS", "partner_advisory", "NEA"):
            if candidate in source_dir.name.upper():
                meta["project"] = candidate
                break

    return meta


def inspect_and_derive_canonical_identity(
    source_dir: Path,
) -> Optional[Tuple[str, Path, Dict[str, Any]]]:
    """Inspects artifacts in source_dir and derives (canonical_branch, canonical_worktree_dir, details)."""
    meta = _extract_metadata_from_worktree(source_dir)
    project = meta.get("project") or meta.get("project_name") or "MEETING"
    raw_date = meta.get("date") or meta.get("meeting_date") or ""
    date = raw_date.replace("/", "-") if raw_date else ""
    clean_slug = meta.get("clean_slug") or meta.get("filename_slug")
    title = meta.get("meeting_name") or meta.get("meeting") or meta.get("title") or clean_slug or source_dir.name
    modality = meta.get("modality", "AUDIO")

    if not date and not clean_slug:
        return None

    target_dir_str, target_branch = derive_worktree_identity(
        modality=modality,
        title=title,
        project=project,
        date=date,
        clean_slug=clean_slug,
    )
    target_dir = source_dir.parent / Path(target_dir_str).name
    return target_branch, target_dir, {"project": project, "date": date, "clean_slug": clean_slug, "modality": modality}


def is_legacy_misnamed_worktree(wt_dir: Path) -> Tuple[bool, str, Optional[Tuple[str, Path]]]:
    """Checks whether a worktree lacks project, date, or clean slug tokens."""
    name = wt_dir.name.lower()
    if not name.startswith(("audio_", "live_", "feat_live_", "transcript_", "ingest_meeting_")):
        return False, "Not a live/meeting/transcript worktree", None

    derived = inspect_and_derive_canonical_identity(wt_dir)
    if not derived:
        date_pattern = re.compile(r"\d{2}[-_]\d{2}[-_]\d{4}")
        if not date_pattern.search(name):
            return True, "Missing date token (MM-DD-YYYY)", None
        return False, "Canonical (no metadata to re-derive)", None

    target_branch, target_dir, _ = derived
    if wt_dir.name != target_dir.name:
        return True, f"Diverges from canonical name '{target_dir.name}'", (target_branch, target_dir)

    return False, "Canonical", None


def scan_legacy_worktrees(repo_root: Path = PRIMARY_ROOT) -> List[Dict[str, Any]]:
    """Scans .worktrees/ and reports legacy/misnamed worktrees."""
    wt_root = repo_root / ".worktrees"
    if not wt_root.is_dir():
        return []
    candidates: List[Dict[str, Any]] = []
    for child in sorted(wt_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        is_legacy, reason, canonical = is_legacy_misnamed_worktree(child)
        if is_legacy:
            item: Dict[str, Any] = {
                "path": str(child),
                "name": child.name,
                "reason": reason,
            }
            if canonical:
                item["canonical_branch"] = canonical[0]
                item["canonical_dir"] = str(canonical[1])
            candidates.append(item)
    return candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Remediate misnamed Git worktrees.")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Source worktree directory to migrate.",
    )
    parser.add_argument(
        "--new-branch",
        type=str,
        default=None,
        help="Target conventional branch name.",
    )
    parser.add_argument(
        "--new-dir",
        type=Path,
        default=None,
        help="Target worktree directory.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-derive target branch and directory from source worktree metadata.",
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Scan .worktrees/ and report legacy worktrees needing remediation.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without modifying disk or git state.",
    )
    return parser.parse_args()


def update_worktree_metadata(target_dir: Path, new_branch: str, dry_run: bool = False) -> int:
    """Updates metadata.json files in target worktree to reflect new worktree dir and branch."""
    updated_count = 0
    for meta_file in target_dir.rglob("metadata.json"):
        try:
            data = json.loads(meta_file.read_text(encoding="utf-8"))
            changed = False
            if "worktree_dir" in data:
                data["worktree_dir"] = str(target_dir)
                changed = True
            if "worktree_branch" in data:
                data["worktree_branch"] = new_branch
                changed = True
            if changed:
                if not dry_run:
                    meta_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                updated_count += 1
                logger.info(f"{'[DRY RUN] Would update' if dry_run else 'Updated'} metadata in {meta_file.name}")
        except Exception as e:
            logger.warning(f"Could not update {meta_file}: {e}")
    return updated_count


def remediate_worktree(
    source_dir: Path,
    new_branch: Optional[str] = None,
    target_dir: Optional[Path] = None,
    repo_root: Path = ROOT,
    dry_run: bool = False,
    auto_derive: bool = False,
) -> Dict[str, Any]:
    """Migrates worktree from source_dir to target_dir and renames branch to new_branch."""
    if not source_dir.exists():
        return {
            "status": "FAILED",
            "source_dir": str(source_dir),
            "error": f"Source worktree directory {source_dir} does not exist.",
            "dry_run": dry_run,
        }

    if not new_branch or not target_dir or auto_derive:
        derived = inspect_and_derive_canonical_identity(source_dir)
        if not derived:
            return {
                "status": "FAILED",
                "source_dir": str(source_dir),
                "error": f"Cannot auto-derive canonical identity for {source_dir}. Specify --new-branch and --new-dir.",
                "dry_run": dry_run,
            }
        canonical_branch, canonical_dir, _ = derived
        new_branch = new_branch or canonical_branch
        target_dir = target_dir or canonical_dir

    res: Dict[str, Any] = {
        "status": "FAILED",
        "source_dir": str(source_dir),
        "target_dir": str(target_dir),
        "new_branch": new_branch,
        "dry_run": dry_run,
    }

    if target_dir.exists():
        res["error"] = f"Target directory {target_dir} already exists."
        logger.error(res["error"])
        return res

    if not CONVENTIONAL_BRANCH_RE.match(new_branch):
        res["error"] = f"New branch '{new_branch}' violates CONVENTIONAL_BRANCH_RE."
        logger.error(res["error"])
        return res

    # Resolve active branch in source worktree
    old_branch = run_git(["branch", "--show-current"], cwd=source_dir)
    res["old_branch"] = old_branch

    if dry_run:
        logger.info(f"[DRY RUN] Would move worktree {source_dir} -> {target_dir}")
        logger.info(f"[DRY RUN] Would rename branch {old_branch} -> {new_branch}")
        res["status"] = "DRY_RUN_PASS"
        return res

    with git_worktree_provision_lock(repo_root):
        logger.info(f"Moving git worktree {source_dir} -> {target_dir}...")
        move_proc = subprocess.run(
            ["git", "worktree", "move", str(source_dir), str(target_dir)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if move_proc.returncode != 0:
            res["error"] = f"git worktree move failed: {move_proc.stderr}"
            logger.error(res["error"])
            return res

        # Rename branch if old_branch differs
        if old_branch and old_branch != new_branch:
            logger.info(f"Renaming branch {old_branch} -> {new_branch}...")
            branch_proc = subprocess.run(
                ["git", "branch", "-m", old_branch, new_branch],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                timeout=30.0,
            )
            if branch_proc.returncode != 0:
                res["error"] = f"git branch -m failed: {branch_proc.stderr}"
                logger.error(res["error"])
                return res

        # Link dependencies in new location
        link_worktree_dependencies(repo_root, target_dir)

        # Update metadata.json files
        meta_count = update_worktree_metadata(target_dir, new_branch, dry_run=False)
        res["updated_metadata_files"] = meta_count

    res["status"] = "SUCCESS"
    logger.info(f"Successfully migrated worktree to {target_dir} on branch {new_branch}.")
    return res


def main() -> int:
    args = parse_args()
    if args.scan or (args.dry_run and not args.source):
        legacy = scan_legacy_worktrees(PRIMARY_ROOT)
        out = {
            "status": "SCAN_COMPLETE",
            "legacy_worktrees_count": len(legacy),
            "legacy_worktrees": legacy,
            "dry_run": args.dry_run,
        }
        print(json.dumps(out, indent=2))
        return 0

    source = (args.source or (PRIMARY_ROOT / ".worktrees" / "live_product_owner_role_description_partner_advisory")).resolve()
    result = remediate_worktree(
        source_dir=source,
        new_branch=args.new_branch,
        target_dir=args.new_dir.resolve() if args.new_dir else None,
        repo_root=PRIMARY_ROOT,
        dry_run=args.dry_run,
        auto_derive=args.auto or (args.new_branch is None and args.new_dir is None),
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("SUCCESS", "DRY_RUN_PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
