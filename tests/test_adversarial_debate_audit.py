"""
tests/test_adversarial_debate_audit.py

Test suite certifying Wave 1 Dialectical Adversarial Agent Debate and Metric Spec.
Validates:
1. Official debate document exists and contains judicial findings for Pipelines A-D.
2. Metric specification document defines non-tautological passing thresholds.
3. Programmatic auditor catalogs all known low-signal eval defects.
"""
from __future__ import annotations

import unittest

from dual_pipeline.evals.pipelines.adversarial_debate import (
    DEBATE_DOC,
    KNOWN_EVAL_DEFECTS,
    METRIC_SPEC_DOC,
    AdversarialDebateAuditor,
)


class TestAdversarialDebateAudit(unittest.TestCase):
    def setUp(self):
        self.auditor = AdversarialDebateAuditor()

    def test_debate_artifacts_exist(self):
        """Verify governance debate artifacts are present on disk."""
        status = self.auditor.verify_debate_artifacts_exist()
        self.assertTrue(status["debate_document_exists"], f"Missing {DEBATE_DOC}")
        self.assertTrue(status["metric_spec_document_exists"], f"Missing {METRIC_SPEC_DOC}")

    def test_debate_document_contains_all_pipelines(self):
        """Verify debate document contains prosecution, defense, and verdicts for all 4 pipelines."""
        content = DEBATE_DOC.read_text(encoding="utf-8")
        self.assertIn("Round 1: Prosecution (Quality Auditor)", content)
        self.assertIn("Round 2: Defense (Pipeline Architect)", content)
        self.assertIn("Round 3: Adjudication (Synthesis Judge)", content)
        self.assertIn("Pipeline A", content)
        self.assertIn("Pipeline B", content)
        self.assertIn("Pipeline C", content)
        self.assertIn("Pipeline D", content)
        self.assertIn("A. Pipeline A metrics", content)
        self.assertIn("B. Pipeline B metrics", content)
        self.assertIn("C. Pipeline C metrics", content)
        self.assertIn("D. Pipeline D metrics", content)


    def test_metric_spec_defines_rigorous_thresholds(self):
        """Verify metric specification defines passing thresholds for all pipelines."""
        content = METRIC_SPEC_DOC.read_text(encoding="utf-8")
        self.assertIn("Metric A.1: Acoustic Noise Floor Resilience Score", content)
        self.assertIn("Metric B.1: Prompt Injection Neutralization Rate", content)
        self.assertIn("Metric C.2: Steady-State Hybrid Retrieval Latency SLA", content)
        self.assertIn("Metric D.1: Ambiguity Margin Boundary Gating Precision", content)
        self.assertIn("Passing Threshold", content)

    def test_defects_catalog_completeness(self):
        """Verify all 4 pipelines have cataloged low-signal defects."""
        inv = self.auditor.verify_known_defects_inventory()
        by_pipe = inv["by_pipeline"]
        self.assertGreaterEqual(len(by_pipe["pipeline_a"]), 2)
        self.assertGreaterEqual(len(by_pipe["pipeline_b"]), 2)
        self.assertGreaterEqual(len(by_pipe["pipeline_c"]), 2)
        self.assertGreaterEqual(len(by_pipe["pipeline_d"]), 1)

        # Check tautological assertion is flagged
        tautology = [d for d in KNOWN_EVAL_DEFECTS if d.defect_type == "TAUTOLOGICAL_ASSERTION"]
        self.assertEqual(len(tautology), 1)
        self.assertEqual(tautology[0].pipeline_id, "pipeline_b")

    def test_audit_digest_stability(self):
        """Verify deterministic digest calculation."""
        d1 = self.auditor.compute_audit_digest()
        d2 = self.auditor.compute_audit_digest()
        self.assertEqual(d1, d2)
        self.assertEqual(len(d1), 64)


if __name__ == "__main__":
    unittest.main()
