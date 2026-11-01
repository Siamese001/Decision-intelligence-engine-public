#!/usr/bin/env python3
"""Fail closed when a retired synthetic embedding identifier enters the workspace."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

RETIRED_IDENTIFIERS = (
    "deterministic" + "-test",
    "nee/" + "deterministic-hybrid-test",
    "isolated" + "-test-fixture",
    "Test" + "EmbeddingBackend",
    "_Production" + "DeterministicBackend",
    "allow_" + "test_backend",
)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _registered_nested_worktree_roots(repository_root: Path) -> tuple[Path, ...]:
    """Return separate registered checkouts nested below this checkout.

    A repository-local ``.local`` directory may contain other Git worktrees.
    They are independently governed checkouts, not mutable artifacts of the
    current checkout.  The ban must scan current runtime state under ``.local``
    while leaving those independent worktrees to their own hooks.
    """

    try:
        completed = subprocess.run(
            ["git", "-C", str(repository_root), "worktree", "list", "--porcelain"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return ()
    if completed.returncode:
        return ()
    roots: set[Path] = set()
    for line in completed.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        candidate = Path(line[len("worktree ") :]).expanduser().resolve()
        if candidate != repository_root and _is_within(candidate, repository_root):
            roots.add(candidate)
    return tuple(sorted(roots, key=lambda value: str(value).casefold()))


def _resolve_ripgrep(repository_root: Path | None = None) -> str:
    override = os.environ.get("RIPGREP_PATH")
    if override and Path(override).is_file():
        return override
    which = shutil.which("rg") or shutil.which("rg.exe")
    if which:
        return which
    sibling = Path(sys.executable).parent / ("rg.exe" if sys.platform == "win32" else "rg")
    if sibling.is_file():
        return str(sibling)
    if repository_root is not None:
        venv_rg = repository_root / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("rg.exe" if sys.platform == "win32" else "rg")
        if venv_rg.is_file():
            return str(venv_rg)
    repo_guess = Path(__file__).resolve().parents[1] / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / ("rg.exe" if sys.platform == "win32" else "rg")
    if repo_guess.is_file():
        return str(repo_guess)
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            winget_candidates = list(Path(local_app_data).glob("Microsoft/WinGet/Packages/BurntSushi.ripgrep*/**/rg.exe"))
            if winget_candidates and winget_candidates[0].is_file():
                return str(winget_candidates[0])
            codex_candidates = list(Path(local_app_data).glob("OpenAI/Codex/bin/*/rg.exe"))
            if codex_candidates and codex_candidates[0].is_file():
                return str(codex_candidates[0])
        program_files = os.environ.get("PROGRAMFILES")
        if program_files:
            pf = Path(program_files) / "ripgrep" / "rg.exe"
            if pf.is_file():
                return str(pf)
    return "rg"


def _collect_staged_paths(root: Path, nested_worktrees: tuple[Path, ...]) -> list[str] | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            return None
        raw_staged = [p.strip() for p in completed.stdout.splitlines() if p.strip()]
        targets: list[str] = []
        for rel in raw_staged:
            full_p = root / rel
            if full_p.is_file() and not any(_is_within(full_p, wt) for wt in nested_worktrees):
                targets.append(str(full_p))
        return targets
    except Exception:
        return None


def _attribute_matches(
    root: Path,
    matched_files: list[Path],
    nested_worktrees: tuple[Path, ...],
) -> dict[str, list[str]]:
    matches: dict[str, list[str]] = {value: [] for value in RETIRED_IDENTIFIERS}
    for path in matched_files:
        if any(_is_within(path, worktree) for worktree in nested_worktrees):
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        rel_str = str(path.relative_to(root))
        for identifier in RETIRED_IDENTIFIERS:
            if identifier.encode("utf-8") in content:
                matches[identifier].append(rel_str)
    return matches


def _python_scan(root: Path, target_paths: list[str] | None, nested_worktrees: tuple[Path, ...]) -> list[Path]:
    """Pure-Python fallback when ripgrep binary is unavailable."""
    matched_files: list[Path] = []
    id_bytes = [ident.encode("utf-8") for ident in RETIRED_IDENTIFIERS]
    if target_paths:
        candidate_files = [root / p for p in target_paths if (root / p).is_file()]
    else:
        candidate_files = []
        skip_dirs = {
            ".git",
            ".worktrees",
            ".venv",
            "venvs",
            "node_modules",
            "__pycache__",
            ".pytest_cache",
            ".ruff_cache",
            "scratch",
            "models",
            ".cache",
            "coverage_qualification",
            "readiness_cache",
        }
        for dirpath, dirnames, filenames in os.walk(root):
            p_dir = Path(dirpath)
            if any(_is_within(p_dir, nw) for nw in nested_worktrees):
                dirnames.clear()
                continue
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for fn in filenames:
                if fn.endswith((".pyc", ".db", ".sqlite", ".db-wal", ".png", ".jpg", ".tar", ".gz", ".zip", ".bin", ".pt", ".pth")):
                    continue
                candidate_files.append(p_dir / fn)
    for cf in candidate_files:
        try:
            content = cf.read_bytes()
            if any(ib in content for ib in id_bytes):
                matched_files.append(cf.resolve())
        except (OSError, PermissionError):
            continue
    return matched_files


def scan(
    repository_root: Path,
    staged: bool = False,
    files: Sequence[str] | None = None,
) -> dict[str, object]:
    root = repository_root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"repository root does not exist: {root}")
    nested_worktrees = _registered_nested_worktree_roots(root)
    exclusion_globs = (
        "!**/.git/**",
        "!**/.worktrees/**",
        "!**/.venv/**",
        "!**/venvs/**",
        "!**/node_modules/**",
        "!**/__pycache__/**",
        "!**/.mypy_cache/**",
        "!**/.pytest_cache/**",
        "!**/scratch/**",
        "!**/.local/**/generations/**",
        "!**/.local/**/runs/**",
        "!**/.local/coverage_qualification/**",
        "!**/.local/readiness_cache/**",
        "!**/.local/receipts/**",
        "!**/models/**",
        "!**/.cache/**",
        "!**/.huggingface/**",
        "!**/*.safetensors",
        "!**/*.bin",
        "!**/*.pt",
        "!**/*.pth",
        "!**/*.wav",
        "!**/*.mp3",
        "!**/*.m4a",
        "!**/*.sqlite-shm",
        "!**/*.sqlite-wal",
        "!**/*.db-shm",
        "!**/*.db-wal",
    )
    rg_binary = _resolve_ripgrep(root)

    target_paths: list[str] | None = None
    if files:
        target_paths = [
            str((root / f).resolve() if not Path(f).is_absolute() else Path(f).resolve())
            for f in files
            if (root / f).is_file() or Path(f).is_file()
        ]
        if not target_paths:
            return {
                "status": "PASS",
                "repository_root": str(root),
                "retired_identifier_count": len(RETIRED_IDENTIFIERS),
                "match_count": 0,
                "matches": {},
                "excluded_registered_worktrees": [
                    str(path.relative_to(root)) for path in nested_worktrees
                ],
            }
    elif staged:
        target_paths = _collect_staged_paths(root, nested_worktrees)
        if target_paths is not None and not target_paths:
            return {
                "status": "PASS",
                "repository_root": str(root),
                "retired_identifier_count": len(RETIRED_IDENTIFIERS),
                "match_count": 0,
                "matches": {},
                "excluded_registered_worktrees": [
                    str(path.relative_to(root)) for path in nested_worktrees
                ],
            }

    command = [rg_binary, "-l", "--text", "-F", "--hidden", "--no-ignore"]
    if not target_paths:
        for glob in exclusion_globs:
            command.extend(("--glob", glob))
    for identifier in RETIRED_IDENTIFIERS:
        command.extend(("-e", identifier))

    if target_paths:
        command.append("--")
        command.extend(target_paths)
    else:
        command.append(str(root))

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode in (0, 1):
            matched_files = [Path(raw).resolve() for raw in completed.stdout.splitlines() if raw.strip()]
        else:
            matched_files = _python_scan(root, target_paths, nested_worktrees)
    except (FileNotFoundError, OSError):
        matched_files = _python_scan(root, target_paths, nested_worktrees)
    matches = _attribute_matches(root, matched_files, nested_worktrees)
    present = {key: sorted(value) for key, value in matches.items() if value}
    return {
        "status": "PASS" if not present else "FAIL",
        "repository_root": str(root),
        "retired_identifier_count": len(RETIRED_IDENTIFIERS),
        "match_count": sum(len(value) for value in present.values()),
        "matches": present,
        "excluded_registered_worktrees": [
            str(path.relative_to(root)) for path in nested_worktrees
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reject current-workspace synthetic embedding identifiers."
    )
    parser.add_argument("--repository-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--staged", action="store_true", help="Scan only staged files for high-speed pre-commit")
    parser.add_argument("files", nargs="*", default=None, help="Specific files to scan")
    args = parser.parse_args(argv)
    result = scan(Path(args.repository_root), staged=args.staged, files=args.files)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
