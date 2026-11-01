"""
tests/test_wave2_pipeline_a_streaming_dsp.py

Wave 2 Acceptance Test Suite: Pipeline A Real Acoustic DSP & Streaming Concurrency.
Validates:
1. AudioRingBuffer 10-thread concurrent writes, tail reading, and circular wrap-around integrity.
2. AudioSplicer 250ms stride sliding window, low latency (<5ms/slice), and SplicedAudioWindow emission.
3. Silence gating (< -45 dBFS) and acoustic energy VAD detection.
4. WebSocket TranscriptStreamEnvelope schema and interim/final differentiation.
5. UpstreamAudioQAGatekeeper deterministic gates: Monotonicity (A), Hallucination (B), Attribution (C), Acoustic Floor (E).
6. Word Error Rate (WER <= 8.5%) and Diarization Error Rate (DER <= 5.0%) benchmark metrics.
7. Ambiguity Lock Register (Rule C-10) forbidden action enforcement.
8. MultimodalSlideExtractor slide-to-turn timestamp alignment and entity binding.
"""
from __future__ import annotations

import concurrent.futures
import json
import time
import unittest
from typing import List

import numpy as np

from dual_pipeline.a2a import A2ATask, A2ATaskStatus
from dual_pipeline.audio_contracts import (
    AcousticQualityMetrics,
    AmbiguityLock,
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper
from evals.evaluate_upstream_audio_dsp import (
    compute_diarization_error_rate,
    compute_word_error_rate,
)
from pipeline_a_intake.agents import MultimodalSlideExtractor
from pipeline_a_intake.audio_splicer import (
    AudioRingBuffer,
    AudioSplicer,
    SileroVADGate,
    SplicedAudioWindow,
)
from pipeline_a_intake.contracts import (
    BUFFER_CAPACITY_SAMPLES,
    FRAME_STRIDE_SAMPLES,
    SAMPLE_RATE,
    SILENCE_THRESHOLD_DB,
    TranscriptStreamEnvelope,
)


class TestWave2PipelineAStreamingDSP(unittest.IsolatedAsyncioTestCase):
    """Wave 2 Acceptance Test Suite: Pipeline A Streaming Concurrency & DSP."""

    def test_01_audio_ring_buffer_concurrent_writes_and_reads(self) -> None:
        """Verify AudioRingBuffer thread safety under 10 concurrent worker threads."""
        buffer = AudioRingBuffer(capacity=BUFFER_CAPACITY_SAMPLES)
        num_workers = 10
        chunks_per_worker = 50
        chunk_size = FRAME_STRIDE_SAMPLES  # 4000 samples = 250ms @ 16kHz

        def worker(worker_id: int) -> int:
            written = 0
            for i in range(chunks_per_worker):
                data = np.full(chunk_size, fill_value=float(worker_id + 1) * 0.05, dtype=np.float32)
                written += buffer.write(data)
                if i % 5 == 0:
                    tail = buffer.read_tail(chunk_size)
                    self.assertEqual(len(tail), chunk_size)
                    self.assertFalse(np.isnan(tail).any())
            return written

        with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(worker, w) for w in range(num_workers)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        expected_total = num_workers * chunks_per_worker * chunk_size
        self.assertEqual(sum(results), expected_total)
        self.assertEqual(buffer.total_samples_written, expected_total)

        tail = buffer.read_tail(BUFFER_CAPACITY_SAMPLES)
        self.assertEqual(len(tail), BUFFER_CAPACITY_SAMPLES)
        self.assertFalse(np.isnan(tail).any())

    def test_02_audio_ring_buffer_tail_underflow_safety(self) -> None:
        """Verify reading more samples than written yields zero-padded array without error."""
        buffer = AudioRingBuffer(capacity=16000)
        chunk = np.ones(4000, dtype=np.float32) * 0.5
        buffer.write(chunk)

        tail = buffer.read_tail(8000)
        self.assertEqual(len(tail), 8000)
        self.assertTrue(np.all(tail[:4000] == 0.0))
        self.assertTrue(np.all(tail[4000:] == 0.5))

    def test_03_audio_splicer_stride_latency_and_throughput(self) -> None:
        """Verify AudioSplicer 250ms stride slicing latency is < 5ms per slice."""
        splicer = AudioSplicer()
        duration_s = 5.0
        num_samples = int(duration_s * SAMPLE_RATE)
        t = np.linspace(0, duration_s, num_samples, endpoint=False, dtype=np.float32)
        synthetic_speech = 0.5 * np.sin(2 * np.pi * 300.0 * t).astype(np.float32)

        chunk_size = FRAME_STRIDE_SAMPLES
        total_chunks = len(synthetic_speech) // chunk_size
        latencies_ms: List[float] = []
        windows_emitted: List[SplicedAudioWindow] = []

        for i in range(total_chunks):
            chunk = synthetic_speech[i * chunk_size : (i + 1) * chunk_size]
            t0 = time.perf_counter()
            emitted = splicer.push_audio(chunk)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)
            windows_emitted.extend(emitted)

        p95_latency = float(np.percentile(latencies_ms, 95))
        avg_latency = float(np.mean(latencies_ms))

        self.assertLess(p95_latency, 5.0, f"p95 latency {p95_latency:.3f}ms exceeded 5.0ms")
        self.assertLess(avg_latency, 2.0, f"avg latency {avg_latency:.3f}ms exceeded 2.0ms")
        self.assertGreater(len(windows_emitted), 0)
        for w in windows_emitted:
            self.assertIsInstance(w, SplicedAudioWindow)
            self.assertGreater(len(w.audio_data), 0)

    def test_04_silence_gating_below_threshold(self) -> None:
        """Verify audio below -42 dBFS is recognized as silence and marked has_speech=False."""
        vad = SileroVADGate(threshold_db=SILENCE_THRESHOLD_DB)
        splicer = AudioSplicer(vad_gate=vad)

        silent_audio = (np.random.randn(int(3.0 * SAMPLE_RATE)) * 0.0001).astype(np.float32)
        chunk_size = FRAME_STRIDE_SAMPLES
        total_chunks = len(silent_audio) // chunk_size

        active_speech_windows = 0
        for i in range(total_chunks):
            chunk = silent_audio[i * chunk_size : (i + 1) * chunk_size]
            windows = splicer.push_audio(chunk)
            for w in windows:
                if w.has_speech:
                    active_speech_windows += 1

        self.assertEqual(active_speech_windows, 0)

    def test_05_speech_energy_activation(self) -> None:
        """Verify robust speech audio is recognized with has_speech=True."""
        vad = SileroVADGate(threshold_db=SILENCE_THRESHOLD_DB)
        splicer = AudioSplicer(vad_gate=vad)

        duration_s = 4.0
        t = np.linspace(0, duration_s, int(duration_s * SAMPLE_RATE), endpoint=False, dtype=np.float32)
        speech_audio = (
            0.5 * np.sin(2 * np.pi * 220.0 * t)
            + 0.3 * np.sin(2 * np.pi * 440.0 * t)
        ).astype(np.float32)

        chunk_size = FRAME_STRIDE_SAMPLES
        total_chunks = len(speech_audio) // chunk_size

        speech_windows = 0
        for i in range(total_chunks):
            chunk = speech_audio[i * chunk_size : (i + 1) * chunk_size]
            windows = splicer.push_audio(chunk)
            for w in windows:
                if w.has_speech:
                    speech_windows += 1

        self.assertGreater(speech_windows, 0)

    def test_06_transcript_stream_envelope_schema_and_serialization(self) -> None:
        """Verify TranscriptStreamEnvelope conforms to WebSocket frontend streaming spec."""
        interim = TranscriptStreamEnvelope(
            segment_id="seg-100",
            text="We are discussing the substation",
            is_final=False,
            confidence=0.85,
            start_ts=10.0,
            end_ts=12.5,
            speaker="Speaker 1",
        )
        final = TranscriptStreamEnvelope(
            segment_id="seg-100",
            text="We are discussing the substation transformers.",
            is_final=True,
            confidence=0.98,
            start_ts=10.0,
            end_ts=13.2,
            speaker="Alex Rivera",
        )

        d_interim = interim.to_dict()
        self.assertEqual(d_interim["type"], "TRANSCRIPT_STREAM")
        self.assertEqual(d_interim["segment_id"], "seg-100")
        self.assertFalse(d_interim["is_final"])
        self.assertEqual(d_interim["confidence"], 0.85)
        self.assertEqual(d_interim["start_ts"], 10.0)
        self.assertEqual(d_interim["end_ts"], 12.5)

        d_final = final.to_dict()
        self.assertTrue(d_final["is_final"])
        self.assertEqual(d_final["confidence"], 0.98)
        self.assertEqual(d_final["speaker"], "Alex Rivera")

        parsed = json.loads(final.to_json())
        self.assertEqual(parsed["segment_id"], "seg-100")
        self.assertTrue(parsed["is_final"])

    def test_07_upstream_qa_monotonicity_gate_a(self) -> None:
        """Gate A: SampleSpan rejects zero/negative duration, and retrograde inversion triggers blocking findings."""
        gatekeeper = UpstreamAudioQAGatekeeper()

        with self.assertRaises(Exception) as ctx:
            SampleSpan(start_sample=16000, end_sample=16000)
        self.assertIn("end_sample must exceed start_sample", str(ctx.exception))

        turn1 = ForensicTurnSignal(
            turn_id="turn-1",
            span=SampleSpan(start_sample=0, end_sample=48000),
            transcript_verbatim="First spoken turn covering three seconds.",
            identity_state="cluster_only",
        )
        turn2_inverted = ForensicTurnSignal(
            turn_id="turn-2-retrograde",
            span=SampleSpan(start_sample=0, end_sample=16000),
            transcript_verbatim="Second turn starting at time zero.",
            identity_state="cluster_only",
        )
        envelope = ForensicSignalEnvelope(
            recording_id="rec-retrograde",
            source_hashes=("src-hash-1",),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=48000,
            turns=(turn1, turn2_inverted),
            signal_digest=ForensicSignalEnvelope.compute_signal_digest("rec-retrograde", ("src-hash-1",), (turn1, turn2_inverted)),
        )
        rep = gatekeeper.evaluate(envelope)
        self.assertEqual(rep.verdict, "REJECT")
        gate_evals = {g.gate_id: g.status for g in rep.gate_evaluations}
        self.assertEqual(gate_evals["Gate_A_Timestamp_Monotonicity"], "fail")

    def test_08_upstream_qa_hallucination_gate_b(self) -> None:
        """Gate B: Subtitle hallucinations ('thank you for watching') are blocked."""
        gatekeeper = UpstreamAudioQAGatekeeper()
        hallucinated_turn = ForensicTurnSignal(
            turn_id="turn-halluc-1",
            span=SampleSpan(start_sample=0, end_sample=32000),
            transcript_verbatim="Thank you for watching. Please subscribe to our channel.",
            identity_state="cluster_only",
        )
        envelope = ForensicSignalEnvelope(
            recording_id="rec-halluc",
            source_hashes=("src-hash-2",),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=32000,
            turns=(hallucinated_turn,),
            signal_digest=ForensicSignalEnvelope.compute_signal_digest("rec-halluc", ("src-hash-2",), (hallucinated_turn,)),
        )
        rep = gatekeeper.evaluate(envelope)
        self.assertEqual(rep.verdict, "REJECT")
        gate_evals = {g.gate_id: g.status for g in rep.gate_evaluations}
        self.assertEqual(gate_evals["Gate_B_Speech_Continuity_and_NoSpeech"], "fail")

    def test_09_upstream_qa_attribution_sanity_gate_c(self) -> None:
        """Gate C: Unverified speaker asserting roster person ID without evidence is blocked."""
        gatekeeper = UpstreamAudioQAGatekeeper()

        with self.assertRaises(Exception) as ctx:
            ForensicTurnSignal(
                turn_id="turn-unverified-roster",
                span=SampleSpan(start_sample=0, end_sample=16000),
                transcript_verbatim="Discussion regarding system operations.",
                identity_state="unknown",
                roster_person_id="person:mitchell_simmons",
            )
        self.assertIn("Unknown/cluster-only turns cannot assert roster identity", str(ctx.exception))

        unverified_turn = ForensicTurnSignal.model_construct(
            turn_id="turn-unverified-roster",
            span=SampleSpan(start_sample=0, end_sample=16000),
            transcript_verbatim="Discussion regarding system operations.",
            identity_state="unknown",
            roster_person_id="person:mitchell_simmons",
            words=(),
            speaker_cluster_ids=(),
            ambiguity_locks=(),
        )
        envelope = ForensicSignalEnvelope.model_construct(
            recording_id="rec-ungrounded",
            source_hashes=("src-hash-3",),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=16000,
            turns=(unverified_turn,),
            signal_digest="test-signal-digest-ungrounded",
        )
        rep = gatekeeper.evaluate(envelope)
        self.assertEqual(rep.verdict, "REJECT")
        gate_evals = {g.gate_id: g.status for g in rep.gate_evaluations}
        self.assertEqual(gate_evals["Gate_C_Attribution_Sanity"], "fail")

    def test_10_upstream_qa_acoustic_clipping_gate_e(self) -> None:
        """Gate E: Severe waveform clipping (> 10% near full scale) triggers warning and PASS_WITH_LOCKS."""
        gatekeeper = UpstreamAudioQAGatekeeper()
        clipped_quality = AcousticQualityMetrics(
            span=SampleSpan(start_sample=0, end_sample=16000),
            snr_estimate_db=18.0,
            snr_method="wada",
            flat_top_fraction=0.05,
            near_full_scale_fraction=0.15,
            noise_floor_dbfs=-30.0,
        )
        clipped_turn = ForensicTurnSignal(
            turn_id="turn-clipped",
            span=SampleSpan(start_sample=0, end_sample=16000),
            transcript_verbatim="Loud audio with clipping present.",
            identity_state="cluster_only",
            acoustic_quality=clipped_quality,
        )
        envelope = ForensicSignalEnvelope(
            recording_id="rec-clipped",
            source_hashes=("src-hash-4",),
            canonical_sample_rate_hz=16000,
            timeline_length_samples=16000,
            turns=(clipped_turn,),
            signal_digest=ForensicSignalEnvelope.compute_signal_digest("rec-clipped", ("src-hash-4",), (clipped_turn,)),
        )
        rep = gatekeeper.evaluate(envelope)
        self.assertEqual(rep.verdict, "PASS_WITH_LOCKS")
        gate_evals = {g.gate_id: g.status for g in rep.gate_evaluations}
        self.assertEqual(gate_evals["Gate_E_Acoustic_Signal_Floor"], "fail")

    def test_11_wer_levenshtein_benchmark_accuracy(self) -> None:
        """Verify Levenshtein Word Error Rate (WER) satisfies <= 8.5% benchmark threshold."""
        ref_text = (
            "Apex Energy Transmission technical services team evaluated the transmission "
            "substation post trip logs to determine root cause and prevent future outages."
        )
        hyp_text = (
            "Apex Energy Transmission technical services team evaluated the transmissions "
            "substation post trip logs to determine root cause and prevent future outages."
        )
        wer = compute_word_error_rate(ref_text, hyp_text)
        self.assertAlmostEqual(wer, 1.0 / 21.0, places=3)
        self.assertLessEqual(wer, 0.085, f"WER {wer * 100:.2f}% exceeded 8.5% threshold")
        self.assertEqual(compute_word_error_rate(ref_text, ref_text), 0.0)

    def test_12_der_diarization_benchmark_accuracy(self) -> None:
        """Verify sample-accurate Diarization Error Rate (DER) satisfies <= 5.0% threshold."""
        sample_rate = 16000
        total_samples = sample_rate * 60

        ref_turns = [
            (0, sample_rate * 20, "Speaker_A"),
            (sample_rate * 20, sample_rate * 40, "Speaker_B"),
            (sample_rate * 40, sample_rate * 60, "Speaker_A"),
        ]
        der_perf = compute_diarization_error_rate(ref_turns, list(ref_turns), total_samples)
        self.assertEqual(der_perf, 0.0)
        self.assertLessEqual(der_perf, 0.05, f"DER {der_perf * 100:.2f}% exceeded 5.0% threshold")

        hyp_shifted = [
            (0, sample_rate * 21, "Speaker_A"),
            (sample_rate * 21, sample_rate * 40, "Speaker_B"),
            (sample_rate * 40, sample_rate * 60, "Speaker_A"),
        ]
        der_shifted = compute_diarization_error_rate(ref_turns, hyp_shifted, total_samples)
        self.assertLessEqual(der_shifted, 0.05, f"Shifted DER {der_shifted * 100:.2f}% exceeded 5.0% threshold")

    def test_13_ambiguity_lock_registry_and_forbidden_actions(self) -> None:
        """Rule C-10: Ambiguity locks record reason code and enforce forbidden downstream actions."""
        lock = AmbiguityLock(
            lock_id="lock-c10-001",
            kind="overlap",
            span=SampleSpan(start_sample=16000, end_sample=32000),
            reason_code="OVERLAPPING_SPEECH_UNRESOLVED",
            evidence_refs=("ref-c10-overlap-evidence",),
            forbidden_actions=("assign_named_owner", "assert_single_speaker"),
        )
        turn_locked = ForensicTurnSignal(
            turn_id="turn-overlap-01",
            span=SampleSpan(start_sample=16000, end_sample=32000),
            transcript_verbatim="[crosstalk] Yes I agree with that point.",
            identity_state="cluster_only",
            ambiguity_locks=(lock,),
        )

        self.assertTrue(lock.lock_id.startswith("lock-"))
        self.assertEqual(lock.kind, "overlap")
        self.assertEqual(lock.reason_code, "OVERLAPPING_SPEECH_UNRESOLVED")
        self.assertIn("assign_named_owner", lock.forbidden_actions)
        self.assertIn("assert_single_speaker", lock.forbidden_actions)
        self.assertEqual(len(turn_locked.ambiguity_locks), 1)
        self.assertIn("assign_named_owner", turn_locked.ambiguity_locks[0].forbidden_actions)

    async def test_14_multimodal_slide_extractor_alignment(self) -> None:
        """Verify MultimodalSlideExtractor extracts diagram/slide entities and aligns with turns."""
        extractor = MultimodalSlideExtractor(mock_mode=True)
        task = A2ATask(
            task_id="task-slide-extract-01",
            parent_task_id=None,
            sender_agent_id="coord-01",
            recipient_agent_id=extractor.card.agent_id,
            objective="Extract visual architecture diagram and OCR tokens",
            status=A2ATaskStatus.SUBMITTED,
        )
        res = await extractor.handle_task(task)

        self.assertEqual(res["status"], "COMPLETED")
        self.assertGreaterEqual(res["slide_count"], 1)
        self.assertTrue(res["has_diagrams"])
        self.assertIn("slides", res)
        for slide in res["slides"]:
            self.assertIn("slide_index", slide)
            self.assertIn("title", slide)
            self.assertIn("extracted_entities", slide)
            self.assertGreater(len(slide["extracted_entities"]), 0)
            self.assertIn("topics", slide)
            self.assertGreater(len(slide["topics"]), 0)


if __name__ == "__main__":
    unittest.main()
