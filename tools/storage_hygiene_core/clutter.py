"""Reports and repository root clutter planner."""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import logging
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .pins import get_main_repo_root
from .policy import StorageRetentionPolicy, load_storage_retention_policy

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
MAIN_REPO_ROOT = get_main_repo_root(ROOT)

STANDARD_ROOT_KEEP = {
    "nee.py",
    "app.py",
    "mcp_server.py",
    "nee.cmd",
    "nee.ps1",
    "agents.md",
    "project_instructions.md",
    "changelog.md",
    "manifest.json",
    "manifest.md",
    "baseline_notice.md",
    "readme.md",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
    "makefile",
}

ARCHIVE_PATTERNS = [
    "debug_*.txt",
    "pytest_*.txt",
    "mock_*.txt",
    "*_manifest.json",
    "wave_*_receipt.txt",
    "implementation_plan.md",
    "walkthrough.md",
    "qa_report.md",
    "output_hierarchy_audit_report.md",
    "redundancy_analysis_report.md",
    "release_gate_report.md",
    "rca_*.md",
]


@dataclass
class ReceiptCandidate:
    path: str
    name: str
    is_valid_json: bool
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReportMoveCandidate:
    path: str
    name: str
    age_days: int
    is_referenced: bool
    referrers: list[str]
    target_path: str | None
    action: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RootFileCandidate:
    path: str
    name: str
    category: str
    reason: str
    target_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ClutterPlanResult:
    uncommitted_receipts_count: int = 0
    reports_archive_count: int = 0
    reports_kept_referenced_count: int = 0
    root_archive_count: int = 0
    root_review_count: int = 0
    uncommitted_receipts: list[ReceiptCandidate] = field(default_factory=list)
    reports: list[ReportMoveCandidate] = field(default_factory=list)
    root_files: list[RootFileCandidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uncommitted_receipts_count": self.uncommitted_receipts_count,
            "reports_archive_count": self.reports_archive_count,
            "reports_kept_referenced_count": self.reports_kept_referenced_count,
            "root_archive_count": self.root_archive_count,
            "root_review_count": self.root_review_count,
            "uncommitted_receipts": [r.to_dict() for r in self.uncommitted_receipts],
            "reports": [r.to_dict() for r in self.reports],
            "root_files": [r.to_dict() for r in self.root_files],
        }


