#!/usr/bin/env python3
"""
tests/test_wave2_security_provider_free_boundary.py

Acceptance test suite for Wave 2:
1. Provider-Free Boundary Verification (U0 -> L1 plan -> L0 route -> C0).
2. Credential Zero-Leakage & Secret Masking.
3. Rule C-19 Career Leverage & Artifact Independence Firewall.
4. Governed gpt-5.6-luna (high) Model Binding.
5. End-to-End Non-Compensating Security & Provider-Free Boundary Evaluation.
"""

import os
import unittest
from unittest import mock

from dual_pipeline.credentials import mask_secret
from dual_pipeline.llm_judge import (
    DecomposedMultiJudge,
)
from dual_pipeline.transcript_qa import evaluate_all_qa_gates
from dual_pipeline.transcript_stages import (
    C0ConformanceGate,
    L0DeterministicRouter,
    L1ForensicCognition,
    LLMTranscriptSynthesizer,
    U0IngestionBoundary,
)
from evals.evaluate_security_and_provider_free_boundary import SecurityAndProviderFreeEvaluator
from pipeline_b_extraction.pre_egress_sanitizer import LocalEgressSanitizer


class TestWave2SecurityProviderFreeBoundary(unittest.TestCase):
    def setUp(self):
        self.raw_text = (
            "### [00:00:00]\n\n**Alex Rivera:** Initial discussion on telemetry architecture.\n"
            "### [00:01:00]\n\n**Jordan Taylor:** Ensuring reliability and uptime metrics.\n"
        )
        self.clean_meta = {
            "meeting_name": "Grid Operations Review",
            "date": "08/28/2026",
            "time": "09:00AM - 10:00AM",
            "classification": "internal",
            "speaker_map": {"Speaker 1": "Alex Rivera", "Speaker 2": "Jordan Taylor"},
        }

    def test_provider_free_ingestion_pipeline(self):
        """Verify U0 -> L1 plan -> L0 -> C0 execute provider-free."""
        u0 = U0IngestionBoundary()
        u0_ctx = u0.execute(self.raw_text, self.clean_meta)
        self.assertIn("hash", u0_ctx)

        l1 = L1ForensicCognition(default_model="gpt-5.6-luna", reasoning_effort="high")
        plan = l1.plan(u0_ctx)
        self.assertEqual(plan.source_type, "DIARIZED_TEXT")
        self.assertTrue(plan.sanitization_passed)

        l0 = L0DeterministicRouter()
        l0_ctx = l0.execute(u0_ctx, self.raw_text, plan)
        self.assertIn("statements", l0_ctx)

        c0 = C0ConformanceGate()
        c0_res = c0.execute(l0_ctx)
        self.assertTrue(c0_res.get("conformance_checked"))

    def test_l0_router_blocks_restricted_credentials(self):
        """Verify L0 blocks and raises ValueError when toxic credentials are in plan."""
        toxic_text = self.raw_text + "\nConfig: sk_test_redacted_placeholder"
        u0 = U0IngestionBoundary()
        u0_ctx = u0.execute(toxic_text, self.clean_meta)

        l1 = L1ForensicCognition()
        plan = l1.plan(u0_ctx)
        self.assertFalse(plan.sanitization_passed)
        self.assertEqual(plan.classification_tier, "restricted")

        l0 = L0DeterministicRouter()
        with self.assertRaises(ValueError) as ctx:
            l0.execute(u0_ctx, toxic_text, plan)
        self.assertIn("L0 ROUTING BLOCKED", str(ctx.exception))

    def test_secret_masking_and_sanitizer(self):
        """Verify credential secrets are masked in logs and scrubbed by sanitizer."""
        key = "sk_test_redacted_placeholder"
        masked = mask_secret(key)
        self.assertEqual(masked, "sk-proj-...5678")
        self.assertNotIn("abcdef", masked)

        sanitizer = LocalEgressSanitizer()
        result = sanitizer.sanitize(f"Deployment key: {key}")
        self.assertFalse(result.is_clean)
        self.assertIn("[REDACTED_API_KEY]", result.sanitized_text)
        self.assertNotIn(key, result.sanitized_text)

    def test_career_firewall_rule_c19_integrity(self):
        """Verify Gate_Cross_4_Career_Firewall_Integrity enforces strict isolation."""
        f1 = "### [00:00:00]\n\n**Alex Rivera:** Clean engineering discussion."
        f2 = (
            "# Subject: Project Sync -- Executive Recap | 08/28/2026\n\n"
            "**Date:** 08/28/2026\n**Time:** 09:00AM - 10:00AM ET\n"
            "**Attendees:**\n- **NEE:** Alex Rivera\n\n"
            "## Meeting Objective\nAlignment.\n\n"
            "## Next Steps & Action Items\n\n"
            "| # | Action Item / Deliverable | Owner | Target Timing / Deadline | Source Locator |\n"
            "|---|---|---|---|---|\n"
            "| 1 | Architecture review | Alex Rivera | 09/15/2026 | 00:00:00 |\n"
        )
        f3 = {
            "signals": [
                {
                    "from_id": "person:mitchell_simmons",
                    "to_id": "system:architecture",
                    "relationship": "OWNS",
                }
            ]
        }

        # 1. Clean run passes
        qa_clean = evaluate_all_qa_gates(f1, f2, f3, source_text=f1, metadata={"date": "08/28/2026"})
        cross_clean = qa_clean["cross_artifact_consistency"]["gates"]["Gate_Cross_4_Career_Firewall_Integrity"]
        self.assertTrue(cross_clean["passed"])

        # 2. Injected executive compensation band fails closed
        f1_leaked = f1 + "\nStrategic note: targeting $1.2M compensation trajectory."
        qa_leaked = evaluate_all_qa_gates(f1_leaked, f2, f3, source_text=f1_leaked, metadata={"date": "08/28/2026"})
        cross_leaked = qa_leaked["cross_artifact_consistency"]["gates"]["Gate_Cross_4_Career_Firewall_Integrity"]
        self.assertFalse(cross_leaked["passed"])
        self.assertFalse(qa_leaked["passed"])

        # 3. Injected INTERNAL_ADVISORY fails closed
        f2_leaked = f2 + "\nConfidential INTERNAL_ADVISORY wedge."
        qa_leaked_2 = evaluate_all_qa_gates(f1, f2_leaked, f3, source_text=f1, metadata={"date": "08/28/2026"})
        cross_leaked_2 = qa_leaked_2["cross_artifact_consistency"]["gates"]["Gate_Cross_4_Career_Firewall_Integrity"]
        self.assertFalse(cross_leaked_2["passed"])

    def test_decomposed_multi_judge_gpt56_luna_high(self):
        """Verify DecomposedMultiJudge binds gpt-5.6-luna (high) to all sub-judges."""
        judge = DecomposedMultiJudge(
            model="gpt-5.6-luna",
            reasoning_effort="high",
            mock=True,
        )
        self.assertEqual(judge.model, "gpt-5.6-luna")
        self.assertEqual(judge.reasoning_effort, "high")
        self.assertEqual(judge.judge_forensic.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_forensic.reasoning_effort, "high")
        self.assertEqual(judge.judge_recap.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_recap.reasoning_effort, "high")
        self.assertEqual(judge.judge_graph.model, "gpt-5.6-luna")
        self.assertEqual(judge.judge_graph.reasoning_effort, "high")

        res = judge.evaluate_parallel(
            output_1_text=self.raw_text,
            output_2_text="# Recap\n\nDate: 08/28/2026",
            output_3_text='{"signals": []}',
            raw_transcript=self.raw_text,
            metadata={"date": "08/28/2026", "meeting_name": "Grid Operations"},
        )
        self.assertEqual(res["evaluator_model"], "gpt-5.6-luna")
        self.assertEqual(res["reasoning_effort"], "high")
        self.assertIn(res["status"], ("PASS", "PASS_ADVISORY", "FAIL"))

    def test_synthesizer_gpt56_luna_high_model_binding(self):
        """Verify LLMTranscriptSynthesizer properly binds gpt-5.6-luna with high reasoning."""
        fake_key = "sk_test_redacted_placeholder"
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": fake_key}):
            synth = LLMTranscriptSynthesizer(model="gpt-5.6-luna", reasoning_effort="high")
            self.assertEqual(synth.model, "gpt-5.6-luna")
            self.assertEqual(synth.reasoning_effort, "high")
            self.assertEqual(synth.api_key, fake_key)

    def test_full_security_and_provider_free_evaluator(self):
        """Run the automated security evaluation suite and verify all gates pass."""
        evaluator = SecurityAndProviderFreeEvaluator(
            target_model="gpt-5.6-luna",
            target_reasoning_effort="high",
        )
        result = evaluator.run_all()
        self.assertEqual(result["status"], "PASS")
        for gate_name, passed in result["gates"].items():
            self.assertTrue(passed, f"Gate failed: {gate_name}")


if __name__ == "__main__":
    unittest.main()
