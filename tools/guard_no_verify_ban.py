#!/usr/bin/env python3
"""Pre-commit & CI guard: Ban '--no-verify' in PR governance and merge tools.

Enforces Layer 4 Git Lifecycle Invariant:
All merge commits and PR landings must execute through governed git hooks.
Bypassing hooks with '--no-verify' is strictly prohibited.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_DIRS = [
    ROOT / "tools" / "pr_governance_core",
]
TARGET_FILES = [
    ROOT / "tools" / "pr_governance.py",
    ROOT / "tools" / "rebase_orchestrator.py",
    ROOT / "tools" / "verify_merge_receipt.py",
]

# Explicit allowlist of non-git-commit flags (e.g., CLI argument definitions that do not bypass git hooks)
ALLOWLIST_SUBSTRINGS = [
    "--no-verify-disk",
]


def _collect_target_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for d in TARGET_DIRS:
        if d.is_dir():
            files.extend(p for p in d.rglob("*.py") if not p.name.startswith("."))
    for f in TARGET_FILES:
        if f.is_file():
            files.append(f)
    return sorted(set(files))


def _scan_file_for_no_verify(p: Path, root: Path) -> list[str]:
    violations: list[str] = []
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Failed to read {p}: {exc}"]

    for lineno, line in enumerate(content.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("#") or "--no-verify" not in stripped:
            continue
        if any(allowed in stripped for allowed in ALLOWLIST_SUBSTRINGS):
            continue
        violations.append(f"{p.relative_to(root)}:{lineno}: Found banned '--no-verify' flag: {stripped}")
    return violations


def check_no_verify_ban(root: Path = ROOT) -> tuple[bool, list[str]]:
    """Scans governance merge code for forbidden '--no-verify' invocations."""
    violations: list[str] = []
    for p in _collect_target_files(root):
        violations.extend(_scan_file_for_no_verify(p, root))
    return len(violations) == 0, violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ban '--no-verify' in PR governance and merge tools")
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root")
    args = parser.parse_args(argv)

    print("=" * 80)
    print("  NO-VERIFY BAN GUARD (PR GOVERNANCE INTEGRITY)")
    print(f"  Target Root: {args.root}")
    print("=" * 80)

    ok, violations = check_no_verify_ban(args.root)
    if not ok:
        print("\n❌ COMMIT REJECTED: Banned '--no-verify' flag detected in PR governance:")
        for v in violations:
            print(f"   • {v}")
        print("\n   Prescriptive Remediation:")
        print("   Remove '--no-verify' from git commands in tools/pr_governance_core/. All merges must")
        print("   execute under pre-commit and pre-merge-commit hook validation.")
        print("=" * 80)
        return 1

    print("✅ Zero '--no-verify' occurrences found in PR governance tools.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
