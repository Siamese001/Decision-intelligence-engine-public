"""
Unit tests for Wave 1: Enhanced audio contracts, TypedSourceHash, and LiveSourceHashAccumulator.
Verifies backward-compatible legacy string coercion, primary source fingerprint extraction,
and strict cryptographic QA binding validation.
"""
from __future__ import annotations

import hashlib
import time
import unittest

from dual_pipeline.audio_contracts import (
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    LiveSourceHashAccumulator,
    SampleSpan,
    SourceHashSemantics,
    TypedSourceHash,
    UpstreamQAReport,
)


class AudioContractsV2Tests(unittest.TestCase):
    def test_typed_source_hash_hex_digest_and_canonical_string(self) -> None:
        raw_hex = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        th = TypedSourceHash(
            hash_value=f"sha256:{raw_hex}",
            semantics=SourceHashSemantics.CONTENT_SHA256,
        )
        self.assertEqual(th.hex_digest, raw_hex)
        self.assertEqual(len(th.hex_digest), 64)
        self.assertEqual(th.to_canonical_string(), f"content_sha256:{raw_hex}")

        # Label hashing ensures deterministic 64-hex even for arbitrary strings
        th_label = TypedSourceHash(
            hash_value="live_session_test_001",
            semantics=SourceHashSemantics.SESSION_ID_LABEL,
        )
        self.assertEqual(len(th_label.hex_digest), 64)
        expected_label_hex = hashlib.sha256(b"live_session_test_001").hexdigest()
        self.assertEqual(th_label.hex_digest, expected_label_hex)

    def test_forensic_signal_envelope_string_coercion(self) -> None:
        turn = ForensicTurnSignal(
            turn_id="turn-001",
            span=SampleSpan(start_sample=0, end_sample=16000),
            transcript_verbatim="Contract test audio.",
            identity_state="cluster_only",
            speaker_cluster_ids=("Speaker 1",),
        )

        valid_hex = "a" * 64
        digest = ForensicSignalEnvelope.compute_signal_digest(
            recording_id="rec-coerce",
            source_hashes=(f"sha256:{valid_hex}",),
            turns=(turn,),
        )

        # Pass string tuple to source_hashes; field_validator must coerce to TypedSourceHash
        env = ForensicSignalEnvelope(
            recording_id="rec-coerce",
            source_hashes=(f"sha256:{valid_hex}",),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=16000,
            turns=(turn,),
            signal_digest=digest,
        )

        self.assertEqual(len(env.source_hashes), 1)
        self.assertIsInstance(env.source_hashes[0], TypedSourceHash)
        self.assertEqual(env.source_hashes[0].semantics, SourceHashSemantics.CONTENT_SHA256)
        self.assertEqual(env.source_hashes[0].hex_digest, valid_hex)
        self.assertEqual(env.primary_source_fingerprint, valid_hex)

    def test_live_source_hash_accumulator_latency_and_determinism(self) -> None:
        accum1 = LiveSourceHashAccumulator()
        accum2 = LiveSourceHashAccumulator()

        chunk = b"\x01\x00" * 4000  # 8000 bytes = 250ms of 16kHz 16-bit mono

        t0 = time.perf_counter()
        for _ in range(100):
            accum1.feed_pcm(chunk)
        elapsed_sec = time.perf_counter() - t0

        # 100 frames must process in less than 50ms (achieves << 0.5ms per 250ms frame)
        self.assertLess(elapsed_sec, 0.05, f"Accumulator too slow: {elapsed_sec*1000:.2f}ms for 100 frames")

        for _ in range(100):
            accum2.feed_pcm(chunk)

        h1 = accum1.finalize()
        h2 = accum2.finalize()

        self.assertEqual(h1.semantics, SourceHashSemantics.SESSION_PCM_SHA256)
        self.assertEqual(h1.hex_digest, h2.hex_digest)
        self.assertEqual(h1.byte_count, 800000)

    def test_qa_binding_rejects_session_id_label_only(self) -> None:
        turn = ForensicTurnSignal(
            turn_id="turn-001",
            span=SampleSpan(start_sample=0, end_sample=16000),
            transcript_verbatim="Unanchored test audio.",
            identity_state="cluster_only",
            speaker_cluster_ids=("Speaker 1",),
        )

        label_hash = TypedSourceHash(
            hash_value="live_session_unanchored",
            semantics=SourceHashSemantics.SESSION_ID_LABEL,
        )
        digest = ForensicSignalEnvelope.compute_signal_digest(
            recording_id="rec-unanchored",
            source_hashes=(label_hash,),
            turns=(turn,),
        )

        qa_report = UpstreamQAReport(
            report_id="qa-001",
            signal_digest=digest,
            verdict="PASS",
        )

        # Envelopes bearing only SESSION_ID_LABEL cannot receive a PASS verdict
        with self.assertRaises(ValueError) as ctx:
            ForensicSignalEnvelope(
                recording_id="rec-unanchored",
                source_hashes=(label_hash,),
                canonical_sample_rate_hz=16000,
                timeline_length_samples=16000,
                turns=(turn,),
                signal_digest=digest,
                qa_report=qa_report,
            )
        self.assertIn("SESSION_ID_LABEL cannot receive PASS verdict", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