def _run_git_cmd(repo_root: Path, args: list[str]) -> str:
    """Run a git command safely in repo_root without printing credentials."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_root)] + args,
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def plan_uncommitted_receipts(repo_root: Path) -> list[ReceiptCandidate]:
    """Find uncommitted PR receipts in reports/ directory."""
    candidates: list[ReceiptCandidate] = []
    reports_dir = repo_root / "reports"
    if not reports_dir.exists():
        return candidates

    # Untracked files in reports/
    out = _run_git_cmd(repo_root, ["status", "--porcelain", "reports/"])
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("??"):
            fpath_str = line[3:].strip()
            p = repo_root / fpath_str
            if not p.is_file() or not p.name.startswith("pr_review_"):
                continue

            # Validate JSON syntax
            is_valid = False
            reason = "Valid PR review receipt"
            action = "COMMIT_RECEIPT"
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    is_valid = True
                else:
                    reason = "JSON root is not an object"
                    action = "REVIEW"
            except Exception as exc:
                reason = f"Malformed JSON: {exc}"
                action = "REVIEW"

            candidates.append(
                ReceiptCandidate(
                    path=fpath_str,
                    name=p.name,
                    is_valid_json=is_valid,
                    action=action,
                    reason=reason,
                )
            )
    return candidates


def _build_referenced_map(
    repo_root: Path,
    older_entries: list[tuple[Path, int, str]],
) -> dict[str, list[str]]:
    import tempfile

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as pat_file:
        for p, _, _ in older_entries:
            pat_file.write(f"{p.name}\n")
        pat_file.flush()

        grep_out = _run_git_cmd(
            repo_root,
            ["grep", "-F", "-f", pat_file.name, "--", ":!reports/", ":!.worktrees/"],
        )

    referenced_map: dict[str, list[str]] = {}
    for line in grep_out.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        file_part, _, match_part = line.partition(":")
        for p, _, _ in older_entries:
            if p.name in match_part:
                if file_part not in referenced_map.setdefault(p.name, []):
                    referenced_map[p.name].append(file_part)
    return referenced_map


def _classify_older_reports(
    older_entries: list[tuple[Path, int, str]],
    referenced_map: dict[str, list[str]],
) -> list[ReportMoveCandidate]:
    candidates: list[ReportMoveCandidate] = []
    for p, age_days, target_dir_str in older_entries:
        referrers = referenced_map.get(p.name, [])
        if referrers:
            candidates.append(
                ReportMoveCandidate(
                    path=f"reports/{p.name}",
                    name=p.name,
                    age_days=age_days,
                    is_referenced=True,
                    referrers=referrers[:5],
                    target_path=None,
                    action="KEEP_REFERENCED",
                    reason=f"Referenced in {len(referrers)} tracked files",
                )
            )
        else:
            candidates.append(
                ReportMoveCandidate(
                    path=f"reports/{p.name}",
                    name=p.name,
                    age_days=age_days,
                    is_referenced=False,
                    referrers=[],
                    target_path=f"{target_dir_str}/{p.name}",
                    action="ARCHIVE_MOVE",
                    reason=f"Eligible for move to {target_dir_str}/",
                )
            )
    return candidates


def plan_report_archival(
    repo_root: Path,
    policy: StorageRetentionPolicy,
) -> list[ReportMoveCandidate]:
    """Identify tracked top-level reports older than threshold, excluding referenced files."""
    candidates: list[ReportMoveCandidate] = []
    reports_dir = repo_root / "reports"
    if not reports_dir.exists():
        return candidates

    now = dt.datetime.now(dt.timezone.utc)
    threshold_days = policy.reports.archive_age_days

    older_entries: list[tuple[Path, int, str]] = []
    for p in reports_dir.iterdir():
        if not p.is_file() or p.name.startswith("."):
            continue

        try:
            mdate = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc)
        except OSError:
            mdate = now

        age_days = (now - mdate).days
        year_month = mdate.strftime("%Y-%m")
        target_dir_str = f"{policy.reports.archive_root}/{year_month}"

        if age_days <= threshold_days:
            candidates.append(
                ReportMoveCandidate(
                    path=f"reports/{p.name}",
                    name=p.name,
                    age_days=age_days,
                    is_referenced=False,
                    referrers=[],
                    target_path=None,
                    action="KEEP_RECENT",
                    reason=f"Modified {age_days}d ago (threshold: {threshold_days}d)",
                )
            )
        else:
            older_entries.append((p, age_days, target_dir_str))

    if not older_entries:
        return candidates

    referenced_map = _build_referenced_map(repo_root, older_entries)
    candidates.extend(_classify_older_reports(older_entries, referenced_map))
    return candidates


def plan_root_clutter(
    repo_root: Path,
    policy: StorageRetentionPolicy,
) -> list[RootFileCandidate]:
    """Categorize loose files in repository root without touching protected files."""
    candidates: list[RootFileCandidate] = []
    now_date_str = dt.datetime.now(dt.timezone.utc).strftime("%m-%d-%Y")
    archive_dest = f"archive/root_{now_date_str}"

    for entry in repo_root.iterdir():
        if entry.is_dir() or entry.name.startswith("."):
            continue

        name_lower = entry.name.lower()

        # Check NEVER_TOUCH globs (credential & DB firewall)
        is_protected = any(
            fnmatch.fnmatch(entry.name, pat) or fnmatch.fnmatch(name_lower, pat.lower())
            for pat in policy.never_touch
        )
        if is_protected:
            candidates.append(
                RootFileCandidate(
                    path=entry.name,
                    name=entry.name,
                    category="KEEP",
                    reason="Protected by retention policy never_touch firewall",
                    target_path=None,
                )
            )
            continue

        # Check standard root keep entry points
        if name_lower in STANDARD_ROOT_KEEP:
            candidates.append(
                RootFileCandidate(
                    path=entry.name,
                    name=entry.name,
                    category="KEEP",
                    reason="Standard repository root entry point",
                    target_path=None,
                )
            )
            continue

        # Check known archive patterns
        is_archive_match = any(
            fnmatch.fnmatch(name_lower, pat.lower()) for pat in ARCHIVE_PATTERNS
        )
        if is_archive_match:
            candidates.append(
                RootFileCandidate(
                    path=entry.name,
                    name=entry.name,
                    category="ARCHIVE_CANDIDATE",
                    reason="Transient test output, debug log, or superseded manifest",
                    target_path=f"{archive_dest}/{entry.name}",
                )
            )
        else:
            candidates.append(
                RootFileCandidate(
                    path=entry.name,
                    name=entry.name,
                    category="REVIEW",
                    reason="Uncategorized loose root file requiring operator review",
                    target_path=None,
                )
            )

    return candidates


def plan_repo_clutter(
    repo_root: Path | None = None,
    policy: StorageRetentionPolicy | None = None,
) -> ClutterPlanResult:
    """Run full clutter analysis for reports and root files."""
    r_root = (repo_root or MAIN_REPO_ROOT).resolve()
    pol = policy or load_storage_retention_policy()

    receipts = plan_uncommitted_receipts(r_root)
    reports = plan_report_archival(r_root, pol)
    root_files = plan_root_clutter(r_root, pol)

    res = ClutterPlanResult(
        uncommitted_receipts_count=len(receipts),
        reports_archive_count=sum(1 for r in reports if r.action == "ARCHIVE_MOVE"),
        reports_kept_referenced_count=sum(1 for r in reports if r.action == "KEEP_REFERENCED"),
        root_archive_count=sum(1 for rf in root_files if rf.category == "ARCHIVE_CANDIDATE"),
        root_review_count=sum(1 for rf in root_files if rf.category == "REVIEW"),
        uncommitted_receipts=receipts,
        reports=reports,
        root_files=root_files,
    )
    return res
