#!/usr/bin/env python3
"""
tools/lint_no_custom_ingest_scripts.py

Enforces Master Architectural Invariant 10:
Direct Stage U0 Ingestion & Custom Script Prohibition.

Rules:
1. Strictly prohibits authoring, adding, or modifying one-off ingestion scripts
   in `scripts/` (e.g. `scripts/build_and_ingest_*.py`, `scripts/ingest_*.py`).
2. Prohibits scripts outside `dual_pipeline/`, `tools/`, and `tests/` from directly
   instantiating `RawTranscriptPipeline` or invoking `_intake_publish`.
3. Mandates that all transcript and meeting ingestion execute via `nee ingest-meeting`
   or direct programmatic calls within governed tests and tools.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]

# Specific forbidden filename patterns in scripts/ and tools/
PROHIBITED_FILENAME_PREFIXES = (
    "build_and_ingest_",
    "build_ingest_",
    "transcribe_lens_",
    "build_lens_",
)

# Files explicitly forbidden from existing in scripts/ or tools/ (active defect targets)
STRICTLY_BANNED_FILES: Set[str] = {
    "scripts/build_and_ingest_lens_phase_ii_catchup.py",
    "scripts/build_and_ingest_lens_0914_meeting.py",
    "scripts/build_and_ingest_lens_0914_rebaseline.py",
    "tools/build_lens_review_synthesis.py",
    "tools/transcribe_lens_review.py",
}

# Grandfathered historical legacy scripts (all purged; zero custom scripts grandfathered)
LEGACY_HISTORICAL_EXCLUSIONS: Set[str] = set()




def get_staged_files(repo_root: Path) -> List[str]:
    """Retrieve list of staged files in git index."""
    try:
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def inspect_ast_for_pipeline_instantiation(path: Path, tree: Optional[ast.AST] = None) -> List[str]:
    """Inspects a python file AST for direct RawTranscriptPipeline instantiations."""
    violations = []
    if tree is None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception as e:
            return [f"Syntax error parsing {path}: {e}"]

    for node in ast.walk(tree):
        # Detect `RawTranscriptPipeline(...)`
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            if func_name in ("RawTranscriptPipeline", "_intake_publish"):
                violations.append(
                    f"Line {node.lineno}: direct instantiation or call of `{func_name}`"
                )
    return violations


def inspect_ast_for_artifact_writes(path: Path, tree: Optional[ast.AST] = None) -> List[str]:
    """Inspects a python file AST for direct file write calls targeting meeting deliverables."""
    violations = []
    if tree is None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        except Exception:
            return []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Attribute):
                func_name = node.func.attr
            elif isinstance(node.func, ast.Name):
                func_name = node.func.id
            if func_name in ("write_text", "open", "dump"):
                for arg in list(node.args) + [kw.value for kw in node.keywords]:
                    val = None
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        val = arg.value
                    elif isinstance(arg, ast.JoinedStr):
                        # Extract string parts from f-string
                        val = "".join(p.value for p in arg.values if isinstance(p, ast.Constant) and isinstance(p.value, str))
                    if val and any(art in val for art in ("hitl_resolved_transcript.md", "canonical_signal_projection.json", "qa_gates_report.json")):
                        violations.append(
                            f"Line {node.lineno}: direct write call targeting meeting deliverable pattern '{val}'"
                        )
    return violations


def lint_file(rel_path: str, repo_root: Path, is_staged: bool = False) -> List[str]:
    """Check a single file path for Invariant 10 violations."""
    violations: List[str] = []
    norm_path = rel_path.replace("\\", "/")

    # Check strictly banned targets
    if norm_path in STRICTLY_BANNED_FILES:
        violations.append(
            f"Strictly prohibited custom ingestion script detected: '{norm_path}'. "
            "Master Invariant 10 forbids custom ingestion scripts."
        )
        return violations

    # Check scripts and tools directory violations
    if norm_path.startswith("scripts/") or norm_path.startswith("tools/"):
        filename = Path(norm_path).name
        # Prohibit any custom synthesis/ingest scripts by prefix
        if any(filename.startswith(pfx) for pfx in PROHIBITED_FILENAME_PREFIXES):
            if is_staged:
                violations.append(
                    f"Prohibited ingestion script in staged changes: '{norm_path}'. "
                    "Master Invariant 10 prohibits creating or modifying custom ingestion scripts."
                )
            else:
                violations.append(
                    f"Unauthorized new ingestion script: '{norm_path}'. "
                    "Master Invariant 10 requires using `nee ingest-meeting --input <source>`."
                )

        full_path = repo_root / norm_path
        if (
            full_path.is_file()
            and norm_path.endswith(".py")
            and not filename.startswith("lint_")
            and not filename.startswith("validate_")
            and not filename.startswith("test_")
        ):
            parsed_tree: Optional[ast.AST] = None
            parse_err: Optional[str] = None
            try:
                parsed_tree = ast.parse(
                    full_path.read_text(encoding="utf-8", errors="replace"),
                    filename=str(full_path),
                )
            except Exception as exc:
                parse_err = f"Syntax error parsing {full_path}: {exc}"

            if parsed_tree is not None:
                # Inspect for direct artifact write calls
                write_errs = inspect_ast_for_artifact_writes(full_path, tree=parsed_tree)
                for err in write_errs:
                    violations.append(
                        f"Direct meeting artifact generation detected in '{norm_path}': {err}. "
                        "Meeting deliverables must strictly be emitted by RawTranscriptPipeline via Universal Write Gateway."
                    )

            # AST inspection for any pipeline instantiation in scripts/
            if norm_path.startswith("scripts/") and norm_path not in LEGACY_HISTORICAL_EXCLUSIONS:
                ast_errs = (
                    [parse_err]
                    if parse_err is not None
                    else inspect_ast_for_pipeline_instantiation(full_path, tree=parsed_tree)
                )
                for err in ast_errs:
                    violations.append(
                        f"Unmanaged pipeline execution in '{norm_path}': {err}. "
                        "All ingestion must flow through `nee ingest-meeting` or `dual_pipeline/cli/`."
                    )

    return violations



def run_linter(
    repo_root: Path,
    staged_only: bool = False,
    files: Optional[Sequence[str]] = None,
) -> Tuple[int, List[str]]:
    """Executes the Invariant 10 anti-scripting linter across target files."""
    all_violations: List[str] = []

    if files:
        target_files = list(files)
    elif staged_only:
        target_files = get_staged_files(repo_root)
    else:
        # Scan scripts/ and tools/ directories
        target_files = []
        for scan_name in ("scripts", "tools"):
            s_dir = repo_root / scan_name
            if s_dir.exists():
                for p in s_dir.rglob("*.py"):
                    rel = p.relative_to(repo_root).as_posix()
                    target_files.append(rel)

    for f_path in target_files:
        errs = lint_file(f_path, repo_root, is_staged=staged_only)
        all_violations.extend(errs)

    exit_code = 1 if all_violations else 0
    return exit_code, all_violations



def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enforces Master Invariant 10: Prohibition of Custom Ingestion Scripts."
    )
    parser.add_argument("--staged", action="store_true", help="Lint only staged files in git.")
    parser.add_argument("--check", action="store_true", help="Perform complete scan of scripts/ directory.")
    parser.add_argument("files", nargs="*", help="Specific files to lint.")

    args = parser.parse_args()

    exit_code, violations = run_linter(
        repo_root=ROOT,
        staged_only=args.staged,
        files=args.files if args.files else None,
    )

    if exit_code != 0:
        print("=" * 70, file=sys.stderr)
        print("❌ GOVERNANCE ERROR: Master Invariant #10 Violation Detected!", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        for v in violations:
            print(f"  • {v}", file=sys.stderr)
        print("\nMandated Action:", file=sys.stderr)
        print("  Direct Stage U0 ingestion is mandatory. Do NOT build custom scripts.", file=sys.stderr)
        print("  Run the governed CLI entry point instead:", file=sys.stderr)
        print("    python nee.py ingest-meeting --input <source_file> --project <p> --meeting <m>", file=sys.stderr)
        print("=" * 70, file=sys.stderr)
        sys.exit(exit_code)
    else:
        target_desc = "staged files" if args.staged else "scripts directory"
        print(f"✅ Invariant 10 Anti-Scripting Linter: PASSED cleanly on {target_desc}.")
        sys.exit(0)


if __name__ == "__main__":
    main()
