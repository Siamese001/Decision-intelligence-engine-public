#!/usr/bin/env python3
"""
Model-Neutral Artifact Naming Linter.

Enforces Invariant 7 of AGENTS.md and .agents/rules/model-neutral-artifact-naming.md:
- Strictly prohibits prepending, appending, or embedding LLM model identifiers, provider
  names, or reasoning tier tags (gpt*, astra, luna, sol, terra, claude*, gemini*, llama*)
  into filenames or directory paths for reports, specifications, receipts, documentation,
  and persistent artifacts.
- Authoring/executing models are ephemeral runtime telemetry and belong exclusively in internal
  audit metadata fields (e.g., 'model_id', 'evaluator_model'), never in the filesystem namespace.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import List, NamedTuple, Sequence

ROOT = Path(__file__).resolve().parents[1]

# Forbidden model tokens and regex patterns
PROHIBITED_TOKEN_PATTERNS = [
    re.compile(r"(?i)\bgpt[0-9_\-a-z]*\b"),
    re.compile(r"(?i)\bastra\b"),
    re.compile(r"(?i)\bluna\b"),
    re.compile(r"(?i)\bterra\b"),
    re.compile(r"(?i)\bsol(?:_medium|_high|-medium|-high)?\b"),
    re.compile(r"(?i)\bclaude[0-9_\-a-z]*\b"),
    re.compile(r"(?i)\bgemini[0-9_\-a-z]*\b"),
    re.compile(r"(?i)\bllama[0-9_\-a-z]*\b"),
    re.compile(r"(?i)\b(?:sonnet|haiku|opus)\b"),
    re.compile(r"\b(?:o1|o3)(?:-[a-z0-9]+)?\b"),
    re.compile(r"(?i)\b(?:openai_o1|openai_o3)\b"),
]

# Paths/patterns strictly exempted from model naming checks
# (e.g. historical client consulting fixtures, model binding unit tests, git metadata, protocol archives)
EXEMPT_PATH_PATTERNS = [
    r"^\.git/",
    r"^\.venv/",
    r"(?:^|/)__pycache__/",
    r"(?:^|/)\.pytest_cache/",
    r"^\.agents/",
    r"^archive/",
    r"^governance/",
    r"^approvals/",
    r"^contracts/dual_pipeline/fixtures/",
    r"^contracts/dual_pipeline/schemas/google-meet-gemini-intake\.schema\.json",
    r"(?i)^inputs/.*gemini",
    r"^intake/.*GEMINI",
    r"^exports/.*gemini",
    r"^graph/migrations/.*gemini",
    r"^documentation/INGESTION_GRAPHRAG_PROMPT_GOOGLE_MEET_GEMINI\.md",
    r"^output/gemini_enablement/",
    r"^dual_pipeline/transcript_stages_core/gemini_context_cache\.py$",
    # Legacy grandfathered model-named test files (strictly closed list; new tests must be model-neutral)
    r"^tests/test_astra_cost_governance\.py$",
    r"^tests/test_gpt_56_luna_agents\.py$",
    r"^tests/test_e2e_smart_caching_and_incremental_gpt6_astra\.py$",
    r"^tests/test_e2e_upstream_qa_luna\.py$",
    r"^tests/test_wave1_gpt56_luna_medium\.py$",
    r"^tests/test_gpt_5_6_sol_high_agents\.py$",
    # Legacy grandfathered model-named scripts and evals (strictly closed list; new scripts must be model-neutral)
    r"^scripts/adversarial_review_luna\.py$",
    r"^scripts/analyze_output_hierarchy_luna\.py$",
    r"^scratch/.*",
    # Legacy grandfathered documentation and specifications from prior commits
    r"^reports/gpt56_terra_refactor_specification\.md$",
    r"^reports/gpt56_luna_output2_refactor_specification\.md$",
    r"^reports/upstream_audio_qa_fidelity_analysis_gpt56_luna\.md$",
    r"^reports/wave2_gpt56_sol_medium_intake_receipt\.json$",
    r"^reports/wave1_gpt56_sol_medium_judge_receipt\.json$",
    r"^reports/wave1_gpt56_luna_medium_receipt\.json$",
    r"^reports/wave4_gpt56_sol_medium_governance_receipt\.json$",
    r"^reports/wave3_gpt56_sol_medium_decision_receipt\.json$",
    r"^reports/gpt56_luna_output3_refactor_specification\.md$",
    r"^receipts/wave1_credentials_gpt56_luna_receipt\.json$",
    r"^docs/rca_gpt56_sol_pipeline_architecture\.md$",
    r"^docs/adversarial_review_luna_output\.md$",
    r"^documentation/CODEX_SOL_READ_WRITE_OPTIMIZATION_ASSESSMENT\.md$",
    r"^reports/(?:history/(?:\d{4}-\d{2}/)?)?pr_(?:review|merge)_.*\.json$",
]

COMPILED_EXEMPT_PATTERNS = [re.compile(p) for p in EXEMPT_PATH_PATTERNS]


class NamingViolation(NamedTuple):
    file_path: str
    matched_token: str
    detail: str


def is_path_exempt(rel_path: str) -> bool:
    """Check if the given relative path is exempt from model-naming constraints."""
    normalized = rel_path.replace("\\", "/")
    return any(pattern.search(normalized) for pattern in COMPILED_EXEMPT_PATTERNS)


def scan_path_for_model_tokens(rel_path: str) -> List[NamingViolation]:
    """Scan a relative path and its filename for forbidden model naming tokens."""
    if is_path_exempt(rel_path):
        return []

    normalized = rel_path.replace("\\", "/")
    path_obj = Path(normalized)
    stem_and_ext = path_obj.name

    violations: List[NamingViolation] = []

    # Tokenize the path components into distinct words/segments
    # Split on underscores, dashes, dots, and directory separators
    tokens = re.split(r"[/\\._\-]+", normalized)

    for pattern in PROHIBITED_TOKEN_PATTERNS:
        # Check against full filename
        match = pattern.search(stem_and_ext)
        if match:
            violations.append(
                NamingViolation(
                    file_path=rel_path,
                    matched_token=match.group(0),
                    detail=f"Filename '{stem_and_ext}' contains prohibited model identifier '{match.group(0)}'.",
                )
            )
            continue

        # Check against individual path directory tokens
        for token in tokens:
            token_match = pattern.fullmatch(token)
            if token_match:
                violations.append(
                    NamingViolation(
                        file_path=rel_path,
                        matched_token=token_match.group(0),
                        detail=f"Path component '{token}' contains prohibited model identifier '{token_match.group(0)}'.",
                    )
                )
                break

    return violations


def get_staged_files(repo_root: Path) -> List[str]:
    """Retrieve list of staged files in git index."""
    cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"]
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def get_all_tracked_files(repo_root: Path) -> List[str]:
    """Retrieve list of all tracked files in git repository."""
    cmd = ["git", "ls-files"]
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=True)
    return [line.strip() for line in res.stdout.splitlines() if line.strip()]


def lint_files(files: Sequence[str], verbose: bool = False) -> List[NamingViolation]:
    """Lint a sequence of file paths and return all violations."""
    all_violations: List[NamingViolation] = []
    for f in files:
        if verbose:
            print(f"[CHECK] {f}")
        violations = scan_path_for_model_tokens(f)
        all_violations.extend(violations)
    return all_violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that files and artifacts do not embed LLM model names in filenames or paths."
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Scan git staged files only (for pre-commit hooks).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan all tracked files in the repository.",
    )
    parser.add_argument(
        "--path",
        nargs="*",
        default=[],
        help="Scan specific file or directory paths.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging.",
    )

    args = parser.parse_args()

    files_to_check: List[str] = []

    if args.staged:
        files_to_check = get_staged_files(ROOT)
    elif args.all:
        files_to_check = get_all_tracked_files(ROOT)
    elif args.path:
        for p in args.path:
            path_obj = Path(p)
            if not path_obj.is_absolute():
                path_obj = ROOT / path_obj
            if path_obj.is_file():
                files_to_check.append(str(path_obj.relative_to(ROOT)))
            elif path_obj.is_dir():
                for sub in path_obj.rglob("*"):
                    if sub.is_file():
                        files_to_check.append(str(sub.relative_to(ROOT)))
    else:
        # Default: scan core artifact directories (reports, receipts, docs, output, documentation, tests)
        core_dirs = ["reports", "receipts", "docs", "output", "documentation", "tests"]
        for d in core_dirs:
            dir_path = ROOT / d
            if dir_path.is_dir():
                for sub in dir_path.rglob("*"):
                    if sub.is_file():
                        files_to_check.append(str(sub.relative_to(ROOT)))

    violations = lint_files(files_to_check, verbose=args.verbose)

    if violations:
        print("\n" + "=" * 80, file=sys.stderr)
        print("ERROR: PROHIBITED LLM MODEL NAME DETECTED IN FILE PATHS", file=sys.stderr)
        print("Enforcing AGENTS.md Invariant 7 & Model-Neutral Artifact Naming Rules", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        print(f"Found {len(violations)} violation(s):\n", file=sys.stderr)

        for v in violations:
            print(f"  [VIOLATION] {v.file_path}", file=sys.stderr)
            print(f"              Matched Token: '{v.matched_token}'", file=sys.stderr)
            print(f"              Detail: {v.detail}\n", file=sys.stderr)

        print("REMEDIATION:", file=sys.stderr)
        print("  1. Rename files strictly by DOMAIN FUNCTION, ARCHITECTURAL SCOPE, or LIFECYCLE ROLE.", file=sys.stderr)
        print("     Example: 'reports/output2_hardening_refactor_specification.md'", file=sys.stderr)
        print("  2. Place model identity inside structured JSON metadata (e.g. 'model_id': '...'),", file=sys.stderr)
        print("     NEVER in the filesystem path or filename.", file=sys.stderr)
        print("=" * 80 + "\n", file=sys.stderr)
        return 1

    count = len(files_to_check)
    print(f"[PASS] Model-neutral artifact naming: verified {count} file(s) with 0 violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
