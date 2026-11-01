"""
tests/test_metric_discrepancy_hitl.py

Unit test suite for Wave 3: Numerical Metric Discrepancy & Enterprise Asset Reconciler.
Validates variance computation, metric extraction, HITLRequiredError formulation,
interactive resolution, and Gate 1.7 fail-closed enforcement for unreconciled ALR metrics.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.hitl_governance_core.ledger import HITLDecisionStore
from dual_pipeline.transcript_qa_core.gate1_alr import (
    check_ambiguity_lock_register_and_speaker_hitl,
)
from dual_pipeline.transcript_stages_core.metric_discrepancy_evaluator import (
    compute_metric_variance,
    evaluate_metric_discrepancies,
    extract_numerical_metrics,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError


class TestMetricDiscrepancyHITL(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.temp_dir.name) / "hitl_decision_ledger.json"
        self.store = HITLDecisionStore(self.ledger_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()
    def test_compute_metric_variance(self) -> None:
        """Verify mathematical calculation of relative metric variance."""
        self.assertEqual(compute_metric_variance([5000, 5000]), 0.0)
        self.assertEqual(compute_metric_variance([100]), 0.0)

        # 5,750 vs 2,000 -> (5750 - 2000) / 5750 = 3750 / 5750 ~= 0.652
        v1 = compute_metric_variance([5750, 2000])
        self.assertAlmostEqual(v1, 3750.0 / 5750.0, places=3)
        self.assertGreater(v1, 0.15)

        # 5,000 vs 4,800 -> (5000 - 4800) / 5000 = 0.04 (< 15%)
        v2 = compute_metric_variance([5000, 4800])
        self.assertAlmostEqual(v2, 0.04, places=3)
        self.assertLess(v2, 0.15)

    def test_extract_numerical_metrics(self) -> None:
        """Verify regex and semantic domain extraction for Gemini Enterprise licenses."""
        transcript = """00:15:30
Alex Brannan: We have 5,750 provisioned Gemini Enterprise licenses across the account, but only 4,750 users have logged in.
Dianne Eldridge: In the nuclear organization specifically, we only have 2,000 assigned Gemini Enterprise licenses.
"""
        extracted = extract_numerical_metrics(transcript)
        self.assertIn("gemini_enterprise_licenses", extracted)
        values = [e["value"] for e in extracted["gemini_enterprise_licenses"]]
        self.assertIn(5750.0, values)
        self.assertIn(4750.0, values)
        self.assertIn(2000.0, values)

    def test_evaluate_metric_discrepancies_raises_hitl(self) -> None:
        """Evaluating conflicting license counts raises HITLRequiredError with calibrated options."""
        transcript = """00:15:30
Alex Brannan: We have 5,750 provisioned Gemini Enterprise licenses.
Dianne Eldridge: No, we have 2,000 assigned Gemini Enterprise licenses.
"""
        meta: Dict[str, Any] = {"meeting_name": "License Sync"}
        with self.assertRaises(HITLRequiredError) as ctx:
            evaluate_metric_discrepancies(transcript, meta)

        err = ctx.exception
        self.assertEqual(err.hitl_type, "metric_discrepancy")
        self.assertIn("Authoritative Metric Reconciliation", err.prompt)
        self.assertTrue(len(err.options) >= 2)
        self.assertTrue(meta.get("invalidate_prior_outputs"))

    def test_evaluate_metric_discrepancies_with_interactive_resolver(self) -> None:
        """An interactive resolver resolves the metric and records the receipt without raising."""
        transcript = """00:15:30
Alex Brannan: We have 5,750 provisioned Gemini Enterprise licenses.
Dianne Eldridge: No, we have 2,000 assigned Gemini Enterprise licenses.
"""
        meta: Dict[str, Any] = {"meeting_name": "License Sync"}

        def mock_resolver(envelope: Any) -> str:
            return "5,750 units (Provisioned Baseline)"

        res_meta = evaluate_metric_discrepancies(
            transcript, meta, store=self.store, interactive_resolver=mock_resolver
        )
        self.assertIn("authoritative_metrics", res_meta)
        self.assertEqual(res_meta["authoritative_metrics"]["gemini_enterprise_licenses"], "5,750 units (Provisioned Baseline)")
        self.assertIn("hitl_metric_receipts", res_meta)

    def test_gate1_alr_fails_closed_on_unreconciled_differing_figures(self) -> None:
        """Gate 1.7 fails closed if ALR passively dumps differing metric figures without HITL receipt."""
        f1_text = """# Forensic Transcript: License Sync
