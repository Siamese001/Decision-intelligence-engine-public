"""
Branch Birth and Inception Introspection Engine.

Determines the origin, timestamp, age, and inception lease state of a git branch:
- Parses `git reflog show --date=iso refs/heads/<branch>` to find initial creation event.
- Inspects worktree directory metadata (ctime/mtime) as secondary evidence.
- Guarantees 48-hour Inception Immunity to prevent premature pruning of fresh branches.
"""

from __future__ import annotations

import contextlib
import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from tools.pr_governance_core.common import ROOT, run_git

INCEPTION_IMMUNITY_HOURS = 48.0


@dataclass
class BranchBirthInfo:
    branch: str
    birth_time: Optional[dt.datetime]
    age_hours: float
    is_inception_active: bool
    creation_event: str
    source_ref: str
    birth_sha: str
    reflog_entries_count: int

    @property
    def age_desc(self) -> str:
        if self.age_hours < 1.0:
            mins = max(1, int(self.age_hours * 60))
            return f"created {mins}m ago"
        elif self.age_hours < 24.0:
            hrs = round(self.age_hours, 1)
            return f"created {hrs}h ago"
        else:
            days = round(self.age_hours / 24.0, 1)
            return f"created {days}d ago"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "branch": self.branch,
            "birth_time": self.birth_time.isoformat() if self.birth_time else None,
            "age_hours": round(self.age_hours, 2),
            "age_desc": self.age_desc,
            "is_inception_active": self.is_inception_active,
            "creation_event": self.creation_event,
            "source_ref": self.source_ref,
            "birth_sha": self.birth_sha,
            "reflog_entries_count": self.reflog_entries_count,
        }


def inspect_branch_birth(
    branch: str,
    root: Path = ROOT,
    worktree_path: Optional[Path] = None,
    now: Optional[dt.datetime] = None,
) -> BranchBirthInfo:
    """
    Analyzes branch birth from git reflog and worktree directory metadata.
    Returns BranchBirthInfo with is_inception_active=True if within 48h inception window.
    """
    current_time = now or dt.datetime.now(dt.timezone.utc)
    reflog_lines: list[str] = []

    with contextlib.suppress(Exception):
        out = run_git(
            ["reflog", "show", "--date=iso", f"refs/heads/{branch}"],
            cwd=root,
            check=False,
        )
        if out and out.strip():
            reflog_lines = [line.strip() for line in out.strip().splitlines() if line.strip()]

    birth_time: Optional[dt.datetime] = None
    creation_event = "unknown"
    source_ref = "unknown"
    birth_sha = ""
    reflog_count = len(reflog_lines)

    if reflog_lines:
        # The oldest entry in `git reflog show` is the last line
        oldest_entry = reflog_lines[-1]
        parts = oldest_entry.split()
        if parts:
            birth_sha = parts[0][:8]

        # Extract timestamp between @{ and }
        if "@{" in oldest_entry and "}" in oldest_entry:
            ts_str = oldest_entry.split("@{", 1)[1].split("}", 1)[0].strip()
            with contextlib.suppress(Exception):
                # ISO format: 2026-10-04 17:16:57 -0400
                birth_time = dt.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S %z")

        if ": branch: Created from" in oldest_entry:
            creation_event = "Created from"
            source_ref = oldest_entry.split(": branch: Created from", 1)[1].strip()
        elif ": " in oldest_entry:
            creation_event = oldest_entry.split(": ", 1)[1].strip()

    # Fallback to worktree directory creation time if reflog has no timestamp
    if birth_time is None and worktree_path and worktree_path.exists():
        with contextlib.suppress(Exception):
            stat = worktree_path.stat()
            # On macOS st_birthtime is available; fallback to st_ctime / st_mtime
            ts = getattr(stat, "st_birthtime", None) or stat.st_ctime or stat.st_mtime
            birth_time = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)

    # Fallback to branch tip commit timestamp if still None
    if birth_time is None:
        with contextlib.suppress(Exception):
            c_ts = run_git(["log", "-1", "--format=%cI", branch], cwd=root, check=False).strip()
            if c_ts:
                birth_time = dt.datetime.fromisoformat(c_ts)

    if birth_time is not None:
        # Ensure UTC comparison
        if birth_time.tzinfo is None:
            birth_time = birth_time.replace(tzinfo=dt.timezone.utc)
        age_hours = max(0.0, (current_time - birth_time).total_seconds() / 3600.0)
    else:
        age_hours = 999.0

    # Inception active if within immunity window, or if single reflog entry proves freshly created
    is_inception = (
        age_hours < INCEPTION_IMMUNITY_HOURS
        or (reflog_count <= 1 and creation_event == "Created from" and age_hours < 72.0)
    )

    return BranchBirthInfo(
        branch=branch,
        birth_time=birth_time,
        age_hours=age_hours,
        is_inception_active=is_inception,
        creation_event=creation_event,
        source_ref=source_ref,
        birth_sha=birth_sha,
        reflog_entries_count=reflog_count,
    )
