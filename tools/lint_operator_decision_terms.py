#!/usr/bin/env python3
"""Linter enforcing Operator Decision Vocabulary SSOT and blocking replaced terms.

Enforces that new code and configurations do not introduce deprecated or
overloaded legacy 'HITL' nomenclature:
- 'requires_hitl' -> use 'needs_operator_decision'
- 'HITL_REQUIRED' -> use 'OPERATOR_INPUT_REQUIRED'
- 'hitl_receipt_bound' -> use 'approval_signature_valid'
- 'HITL_REQUIRED_TOOLS' -> use 'TOOL_APPROVAL_REQUIRED'

Allowlisted paths (immutable records, glossary, vocabulary definitions, and test fixtures)
are exempt from this check.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Set, Tuple

# Replaced terms to search for
REPLACED_TERMS = {
    "requires_hitl": "needs_operator_decision",
    "HITL_REQUIRED": "OPERATOR_INPUT_REQUIRED",
    "hitl_receipt_bound": "approval_signature_valid",
    "HITL_REQUIRED_TOOLS": "TOOL_APPROVAL_REQUIRED",
}

# Immutable or definition directories/files exempt from linting
EXEMPT_PREFIXES = (
    "reports/",
    "receipts/",
    "archive/",
    "migrations/",
    "governance/OPERATOR_DECISION_GLOSSARY.md",
    "config/operator_decision_vocabulary.py",
    "docs/plans/",
    "tools/lint_operator_decision_terms.py",
    "tools/audit_governance_harmonization_core/",
)

# Exempt test files testing the linter itself or testing backwards compatibility wrappers
EXEMPT_TEST_FILES = {
    "tests/test_operator_decision_vocabulary.py",
    "tests/test_operator_decision_vocabulary_adversarial.py",
    "tests/end_to_end/test_operator_decision_vocabulary_e2e.py",
    "tests/test_ambiguity_rule_30.py",
}


def load_baseline_allowlist(repo_root: Path) -> Set[str]:
    """Load baseline files from Wave 1 term inventory to support grandfathered files."""
    inventory_path = repo_root / "reports" / "ask_user_question_enforcement" / "term_inventory.json"
    if not inventory_path.exists():
        return set()
    try:
        data = json.loads(inventory_path.read_text(encoding="utf-8"))
        return {item["file"] for item in data.get("inventory", [])}
    except Exception:
        return set()


def is_exempt_path(rel_path: str, allow_grandfathered: bool, baseline_files: Set[str]) -> bool:
    """Return True if the relative path is exempt from replaced term enforcement."""
    for prefix in EXEMPT_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    if rel_path in EXEMPT_TEST_FILES:
        return True
    return bool(allow_grandfathered and rel_path in baseline_files)


def scan_file_for_replaced_terms(
    file_path: Path, repo_root: Path
) -> List[Tuple[int, str, str, str]]:
    """Scan a single file for occurrences of replaced terms.

    Returns list of tuples: (line_number, matched_term, suggested_replacement, line_content).
    """
    findings = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings

    lines = content.splitlines()
    for idx, line in enumerate(lines, 1):
        for term, replacement in REPLACED_TERMS.items():
            # Match word boundary or exact token to avoid partial matches
            if re.search(r"\b" + re.escape(term) + r"\b", line):
                findings.append((idx, term, replacement, line.strip()))
    return findings


def lint_files(
    repo_root: Path,
    target_files: Optional[Sequence[Path]] = None,
    allow_grandfathered: bool = True,
) -> int:
    """Run lint across files and print findings. Returns exit code 0 on pass, 1 on fail."""
    baseline_files = load_baseline_allowlist(repo_root) if allow_grandfathered else set()

    if target_files is None:
        # Scan tracked and untracked git files
        cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
        res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, check=True)
        paths = [repo_root / p for p in res.stdout.splitlines() if p.strip()]
    else:
        paths = [p.resolve() for p in target_files]

    total_violations = 0
    violation_reports = []

    for path in paths:
        if not path.is_file():
            continue
        try:
            rel_path = str(path.relative_to(repo_root))
        except ValueError:
            rel_path = str(path)

        if is_exempt_path(rel_path, allow_grandfathered, baseline_files):
            continue

        findings = scan_file_for_replaced_terms(path, repo_root)
        if findings:
            total_violations += len(findings)
            for line_no, term, replacement, line_snippet in findings:
                violation_reports.append(
                    f"{rel_path}:{line_no}: Replaced term '{term}' found. "
                    f"Use canonical SSOT term '{replacement}' instead.\n"
                    f"    Line: {line_snippet}"
                )

    if total_violations > 0:
        print(f"\n[FAIL] Operator Decision Vocabulary Lint: {total_violations} violation(s) found:")
        for report in violation_reports:
            print(f"  - {report}")
        print("\nRefer to governance/OPERATOR_DECISION_GLOSSARY.md for canonical vocabulary.")
        return 1

    print(
        "[PASS] Operator Decision Vocabulary Lint: No prohibited legacy terms found in non-exempt files."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lint repository for replaced operator decision / HITL terms."
    )
    parser.add_argument("--file", type=Path, action="append", help="Specific file(s) to check.")
    parser.add_argument(
        "--strict", action="store_true", help="Disallow grandfathered baseline files."
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    exit_code = lint_files(
        repo_root=repo_root,
        target_files=args.file,
        allow_grandfathered=not args.strict,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
