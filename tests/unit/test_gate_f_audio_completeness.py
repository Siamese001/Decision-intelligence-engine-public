"""
tests/unit/test_gate_f_audio_completeness.py

Unit tests for Gate F (Audio Completeness & Cryptographic Integrity).
Validates duration completeness thresholds, silence gap classification,
micro-dropout accumulation, and AudioCaptureCertificate emission.
"""

import unittest

from dual_pipeline.audio_capture_certificate import AudioSourceType, CertificateVerdict
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


class GateFAudioCompletenessUnitTests(unittest.TestCase):
    def setUp(self):
        self.gatekeeper = UpstreamAudioQAGatekeeper()
        self.sample_rate = 16000

    def _make_turn(self, turn_id: str, start_sec: float, end_sec: float, text: str = "Test speech") -> ForensicTurnSignal:
        return ForensicTurnSignal(
            turn_id=turn_id,
            span=SampleSpan(
                start_sample=int(start_sec * self.sample_rate),
                end_sample=int(end_sec * self.sample_rate),
            ),
            transcript_verbatim=text,
            identity_state="cluster_only",
            speaker_cluster_ids=("Speaker 1",),
        )

    def _make_envelope(
        self,
        recording_id: str,
        turns: tuple[ForensicTurnSignal, ...],
        total_duration_s: float,
        source_hash: str = "a" * 64,
    ) -> ForensicSignalEnvelope:
        digest = ForensicSignalEnvelope.compute_signal_digest(recording_id, (source_hash,), turns)
        return ForensicSignalEnvelope(
            recording_id=recording_id,
            source_hashes=(source_hash,),
            canonical_sample_rate_hz=self.sample_rate,
            timeline_length_samples=int(total_duration_s * self.sample_rate),
            turns=turns,
            signal_digest=digest,
        )

    def test_gate_f_pass_on_full_duration(self):
        turn1 = self._make_turn("turn-01", 0.0, 10.0, "Welcome to the meeting.")
        turn2 = self._make_turn("turn-02", 12.0, 30.0, "Discussing LAMS and Unifier.")
        turn3 = self._make_turn("turn-03", 32.0, 60.0, "Meeting concluded.")
        env = self._make_envelope("rec-pass", (turn1, turn2, turn3), 60.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=60.0)
        self.assertEqual(report.verdict, "PASS")
        self.assertIsNotNone(report.audio_certificate)
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.PASS)
        self.assertGreaterEqual(report.audio_certificate.gate_f_evidence.completeness_pct, 95.0)

    def test_gate_f_fail_on_under_80_percent_duration_pr_n4k5p7m_pin(self):
        """Regression pin for PR n4k5p7m: 30% audio capture must fail closed."""
        turn1 = self._make_turn("turn-01", 0.0, 500.0, "Early meeting section.")
        turn2 = self._make_turn("turn-02", 505.0, 1080.0, "Premature cutoff.")
        # Expected duration was 3600s (1 hour), but recording only has 1080s (30%)
        env = self._make_envelope("rec-cutoff", (turn1, turn2), 1080.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=3600.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertIsNotNone(report.audio_certificate)
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)
        self.assertLess(report.audio_certificate.gate_f_evidence.completeness_pct, 80.0)
        self.assertTrue(any(f.code == "AUDIO_DURATION_INCOMPLETE" for f in report.findings))

    def test_gate_f_warn_on_marginal_duration(self):
        turn1 = self._make_turn("turn-01", 0.0, 40.0)
        turn2 = self._make_turn("turn-02", 42.0, 88.0)
        env = self._make_envelope("rec-marginal", (turn1, turn2), 88.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=100.0)
        self.assertEqual(report.verdict, "PASS_WITH_LOCKS")
        self.assertIsNotNone(report.audio_certificate)
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.WARN)
        self.assertTrue(any(f.code == "AUDIO_DURATION_MARGINAL" for f in report.findings))

    def test_gate_f_bluetooth_relaxed_threshold(self):
        """Bluetooth audio has a 90% PASS threshold instead of standard 95%."""
        turn1 = self._make_turn("turn-01", 0.0, 50.0)
        turn2 = self._make_turn("turn-02", 52.0, 91.0)
        env = self._make_envelope("rec-bt", (turn1, turn2), 91.0)

        # 91% on generic would warn, but on BLUETOOTH it passes
        report = self.gatekeeper.evaluate(env, expected_duration_s=100.0, source_type=AudioSourceType.BLUETOOTH)
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.PASS)
        self.assertEqual(report.audio_certificate.source_type, AudioSourceType.BLUETOOTH)

    def test_gate_f_blocks_excessive_silence_gap_pr_c9f1e3b_pin(self):
        """Regression pin for PR c9f1e3b: silence gap > 60s indicates dropped audio."""
        turn1 = self._make_turn("turn-01", 0.0, 10.0, "Before gap.")
        turn2 = self._make_turn("turn-02", 75.0, 90.0, "After 65-second gap.")
        env = self._make_envelope("rec-gap-crit", (turn1, turn2), 90.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=90.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any(f.code == "EXCESSIVE_SILENCE_GAP" for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)

    def test_gate_f_warns_on_moderate_silence_gap(self):
        turn1 = self._make_turn("turn-01", 0.0, 10.0)
        turn2 = self._make_turn("turn-02", 25.0, 50.0)  # 15s gap (>10s, <=60s)
        env = self._make_envelope("rec-gap-warn", (turn1, turn2), 50.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=50.0)
        self.assertEqual(report.verdict, "PASS_WITH_LOCKS")
        self.assertTrue(any(f.code == "UNANNOTATED_SILENCE_GAP" for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.WARN)

    def test_gate_f_blocks_missing_source_hash(self):
        turn = self._make_turn("turn-01", 0.0, 20.0)
        digest = ForensicSignalEnvelope.compute_signal_digest("rec-nohash", (), (turn,))
        # Construct envelope with zero-filled hash
        env = ForensicSignalEnvelope(
            recording_id="rec-nohash",
            source_hashes=("0" * 64,),
            canonical_sample_rate_hz=self.sample_rate,
            timeline_length_samples=20 * self.sample_rate,
            turns=(turn,),
            signal_digest=digest,
        )

        report = self.gatekeeper.evaluate(env, expected_duration_s=20.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any(f.code == "MISSING_SOURCE_HASH" for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)

    def test_gate_f_dropout_accumulation_warn_and_fail(self):
        # 65 dropouts * 0.5s = 32.5s (> 30s -> WARN)
        warn_turns = tuple(
            self._make_turn(f"turn-w-{i}", i * 2.0, (i * 2.0) + 1.5, f"Speech [AUDIO_GAP: ~50ms] turn {i}")
            for i in range(65)
        )
        env_warn = self._make_envelope("rec-drop-warn", warn_turns, 140.0)
        report_warn = self.gatekeeper.evaluate(env_warn, expected_duration_s=140.0)
        self.assertTrue(any(f.code == "ACCUMULATED_AUDIO_DROPOUTS" for f in report_warn.findings))
        self.assertEqual(report_warn.audio_certificate.verdict, CertificateVerdict.WARN)

        # 250 dropouts * 0.5s = 125s (> 120s -> FAIL)
        fail_turns = tuple(
            self._make_turn(f"turn-f-{i}", i * 1.5, (i * 1.5) + 1.0, f"Speech [AUDIO_GAP: ~50ms] turn {i}")
            for i in range(250)
        )
        env_fail = self._make_envelope("rec-drop-fail", fail_turns, 380.0)
        report_fail = self.gatekeeper.evaluate(env_fail, expected_duration_s=380.0)
        self.assertTrue(any(f.code == "EXCESSIVE_DROPOUT_ACCUMULATION" for f in report_fail.findings))
        self.assertEqual(report_fail.audio_certificate.verdict, CertificateVerdict.FAIL)


if __name__ == "__main__":
    unittest.main()
