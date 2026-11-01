"""
tests/end_to_end/test_audio_hardening_matrix_e2e.py

End-to-end multi-source hardening acceptance test suite.
Verifies complete pass-through of multi-source audio across the 7-step lifecycle.
"""

import hashlib
import shutil
import struct
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.audio_capture_certificate import CertificateVerdict
from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper


@dataclass
class _NormalizedAudio:
    output_wav_path: str
    sha256: str
    duration_seconds: float


class AudioHardeningMatrixE2ETests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.gatekeeper = UpstreamAudioQAGatekeeper()

    def _normalize(self, path: Path, duration_sec: float) -> _NormalizedAudio:
        return _NormalizedAudio(
            output_wav_path=str(path),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            duration_seconds=duration_sec,
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_audio_file(self, filename: str, duration_sec: float = 2.0) -> Path:
        p = self.temp_dir / filename
        sample_rate = 16000
        total_samples = int(sample_rate * duration_sec)
        with p.open("wb") as f:
            f.write(b"RIFF")
            f.write(struct.pack("<I", 36 + total_samples * 2))
            f.write(b"WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data")
            f.write(struct.pack("<I", total_samples * 2))
            for i in range(total_samples):
                f.write(struct.pack("<h", int(2000 * ((i % 100) / 100.0))))
        return p

    def test_e2e_multi_source_lifecycle_pass_path(self):
        """End-to-end path from raw source to Output authorization for valid audio."""
        raw_audio = self._create_audio_file("teams_meeting.wav", duration_sec=3.0)
        norm = self._normalize(raw_audio, 3.0)

        turn = ForensicTurnSignal(
            turn_id="t-matrix-01",
            span=SampleSpan(start_sample=0, end_sample=int(norm.duration_seconds * 16000)),
            transcript_verbatim="Discussing cross-platform audio parity across Teams and Zoom.",
            identity_state="cluster_only",
            speaker_cluster_ids=("Speaker 1",),
        )
        digest = ForensicSignalEnvelope.compute_signal_digest("rec-matrix-pass", (norm.sha256,), (turn,))
        env = ForensicSignalEnvelope(
            recording_id="rec-matrix-pass",
            source_hashes=(norm.sha256,),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=int(norm.duration_seconds * 16000),
            turns=(turn,),
            signal_digest=digest,
        )

        qa_report = self.gatekeeper.evaluate(env, expected_duration_s=norm.duration_seconds)
        self.assertEqual(qa_report.verdict, "PASS")

        # Now pass to synthesis circuit breaker
        metadata = {
            "is_audio": True,
            "audio_path": norm.output_wav_path,
            "audio_certificate": qa_report.audio_certificate,
            "qa_report": qa_report,
        }
        cert = AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)
        self.assertIsNotNone(cert)
        self.assertEqual(cert.verdict, CertificateVerdict.PASS)

    def test_e2e_multi_source_lifecycle_fail_fast_path(self):
        """End-to-end path verifying circuit breaker blocks when duration is 30%."""
        raw_audio = self._create_audio_file("partial_meeting.wav", duration_sec=1.0)
        norm = self._normalize(raw_audio, 1.0)

        turn = ForensicTurnSignal(
            turn_id="t-matrix-02",
            span=SampleSpan(start_sample=0, end_sample=16000),
            transcript_verbatim="Snippet before crash.",
            identity_state="cluster_only",
            speaker_cluster_ids=("Speaker 1",),
        )
        digest = ForensicSignalEnvelope.compute_signal_digest("rec-matrix-fail", (norm.sha256,), (turn,))
        env = ForensicSignalEnvelope(
            recording_id="rec-matrix-fail",
            source_hashes=(norm.sha256,),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=16000,
            turns=(turn,),
            signal_digest=digest,
        )

        # Expected duration was 10.0s, actual was 1.0s (10%)
        qa_report = self.gatekeeper.evaluate(env, expected_duration_s=10.0)
        self.assertEqual(qa_report.verdict, "REJECT")

        metadata = {
            "is_audio": True,
            "audio_path": norm.output_wav_path,
            "audio_certificate": qa_report.audio_certificate,
            "qa_report": qa_report,
        }
        with self.assertRaises(AudioIncompleteFailFastError):
            AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata)


if __name__ == "__main__":
    unittest.main()
