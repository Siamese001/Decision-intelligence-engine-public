"""
End-to-End Test Suite for PR Semantic Code Review (Wave 3).
Verifies the 5 sample branch reviews against live repository branches,
cache integrity, and cmd_review governance gating.
"""

from __future__ import annotations

import unittest

from tools.pr_governance_core.code_review import compute_code_review
from tools.pr_governance_core.code_review_rules import ReviewVerdict
from tools.pr_governance_core.common import ROOT


class TestPRCodeReviewE2E(unittest.TestCase):
    def test_sample_branch_reviews_match_spec(self):
        """
        Verifies that compute_code_review produces the exact verdicts derived by the council:
        1. feat/rca-llm-judge-cost-opt -> SUPERSEDED_TOMBSTONE (code in main, doc remaining)
        2. feat/rca-mlx-whisper-council -> SPLIT_REQUIRED (mass reformat of 681 files)
        3. feat/output-1-pipeline-hardening -> SUPERSEDED_TOMBSTONE (whitespace formatting only)
        4. feat/graphdb-freshness-agent-panel -> GOVERNANCE_BLOCK (unsigned ledger, direct SQL)
        5. feat/rca-output3-llm-call-council -> REBASE_RETARGET (touches archived judge modules)
        """
        expected_verdicts = {
            "feat/rca-llm-judge-cost-opt": ReviewVerdict.SUPERSEDED_TOMBSTONE,
            "feat/rca-mlx-whisper-council": ReviewVerdict.SPLIT_REQUIRED,
            "feat/output-1-pipeline-hardening": ReviewVerdict.SUPERSEDED_TOMBSTONE,
            "feat/graphdb-freshness-agent-panel": ReviewVerdict.GOVERNANCE_BLOCK,
            "feat/rca-output3-llm-call-council": ReviewVerdict.REBASE_RETARGET,
        }

        for branch, expected in expected_verdicts.items():
            review = compute_code_review(branch, base_branch="main", root=ROOT, use_cache=False)
            self.assertEqual(
                review.verdict,
                expected,
                f"Branch {branch} verdict {review.verdict} did not match expected {expected}. Reason: {review.verdict_reason}",
            )
            self.assertTrue(len(review.evidence_digest) == 64, "Evidence digest must be a valid SHA-256")

    def test_cache_roundtrip_and_warm_lookup(self):
        """Verifies that review cache correctly persists and reloads identically."""
        branch = "feat/output-1-pipeline-hardening"
        cold_review = compute_code_review(branch, base_branch="main", root=ROOT, use_cache=False)
        warm_review = compute_code_review(branch, base_branch="main", root=ROOT, use_cache=True)

        self.assertEqual(cold_review.verdict, warm_review.verdict)
        self.assertEqual(cold_review.evidence_digest, warm_review.evidence_digest)
        self.assertEqual(cold_review.signals.files_changed, warm_review.signals.files_changed)
        self.assertEqual(cold_review.signals.is_whitespace_or_import_only, warm_review.signals.is_whitespace_or_import_only)

    def test_governance_block_receipt_integrity(self):
        """Verifies that a branch under GOVERNANCE_BLOCK records hazardous findings."""
        branch = "feat/graphdb-freshness-agent-panel"
        review = compute_code_review(branch, base_branch="main", root=ROOT, use_cache=True)
        self.assertEqual(review.verdict, ReviewVerdict.GOVERNANCE_BLOCK)
        self.assertTrue(any("hitl_decision_ledger" in h for h in review.signals.governance_hazards))
        self.assertTrue(any("Direct SQL" in h for h in review.signals.governance_hazards))


if __name__ == "__main__":
    unittest.main()
