"""Tests for Wave 9 requirement-to-test-to-receipt inventory closure and rollout runbook.

Verifies:
1. Every criterion in reports/jarvis_judge_forensics/contract_inventory.json is accounted for.
2. 100% of criteria are marked RESOLVED with verified wave numbers and test file references.
3. Closure rate equals 1.0 (100% closure).
4. Rollout and rollback runbook exists and includes mandatory stop criteria and invariants.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_all_inventory_criteria_resolved():
    """All 19 criteria from contract_inventory.json are documented and marked RESOLVED."""
    inv_path = REPO_ROOT / "reports/jarvis_judge_forensics/contract_inventory.json"
    rep_path = REPO_ROOT / "reports/jarvis_judge_forensics/requirement_closure_report.json"

    assert inv_path.is_file(), f"Missing inventory file at {inv_path}"
    assert rep_path.is_file(), f"Missing closure report at {rep_path}"

    inv = json.loads(inv_path.read_text(encoding="utf-8"))
    rep = json.loads(rep_path.read_text(encoding="utf-8"))

    inv_criteria = {c["criterion_id"]: c for c in inv.get("criteria", [])}
    assert len(inv_criteria) == 19

    rep_criteria = {c["criterion_id"]: c for c in rep.get("criteria_closures", [])}
    assert len(rep_criteria) == 19

    # Verify 1:1 criterion matching
    for cid, c_data in inv_criteria.items():
        assert cid in rep_criteria, f"Criterion {cid} missing from closure report"
        closure = rep_criteria[cid]
        assert closure["status"] == "RESOLVED"
        assert 1 <= closure["resolving_wave"] <= 8
        assert len(closure["test_files"]) > 0
        assert closure["receipt"].startswith("reports/jarvis_judge_forensics/receipts/wave-")

    # Summary metrics
    summary = rep.get("summary", {})
    assert summary.get("total_criteria") == 19
    assert summary.get("resolved_count") == 19
    assert summary.get("unresolved_count") == 0
    assert summary.get("closure_rate") == 1.0
    assert summary.get("terminal_disposition") == "ALL_REQUIREMENTS_RESOLVED"


def test_rollout_runbook_invariants_and_stop_criteria():
    """Rollout runbook defines mandatory stop criteria and zero direct-to-main deployment."""
    runbook_path = REPO_ROOT / "reports/jarvis_judge_forensics/rollout_rollback_runbook.md"
    assert runbook_path.is_file()

    text = runbook_path.read_text(encoding="utf-8")
    assert "Never Deploy Main Directly" in text
    assert "Zero Fabricated Passes" in text
    assert "No Revived Revocations" in text
    assert "Fabricated Pass" in text
    assert "Stale Cache Acceptance" in text
    assert "Accepted Regression" in text
    assert "Unbound Write" in text
    assert "PRAGMA integrity_check;" in text
