"""Wave 2 fail-closed checks for stale receipts and priority tie-breaking."""
from __future__ import annotations

import json

from tools.pr_governance_core.decision_api import MergeEligibility, _determine_branch_eligibility
from tools.pr_governance_core.plan_ownership import _verified_wave_receipt
from tools.pr_governance_core.queue_order import sort_queue
from tools.pr_governance_core.outcome_metadata import evaluate_branch_outcome


def test_receipt_from_another_branch_cannot_prove_wave_completion(tmp_path):
    receipt = {
        "terminal_outcome": "PASS", "wave": 2, "branch": "feat/other",
        "head_sha": "head-current", "plan_sha256": "plan-current",
    }
    receipt_text = json.dumps(receipt)
    assert _verified_wave_receipt("feat/current", 2, "head-current", "plan-current", tmp_path, None, lambda *_: receipt_text) is None
    receipt["branch"] = "feat/current"
    valid_text = json.dumps(receipt)
    assert _verified_wave_receipt("feat/current", 2, "head-current", "plan-current", tmp_path, None, lambda *_: valid_text)


def test_unknown_git_facts_block_merge_eligibility():
    status, reasons = _determine_branch_eligibility("feat/unknown", "P0", None, None, False, [], "main")
    assert status == MergeEligibility.BLOCKED_GOVERNANCE.value
    assert "unknown" in reasons[0].lower()


def test_missing_current_revision_readiness_blocks_merge_eligibility():
    status, reasons = _determine_branch_eligibility("feat/unreviewed", "P1", 2, True, False, [], "main", False)
    assert status == MergeEligibility.BLOCKED_GOVERNANCE.value
    assert "review" in reasons[0].lower()


def test_cost_precedes_latency_even_when_latency_is_lower_risk():
    items = [
        {"branch": "feat/latency", "tier": "P0", "benefit_category": "LATENCY_IMPROVEMENT", "risk_tier": 0, "value_score": 100},
        {"branch": "feat/cost", "tier": "P0", "benefit_category": "COST_REDUCTION", "risk_tier": 3, "value_score": 1},
    ]
    sort_queue(items)
    assert [item["branch"] for item in items] == ["feat/cost", "feat/latency"]


def test_unverified_magnitude_preserves_supported_benefit_explanation():
    from tools.pr_governance_core.outcome_metadata import UNMEASURED_VALUE_STATEMENT
    outcome = evaluate_branch_outcome("perf/cache", "P0", ["dual_pipeline/token_optimization/cache.py"])
    assert "paid model calls" in outcome.expected_benefit
    assert outcome.measurement_status == "UNVERIFIED"
    assert outcome.value_statement() == UNMEASURED_VALUE_STATEMENT
