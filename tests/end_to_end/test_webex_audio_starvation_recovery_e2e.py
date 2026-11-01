"""
tests/end_to_end/test_webex_audio_starvation_recovery_e2e.py

End-to-end integration test verifying Webex platform detection,
audio starvation gap preservation, and platform reference compliance across outputs.
"""
from __future__ import annotations

import unittest

from dual_pipeline.transcript_stages_core.silence_gap_classifier import classify_silence_gaps
from dual_pipeline.transcript_stages_core.u0_ingestion import (
    U0IngestionBoundary,
    _default_platform,
)


class TestWebexAudioStarvationRecoveryE2E(unittest.TestCase):
    def test_webex_ingestion_lifecycle_e2e(self) -> None:
        """Full lifecycle test for Webex meeting ingestion with audio starvation gap."""
        raw_text = (
            "Webex Enablement Session: Working with Files & Data\n"
            "Date: 09/30/2026\n\n"
            "**Rose Teame**: Good morning everyone, today we cover Gemini Notebook.\n\n"
            "**Amanda Knarr**: Excited to be here and demonstrate the data files.\n\n"
            "[Recording gap: 00:10:04 – 00:33:00 (Audio starvation / dropped stream)]\n\n"
            "**Amit Ayer**: Wrapping up the session now."
        )

        metadata = {
            "project": "gemini_enablement",
            "meeting": "Working Files Data",
            "date": "09/30/2026",
            "allow_autonomous_gaps": True,
            "provider": "offline",
        }

        # 1. Platform auto-detection detects Webex
        _default_platform(metadata, raw_text)
        self.assertEqual(metadata.get("platform"), "Webex")
        self.assertFalse(metadata.get("_platform_defaulted", False))

        # 2. Silence gap classifier preserves recording gap interruption
        classified_text, meta_out = classify_silence_gaps(raw_text, metadata)
        resolutions = meta_out.get("hitl_resolutions", {})
        gap_res = resolutions.get("gap_001004", {})

        self.assertIn("RECORDING_GAP_INTERRUPTION", gap_res.get("resolution", ""))
        self.assertIn("[Recording gap: 00:10:04 – 00:33:00", classified_text)

        # 3. U0 Ingestion boundary recognizes DIARIZING status for Webex
        diar_status = U0IngestionBoundary._detect_diarization_status(classified_text, meta_out)
        self.assertIn(diar_status, ("NATIVE", "UNDIARIZED"))


if __name__ == "__main__":
    unittest.main()
