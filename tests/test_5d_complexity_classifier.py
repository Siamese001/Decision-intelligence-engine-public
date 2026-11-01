"""
tests/test_5d_complexity_classifier.py

Unit test suite verifying Wave 3 5-Dimensional Complexity Tensor & Model Routing:
- False-Negative Protection (short prompt with CapEx/SOW conflict routes to Sol 6.1)
- False-Positive Protection (45k-token lexical ASR cleanup routes to Luna)
- Real-time Copilot Decoupling (routes to COPILOT_FAST_SNAP)
- Epistemic Ambiguity Escalation (Delta < 0.15 triggers Sol)
- Dialectical Judgment Lock (release gates / judges route to Astra)
- Stage Synthesizer Model Decoupling (Stage 01/02 Luna, Stage 03 Sol 6.1)
"""
from __future__ import annotations

import unittest

from dual_pipeline.model_registry import ModelTier, resolve_tier_model_name
from dual_pipeline.query_router_core.complexity_classifier import (
    ComplexityTensor,
    evaluate_complexity_tensor,
    has_financial_or_contractual_conflict,
)
from dual_pipeline.transcript_stages_core.external_synthesizer import init_stage_models


class Test5DComplexityClassifier(unittest.TestCase):
    """Test suite for 5D Complexity Tensor and model routing."""

    def setUp(self) -> None:
        import os
        from unittest import mock
        self.env_patcher = mock.patch.dict(
            os.environ,
            {
                "FAST_ROUTING_MODEL": "",
                "FAST_ROUTING_REASONING_EFFORT": "",
                "COPILOT_MODEL": "",
                "COPILOT_REASONING_EFFORT": "",
                "QUERY_REASONING_MODEL": "",
                "QUERY_REASONING_REASONING_EFFORT": "",
                "OPENAI_MODEL": "",
                "OPENAI_REASONING_EFFORT": "",
            },
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    def test_false_negative_protection_short_financial_prompt(self) -> None:
        """
        Adversarial Test: Short 400-token prompt with CapEx and SOW contradiction
        MUST route to Sol 6.1 (QUERY_REASONING), NEVER to cheap Luna.
        """
        short_financial_prompt = (
            "Review the vendor invoice against SOW milestone 3. "
            "There is a $450,000 CapEx overrun dispute regarding battery storage "
            "interconnection penalties and contractual liquidated damages under section 14.2."
        )
        # Token count is tiny (~40 tokens << 16k)
        tensor, tier = evaluate_complexity_tensor(
            short_financial_prompt,
            metadata={"task_type": "decision_query", "blast_radius": 3},
        )
        self.assertEqual(tier, ModelTier.QUERY_REASONING)
        self.assertEqual(resolve_tier_model_name(tier), "gpt-6.1-sol")
        self.assertGreaterEqual(tensor.cognitive_depth, 4)
        self.assertTrue(has_financial_or_contractual_conflict(short_financial_prompt))

    def test_false_positive_protection_large_lexical_transform(self) -> None:
        """
        Adversarial Test: Massive 45,000-token WebVTT caption file requiring
        simple timestamp stripping MUST route to Luna (FAST_ROUTING), saving 97.5%.
        """
        # Simulate 45,000 token caption payload
        fake_caption_block = "00:01:23.450 --> 00:01:26.780\nGood morning everyone, welcome to the sync.\n\n"
        large_vtt_payload = fake_caption_block * 3000  # ~45k tokens

        tensor, tier = evaluate_complexity_tensor(
            large_vtt_payload,
            metadata={"task_type": "asr_cleanup", "blast_radius": 1, "cognitive_depth": 1},
        )
        self.assertEqual(tier, ModelTier.FAST_ROUTING)
        self.assertEqual(resolve_tier_model_name(tier), "gpt-6-luna")
        self.assertEqual(tensor.cognitive_depth, 1)
        self.assertEqual(tensor.needle_dispersion, 3)  # Large context recognized, but not penalized

    def test_realtime_copilot_streaming_decoupling(self) -> None:
        """
        Verifies real-time streaming tasks pin strictly to COPILOT_FAST_SNAP (Luna)
        under tight latency SLAs (<1.5s TTFT, <7s hard timeout).
        """
        dialogue_turn = "Speaker 2: We should reconsider the turbine deployment timetable."
        tensor, tier = evaluate_complexity_tensor(
            dialogue_turn,
            metadata={"task_type": "copilot", "is_streaming": True, "sla_seconds": 1.5},
        )
        self.assertEqual(tier, ModelTier.COPILOT_FAST_SNAP)
        self.assertEqual(resolve_tier_model_name(tier), "gpt-6-luna")
        self.assertEqual(tensor.latency_sla, 1)

    def test_epistemic_ambiguity_narrow_margin_escalation(self) -> None:
        """
        Verifies narrow ambiguity margin (Delta < 0.15) escalates to Sol 6.1
        to prevent hallucinated attributions or superficial entity links.
        """
        payload = "Assign attribution for ambiguous speaker remark on SOW scope boundaries."

        # Case 1: Ambiguity Delta = 0.05 (< 0.15) -> Escalate to Sol
        tensor_ambiguous, tier_ambiguous = evaluate_complexity_tensor(
            payload,
            metadata={"task_type": "attribution", "ambiguity_margin": 0.05, "blast_radius": 3},
        )
        self.assertEqual(tier_ambiguous, ModelTier.QUERY_REASONING)
        self.assertEqual(resolve_tier_model_name(tier_ambiguous), "gpt-6.1-sol")

        # Case 2: Decisive Delta = 0.45 (>= 0.15) on standard payload -> Clean resolution remains on Luna
        decisive_payload = "Assign attribution for standard speaker remark on meeting schedule."
        tensor_decisive, tier_decisive = evaluate_complexity_tensor(
            decisive_payload,
            metadata={"task_type": "extraction", "ambiguity_margin": 0.45, "blast_radius": 1, "cognitive_depth": 2},
        )
        self.assertEqual(tier_decisive, ModelTier.FAST_ROUTING)
        self.assertEqual(resolve_tier_model_name(tier_decisive), "gpt-6-luna")

    def test_dialectical_judgment_and_release_gates_lock(self) -> None:
        """
        Verifies release gates and dialectical evaluations lock strictly to Astra (DIALECTICAL_JUDGE).
        """
        gate_payload = "Evaluate 11 non-compensating BGE-M3 retrieval gates and confirm zero drift."
        tensor, tier = evaluate_complexity_tensor(
            gate_payload,
            metadata={"task_type": "release_gate", "blast_radius": 4, "cognitive_depth": 5},
        )
        self.assertEqual(tier, ModelTier.DIALECTICAL_JUDGE)
        self.assertEqual(resolve_tier_model_name(tier), "gpt-6-luna")
        self.assertEqual(tensor.cognitive_depth, 5)
        self.assertEqual(tensor.blast_radius, 4)

    def test_complexity_tensor_composite_score_properties(self) -> None:
        """Verifies mathematical properties of ComplexityTensor composite score."""
        t1 = ComplexityTensor(
            cognitive_depth=1,
            ambiguity_margin=1.0,
            blast_radius=1,
            needle_dispersion=1,
            latency_sla=1,
        )
        # 0.40(1) + 0.30(1) + 0.15(1) + 0.15(0) = 0.85
        self.assertAlmostEqual(t1.composite_score, 0.85, places=2)

        t_max = ComplexityTensor(
            cognitive_depth=5,
            ambiguity_margin=0.0,
            blast_radius=4,
            needle_dispersion=3,
            latency_sla=3,
        )
        # 0.40(5) + 0.30(4) + 0.15(3) + 0.15(5) = 2.0 + 1.2 + 0.45 + 0.75 = 4.40
        self.assertAlmostEqual(t_max.composite_score, 4.40, places=2)

    def test_stage_models_initialization_routing(self) -> None:
        """
        Verifies init_stage_models uses 5D complexity tensor to decouple stages:
        - Stage 01 (Forensic Transcript ASR) -> gpt-6-luna
        - Stage 02 (Executive Recap) -> gpt-6-luna
        - Stage 03 (Canonical Signal Projection) -> gpt-6.1-sol
        - Agentic Leverage -> gpt-6.1-sol
        """
        class DummySynth:
            pass

        synth = DummySynth()
        init_stage_models(
            synth=synth,
            model=None,
            d01=None,
            d02=None,
            d03=None,
            agentic=None,
            use_complexity_classifier=True,
        )

        self.assertEqual(synth.d01_model, "gpt-6-luna")
        self.assertEqual(synth.d02_model, "gpt-6-luna")
        self.assertEqual(synth.d03_model, "gpt-6.1-sol")
        self.assertEqual(synth.agentic_model, "gpt-6.1-sol")

    def test_extended_financial_synonyms_escalate_to_sol(self) -> None:
        """Verifies natural language financial synonyms trigger Sol escalation."""
        synonym_prompts = [
            "We have a 10% retainage on the invoice pending commercial breach remediation.",
            "The capital expenditure for substation transformers was unbudgeted.",
            "There is a material breach claim regarding withheld payment under section 9.",
        ]
        for prompt in synonym_prompts:
            self.assertTrue(
                has_financial_or_contractual_conflict(prompt),
                f"Failed to detect financial conflict in: {prompt}",
            )
            tensor, tier = evaluate_complexity_tensor(prompt, metadata={"blast_radius": 3})
            self.assertEqual(tier, ModelTier.QUERY_REASONING)
            self.assertEqual(tensor.cognitive_depth, 4)

    def test_speaker_dialogue_does_not_inflate_lexical_cognitive_depth(self) -> None:
        """Verifies 'Speaker 1:' in dialogue does not falsely inflate cognitive depth for lexical cleanup."""
        dialogue = "Speaker 1: Hi there.\nSpeaker 2: Hello.\nSpeaker 1: Can we check the calendar?"
        tensor, tier = evaluate_complexity_tensor(
            dialogue,
            metadata={"task_type": "format_text", "cognitive_depth": 1, "blast_radius": 1},
        )
        self.assertEqual(tensor.cognitive_depth, 1)
        self.assertEqual(tier, ModelTier.FAST_ROUTING)


if __name__ == "__main__":
    unittest.main()
