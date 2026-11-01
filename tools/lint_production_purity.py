#!/usr/bin/env python3
"""
tools/lint_production_purity.py

Enforces AGENTS.md Invariant 9: Production Directory Anti-Pollution & Synthetic Fixture Quarantine.

Checks:
1. Prohibits evaluation and test code under evals/ and tests/ from defaulting write paths
   (e.g., output_dir) into the production output/ directory.
2. Scans output/ to detect prohibited synthetic signatures, test personas leaked into wrong projects,
   or fabricated dates (such as the hallucinated project_nexus/09-12-2026).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Code paths that must NOT default write targets into output/
SCAN_DIRS = ["dual_pipeline", "tools", "evals", "tests"]

# Files exempt from self-matching linter rules
EXEMPT_FILES = {
    "tools/lint_production_purity.py",
    "tests/test_lint_production_purity.py",
    "tests/test_release_uwg_and_governance_gates_adversarial.py",
    "tools/validate_output_hierarchy.py",
    "tools/validate_output_hierarchy_core/constants.py",
    "tools/validate_output_hierarchy_core/scanner.py",
    "dual_pipeline/transcript_pipeline_core/cli_parser.py",
}

# Regex patterns in code that indicate hardcoded default write destinations to output/
PROHIBITED_CODE_PATTERNS: List[Tuple[str, re.Pattern[str]]] = [
    (
        "Hardcoded default write to output/",
        re.compile(r"""(?:output_dir\s*=\s*(?:output_dir\s+or\s+)?\(?ROOT\s*/\s*["']output["']|\.output_dir\s*=\s*Path\([^)]*["']output["'])"""),
    ),
    (
        "Hardcoded output/project_nexus/09-12-2026 default write",
        re.compile(r"""["']output/project_nexus/09-12-2026["']"""),
    ),
]

# Markers in output/ that indicate synthetic test fixture leakage
PROHIBITED_OUTPUT_MARKERS: List[Tuple[str, str]] = [
    ("Synthetic Test Topic: Thirty legacy platforms", "Thirty legacy platforms identified for consolidation"),
    ("Synthetic Candidate Node", "candidate-unverified-speculation"),
    ("Synthetic Node ID Prefix", "cnode_"),
]

# Specific forbidden directories in output/
PROHIBITED_OUTPUT_DIRS = [
    "project_nexus/09-12-2026",
]


def lint_code_for_production_writes(
    repo_root: Path, target_files: Optional[Sequence[Path]] = None
) -> List[str]:
    """Scans code files to ensure no test/eval script defaults write targets to output/."""
    violations: List[str] = []

    if target_files is not None:
        scan_paths = [
            p
            for p in target_files
            if p.suffix == ".py"
            and any(
                p.resolve().is_relative_to((repo_root / d).resolve())
                for d in SCAN_DIRS
            )
        ]
    else:
        scan_paths = []
        for s_dir in SCAN_DIRS:
            scan_path = repo_root / s_dir
            if scan_path.is_dir():
                scan_paths.extend(scan_path.rglob("*.py"))

    for p in scan_paths:
        try:
            rel_str = p.relative_to(repo_root).as_posix()
        except ValueError:
            rel_str = p.as_posix()

        if rel_str in EXEMPT_FILES:
            continue

        try:
            content = p.read_text(encoding="utf-8")
        except Exception:
            continue

        for desc, pattern in PROHIBITED_CODE_PATTERNS:
            for match in pattern.finditer(content):
                line_no = content[: match.start()].count("\n") + 1
                matched_snippet = match.group(0).strip()
                violations.append(
                    f"[CODE VIOLATION] {rel_str}:{line_no}: {desc} -> '{matched_snippet}'"
                )

    return violations


