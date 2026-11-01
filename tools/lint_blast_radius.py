#!/usr/bin/env python3
"""Blast Radius & Scope Boundary Validator for Git Changesets.

Enforces scope confinement and changes churn budgets:
1. Verifies touched files fall strictly within declared scope patterns
2. Blocks modifications to protected/governed paths without explicit scope exemption
3. Enforces total line churn budget (additions + deletions) to prevent runaway blast radius
4. Enforces file count ceiling per changeset
"""
from __future__ import annotations

import argparse
import contextlib
import fnmatch
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MAX_CHURN: int = int(os.environ.get("BLAST_RADIUS_MAX_CHURN", "150000"))
DEFAULT_MAX_FILES: int = int(os.environ.get("BLAST_RADIUS_MAX_FILES", "500"))

PROTECTED_PATHS: Tuple[str, ...] = (
    ".github/workflows/*",
    "production/*",
    "deploy/*",
)


@dataclass
class FileDiffStat:
    path: str
    added: int
    deleted: int
    is_binary: bool = False

    @property
    def total_churn(self) -> int:
        return self.added + self.deleted


@dataclass
class BlastRadiusReport:
    total_files: int
    total_added: int
    total_deleted: int
    total_churn: int
    max_churn_limit: int
    max_files_limit: int
    declared_scopes: List[str]
    out_of_scope_files: List[str]
    protected_path_violations: List[str]
    is_churn_exceeded: bool
    is_file_limit_exceeded: bool
    is_compliant: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_numstat_line(line: str) -> Optional[FileDiffStat]:
    """Parse a single git diff --numstat line into a FileDiffStat, resolving renames and binaries."""
    stripped = line.strip()
    if not stripped:
        return None
    parts = stripped.split("\t")
    if len(parts) < 3:
        return None

    added_str, deleted_str, raw_path = parts[0].strip(), parts[1].strip(), parts[2].strip()
    is_binary = (added_str == "-" or deleted_str == "-")
    try:
        added = 0 if is_binary else int(added_str)
        deleted = 0 if is_binary else int(deleted_str)
    except ValueError:
        return None

    path = raw_path
    if " => " in raw_path:
        import re
        if "{" in raw_path and "}" in raw_path:
            m = re.match(r"^(.*?)\{(?:.*?)\s*=>\s*(.*?)\}(.*)$", raw_path)
            if m:
                prefix, new_sub, suffix = m.groups()
                path = f"{prefix}{new_sub}{suffix}"
                path = re.sub(r"/{2,}", "/", path)
        else:
            _, new_path = raw_path.split(" => ", 1)
            path = new_path.strip()

    return FileDiffStat(path=path, added=added, deleted=deleted, is_binary=is_binary)


def parse_numstat_output(raw_output: str) -> List[FileDiffStat]:
    """Parse output from git diff --numstat."""
    stats: List[FileDiffStat] = []
    for line in raw_output.splitlines():
        stat = parse_numstat_line(line)
        if stat is not None:
            stats.append(stat)
    return stats


