"""
tests/unit/audio/test_circuit_breaker.py

Unit tests for AudioCompletenessCircuitBreaker and require_audio_certificate decorator.
Guarantees that downstream synthesis (Outputs 01, 02, 03) fails fast when audio
completeness cannot be confirmed.
"""

import unittest

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
    require_audio_certificate,
)
from dual_pipeline.audio_capture_certificate import (
    AudioCaptureCertificate,
    AudioSourceType,
    CertificateVerdict,
    GateFEvidence,
)
from dual_pipeline.transcript_stages import HITLRequiredError


class AudioCircuitBreakerUnitTests(unittest.TestCase):
    def _create_cert(self, verdict: CertificateVerdict, completeness_pct: float = 100.0, hitl_approved: bool = False) -> AudioCaptureCertificate:
        evidence = GateFEvidence(
            expected_duration_s=100.0,
            actual_duration_s=completeness_pct,
            completeness_pct=completeness_pct,
            silence_gaps=(),
            dropout_count=0,
            total_dropout_duration_s=0.0,
            source_type=AudioSourceType.GENERIC,
            audio_sha256="e" * 64,
            remediation_hint="Test remediation hint" if verdict != CertificateVerdict.PASS else None,
        )
        return AudioCaptureCertificate.create(
            audio_sha256="e" * 64,
            verdict=verdict,
            gate_f_evidence=evidence,
            expected_duration_s=100.0,
            actual_duration_s=completeness_pct,
            hitl_approved=hitl_approved,
        )

    def test_non_audio_run_not_blocked(self):
        """Plain text/notes ingestion without audio does not trigger circuit breaker."""
        metadata = {"input_type": "text", "is_audio": False}
        res = AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)
        self.assertIsNone(res)

    def test_pass_certificate_authorizes_outputs(self):
        """Passing audio certificate authorizes Outputs 01, 02, and 03."""
        cert = self._create_cert(CertificateVerdict.PASS, 98.0)
        metadata = {"is_audio": True, "audio_certificate": cert}
        res = AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)
        self.assertIsNotNone(res)
        self.assertEqual(res.verdict, CertificateVerdict.PASS)

    def test_fail_certificate_blocks_outputs_fail_fast(self):
        """Failing audio certificate (e.g. 30% audio) immediately raises AudioIncompleteFailFastError."""
        cert = self._create_cert(CertificateVerdict.FAIL, 30.0)
        metadata = {"is_audio": True, "audio_certificate": cert}
        with self.assertRaises(AudioIncompleteFailFastError) as ctx:
            AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)
        self.assertIn("Outputs 01, 02, and 03 are BLOCKED", str(ctx.exception))
        self.assertIn("30.0%", str(ctx.exception))

    def test_warn_certificate_without_hitl_blocks_with_hitl_required(self):
        """Warning audio certificate halts with HITLRequiredError until operator confirms."""
        cert = self._create_cert(CertificateVerdict.WARN, 85.0, hitl_approved=False)
        metadata = {"is_audio": True, "audio_certificate": cert}
        with self.assertRaises(HITLRequiredError) as ctx:
            AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)
        self.assertIn("explicit operator HITL approval receipt", str(ctx.exception))

    def test_warn_certificate_with_hitl_approval_proceeds(self):
        """Warning audio certificate proceeds once explicit operator approval is present."""
        cert = self._create_cert(CertificateVerdict.WARN, 85.0, hitl_approved=True)
        metadata = {"is_audio": True, "audio_certificate": cert}
        res = AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)
        self.assertIsNotNone(res)
        self.assertTrue(res.hitl_approved)

    def test_require_audio_certificate_decorator_enforcement(self):
        """Decorator halts execution when audio certificate is failing."""
        cert_fail = self._create_cert(CertificateVerdict.FAIL, 40.0)

        @require_audio_certificate
        def generate_deliverable(source: str, metadata: dict):
            return "Output 01 successfully generated"

        with self.assertRaises(AudioIncompleteFailFastError):
            generate_deliverable("raw transcript", metadata={"is_audio": True, "audio_certificate": cert_fail})


if __name__ == "__main__":
    unittest.main()
