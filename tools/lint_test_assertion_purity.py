#!/usr/bin/env python3
"""
tools/lint_test_assertion_purity.py

Enforces Master Invariant 13 (Test Assertion Purity & Anti-Entanglement Mandate).
Scans tests/ to ensure test suites assert exclusively on structural, ontological,
and behavioral invariants, and do not assert on hardcoded synthetic meeting constants
(e.g., `ev-910-03`, `stmt-opus-3616`, `REID_NEXUS_CONSOLIDATED_NEXT_STEPS`).

Exits with code 0 if clean, 1 if any anti-pattern is detected.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Synthetic tokens that were injected by meeting healers and should never be asserted in tests
FORBIDDEN_TEST_ASSERTION_TOKENS = [
    re.compile(r"\bev-910-\d{2}\b", re.I),
    re.compile(r"\bstmt-opus-3616\b", re.I),
    re.compile(r"\bREID_NEXUS_CONSOLIDATED_NEXT_STEPS\b", re.I),
    re.compile(r"\bLENS_PHASE1_CONSOLIDATED_NEXT_STEPS\b", re.I),
    re.compile(r"\bcoaching future candidates\b", re.I),
    re.compile(r"\bharper Hoffman Reported Operational Work Scope\b", re.I),
    re.compile(r"\bEscalate Three-Week Schedule Overage and Governance Deficits\b", re.I),
    re.compile(r"\bDemands Code-Faithful Architecture Reference to Eliminate Client Handoff Drift\b", re.I),
]

EXEMPT_TEST_FILES = {
    "test_lint_no_transcript_overfitting.py",
    "test_lint_test_assertion_purity.py",
    "test_lint_no_knowledge_popping.py",
    "test_lint_plain_english_recap.py",
}


def check_test_file(filepath: Path) -> List[str]:
    if filepath.name in EXEMPT_TEST_FILES:
        return []

    violations: List[str] = []
    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Unable to read {filepath}: {exc}"]

    try:
        rel_path = str(filepath.relative_to(REPO_ROOT))
    except ValueError:
        rel_path = str(filepath)

    lines = content.splitlines()
    for idx, line in enumerate(lines, start=1):
        for pattern in FORBIDDEN_TEST_ASSERTION_TOKENS:
            if pattern.search(line):
                violations.append(
                    f"{rel_path}:{idx}: Assertion on synthetic healer constant detected: '{line.strip()[:60]}'. "
                    f"Tests must assert on structural invariants under Invariant 13."
                )

    return violations


def run_linter(target_files: Optional[Sequence[Path]] = None) -> Tuple[bool, List[str]]:
    all_violations: List[str] = []
    if target_files is None:
        files_to_check = [p for p in TESTS_DIR.rglob("test_*.py") if "__pycache__" not in str(p)]
    else:
        files_to_check = [
            p for p in target_files
            if p.suffix == ".py" and p.name.startswith("test_") and p.exists()
        ]

    for f in files_to_check:
        all_violations.extend(check_test_file(f))

    return len(all_violations) == 0, all_violations


def main() -> int:
    print("=" * 80)
    print("  TEST ASSERTION PURITY & ANTI-ENTANGLEMENT LINTER (Invariant 13)")
    print("================================================================================")

    args = sys.argv[1:]
    receipt_path: Optional[Path] = None
    if "--receipt" in args:
        idx = args.index("--receipt")
        if idx + 1 < len(args):
            receipt_path = Path(args[idx + 1])

    target_files: Optional[List[Path]] = None
    if "--staged" in args:
        import subprocess
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if res.returncode == 0:
            target_files = [REPO_ROOT / p.strip() for p in res.stdout.splitlines() if p.strip()]
    else:
        raw_files = [Path(a) for a in args if not a.startswith("--") and (REPO_ROOT / a).exists()]
        if raw_files:
            target_files = [f.resolve() for f in raw_files]

    passed, violations = run_linter(target_files)

    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_data = {
            "linter": "lint_test_assertion_purity",
            "invariant": "Master Invariant 13",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "passed": passed,
            "violations_count": len(violations),
            "violations": violations,
        }
        receipt_path.write_text(json.dumps(receipt_data, indent=2), encoding="utf-8")
        print(f"📄 Receipt saved to: {receipt_path}")

    if not passed:
        print(f"❌ FAILED: {len(violations)} test assertion purity violation(s) detected:")
        for v in violations:
            print(f"   • {v}")
        print("================================================================================")
        return 1

    print("✅ PASSED: 0 synthetic healer constants or copy-pasted meeting assertions detected.")
    print("   All tests assert on structural and behavioral invariants.")
    print("================================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
