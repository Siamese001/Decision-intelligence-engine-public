"""
tests/end_to_end/test_circuit_breaker_e2e.py

End-to-end pipeline test for AudioCompletenessCircuitBreaker integration into FullMeetingSynthesisStrategy.
Proves that Outputs 01, 02, and 03 are blocked immediately when audio completeness cannot be confirmed.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from dual_pipeline.audio.circuit_breaker import AudioIncompleteFailFastError
from dual_pipeline.audio_capture_certificate import (
    AudioCaptureCertificate,
    AudioSourceType,
    CertificateVerdict,
    GateFEvidence,
)
from dual_pipeline.canonical_pipeline.strategies.synthesis import FullMeetingSynthesisStrategy


class CircuitBreakerE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.mock_provider = MagicMock()
        # Mock responses from stage executors
        self.mock_forensic = MagicMock()
        self.mock_forensic.execute.return_value = "# Output 01 Forensic Transcript"
        self.mock_recap = MagicMock()
        self.mock_recap.execute.return_value = "# Output 02 Executive Recap"
        self.mock_graph = MagicMock()
        self.mock_graph.execute.return_value = '{"entities": [], "relationships": []}'

        self.strategy = FullMeetingSynthesisStrategy(
            provider=self.mock_provider,
            l2_forensic=self.mock_forensic,
            l2_recap=self.mock_recap,
            l2_graph=self.mock_graph,
        )

    def _create_cert(self, verdict: CertificateVerdict, pct: float) -> AudioCaptureCertificate:
        evidence = GateFEvidence(
            expected_duration_s=3600.0,
            actual_duration_s=36.0 * pct,
            completeness_pct=pct,
            silence_gaps=(),
            dropout_count=0,
            total_dropout_duration_s=0.0,
            source_type=AudioSourceType.ZOOM,
            audio_sha256="0" * 64,
        )
        return AudioCaptureCertificate.create(
            audio_sha256="0" * 64,
            verdict=verdict,
            gate_f_evidence=evidence,
            expected_duration_s=3600.0,
            actual_duration_s=36.0 * pct,
            source_type=AudioSourceType.ZOOM,
        )

    def test_e2e_circuit_breaker_blocks_incomplete_audio_synthesis(self):
        """When Gate F audio completeness fails, FullMeetingSynthesisStrategy fails fast with zero outputs."""
        fail_cert = self._create_cert(CertificateVerdict.FAIL, 30.0)
        metadata = {
            "is_audio": True,
            "audio_path": "/tmp/zoom_partial.m4a",
            "audio_certificate": fail_cert,
        }
        pa_context = {
            "metadata": metadata,
            "output_dir": str(self.temp_dir),
        }

        with self.assertRaises(AudioIncompleteFailFastError) as ctx:
            self.strategy.synthesize(
                source_text="Partial transcript",
                pa_context=pa_context,
                metadata=metadata,
            )

        self.assertIn("Outputs 01, 02, and 03 are BLOCKED", str(ctx.exception))
        # Verify that none of the executors were called
        self.mock_forensic.execute.assert_not_called()
        self.mock_recap.execute.assert_not_called()
        self.mock_graph.execute.assert_not_called()

    def test_e2e_circuit_breaker_authorizes_complete_audio_synthesis(self):
        """When Gate F audio completeness passes, all 3 outputs are generated."""
        pass_cert = self._create_cert(CertificateVerdict.PASS, 99.0)
        metadata = {
            "is_audio": True,
            "audio_path": "/tmp/zoom_full.m4a",
            "audio_certificate": pass_cert,
        }
        pa_context = {
            "metadata": metadata,
            "output_dir": str(self.temp_dir),
        }

        result = self.strategy.synthesize(
            source_text="Full meeting transcript with all attendees.",
            pa_context=pa_context,
            metadata=metadata,
        )

        self.assertIsNotNone(result.output_1)
        self.assertIsNotNone(result.output_2)
        self.assertIsNotNone(result.output_3)
        self.mock_forensic.execute.assert_called_once()
        self.mock_recap.execute.assert_called_once()
        self.mock_graph.execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
