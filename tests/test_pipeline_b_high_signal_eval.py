"""High-Signal Test Suite for Wave 3: Pipeline B Extraction, C-13 Injection & C-19 Firewall.

Governed under AGENTS.md, .agents/rules/transcript-synthesis.md, and
governance/decision_intelligence/PIPELINES_AD_HIGH_SIGNAL_METRIC_SPEC_V1.md.

Validates:
1. Metric B.1: Prompt Injection Neutralization Rate (R_c13 >= 0.80) strictly non-tautological.
2. Metric B.2: Career Leverage Firewall Zero-Leakage (C_c19 == 0) across Outputs 01, 02, and 03.
3. Metric B.3: JSON Extraction Repair Success Rate (R_repair == 1.00) on malformed completions.
4. Metric B.4: DLQ Quarantine Reliability (Q_dlq == 1.00) fail-closed without mock poisoning.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.evals.pipelines.models import PipelineBEvalScorecard
from dual_pipeline.evals.pipelines.pipeline_b_evaluator import PipelineBEvaluator
from pipeline_b_extraction.pre_egress_sanitizer import (
    LocalEgressSanitizer,
    SanitizationResult,
)


class DummyPassthroughSanitizer(LocalEgressSanitizer):
    """Adversarial mock sanitizer that performs no redaction and no modification."""

    def sanitize(self, raw_text: str) -> SanitizationResult:
        return SanitizationResult(
            sanitized_text=raw_text,
            redacted_signals=[],
            has_redactions=False,
            redaction_count=0,
        )

    def is_clean(self, raw_text: str) -> bool:
        return True


class TestPipelineBHighSignalEval(unittest.TestCase):
    """High-signal adversarial evaluation tests for Pipeline B."""

    def setUp(self) -> None:
        self.evaluator = PipelineBEvaluator()

    def test_c13_injection_neutralization_strictly_non_tautological(self) -> None:
        """Verify C-13 injection defense catches attacks and fails closed against dummy sanitizers."""
        res = self.evaluator.evaluate_c13_injection_defense()
        self.assertTrue(res["passed"])
        self.assertGreaterEqual(res["neutralization_rate"], 0.80)
        self.assertGreaterEqual(res["neutralized_count"], 4)

        # Permanent regression guard: a dummy sanitizer that does not alter text MUST FAIL
        dummy_evaluator = PipelineBEvaluator(sanitizer=DummyPassthroughSanitizer())
        dummy_res = dummy_evaluator.evaluate_c13_injection_defense()
        self.assertFalse(dummy_res["passed"])
        self.assertEqual(dummy_res["neutralized_count"], 0)
        self.assertEqual(dummy_res["neutralization_rate"], 0.0)

    def test_c19_career_firewall_zero_leakage_outputs_1_2_3(self) -> None:
        """Verify zero leakage across simulated Output 01, Output 02, and Output 03."""
        clean_outputs = [
            # Simulated Output 01 (Forensic Transcript)
            "00:01:23 Marcus Vance: We completed the substation SCADA migration on schedule.",
            # Simulated Output 02 (Executive Recap)
            "Meeting Objective: Align on Apex Global Enterprises Substation Automation Architecture.",
            # Simulated Output 03 (Canonical Signal Projection)
            '{"nodes": [{"id": "system:scada", "type": "SYSTEM"}], "predicates": ["DEPENDS_ON"]}',
        ]
        res = self.evaluator.evaluate_c19_career_firewall_isolation(client_safe_candidates=clean_outputs)
        self.assertTrue(res["passed"])
        self.assertEqual(res["leak_count"], 0)
        self.assertEqual(res["clean_candidates_tested"], 3)
        self.assertGreaterEqual(res["adversarial_leaks_caught"], 7)
        self.assertEqual(res["clean_after_sanitization_count"], 7)

    def test_json_repair_resilience_malformed_llm_payloads(self) -> None:
        """Verify recovery across diverse real-world LLM truncation and markdown artifacts."""
        malformed_payloads = [
            '```json\n{"signal": {"from_id": "system:nexus", "predicate": "DEPENDS_ON"}}\n```',
            'Here is the extraction output:\n{"assertions": [{"id": "a:1"}]}\nEnsure valid schema.',
            '{"entities": [{"id": "person:elena"}, {"id": "person:marcus"}], "truncated": true',
            '{"items": ["item1", "item2", ], }',
            '{"statement": "Substation upgrade completed", "status": "CONFIRMED"}',
        ]
        res = self.evaluator.evaluate_json_repair_resilience(truncated_payloads=malformed_payloads)
        self.assertTrue(res["passed"])
        self.assertEqual(res["success_rate"], 1.0)
        self.assertEqual(res["repaired_count"], len(malformed_payloads))

    def test_dlq_fail_closed_quarantine_without_silent_fallbacks(self) -> None:
        """Verify DLQ routes unrecoverable extraction errors without mock poisoning."""
        res = self.evaluator.evaluate_dlq_quarantine_reliability()
        self.assertTrue(res["passed"])
        self.assertEqual(res["reliability_score"], 1.0)
        self.assertEqual(res["quarantined_count"], 3)

    def test_pipeline_b_evaluator_scorecard_rfc8259_compliance(self) -> None:
        """Verify PipelineBEvalScorecard compiles into strict RFC-8259 compliant JSON."""
        scorecard: PipelineBEvalScorecard = self.evaluator.run_evaluation()
        self.assertTrue(scorecard.passed)
        self.assertGreaterEqual(scorecard.c13_injection_neutralization_rate, 0.80)
        self.assertEqual(scorecard.c19_career_firewall_leak_count, 0)
        self.assertEqual(scorecard.json_repair_success_rate, 1.0)
        self.assertEqual(scorecard.dlq_quarantine_reliability, 1.0)

        # RFC-8259 JSON serialization roundtrip
        payload = scorecard.to_dict()
        serialized = json.dumps(payload, sort_keys=True)
        self.assertIsInstance(serialized, str)
        self.assertIn("Pipeline B Evaluation: PASS", serialized)


if __name__ == "__main__":
    unittest.main()
