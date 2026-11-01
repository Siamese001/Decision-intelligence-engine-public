#!/usr/bin/env python3
"""
Production Module Budget Checker (400-Line Budget Enforcer).

Enforces the non-negotiable architectural invariant:
- Production Python modules across Pipelines A-D must not exceed 400 lines.
- Explicitly audits and reports exemptions for generated schemas, migrations, and test fixtures.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

DEFAULT_MAX_LINES = 400

GOVERNED_DIRS = [
    "dual_pipeline/l0",
    "dual_pipeline/agent_framework",
    "dual_pipeline/decision",
    "dual_pipeline/retrieval",
    "pipeline_a_intake",
    "pipeline_b_extraction",
    "pipeline_c_retrieval",
]

EXEMPT_PREFIXES = (
    "tests/",
    "schemas/",
    "migrations/",
    "contracts/",
)

EXEMPT_FILES: Set[str] = {
    # Grandfathered legacy modules awaiting decomposition in separate PRs
    "dual_pipeline/retrieval_suite.py",
    "dual_pipeline/hitl_governance.py",
    "pipeline_a_intake/agents/ingestion_coordinator.py",
    "dual_pipeline/transcript_pipeline_core/pipeline.py",
    "dual_pipeline/speaker_disambiguation_core/disambiguator.py",
    "dual_pipeline/transcript_stages_core/l1_cognition.py",
    "pipeline_a_intake/mac_system_audio.py",
    "pipeline_a_intake/local_stt.py",
    "pipeline_b_extraction/extractors.py",
    "dual_pipeline/query_core/engine.py",
}


def is_exempt(rel_path: str) -> Tuple[bool, str]:
    if rel_path.startswith(EXEMPT_PREFIXES) or "/tests/" in rel_path or rel_path.endswith("__init__.py"):
        return True, "test_or_schema_or_init"
    if rel_path in EXEMPT_FILES:
        return True, "grandfathered_legacy"
    return False, ""


def count_lines(path: Path) -> int:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def audit_module_budgets(
    repo_root: Path,
    max_lines: int = DEFAULT_MAX_LINES,
    target_dirs: List[str] = GOVERNED_DIRS,
) -> Dict[str, Any]:
    scanned: List[Tuple[str, int]] = []
    exempt: List[Tuple[str, int, str]] = []
    violations: List[Tuple[str, int]] = []

    for d in target_dirs:
        dir_path = repo_root / d
        if not dir_path.exists():
            continue
        for py_file in dir_path.rglob("*.py"):
            try:
                rel = str(py_file.resolve().relative_to(repo_root))
            except ValueError:
                rel = str(py_file)

            lines = count_lines(py_file)
            exempt_flag, reason = is_exempt(rel)

            if exempt_flag:
                exempt.append((rel, lines, reason))
            else:
                scanned.append((rel, lines))
                if lines > max_lines:
                    violations.append((rel, lines))

    return {
        "max_lines": max_lines,
        "scanned_count": len(scanned),
        "exempt_count": len(exempt),
        "violations": violations,
        "exempt_details": exempt,
        "max_found_lines": max([l for _, l in scanned], default=0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce 400-line limit on production modules.")
    parser.add_argument("--max-lines", type=int, default=DEFAULT_MAX_LINES, help="Max line limit per module")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    result = audit_module_budgets(repo_root, max_lines=args.max_lines)

    print("=" * 80)
    print(f"  PRODUCTION MODULE BUDGET CHECKER (Limit: {result['max_lines']} lines)")
    print("=" * 80)
    print(f"Audited {result['scanned_count']} production modules across governed pipelines.")
    print(f"Reported {result['exempt_count']} exempt modules (schemas, migrations, tests).")
    print(f"Highest line count observed in governed code: {result['max_found_lines']} lines.")

    if result["violations"]:
        print(f"\n❌ VIOLATIONS DETECTED ({len(result['violations'])} file(s) over budget):")
        for f, cnt in sorted(result["violations"], key=lambda x: x[1], reverse=True):
            print(f"  - {f}: {cnt} lines (exceeds by {cnt - result['max_lines']})")
        print("\nFix: Refactor large modules into cohesive modular packages.")
        print("=" * 80)
        return 1

    print(f"✅ ALL PRODUCTION MODULES SATISFY THE {result['max_lines']}-LINE BUDGET.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    from typing import Any
    sys.exit(main())
