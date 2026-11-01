"""
tools/pr_governance_core/worktree_inventory.py

Comprehensive, read-only worktree and stash inventory engine.
Audits all registered git worktrees, orphaned directories under .worktrees/,
stale registrations, and git stashes without mutating any repository state.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .common import ROOT
from .worktree_orphan_classifier import (
    classify_orphan_directory,
    compute_file_sha256,
)


def list_stashes(repo_root: Path, live_branches: Set[str]) -> List[Dict[str, Any]]:
    """Lists and categorizes all git stashes in refs/stash."""
    stashes: List[Dict[str, Any]] = []
    try:
        proc = subprocess.run(
            ["git", "log", "-g", "--format=%gd%x00%H%x00%P%x00%gs", "refs/stash"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return []

        branch_pattern = re.compile(r"(?:WIP on|On)\s+([^:]+):")
        for line in proc.stdout.strip().splitlines():
            parts = line.split("\x00")
            if len(parts) < 4:
                continue
            ref, commit_sha, _parents, subject = parts[0], parts[1], parts[2], parts[3]
            m = branch_pattern.search(subject)
            branch_name = m.group(1).strip() if m else "main"

            if branch_name == "main":
                classification = "STASH_MAIN"
            elif branch_name in live_branches:
                classification = "STASH_LIVE_BRANCH"
            else:
                classification = "STASH_DELETED_BRANCH"

            stashes.append(
                {
                    "ref": ref,
                    "commit": commit_sha[:8],
                    "branch": branch_name,
                    "subject": subject,
                    "classification": classification,
                }
            )
    except Exception:
        pass
    return stashes


def _parse_registered_block(block: str) -> Optional[Tuple[Path, Dict[str, Any]]]:
    if not block.strip():
        return None
    wt_path: Optional[Path] = None
    branch, head = "", ""
    is_prunable, is_detached = False, False
    for line in block.strip().splitlines():
        if line.startswith("worktree "):
            wt_path = Path(line.split(" ", 1)[1].strip()).resolve()
        elif line.startswith("branch "):
            branch = line.split(" ", 1)[1].strip().replace("refs/heads/", "")
        elif line.startswith("HEAD "):
            head = line.split(" ", 1)[1].strip()[:8]
        elif line.startswith("prunable"):
            is_prunable = True
        elif line.startswith("detached"):
            is_detached = True
    if not wt_path:
        return None
    return wt_path, {
        "branch": branch,
        "head": head,
        "prunable": is_prunable,
        "detached": is_detached,
    }


def _parse_registered_worktrees(repo_root: Path) -> Dict[Path, Dict[str, Any]]:
    """Runs git worktree list --porcelain and returns mapped info."""
    registered_map: Dict[Path, Dict[str, Any]] = {}
    try:
        wt_list = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if wt_list.returncode == 0:
            for block in wt_list.stdout.split("\n\n"):
                parsed = _parse_registered_block(block)
                if parsed:
                    registered_map[parsed[0]] = parsed[1]
    except Exception:
        pass
    return registered_map


def _make_wt_dict(
    name: str, path: str, classification: str, branch: str = "", head: str = "",
    unique_files_count: int = 0, unique_files: Optional[List[Any]] = None, **kwargs: Any
) -> Dict[str, Any]:
    d = {
        "name": name, "path": path, "classification": classification,
        "branch": branch, "head": head,
        "unique_files_count": unique_files_count, "unique_files": unique_files or [],
    }
    d.update(kwargs)
    return d


def _classify_worktree_child(
    child: Path, registered_map: Dict[Path, Dict[str, Any]], repo_root: Path
) -> Dict[str, Any]:
    """Classifies a single child entry under .worktrees/."""
    child_resolved = child.resolve()
    is_registered = child_resolved in registered_map

    from .live_meeting_bridge import classify_live_worktree, is_live_meeting_container

    if is_live_meeting_container(child):
        live_status, live_markers = classify_live_worktree(child, is_registered=is_registered)
        reg = registered_map.get(child_resolved, {})
        classification = live_status.value if not is_registered else "REGISTERED"
        audio_cnt = live_markers.get("audio_chunks_count", 0)
        art_cnt = len(live_markers.get("art01_files", []))
        files = [{"path": p} for p in (live_markers.get("art01_files", []) + live_markers.get("audio_chunks", []))]
        return _make_wt_dict(
            child.name, str(child), classification,
            branch=reg.get("branch", ""), head=reg.get("head", ""),
            unique_files_count=audio_cnt + art_cnt, unique_files=files,
            live_meeting_status=live_status.value, live_markers=live_markers,
        )

    if is_registered:
        reg = registered_map[child_resolved]
        return _make_wt_dict(child.name, str(child), "REGISTERED", branch=reg["branch"], head=reg["head"])
    if not child.is_dir():
        return _make_wt_dict(child.name, str(child), "NON_DIRECTORY")
    classification, unique_files = classify_orphan_directory(child, repo_root)
    return _make_wt_dict(
        child.name, str(child), classification,
        unique_files_count=len(unique_files), unique_files=unique_files,
    )


def _get_live_branches(repo_root: Path) -> Set[str]:
    try:
        b_res = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=repo_root, capture_output=True, text=True, check=False,
        )
        if b_res.returncode == 0:
            return {b.strip() for b in b_res.stdout.splitlines() if b.strip()}
    except Exception:
        pass
    return set()


def _classify_anomalous_entry(
    reg_path: Path, reg_info: Dict[str, Any], wt_dir_resolved: Path, seen_paths: Set[Path]
) -> Optional[Dict[str, Any]]:
    exists = reg_path.exists()
    is_prunable = reg_info.get("prunable", False)
    is_detached = reg_info.get("detached", False) or not reg_info.get("branch")
    is_ext = not str(reg_path).startswith(str(wt_dir_resolved))
    is_nest = str(reg_path).startswith(str(wt_dir_resolved)) and len(reg_path.relative_to(wt_dir_resolved).parts) > 1

    if not exists or is_prunable:
        cls = "STALE_REGISTRATION"
    elif is_detached:
        cls, _ = "DETACHED_HEAD", seen_paths.add(reg_path)
    elif is_ext:
        cls, _ = "EXTERNAL_REGISTERED", seen_paths.add(reg_path)
    elif is_nest:
        cls, _ = "NESTED_REGISTERED", seen_paths.add(reg_path)
    else:
        return None

    return _make_wt_dict(
        reg_path.name, str(reg_path), cls, branch=reg_info["branch"], head=reg_info["head"]
    )


def _collect_anomalous_registered(
    registered_map: Dict[Path, Dict[str, Any]],
    repo_root_resolved: Path,
    wt_dir_resolved: Path,
    seen_paths: Set[Path],
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for reg_path, reg_info in registered_map.items():
        if reg_path == repo_root_resolved:
            continue
        entry = _classify_anomalous_entry(reg_path, reg_info, wt_dir_resolved, seen_paths)
        if entry:
            entries.append(entry)
    return entries


def _collect_worktree_children(
    worktrees_dir: Path,
    registered_map: Dict[Path, Dict[str, Any]],
    repo_root: Path,
    seen_paths: Set[Path],
) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    if not (worktrees_dir.exists() and worktrees_dir.is_dir()):
        return entries
    try:
        children = [
            child for child in sorted(worktrees_dir.iterdir())
            if not child.name.startswith(".") and child.resolve() not in seen_paths
        ]
        if children:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(8, len(children))) as ex:
                entries = list(ex.map(lambda c: _classify_worktree_child(c, registered_map, repo_root), children))
    except Exception as exc:
        entries.append(_make_wt_dict(".worktrees", str(worktrees_dir), "UNREADABLE_WORKTREE", error=str(exc)))
    return entries


def _calculate_inventory_counts(
    entries: List[Dict[str, Any]], stashes: List[Dict[str, Any]], phys_children: List[Path]
) -> Dict[str, Any]:
    covered_wt = len([e for e in entries if e["classification"] not in ("STALE_REGISTRATION", "EXTERNAL_REGISTERED")])
    live_sessions = [e for e in entries if e.get("live_meeting_status")]
    return {
        "total_worktrees_children": len([e for e in entries if e["classification"] != "STALE_REGISTRATION"]),
        "total_physical_children": len(phys_children),
        "covered_worktrees_children": covered_wt,
        "covered_children": covered_wt,
        "total_children": len(phys_children),
        "registered": len([e for e in entries if e["classification"] == "REGISTERED"]),
        "stale_registrations": len([e for e in entries if e["classification"] == "STALE_REGISTRATION"]),
        "orphan_directories": len([e for e in entries if e["classification"].startswith("ORPHAN_")]),
        "orphan_deliverables": len([e for e in entries if e["classification"] == "ORPHAN_DELIVERABLES"]),
        "orphan_audio_only": len([e for e in entries if e["classification"] == "ORPHAN_AUDIO_ONLY"]),
        "orphan_receipts_only": len([e for e in entries if e["classification"] == "ORPHAN_RECEIPTS_ONLY"]),
        "orphan_stubs": len([e for e in entries if e["classification"] == "ORPHAN_STUB"]),
        "stashes_total": len(stashes),
        "covered_stashes": len(stashes),
        "total_stashes": len(stashes),
        "stashes_main": len([s for s in stashes if s["classification"] == "STASH_MAIN"]),
        "stashes_live_branch": len([s for s in stashes if s["classification"] == "STASH_LIVE_BRANCH"]),
        "stashes_deleted_branch": len([s for s in stashes if s["classification"] == "STASH_DELETED_BRANCH"]),
        "live_meeting_sessions": len(live_sessions),
        "live_recording_in_progress": len([e for e in live_sessions if e.get("live_meeting_status") == "RECORDING_IN_PROGRESS"]),
        "live_synthesis_pending": len([e for e in live_sessions if e.get("live_meeting_status") == "SYNTHESIS_PENDING"]),
        "live_stage_u0_ready": len([e for e in live_sessions if e.get("live_meeting_status") == "STAGE_U0_READY"]),
    }


def build_worktree_inventory(
    repo_root: Path,
    include_remote: bool = False,
    fetch_first: bool = False,
    include_github: bool = False,
) -> Dict[str, Any]:
    """Constructs the complete, read-only worktree and stash inventory."""
    worktrees_dir = repo_root / ".worktrees"
    live_branches = _get_live_branches(repo_root)
    registered_map = _parse_registered_worktrees(repo_root)
    seen_paths: Set[Path] = set()

    entries = _collect_anomalous_registered(
        registered_map, repo_root.resolve(), worktrees_dir.resolve(), seen_paths
    )
    entries.extend(_collect_worktree_children(worktrees_dir, registered_map, repo_root, seen_paths))
    stashes = list_stashes(repo_root, live_branches)

    phys_children = (
        [c for c in worktrees_dir.iterdir() if not c.name.startswith(".")]
        if (worktrees_dir.exists() and worktrees_dir.is_dir())
        else []
    )
    entry_names = {e["name"] for e in entries if e.get("classification") not in ("STALE_REGISTRATION", "EXTERNAL_REGISTERED")}
    phys_names = {c.name for c in phys_children}
    if entry_names != phys_names and worktrees_dir.exists() and worktrees_dir.is_dir():
        import time
        time.sleep(0.1)
        phys_children = [c for c in worktrees_dir.iterdir() if not c.name.startswith(".")]
        missing = [c for c in phys_children if c.name not in entry_names]
        for m in missing:
            entries.append(_classify_worktree_child(m, registered_map, repo_root))
            entry_names.add(m.name)

    counts = _calculate_inventory_counts(entries, stashes, phys_children)

    remote_branches: List[str] = []
    if include_remote:
        from .inspection import get_feature_branches
        try:
            remote_branches = [
                b for b in get_feature_branches(include_remote=True, fetch_first=fetch_first, repo_root=repo_root)
                if b.startswith("origin/")
            ]
        except Exception:
            remote_branches = []
        counts["remote_tracking_only"] = len(remote_branches)

    res: Dict[str, Any] = {
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "counts": counts,
        "worktrees": entries,
        "stashes": stashes,
    }
    if include_remote:
        res["remote_branches"] = remote_branches

    if include_github:
        from .github_adapter import get_github_prs

        gh_prs = get_github_prs(repo_root=repo_root, force_refresh=fetch_first)
        unique_prs = {pr.number: pr for pr in gh_prs.values()}
        counts["open_github_prs"] = len(unique_prs)
        res["open_github_prs"] = [
            pr.to_dict()
            for pr in sorted(unique_prs.values(), key=lambda p: p.number)
        ]

    return res


def cmd_worktree_inventory(args: argparse.Namespace) -> int:
    """CLI sub-command entry point for worktree inventory."""
    repo_root = getattr(args, "root", None)
    root_path = Path(repo_root).resolve() if repo_root else ROOT
    include_remote = getattr(args, "remote", False)
    fetch_first = getattr(args, "fetch", False)
    include_github = getattr(args, "github", False)
    inv = build_worktree_inventory(
        root_path,
        include_remote=include_remote,
        fetch_first=fetch_first,
        include_github=include_github,
    )

    if getattr(args, "json", False):
        print(json.dumps(inv, indent=2))
        return 0

    c = inv["counts"]
    print("=" * 80)
    print("WORKTREE & STASH INVENTORY SUMMARY")
    print("=" * 80)
    print(f"Total .worktrees children : {c['total_worktrees_children']}")
    print(f"  - Registered branches   : {c['registered']}")
    print(f"  - Orphan directories    : {c['orphan_directories']}")
    print(f"    * With deliverables   : {c['orphan_deliverables']}")
    print(f"    * Audio only          : {c['orphan_audio_only']}")
    print(f"    * Receipts only       : {c['orphan_receipts_only']}")
    print(f"    * Clean empty stubs   : {c['orphan_stubs']}")
    print(f"Stale git registrations   : {c['stale_registrations']}")
    print(f"Git stashes               : {c['stashes_total']}")
    print(f"  - On main               : {c['stashes_main']}")
    print(f"  - On live branch        : {c['stashes_live_branch']}")
    print(f"  - On deleted branch     : {c['stashes_deleted_branch']}")
    if include_remote:
        print(f"Remote tracking branches  : {c.get('remote_tracking_only', 0)}")
    if include_github:
        print(f"Open GitHub PRs           : {c.get('open_github_prs', 0)}")
    if c.get("live_meeting_sessions", 0) > 0:
        print(f"Live meeting sessions     : {c['live_meeting_sessions']}")
        print(f"  - Recording in progress : {c.get('live_recording_in_progress', 0)}")
        print(f"  - Synthesis pending     : {c.get('live_synthesis_pending', 0)}")
        print(f"  - Stage U0 ready        : {c.get('live_stage_u0_ready', 0)}")
    print("=" * 80)
    return 0


cmd_inventory = cmd_worktree_inventory