def lint_output_directory_purity(
    repo_root: Path, target_files: Optional[Sequence[Path]] = None
) -> List[str]:
    """Scans output/ directory to ensure no synthetic fixtures or forbidden folders exist."""
    violations: List[str] = []

    # Prohibit legacy duplicate outputs symlink or directory at repo root
    outputs_path = repo_root / "outputs"
    if outputs_path.exists() or outputs_path.is_symlink():
        violations.append(
            "[DIRECTORY VIOLATION] Prohibited legacy duplicate directory/symlink 'outputs' exists at repo root. Only canonical 'output/' is permitted."
        )

    output_root = repo_root / "output"

    if not output_root.is_dir():
        return violations

    # Check for explicitly prohibited directories
    for forbidden in PROHIBITED_OUTPUT_DIRS:
        f_path = output_root / forbidden
        if f_path.exists():
            violations.append(
                f"[DIRECTORY VIOLATION] Prohibited synthetic fixture directory exists: output/{forbidden}"
            )

    # Scan files in output/ for prohibited synthetic markers
    if target_files is not None:
        files_to_scan = [
            p
            for p in target_files
            if p.is_file()
            and p.resolve().is_relative_to(output_root.resolve())
        ]
    else:
        files_to_scan = [p for p in output_root.rglob("*") if p.is_file()]

    binary_exts = {
        ".wav", ".mp3", ".m4a", ".db", ".sqlite", ".sqlite3", ".pyc", ".png",
        ".jpg", ".jpeg", ".lock", ".bin", ".tar", ".gz", ".zip",
    }

    for p in files_to_scan:
        if p.suffix.lower() in binary_exts:
            continue

        try:
            rel_str = p.relative_to(repo_root).as_posix()
        except ValueError:
            rel_str = p.as_posix()

        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        for desc, marker in PROHIBITED_OUTPUT_MARKERS:
            if marker in content:
                violations.append(
                    f"[ARTIFACT VIOLATION] {rel_str} contains {desc}"
                )

        if rel_str.startswith("output/project_nexus/"):
            if "Alex Rivera" in content:
                violations.append(
                    f"[PERSONA CONTAMINATION VIOLATION] {rel_str} contains APEX_TRANS attendee 'Alex Rivera' in project_nexus"
                )
            if "Jordan Taylor" in content:
                violations.append(
                    f"[PERSONA CONTAMINATION VIOLATION] {rel_str} contains APEX_TRANS attendee 'Jordan Taylor' in project_nexus"
                )

    return violations


def lint_staged_worktrees(repo_root: Path, files: List[str] | None = None) -> List[str]:
    """Ensures no worktree files or gitlinks under .worktrees/ are staged in the repository."""
    violations: List[str] = []
    if files:
        for f in files:
            norm = f.replace("\\", "/")
            if norm.startswith("./"):
                norm = norm[2:]
            if norm.startswith(".worktrees/") or norm == ".worktrees":
                violations.append(
                    f"[WORKTREE STAGING VIOLATION] Staging worktree path '{norm}' in repository is strictly prohibited."
                )
    try:
        import subprocess
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        for line in res.stdout.splitlines():
            norm = line.strip().replace("\\", "/")
            if norm.startswith("./"):
                norm = norm[2:]
            if norm.startswith(".worktrees/") or norm == ".worktrees":
                violations.append(
                    f"[WORKTREE STAGING VIOLATION] Staging worktree path '{norm}' in repository is strictly prohibited."
                )
    except Exception:
        pass
    return list(dict.fromkeys(violations))


def _get_staged_file_paths(repo_root: Path) -> List[Path]:
    try:
        import subprocess
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        return [(repo_root / line.strip()).resolve() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint production directory purity and synthetic fixture quarantine.")
    parser.add_argument("--root", type=str, default=str(ROOT), help="Repository root path")
    parser.add_argument("--all", action="store_true", help="Inspect all files (compatibility flag)")
    parser.add_argument("--staged", action="store_true", help="Inspect staged files only (compatibility flag)")
    parser.add_argument("files", nargs="*", help="Optional file paths passed by commit verifiers")
    args = parser.parse_args()

    repo_root = Path(args.root).resolve()

    target_paths: Optional[List[Path]] = None
    if args.files:
        target_paths = [(repo_root / f).resolve() for f in args.files]
    elif args.staged and not args.all:
        target_paths = _get_staged_file_paths(repo_root)

    code_violations = lint_code_for_production_writes(repo_root, target_paths)
    output_violations = lint_output_directory_purity(repo_root, target_paths)
    worktree_violations = lint_staged_worktrees(repo_root, args.files if args.files else None)

    all_violations = code_violations + output_violations + worktree_violations

    if all_violations:
        print(f"❌ Production Purity Lint FAILED ({len(all_violations)} violations found):", file=sys.stderr)
        for v in all_violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("✅ Production Purity Lint PASSED (0 violations).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