**Date:** 09/29/2026

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution & Acoustic Grounding: Alex Brannan | [00:00:00 – Session End] | stmt-spk-01 | Attributed to Alex Brannan. | Multi-Tier Acoustic Isolation Protocol |
| 2 | Speaker Attribution & Acoustic Grounding: Dianne Eldridge | [00:00:00 – Session End] | stmt-spk-02 | Attributed to Dianne Eldridge. | Multi-Tier Acoustic Isolation Protocol |
| 3 | Enterprise Domain Lexicon | [00:00:00 – Session End] | stmt-lex-03 | Enterprise taxonomy. | Standard Enterprise Taxonomy Verification |
| 4 | Apex Global license counts | [00:22:40–00:32:11] | stmt-04 | The source contains differing figures: approximately 2,000 assigned, 5,750 provisioned. These figures are not reconciled into one definitive count. | Conflicting contemporaneous statements |

# Transcript Dialogue Turns
Alex Brannan -- Google (Partner Engineering Lead): We have 5,750 licenses.
Dianne Eldridge -- Google (CE Director): We have 2,000 licenses.
"""
        meta: Dict[str, Any] = {
            "attendees": [
                {"name": "Alex Brannan", "org": "Google", "role": "Partner Engineering Lead"},
                {"name": "Dianne Eldridge", "org": "Google", "role": "CE Director"},
            ],
            "pre_enrichment_roles": {
                "Alex Brannan": "Partner Engineering Lead",
                "Dianne Eldridge": "CE Director",
            },
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        self.assertFalse(passed)
        self.assertTrue(any("AMBIGUITY_LOCK_REGISTER_UNRECONCILED_METRICS" in f for f in failures))

    def test_gate1_alr_passes_when_metric_receipt_bound(self) -> None:
        """Gate 1.7 passes when an authentic operator metric resolution receipt is present."""
        f1_text = """# Forensic Transcript: License Sync
**Date:** 09/29/2026

## Ambiguity Lock Register
| Item # | Identified Ambiguity | Source Locator | Affected Statement IDs | Final Resolved Record | Resolution Basis |
|---|---|---|---|---|---|
| 1 | Speaker Attribution & Acoustic Grounding: Alex Brannan | [00:00:00 – Session End] | stmt-spk-01 | Attributed to Alex Brannan. | Multi-Tier Acoustic Isolation Protocol |
| 2 | Speaker Attribution & Acoustic Grounding: Dianne Eldridge | [00:00:00 – Session End] | stmt-spk-02 | Attributed to Dianne Eldridge. | Multi-Tier Acoustic Isolation Protocol |
| 3 | Enterprise Domain Lexicon | [00:00:00 – Session End] | stmt-lex-03 | Enterprise taxonomy. | Standard Enterprise Taxonomy Verification |
| 4 | Apex Global license counts | [00:22:40–00:32:11] | stmt-04 | The source contains differing figures: approximately 2,000 assigned, 5,750 provisioned. | Operator Verified (HITL Metric Reconciliation) |

# Transcript Dialogue Turns
Alex Brannan -- Google (Partner Engineering Lead): We have 5,750 licenses.
Dianne Eldridge -- Google (CE Director): We have 2,000 licenses.
"""
        meta: Dict[str, Any] = {
            "attendees": [
                {"name": "Alex Brannan", "org": "Google", "role": "Partner Engineering Lead"},
                {"name": "Dianne Eldridge", "org": "Google", "role": "CE Director"},
            ],
            "pre_enrichment_roles": {
                "Alex Brannan": "Partner Engineering Lead",
                "Dianne Eldridge": "CE Director",
            },
            "hitl_metric_receipts": {
                "gemini_enterprise_licenses": {"status": "RESOLVED_HITL", "hitl_receipt_bound": True},
            },
            "authoritative_metrics": {
                "gemini_enterprise_licenses": "5,750 provisioned licenses",
            },
        }
        passed, details, failures = check_ambiguity_lock_register_and_speaker_hitl(f1_text, meta)
        # Should not have AMBIGUITY_LOCK_REGISTER_UNRECONCILED_METRICS failure
        self.assertFalse(any("AMBIGUITY_LOCK_REGISTER_UNRECONCILED_METRICS" in f for f in failures))


if __name__ == "__main__":
    unittest.main()
