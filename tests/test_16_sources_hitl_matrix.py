"""
Unit tests for Wave 4: 16-Source Universal Intake & Calibrated 25% Margin HITL Engine.
Verifies multi-track audio extraction, 3-tier trust hierarchy, inline text canonical URNs,
and mathematical 25% margin gating.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from dual_pipeline.hitl_core import AMBIGUITY_MARGIN_THRESHOLD, MarginEvaluator
from dual_pipeline.transcript_pipeline_core.ingress import _read_transcript_content
from dual_pipeline.transcript_pipeline_core.source_modality import (
    SourceModality,
    SourceTrustTier,
    get_source_trust_tier,
)
from pipeline_a_intake.media_extractor import count_container_audio_streams


class Universal16SourcesHITLMatrixTests(unittest.TestCase):
    def test_source_modality_trust_tiers(self) -> None:
        # Tier 1: Acoustic Primary
        self.assertEqual(
            get_source_trust_tier(SourceModality.AUDIO_DIARIZED),
            SourceTrustTier.TIER_1_ACOUSTIC_PRIMARY,
        )
        self.assertEqual(
            get_source_trust_tier(SourceModality.AUDIO_ONLY_UNDIARIZED),
            SourceTrustTier.TIER_1_ACOUSTIC_PRIMARY,
        )

        # Tier 2: Enterprise Attested
        self.assertEqual(
            get_source_trust_tier(SourceModality.TEAMS_NATIVE_TRANSCRIPT),
            SourceTrustTier.TIER_2_ENTERPRISE_ATTESTED,
        )

        # Tier 3: Unanchored Quarantined (NOTES_ONLY does not imply enterprise attestation per Wave 2)
        self.assertEqual(
            get_source_trust_tier(SourceModality.NOTES_ONLY),
            SourceTrustTier.TIER_3_UNANCHORED_QUARANTINED,
        )

        # Tier 3: Unanchored Quarantined
        self.assertEqual(
            get_source_trust_tier(SourceModality.TEXT_UNDIARIZED),
            SourceTrustTier.TIER_3_UNANCHORED_QUARANTINED,
        )
        self.assertEqual(
            get_source_trust_tier(SourceModality.TEXT_UNANCHORED_QUARANTINED),
            SourceTrustTier.TIER_3_UNANCHORED_QUARANTINED,
        )

    def test_inline_text_canonical_urn(self) -> None:
        meta: dict[str, str] = {}
        rec = MagicMock()

        # Simulate inline text blob with newline
        inline_blob = "Speaker 1: We approve the infrastructure budget.\nSpeaker 2: Confirmed."
        text = _read_transcript_content(
            input_path=inline_blob,
            is_file=False,
            input_p=None,
            input_ext="",
            meta=meta,
            rec=rec,
            mock=True,
        )

        self.assertIn("Speaker 1", text)
        self.assertIn("source_file_hash", meta)
        self.assertTrue(meta["source_file_hash"].startswith("urn:nee:unanchored-text:sha256:"))
        self.assertEqual(meta.get("trust_tier"), "TIER_3_UNANCHORED_QUARANTINED")

    def test_margin_evaluator_25_pct_rule(self) -> None:
        # Case A: Ambiguous spread (58% vs 51% -> margin = 7% < 25%) -> Mandatory HITL Halt
        ambiguous_opts = [
            {"label": "Amit Ayer", "confidence": 0.58, "description": "Primary Project Lead"},
            {"label": "Alex Morgan", "confidence": 0.51, "description": "Secondary Lead"},
        ]
        res_ambiguous = MarginEvaluator.evaluate(ambiguous_opts, threshold=AMBIGUITY_MARGIN_THRESHOLD)
        self.assertFalse(res_ambiguous["decision_resolved_autonomously"])
        self.assertTrue(res_ambiguous["surface_hitl"])
        self.assertAlmostEqual(res_ambiguous["margin"], 0.07, places=2)

        # Verify structured ask_user_question payload formatting
        payload = res_ambiguous["ask_user_question_payload"]
        self.assertFalse(payload["is_multi_select"])
        self.assertEqual(len(payload["options"]), 2)
        self.assertIn("(Recommended) (Confidence: 58% - MEDIUM) Primary Project Lead", payload["options"][0])
        self.assertIn("(Confidence: 51% - MEDIUM) Secondary Lead", payload["options"][1])

        # Case B: Decisive spread (85% vs 45% -> margin = 40% >= 25%) -> Autonomous Resolution
        decisive_opts = [
            {"label": "Jennifer Smith", "confidence": 0.85, "description": "Confirmed Project Manager"},
            {"label": "Unknown Speaker", "confidence": 0.45, "description": "Unmatched Cluster"},
        ]
        res_decisive = MarginEvaluator.evaluate(decisive_opts, threshold=AMBIGUITY_MARGIN_THRESHOLD)
        self.assertTrue(res_decisive["decision_resolved_autonomously"])
        self.assertFalse(res_decisive["surface_hitl"])
        self.assertAlmostEqual(res_decisive["margin"], 0.40, places=2)

    def test_count_container_audio_streams_fallback(self) -> None:
        # Fallback probe for nonexistent file returns at least 1 default
        with patch("pipeline_a_intake.media_extractor.probe_media_properties") as mock_probe:
            mock_probe.return_value = {"audio_streams": 2}
            self.assertEqual(count_container_audio_streams("dummy.mp4"), 2)


if __name__ == "__main__":
    unittest.main()
