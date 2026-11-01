#!/usr/bin/env python3
"""
tools/lint_layman_clarity.py

Static linter and AST scanner enforcing the Zero-Jargon Layman Invariant across
HITL decision templates, prompts, and option payloads.
Exits with code 0 if all audited presentation strings are clean.
Exits with code 1 if any forbidden code tokens, file paths, or internal jargon are found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.hitl_governance_core.layman_clarity import (  # noqa: E402
    scan_for_jargon,
    validate_layman_option,
)


def lint_text(text: str, source_label: str) -> List[Tuple[str, str]]:
    """Lints a given text snippet, returning list of (source_label, violation_message)."""
    violations = scan_for_jargon(text)
    return [(source_label, v) for v in violations]


def lint_option_dict(opt: Dict[str, Any], source_label: str) -> List[Tuple[str, str]]:
    """Lints a structured option dictionary for schema and jargon compliance."""
    errors = validate_layman_option(opt)
    return [(source_label, err) for err in errors]


def run_self_test() -> Tuple[bool, List[str]]:
    """Executes deterministic self-tests ensuring the linter catches true violations and allows valid layman text."""
    diagnostics: List[str] = []

    # Valid layman strings
    valid_cases = [
        "Retire the legacy server component and consolidate testing under the primary gateway.",
        "Operational execution guarantees zero regression risk while reducing maintenance overhead.",
        "(Recommended) Primary Option (Confidence: HIGH (90%) [85%–95%, n=20]) - Standard routing. Impact: Reduces latency.",
        "Authorize deployment to production after security compliance review passes.",
    ]
    for case in valid_cases:
        v = scan_for_jargon(case)
        if v:
            diagnostics.append(f"Self-test false positive on: '{case}' -> {v}")

    # Invalid jargon cases that MUST be caught
    invalid_cases = [
        ("mcp_server.py is deprecated", "Forbidden file reference"),
        ("Execute def initialize_runtime() now", "Forbidden code keyword"),
        ("Check the margin_threshold parameter", "Forbidden snake_case identifier"),
        ("Instantiate MarginEvaluator for scoring", "Forbidden PascalCase code identifier"),
        ("Raises HitlConfidenceMissingError upon failure", "Forbidden exception/error name"),
        ("Reset branch state to HEAD~1", "Forbidden git internal token"),
        ("Inspect tools/governance.py for details", "Forbidden filesystem path"),
        ("Option A (Confidence: HIGH (90%) [5%–95%, n=0])", "Forbidden meaningless zero-sample interval"),
        ("Checked with ACID transactions and WAL logging", "Forbidden technical plumbing acronym"),
        ("Calculated with kappa = 0.85 and delta threshold", "Forbidden Greek or mathematical symbol"),
    ]
    for case, expected_desc in invalid_cases:
        v = scan_for_jargon(case)
        if not v:
            diagnostics.append(f"Self-test false negative on: '{case}' (expected {expected_desc})")

    success = len(diagnostics) == 0
    return success, diagnostics


def scan_known_governance_templates(repo_root: Path) -> List[Tuple[str, str]]:
    """Scans repository prompt templates and presentation files for layman compliance."""
    violations: List[Tuple[str, str]] = []

    # Check key HITL presentation modules for embedded default option templates
    files_to_check = [
        repo_root / "dual_pipeline" / "hitl_core" / "margin_evaluator.py",
        repo_root / "dual_pipeline" / "hitl_governance_core" / "surfacing.py",
    ]

    for p in files_to_check:
        if not p.is_file():
            continue
        content = p.read_text(encoding="utf-8")
        # Extract default option labels and descriptions
        pattern = re.compile(r'["\']((?:Option|Retire|Deploy|Proceed|Quarantine)[^"\']{10,120})["\']')
        for match in pattern.finditer(content):
            candidate = match.group(1)
            # Only lint strings that look like presentation options
            if any(term in candidate.lower() for term in ("confidence", "option", "retire", "deploy", "proceed")):
                v = scan_for_jargon(candidate)
                for item in v:
                    violations.append((f"{p.relative_to(repo_root)}:{match.start()}", item))

    return violations


def scan_for_truncation_or_slicing(repo_root: Path) -> List[Tuple[str, str]]:
    """Scans HITL presentation modules for code that slices or abbreviates text."""
    violations: List[Tuple[str, str]] = []
    hitl_dirs = [
        repo_root / "dual_pipeline" / "hitl_core",
        repo_root / "dual_pipeline" / "hitl_governance_core",
    ]
    slicing_pattern = re.compile(r"\b(?:question|prompt|option|opt|choice|text|label)\[\s*:\s*\d+\s*\]")
    trunc_ellipsis_pattern = re.compile(r'(\+\s*["\'](?:\.\.\.|\u2026)["\'])')
    for d in hitl_dirs:
        if not d.is_dir():
            continue
        for p in d.glob("*.py"):
            if p.name in {"question_validator.py", "layman_clarity.py"}:
                continue
            content = p.read_text(encoding="utf-8")
            for m in slicing_pattern.finditer(content):
                violations.append(
                    (f"{p.relative_to(repo_root)}:{m.start()}", f"Forbidden string slicing on decision text: '{m.group(0)}'")
                )
            for m in trunc_ellipsis_pattern.finditer(content):
                violations.append(
                    (f"{p.relative_to(repo_root)}:{m.start()}", f"Forbidden ellipsis truncation concatenation: '{m.group(0)}'")
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint and verify Zero-Jargon Layman Invariant compliance across HITL options."
    )
    parser.add_argument("--check", action="store_true", help="Run full self-test and scan governance templates.")
    parser.add_argument("--path", type=str, default="", help="Optional file or directory path to scan.")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed diagnostic logging.")

    args = parser.parse_args()

    print("=" * 70)
    print("LAYMAN CLARITY LINTER (Zero-Jargon Standard & Schema v1.2.0)")
    print("=" * 70)

    # 1. Run deterministic self-test
    self_test_ok, diagnostics = run_self_test()
    if not self_test_ok:
        print("[FAIL] Self-test validation errors encountered:")
        for d in diagnostics:
            print(f"  - {d}")
        return 1

    print("[PASS] Self-test verified: Jargon scanner correctly isolates code tokens and accepts layman text.")

    # 2. Scan path or known governance templates if requested
    violations: List[Tuple[str, str]] = []
    if args.path:
        target_path = Path(args.path).resolve()
        if target_path.is_file():
            content = target_path.read_text(encoding="utf-8")
            violations.extend(lint_text(content, str(target_path)))
        elif target_path.is_dir():
            for p in target_path.glob("**/*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and "options" in data:
                        for opt in data["options"]:
                            if isinstance(opt, dict):
                                violations.extend(lint_option_dict(opt, str(p)))
                except Exception:
                    pass
    elif args.check:
        violations.extend(scan_known_governance_templates(REPO_ROOT))
        violations.extend(scan_for_truncation_or_slicing(REPO_ROOT))

    if violations:
        print(f"\n[FAIL] Found {len(violations)} layman clarity violation(s):")
        for src, msg in violations:
            print(f"  - [{src}] {msg}")
        return 1

    print("[PASS] Zero jargon and zero text slicing violations detected across audited scope.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
