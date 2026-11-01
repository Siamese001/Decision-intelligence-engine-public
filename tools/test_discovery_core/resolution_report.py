"""
tools/test_discovery_core/resolution_report.py

Generates Wave 5 Test Resolution and Enrollment Parity Report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Sequence, Set

from .discovery import discover_all_test_files, resolve_all_tests
from .quarantine import get_quarantined_modules

ROOT = Path(__file__).resolve().parents[2]


def generate_resolution_report(
    base_deterministic: Sequence[str] = (),
    base_e2e: Sequence[str] = (),
    base_bge: Sequence[str] = (),
    out_path: Path | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """Generates the test resolution and parity report."""
    root = repo_root or ROOT
    all_files = discover_all_test_files(root)
    total_files = len(all_files)

    det_tests, e2e_tests, bge_tests = resolve_all_tests(
        base_deterministic=base_deterministic,
        base_e2e=base_e2e,
        base_bge=base_bge,
        repo_root=root,
    )

    quarantined = get_quarantined_modules()
    resolved_set: Set[str] = set(det_tests) | set(e2e_tests) | set(bge_tests)

    # Parity check against historical base tuples
    old_enrolled_set = set(base_deterministic) | set(base_e2e) | set(base_bge)
    missing_from_resolved = [
        t for t in old_enrolled_set if t not in resolved_set and t not in quarantined
    ]
    parity_passed = len(missing_from_resolved) == 0

    # Coverage computation
    accounted_count = len(resolved_set) + len(quarantined)
    coverage_pct = round((accounted_count / total_files) * 100.0, 2) if total_files > 0 else 100.0

    report = {
        "schema_version": "1.0.0",
        "report_type": "aidlc-wave-5-test-resolution-report",
        "total_test_files_on_disk": total_files,
        "total_resolved_tests": len(resolved_set),
        "total_quarantined_tests": len(quarantined),
        "total_accounted_tests": accounted_count,
        "coverage_percentage": coverage_pct,
        "coverage_threshold_met": coverage_pct >= 95.0,
        "old_enrolled_count": len(old_enrolled_set),
        "parity_passed": parity_passed,
        "missing_from_resolved": missing_from_resolved,
        "profile_breakdown": {
            "deterministic_tests_count": len(det_tests),
            "e2e_tests_count": len(e2e_tests),
            "bge_tests_count": len(bge_tests),
            "quarantined_tests_count": len(quarantined),
        },
    }

    p = out_path or (root / "reports" / "aidlc" / "w5_resolution.json")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