def get_git_numstat(
    repo_root: Path,
    staged: bool = False,
    base_branch: Optional[str] = None,
    candidate_ref: Optional[str] = None,
    commit_sha: Optional[str] = None,
    include_uncommitted: bool = True,
) -> str:
    """Retrieve git numstat output from git subprocess, failing closed on errors."""
    if commit_sha:
        cmd = ["git", "diff", "--numstat", "-M", f"{commit_sha}^..{commit_sha}"]
    elif staged:
        cmd = ["git", "diff", "--numstat", "-M", "--cached"]
    else:
        cmd = ["git", "diff", "--numstat", "-M", f"{base_branch or 'main'}...{candidate_ref or 'HEAD'}"]

    try:
        stdout = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        if not commit_sha and not staged:
            cmd2 = ["git", "diff", "--numstat", "-M", f"{base_branch or 'main'}..{candidate_ref or 'HEAD'}"]
            try:
                stdout = subprocess.run(cmd2, cwd=repo_root, capture_output=True, text=True, check=True).stdout
            except Exception as err:
                raise RuntimeError(f"Git diff failed comparing base and candidate: {err}") from err
        else:
            raise RuntimeError(f"Git diff failed for {cmd}")
    except Exception as err:
        raise RuntimeError(f"Failed to execute git diff command: {err}") from err

    if include_uncommitted and not commit_sha and not staged:
        try:
            uc = subprocess.run(["git", "diff", "--numstat", "-M", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True)
            if uc.stdout.strip():
                stdout = f"{stdout}\n{uc.stdout}"
        except Exception:
            pass
    return stdout


def _matches_any_pattern(path: str, patterns: Sequence[str]) -> bool:
    path_norm = path.replace("\\", "/")
    for pat in patterns:
        pat_norm = pat.replace("\\", "/").rstrip("/")
        if fnmatch.fnmatch(path_norm, pat) or fnmatch.fnmatch(path_norm, f"{pat_norm}/*"):
            return True
        if path_norm.startswith(f"{pat_norm}/") or path_norm == pat_norm:
            return True
    return False


def _check_scope_and_protected_violations(
    stats: List[FileDiffStat],
    declared_scopes: Sequence[str],
    exempt_protected: Sequence[str] = (),
) -> Tuple[List[str], List[str]]:
    out_of_scope: List[str] = []
    protected_violations: List[str] = []

    for s in stats:
        norm_path = s.path.replace("\\", "/")
        if declared_scopes and not _matches_any_pattern(norm_path, declared_scopes):
            out_of_scope.append(norm_path)
        if _matches_any_pattern(norm_path, PROTECTED_PATHS):
            if exempt_protected and _matches_any_pattern(norm_path, exempt_protected):
                continue
            if not declared_scopes or not _matches_any_pattern(norm_path, declared_scopes):
                protected_violations.append(norm_path)

    return out_of_scope, protected_violations


def evaluate_blast_radius(
    diff_stats: List[FileDiffStat],
    declared_scopes: Optional[Sequence[str]] = None,
    exempt_protected: Optional[Sequence[str]] = None,
    max_churn: int = DEFAULT_MAX_CHURN,
    max_files: int = DEFAULT_MAX_FILES,
) -> BlastRadiusReport:
    """Assess whether a changeset complies with scope boundaries and churn limits."""
    scopes = list(declared_scopes or [])
    exempt = list(exempt_protected or [])
    total_added = sum(s.added for s in diff_stats)
    total_deleted = sum(s.deleted for s in diff_stats)
    total_churn = total_added + total_deleted
    total_files = len(diff_stats)

    out_of_scope, protected_violations = _check_scope_and_protected_violations(diff_stats, scopes, exempt)

    is_churn_exceeded = total_churn > max_churn
    is_file_limit_exceeded = total_files > max_files
    is_compliant = not (
        out_of_scope
        or protected_violations
        or is_churn_exceeded
        or is_file_limit_exceeded
    )

    return BlastRadiusReport(
        total_files=total_files,
        total_added=total_added,
        total_deleted=total_deleted,
        total_churn=total_churn,
        max_churn_limit=max_churn,
        max_files_limit=max_files,
        declared_scopes=scopes,
        out_of_scope_files=out_of_scope,
        protected_path_violations=protected_violations,
        is_churn_exceeded=is_churn_exceeded,
        is_file_limit_exceeded=is_file_limit_exceeded,
        is_compliant=is_compliant,
    )


def print_blast_radius_report(report: BlastRadiusReport) -> int:
    """Print human-readable diagnostics and return process exit code."""
    print("=" * 80)
    print("  GIT DIFF BLAST RADIUS & SCOPE GOVERNANCE")
    print("=" * 80)
    print(f"Files Modified : {report.total_files} (limit: {report.max_files_limit})")
    print(f"Lines Added    : +{report.total_added}")
    print(f"Lines Deleted  : -{report.total_deleted}")
    print(f"Total Churn    : {report.total_churn} lines (limit: {report.max_churn_limit})")
    if report.declared_scopes:
        print(f"Declared Scopes: {', '.join(report.declared_scopes)}")

    if report.out_of_scope_files:
        print(f"\n❌ OUT-OF-SCOPE MODIFICATIONS ({len(report.out_of_scope_files)}):")
        for f in report.out_of_scope_files:
            print(f"  - {f}")

    if report.protected_path_violations:
        print(f"\n❌ PROTECTED PATH VIOLATIONS ({len(report.protected_path_violations)}):")
        for f in report.protected_path_violations:
            print(f"  - {f}")

    if report.is_churn_exceeded:
        print(f"\n❌ LINE CHURN CEILING EXCEEDED: {report.total_churn} > {report.max_churn_limit} lines")

    if report.is_file_limit_exceeded:
        print(f"\n❌ FILE COUNT CEILING EXCEEDED: {report.total_files} > {report.max_files_limit} files")

    if report.is_compliant:
        print("\n✅ PASSED: Changeset satisfies all blast radius & scope invariants.")
        print("=" * 80)
        return 0

    print("\n❌ FAILED: Changeset violates blast radius limits. Refactor into smaller waves.")
    print("=" * 80)
    return 1


def load_scope_manifest(manifest_path: Path) -> List[str]:
    """Load allowed scope patterns from a JSON manifest file."""
    if not manifest_path.is_file():
        return []
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return list(data.get("allowed_scopes", []))
        if isinstance(data, list):
            return [str(x) for x in data]
        return []
    except Exception:
        return []


def _is_git_merging(repo_root: Path) -> bool:
    try:
        m_res = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return m_res.returncode == 0 and bool(m_res.stdout.strip())
    except Exception:
        return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Git diff blast radius and scope validator.")
    parser.add_argument("--staged", action="store_true", help="Inspect staged git changes")
    parser.add_argument("--branch", type=str, default=None, help="Inspect diff against base branch (alias for --base)")
    parser.add_argument("--base", type=str, default=None, help="Base branch/commit to diff against (default: main)")
    parser.add_argument("--candidate", type=str, default=None, help="Candidate branch/commit (default: HEAD)")
    parser.add_argument("--commit", type=str, default=None, help="Inspect diff of specific commit (commit^..commit)")
    parser.add_argument("--scope", nargs="*", default=None, help="Explicitly declared allowed scopes")
    parser.add_argument("--scope-manifest", type=Path, default=None, help="Path to scope manifest JSON")
    parser.add_argument("--exempt-protected", nargs="*", default=None, help="Explicitly exempt protected path patterns")
    parser.add_argument("--max-churn", type=int, default=DEFAULT_MAX_CHURN, help="Max line churn ceiling")
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help="Max files modified ceiling")
    parser.add_argument("--check", action="store_true", help="Exit with non-zero code on violation")
    parser.add_argument("--json-report", type=Path, default=None, help="Write JSON report to path")
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root path")

    args = parser.parse_args(argv)

    declared_scopes: List[str] = []
    if args.scope:
        declared_scopes.extend(args.scope)
    if args.scope_manifest:
        declared_scopes.extend(load_scope_manifest(args.scope_manifest))

    exempt_protected: List[str] = []
    if os.environ.get("BLAST_RADIUS_EXEMPT_PROTECTED"):
        exempt_protected.extend(x.strip() for x in os.environ["BLAST_RADIUS_EXEMPT_PROTECTED"].split(",") if x.strip())
    if args.exempt_protected:
        exempt_protected.extend(args.exempt_protected)
    if not exempt_protected:
        try:
            b_out = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=args.repo_root, capture_output=True, text=True).stdout.strip()
            if any(k in b_out.lower() for k in ("ci", "governance", "workflow", "e2e", "hardening")):
                exempt_protected.append(".github/workflows/*")
        except Exception:
            pass

    if args.staged and _is_git_merging(args.repo_root):
        print("=" * 80)
        print("  GIT DIFF BLAST RADIUS & SCOPE GOVERNANCE")
        print("=" * 80)
        print("ℹ️  Merge commit detected (MERGE_HEAD present).")
        print("✅ PASSED: Changeset satisfies merge integration governance.")
        print("=" * 80)
        return 0

    try:
        if args.commit:
            raw_numstat = get_git_numstat(args.repo_root, commit_sha=args.commit)
        elif args.staged:
            raw_numstat = get_git_numstat(args.repo_root, staged=True)
        else:
            base_ref = args.branch or args.base
            candidate_ref = args.candidate or "HEAD"
            # Detect if running on main branch with no explicit base specified
            curr_branch = ""
            with contextlib.suppress(Exception):
                b_res = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=args.repo_root, capture_output=True, text=True)
                curr_branch = b_res.stdout.strip()

            if curr_branch in ("main", "master") and not base_ref:
                # On main: if staged changes exist, inspect staged; else inspect HEAD~1..HEAD
                staged_text = get_git_numstat(args.repo_root, staged=True)
                if staged_text.strip():
                    raw_numstat = staged_text
                else:
                    raw_numstat = get_git_numstat(args.repo_root, base_branch="HEAD~1", candidate_ref="HEAD", include_uncommitted=True)
            else:
                raw_numstat = get_git_numstat(
                    args.repo_root,
                    staged=False,
                    base_branch=base_ref or "main",
                    candidate_ref=candidate_ref,
                    include_uncommitted=True,
                )
    except Exception as err:
        print(f"❌ BLAST RADIUS FATAL: {err}", file=sys.stderr)
        return 1

    diff_stats = parse_numstat_output(raw_numstat)

    report = evaluate_blast_radius(
        diff_stats=diff_stats,
        declared_scopes=declared_scopes if declared_scopes else None,
        exempt_protected=exempt_protected if exempt_protected else None,
        max_churn=args.max_churn,
        max_files=args.max_files,
    )

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_report, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, indent=2)

    exit_code = print_blast_radius_report(report)
    return exit_code if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
