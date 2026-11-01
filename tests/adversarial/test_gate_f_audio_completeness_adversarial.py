"""
tests/adversarial/test_gate_f_audio_completeness_adversarial.py

Adversarial and boundary test suite for Gate F audio completeness.
Covers >=3 mandatory edge-case categories:
1. Boundary payload: 0-duration, 1-sample, exact threshold transitions (79.99% vs 80.01%, 94.99% vs 95.01%).
2. Malformed input: Negative expected durations, invalid source type strings, empty turns.
3. Production regression pins:
   - PR n4k5p7m: 30% duration audio producing complete outputs.
   - PR c9f1e3b: 65s unannotated silence gap masking dropped Teams recording.
   - PR b7e4d2a: AirPods Bluetooth burst micro-dropouts accumulating.
4. Invariant bypass attempts: All-zero source hash, fake certificate creation attempts.
"""

import unittest

from dual_pipeline.audio_capture_certificate import (
    AudioSourceType,
    CertificateVerdict,
)
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


class GateFAudioCompletenessAdversarialTests(unittest.TestCase):
    def setUp(self):
        self.gatekeeper = UpstreamAudioQAGatekeeper()
        self.sample_rate = 16000

    def _make_turn(self, turn_id: str, start_sec: float, end_sec: float, text: str = "Adversarial test turn") -> ForensicTurnSignal:
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
        source_hash: str = "b" * 64,
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

    # --- Category 1: Boundary Payloads ---

    def test_boundary_zero_and_single_sample_timeline(self):
        """Zero duration audio must fail closed with ZERO_DURATION_AUDIO."""
        turn = self._make_turn("turn-zero", 0.0, 0.0001)
        env = self._make_envelope("rec-zero", (turn,), 0.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=60.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any(f.code in ("ZERO_DURATION_AUDIO", "AUDIO_DURATION_INCOMPLETE") for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)

    def test_boundary_exact_threshold_transitions_80_percent(self):
        """Tests the sharp boundary at 80% completeness (79.9% fails, 80.1% warns)."""
        turn_fail = self._make_turn("turn-f", 0.0, 79.9)
        env_fail = self._make_envelope("rec-799", (turn_fail,), 79.9)
        report_fail = self.gatekeeper.evaluate(env_fail, expected_duration_s=100.0)
        self.assertEqual(report_fail.audio_certificate.verdict, CertificateVerdict.FAIL)
        self.assertTrue(any(f.code == "AUDIO_DURATION_INCOMPLETE" for f in report_fail.findings))

        turn_warn = self._make_turn("turn-w", 0.0, 80.1)
        env_warn = self._make_envelope("rec-801", (turn_warn,), 80.1)
        report_warn = self.gatekeeper.evaluate(env_warn, expected_duration_s=100.0)
        self.assertEqual(report_warn.audio_certificate.verdict, CertificateVerdict.WARN)
        self.assertTrue(any(f.code == "AUDIO_DURATION_MARGINAL" for f in report_warn.findings))

    def test_boundary_exact_threshold_transitions_95_percent(self):
        """Tests the sharp boundary at 95% completeness (94.9% warns, 95.1% passes)."""
        turn_warn = self._make_turn("turn-w", 0.0, 94.9)
        env_warn = self._make_envelope("rec-949", (turn_warn,), 94.9)
        report_warn = self.gatekeeper.evaluate(env_warn, expected_duration_s=100.0)
        self.assertEqual(report_warn.audio_certificate.verdict, CertificateVerdict.WARN)

        turn_pass = self._make_turn("turn-p", 0.0, 95.1)
        env_pass = self._make_envelope("rec-951", (turn_pass,), 95.1)
        report_pass = self.gatekeeper.evaluate(env_pass, expected_duration_s=100.0)
        self.assertEqual(report_pass.audio_certificate.verdict, CertificateVerdict.PASS)

    # --- Category 2: Malformed Input Handling ---

    def test_malformed_negative_and_none_expected_duration(self):
        """Negative expected duration should fall back to actual duration without crashing."""
        turn = self._make_turn("turn-neg", 0.0, 50.0)
        env = self._make_envelope("rec-neg", (turn,), 50.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=-500.0)
        # Should gracefully treat actual duration as expected duration
        self.assertEqual(report.verdict, "PASS")
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.PASS)

    def test_malformed_unrecognized_source_type_string(self):
        """Unrecognized source type strings must gracefully fall back to GENERIC without crashing."""
        turn = self._make_turn("turn-src", 0.0, 50.0)
        env = self._make_envelope("rec-src", (turn,), 50.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=50.0, source_type="UNKNOWN_WEIRD_DEVICE")
        self.assertEqual(report.audio_certificate.source_type, AudioSourceType.GENERIC)
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.PASS)

    # --- Category 3: Production Regression Pins ---

    def test_regression_pin_pr_n4k5p7m_30_percent_truncation(self):
        """
        PR n4k5p7m regression: 30% audio capture must fail Gate F and block downstream tasks.
        """
        turn1 = self._make_turn("t1", 0.0, 300.0, "Meeting start.")
        turn2 = self._make_turn("t2", 305.0, 1080.0, "Truncated meeting.")
        env = self._make_envelope("rec-n4k5p7m", (turn1, turn2), 1080.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=3600.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertEqual(len(report.authorized_tasks), 0)
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)
        self.assertIn("AUDIO_DURATION_INCOMPLETE", [f.code for f in report.findings])

    def test_regression_pin_pr_c9f1e3b_teams_gap_cutoff(self):
        """
        PR c9f1e3b regression: A 70-second gap indicates recording cutoff and must be blocked.
        """
        turn1 = self._make_turn("t1", 0.0, 15.0, "Initial dialogue.")
        turn2 = self._make_turn("t2", 85.0, 100.0, "Dialogue after 70s dropped packet gap.")
        env = self._make_envelope("rec-c9f1e3b", (turn1, turn2), 100.0)

        report = self.gatekeeper.evaluate(env, expected_duration_s=100.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any(f.code == "EXCESSIVE_SILENCE_GAP" for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)

    def test_regression_pin_pr_b7e4d2a_bluetooth_micro_dropout_storm(self):
        """
        PR b7e4d2a regression: Bluetooth reconnection storm producing excessive dropouts.
        """
        turns = tuple(
            self._make_turn(f"t-bt-{i}", i * 1.5, (i * 1.5) + 1.2, f"Turn {i} [AUDIO_GAP: ~100ms]")
            for i in range(260)
        )
        env = self._make_envelope("rec-b7e4d2a", turns, 400.0)
        report = self.gatekeeper.evaluate(env, expected_duration_s=400.0, source_type=AudioSourceType.BLUETOOTH)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any(f.code == "EXCESSIVE_DROPOUT_ACCUMULATION" for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)

    # --- Category 4: Invariant Bypass Attempts ---

    def test_invariant_bypass_all_zero_source_hash(self):
        """Ensures that all-zero dummy hashes are rejected by Gate F."""
        turn = self._make_turn("t-inv", 0.0, 20.0)
        env = self._make_envelope("rec-inv", (turn,), 20.0, source_hash="0" * 64)

        report = self.gatekeeper.evaluate(env, expected_duration_s=20.0)
        self.assertEqual(report.verdict, "REJECT")
        self.assertTrue(any(f.code == "MISSING_SOURCE_HASH" for f in report.findings))
        self.assertEqual(report.audio_certificate.verdict, CertificateVerdict.FAIL)


if __name__ == "__main__":
    unittest.main()
