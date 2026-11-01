import tempfile
import unittest
from pathlib import Path

from dual_pipeline.audio_contracts import ForensicSignalEnvelope, ForensicTurnSignal, SampleSpan
from dual_pipeline.exceptions import ZeroSignalError
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper
from tools.prepare_evidence_envelope_intake import _validate_semantic_yield


class TestIngressFailFastGates(unittest.TestCase):
    def test_msyg_empty_source(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("hello " * 10) # 10 words, less than 50
            f.flush()
            path = Path(f.name)

        try:
            with self.assertRaises(ZeroSignalError):
                _validate_semantic_yield(path)
        finally:
            path.unlink()

    def test_msyg_sufficient_source(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("word " * 60) # 60 words, more than 50
            f.flush()
            path = Path(f.name)

        try:
            _validate_semantic_yield(path) # Should not raise
        finally:
            path.unlink()

class TestAudioGates(unittest.TestCase):
    def test_audio_coverage_threshold_fails_fast(self):
        gatekeeper = UpstreamAudioQAGatekeeper()
        envelope = ForensicSignalEnvelope.model_construct(
            turns=[
                ForensicTurnSignal.model_construct(
                    turn_id="1",
                    transcript_verbatim="hello",
                    span=SampleSpan(start_sample=0, end_sample=16000), # 1 second
                    identity_state="unknown",
                )
            ],
            canonical_sample_rate_hz=16000.0,
            timeline_length_samples=16000.0, # 1 second total audio, but expected is 10s
            source_hashes=()
        )
        # Bypassing fingerprint generation issue in test by directly calling _evaluate_gate_f with a mock property
        type(envelope).primary_source_fingerprint = property(lambda self: "0" * 64)
        report, cert = gatekeeper._evaluate_gate_f(envelope, expected_duration_s=10.0, min_coverage_pct=95.0, fail_coverage_pct=85.0)
        from dual_pipeline.audio_capture_certificate import CertificateVerdict
        self.assertEqual(cert.verdict, CertificateVerdict.FAIL)
        self.assertTrue(any(f.code == "AUDIO_DURATION_INCOMPLETE" for f in report))

    def test_asr_hallucination_filter_aborts(self):
        gatekeeper = UpstreamAudioQAGatekeeper()
        envelope = ForensicSignalEnvelope.model_construct(
            turns=[
                ForensicTurnSignal.model_construct(
                    turn_id="1",
                    transcript_verbatim="thank you for watching this video. please subscribe.",
                    span=SampleSpan(start_sample=0, end_sample=16000),
                    identity_state="unknown",
                ),
                ForensicTurnSignal.model_construct(
                    turn_id="2",
                    transcript_verbatim="subtitles by some group.",
                    span=SampleSpan(start_sample=16000, end_sample=32000),
                    identity_state="unknown",
                )
            ],
            canonical_sample_rate_hz=16000.0,
            timeline_length_samples=32000.0,
            primary_source_fingerprint="0" * 64,
            source_hashes={"primary": "0" * 64}
        )
        with self.assertRaises(ZeroSignalError):
            gatekeeper._evaluate_gate_b(envelope)

