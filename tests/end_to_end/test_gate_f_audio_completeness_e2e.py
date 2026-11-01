"""
tests/end_to_end/test_gate_f_audio_completeness_e2e.py

End-to-end pipeline test for Gate F Audio Completeness & Cryptographic Certificate lifecycle.
Exercises the full evaluation loop:
1. ForensicSignalEnvelope construction with source cryptographic hashes.
2. UpstreamAudioQAGatekeeper deterministic evaluation across Gates A through F.
3. UpstreamQAReport generation with GateEvaluation records and AudioCaptureCertificate binding.
4. Authorization verification: PASS enables synthesis plane tasks; FAIL halts and blocks.
"""

import unittest

from dual_pipeline.audio_capture_certificate import AudioSourceType, CertificateVerdict
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


class GateFAudioCompletenessE2ETests(unittest.TestCase):
    def setUp(self):
        self.gatekeeper = UpstreamAudioQAGatekeeper()
        self.sample_rate = 16000

    def _build_pipeline_envelope(
        self,
        recording_id: str,
        speech_segments: list[tuple[float, float, str]],
        total_duration_s: float,
        source_hash: str = "c" * 64,
    ) -> ForensicSignalEnvelope:
        turns = []
        for idx, (start_s, end_s, text) in enumerate(speech_segments):
            turns.append(ForensicTurnSignal(
                turn_id=f"turn-{idx:03d}",
                span=SampleSpan(
                    start_sample=int(start_s * self.sample_rate),
                    end_sample=int(end_s * self.sample_rate),
                ),
                transcript_verbatim=text,
                identity_state="cluster_only",
                speaker_cluster_ids=(f"Speaker {1 + (idx % 2)}",),
            ))

        turn_tuple = tuple(turns)
        digest = ForensicSignalEnvelope.compute_signal_digest(recording_id, (source_hash,), turn_tuple)
        return ForensicSignalEnvelope(
            recording_id=recording_id,
            source_hashes=(source_hash,),
            canonical_sample_rate_hz=self.sample_rate,
            timeline_length_samples=int(total_duration_s * self.sample_rate),
            turns=turn_tuple,
            signal_digest=digest,
        )

    def test_e2e_complete_audio_lifecycle_authorizes_synthesis(self):
        """Full end-to-end lifecycle for complete meeting audio."""
        segments = [
            (0.0, 15.0, "Welcome to the Nexus infrastructure review."),
            (16.0, 45.0, "PartnerAdvisory and client agree on the LAMS Unifier architecture."),
            (46.0, 90.0, "All decisions confirmed and next steps logged."),
        ]
        env = self._build_pipeline_envelope("rec-e2e-pass", segments, 90.0)

        report = self.gatekeeper.evaluate(
            env,
            expected_duration_s=90.0,
            source_type=AudioSourceType.SCK,
        )

        # 1. Verify report level verdicts
        self.assertEqual(report.verdict, "PASS")
        self.assertIn("forensic_rendering", report.authorized_tasks)
        self.assertIn("recap", report.authorized_tasks)
        self.assertIn("graph_extraction", report.authorized_tasks)

        # 2. Verify all 6 gates are recorded
        gate_ids = [ge.gate_id for ge in report.gate_evaluations]
        self.assertIn("Gate_A_Timestamp_Monotonicity", gate_ids)
        self.assertIn("Gate_B_Speech_Continuity_and_NoSpeech", gate_ids)
        self.assertIn("Gate_C_Attribution_Sanity", gate_ids)
        self.assertIn("Gate_D_Domain_Vocabulary_Preservation", gate_ids)
        self.assertIn("Gate_E_Acoustic_Signal_Floor", gate_ids)
        self.assertIn("Gate_F_Audio_Completeness", gate_ids)

        gate_f_eval = next(ge for ge in report.gate_evaluations if ge.gate_id == "Gate_F_Audio_Completeness")
        self.assertEqual(gate_f_eval.status, "pass")

        # 3. Verify AudioCaptureCertificate payload
        cert = report.audio_certificate
        self.assertIsNotNone(cert)
        self.assertEqual(cert.verdict, CertificateVerdict.PASS)
        self.assertEqual(cert.audio_sha256, "c" * 64)
        self.assertEqual(cert.source_type, AudioSourceType.SCK)
        self.assertGreaterEqual(cert.gate_f_evidence.completeness_pct, 95.0)

    def test_e2e_incomplete_audio_lifecycle_blocks_synthesis(self):
        """Full end-to-end lifecycle for incomplete audio blocks downstream tasks."""
        # 10-minute audio when meeting was scheduled for 60 minutes
        segments = [
            (0.0, 300.0, "Opening remarks before recording died."),
            (302.0, 600.0, "Final captured segment before crash."),
        ]
        env = self._build_pipeline_envelope("rec-e2e-fail", segments, 600.0)

        report = self.gatekeeper.evaluate(
            env,
            expected_duration_s=3600.0,
            source_type=AudioSourceType.TEAMS,
        )

        # Synthesis tasks must be completely empty (blocked)
        self.assertEqual(report.verdict, "REJECT")
        self.assertEqual(len(report.authorized_tasks), 0)

        gate_f_eval = next(ge for ge in report.gate_evaluations if ge.gate_id == "Gate_F_Audio_Completeness")
        self.assertEqual(gate_f_eval.status, "fail")

        cert = report.audio_certificate
        self.assertIsNotNone(cert)
        self.assertEqual(cert.verdict, CertificateVerdict.FAIL)
        self.assertLess(cert.gate_f_evidence.completeness_pct, 50.0)
        self.assertIsNotNone(cert.gate_f_evidence.remediation_hint)

    def test_e2e_marginal_audio_lifecycle_requires_locks(self):
        """Marginal audio capture (88%) requires PASS_WITH_LOCKS and emits WARN certificate."""
        segments = [
            (0.0, 40.0, "Section 1."),
            (42.0, 88.0, "Section 2 concluding early."),
        ]
        env = self._build_pipeline_envelope("rec-e2e-warn", segments, 88.0)

        report = self.gatekeeper.evaluate(
            env,
            expected_duration_s=100.0,
            source_type=AudioSourceType.ZOOM,
        )

        self.assertEqual(report.verdict, "PASS_WITH_LOCKS")
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.WARN)
        self.assertIn("AUDIO_DURATION_MARGINAL", [f.code for f in report.findings])


if __name__ == "__main__":
    unittest.main()
