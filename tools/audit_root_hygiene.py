#!/usr/bin/env python3
"""tools/audit_root_hygiene.py

Pre-commit root-file allowlist and large file size auditor (Wave 6 / Finding X-33).
Enforces:
1. Root File Allowlist: No unauthorized files directly in the repository root.
2. File Size Guard: No committed or staged files exceeding 5 MB (5,242,880 bytes).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Sequence, Set, Tuple

DEFAULT_CONFIG_PATH = Path("config/governance/root_file_allowlist.json")
MAX_FILE_SIZE_DEFAULT = 5 * 1024 * 1024  # 5 MB


def load_allowlist(config_path: Path) -> Tuple[Set[str], int, Set[str]]:
    if not config_path.is_file():
        return set(), MAX_FILE_SIZE_DEFAULT, set()
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        allowed = set(data.get("allowed_root_files", []))
        max_size = int(data.get("max_file_size_bytes", MAX_FILE_SIZE_DEFAULT))
        grandfathered = set(data.get("grandfathered_large_files", []))
        return allowed, max_size, grandfathered
    except Exception:
        return set(), MAX_FILE_SIZE_DEFAULT, set()


def get_staged_files(repo_root: Path) -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [f.strip() for f in proc.stdout.splitlines() if f.strip()]
    except Exception:
        return []


def get_all_tracked_files(repo_root: Path) -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [f.strip() for f in proc.stdout.splitlines() if f.strip()]
    except Exception:
        return []


def _check_root_allowlist(p: Path, allowed_root_files: Set[str]) -> Optional[str]:
    if len(p.parts) == 1 and p.name not in allowed_root_files:
        return (
            f"[ROOT_FILE_ALLOWLIST_VIOLATION] '{p.name}' is not in the root-file allowlist. "
            f"Move it to an appropriate subfolder (e.g. tools/, docs/, reports/, archive/) "
            f"or register it in {DEFAULT_CONFIG_PATH}."
        )
    return None


def _check_file_size(
    repo_root: Path,
    f_str: str,
    max_size_bytes: int,
    grandfathered: Set[str],
) -> Optional[str]:
    if f_str in grandfathered:
        return None
    full_p = repo_root / f_str
    if not full_p.is_file():
        return None
    try:
        sz = full_p.stat().st_size
        if sz > max_size_bytes:
            mb = sz / (1024 * 1024)
            return (
                f"[FILE_SIZE_VIOLATION] '{f_str}' is {mb:.2f} MB, exceeding the 5 MB ceiling ({max_size_bytes} bytes). "
                f"Large binary assets, model weights, or scratch files must not be tracked in git."
            )
    except OSError:
        pass
    return None


def audit_root_and_size_hygiene(
    repo_root: Path,
    staged_only: bool = False,
    config_path: Path | None = None,
) -> Tuple[bool, List[str]]:
    cfg_file = config_path or (repo_root / DEFAULT_CONFIG_PATH)
    allowed_root_files, max_size_bytes, grandfathered_files = load_allowlist(cfg_file)

    files_to_check = (
        get_staged_files(repo_root) if staged_only else get_all_tracked_files(repo_root)
    )
    violations: List[str] = []

    for f_str in files_to_check:
        p = Path(f_str)
        root_v = _check_root_allowlist(p, allowed_root_files)
        if root_v:
            violations.append(root_v)

        size_v = _check_file_size(repo_root, f_str, max_size_bytes, grandfathered_files)
        if size_v:
            violations.append(size_v)

    return len(violations) == 0, violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit repository root clutter and file size guards."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--staged", action="store_true", help="Audit only staged git files.")
    parser.add_argument(
        "--check", action="store_true", help="Audit repository files and fail if violations exist."
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="Path to root_file_allowlist.json."
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    staged = args.staged

    compliant, violations = audit_root_and_size_hygiene(
        repo_root, staged_only=staged, config_path=args.config
    )

    if not compliant:
        print("❌ Root & File Size Hygiene Violations Detected:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    mode_str = "staged files" if staged else "all tracked files"
    print(f"✅ Root File Allowlist & File Size Guard: PASS ({mode_str} verified compliant).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
