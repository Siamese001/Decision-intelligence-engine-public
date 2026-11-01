#!/usr/bin/env python3
"""
tools/verify_presentation_linter.py

Verification tool for Wave 5: Decision Service & Presentation Linter (Anti-Scope-Bleed Gate).
Verifies rule enforcement, table header gating, attribution boundary preservation, and pytest suite passage.
Produces reports/wave5_presentation_linter_receipt.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dual_pipeline.presentation_linter import (
    PresentationLinter,
)


def verify_wave5() -> dict:
    linter = PresentationLinter()

    # 1. Invalid compound table header check
    bad_header = "| Role | Expected Capacity | Observed Field Reality (Project LENS / Sherpa) |"
    bad_violations = linter.lint_text(bad_header)
    bad_blocked = (
        len(bad_violations) == 1
        and bad_violations[0].rule_name == "RuleCompoundWorkstreamHeader"
        and bad_violations[0].required_correction == "Observed Field Reality (Project LENS — Jen Pan)"
    )

    # 2. Approved corrected table header check
    good_header = "| Role | Expected Capacity | Observed Field Reality (Project LENS — Jen Pan) |"
    good_violations = linter.lint_text(good_header)
    good_approved = (len(good_violations) == 0)

    # 3. Attribution bleed in Sherpa scope check
    sherpa_scope_text = "#### Project Beacon Pod\n- Jen Pan: Engagement PM"
    bleed_violations = linter.lint_text(sherpa_scope_text)
    bleed_blocked = (
        len(bleed_violations) == 1
        and bleed_violations[0].rule_name == "RuleAttributionBleed"
    )

    # 4. Run pytest suite
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(REPO_ROOT / "tests" / "test_presentation_linter.py"),
        "-v",
        "--timeout=60",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT)
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), env=env)
    pytest_passed = (res.returncode == 0)

    checks = {
        "compound_header_blocked": bad_blocked,
        "approved_header_passed": good_approved,
        "attribution_bleed_blocked": bleed_blocked,
        "pytest_suite_passed": pytest_passed,
    }

    all_passed = all(checks.values())

    receipt = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "wave": "Wave 5: Decision Service & Presentation Linter (Anti-Scope-Bleed Gate)",
        "checks": checks,
        "required_correction": "Observed Field Reality (Project LENS — Jen Pan)",
        "pytest_output_summary": res.stdout.splitlines()[-1] if res.stdout else "",
        "pass": all_passed,
    }

    receipt_path = REPO_ROOT / "reports" / "wave5_presentation_linter_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"Wave 5 receipt written to {receipt_path}")
    print(f"Overall status: {'PASS' if all_passed else 'FAIL'}")
    return receipt


if __name__ == "__main__":
    receipt = verify_wave5()
    if not receipt["pass"]:
        sys.exit(1)
