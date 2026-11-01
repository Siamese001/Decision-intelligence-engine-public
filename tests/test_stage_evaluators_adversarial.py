"""Adversarial and boundary regression tests for Wave 4 stage evaluators."""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pytest

from evals.evaluate_upstream_audio_dsp import (
    UpstreamAudioDSPBenchmarkEvaluator,
    compute_word_error_rate,
    compute_diarization_error_rate,
)
from evals.evaluate_metal_advisory_extraction import MetalAdvisoryExtractionEvaluator
from evals.run_provider_fault_injection import (
    simulate_fault_429,
    simulate_fault_503,
)
from evals.evaluate_fast_path_streaming_load import FastPathStreamingLoadEvaluator
from pipeline_a_intake.audio_splicer import AudioRingBuffer, AudioSplicer


def test_adversarial_zero_byte_audio_and_empty_transcript_boundaries():
    """Category 1 (Boundary payload): Verify 0-byte audio chunks and empty transcripts handle boundaries cleanly."""
    # Empty reference & hypothesis -> 0.0 WER
    assert compute_word_error_rate("", "") == 0.0
    # Empty reference with non-empty hypothesis -> 1.0 WER
    assert compute_word_error_rate("", "hallucinated words") == 1.0
    # Non-empty reference with empty hypothesis -> 1.0 WER
    assert compute_word_error_rate("ground truth words", "") == 1.0

    # Zero duration samples in DER -> 0.0
    assert compute_diarization_error_rate([], [], 0) == 0.0

    # 0-length audio array into AudioSplicer and AudioRingBuffer
    splicer = AudioSplicer()
    empty_audio = np.array([], dtype=np.float32)
    windows = splicer.push_audio(empty_audio)
    assert windows == []

    ring = AudioRingBuffer(capacity=1600)
    written = ring.write(empty_audio)
    assert written == 0


def test_adversarial_perturbed_transcript_and_diarization_fail_gates():
    """Verify severely perturbed hypothesis transcripts and speaker swaps fail WER and DER gates."""
    evaluator = UpstreamAudioDSPBenchmarkEvaluator()
    ref_text = "Sarah Jenkins confirmed the architecture migration cutover schedule for October."
    bad_hyp_text = "Completely unrelated words with severe substitution errors across every token."

    wer_ok, wer_det = evaluator.evaluate_gate_2_transcription_accuracy_wer(ref_text, bad_hyp_text)
    assert wer_ok is False
    assert wer_det["wer"] > 0.50

    sample_rate = 16000
    ref_turns = [(0, sample_rate * 10, "Speaker 1"), (sample_rate * 10, sample_rate * 20, "Speaker 2")]
    # Complete speaker confusion swap
    swapped_turns = [(0, sample_rate * 10, "Speaker 2"), (sample_rate * 10, sample_rate * 20, "Speaker 1")]
    der_ok, der_det = evaluator.evaluate_gate_3_diarization_accuracy_der(ref_turns, swapped_turns, sample_rate * 20)
    assert der_ok is False
    assert der_det["der"] >= 0.90


def test_adversarial_retry_budget_exhaustion_and_ring_buffer_wrap_around():
    """Category 4 (Overflow & exhaustion): Verify 429 retry exhaustion and ring buffer wrap-around."""
    # Retry exhaustion: 3 retries observed before cascading to fallback
    res_429 = simulate_fault_429(max_retries=3)
    assert res_429.retries_observed == 3
    assert res_429.fallback_engaged is True
    assert res_429.passed is True

    # Circuit breaker trip on 503
    res_503 = simulate_fault_503()
    assert res_503.fallback_engaged is True
    assert res_503.details.get("circuit_state") == "OPEN"

    # Ring buffer capacity overflow / wrap-around
    capacity = 1000
    ring = AudioRingBuffer(capacity=capacity)
    oversized_chunk = np.arange(2500, dtype=np.float32)
    written = ring.write(oversized_chunk)
    assert written == 2500
    tail = ring.read_tail(capacity)
    assert len(tail) == capacity
    # Tail must equal the last `capacity` samples of oversized_chunk
    np.testing.assert_array_equal(tail, oversized_chunk[-capacity:])

    # Memory ceiling overflow check fails when ceiling is set impossibly low
    stream_eval = FastPathStreamingLoadEvaluator(num_concurrent_workers=2)
    mem_ok, mem_det = stream_eval.evaluate_gate_5_memory_stability_tracemalloc(ceiling_mb=0.00001)
    assert mem_ok is False


def test_adversarial_invariant_bypass_c19_and_naming_guards():
    """Category 5 (Invariant bypass attempt): Verify C-19 career leverage quarantine and model-neutral naming."""
    evaluator = MetalAdvisoryExtractionEvaluator(mock_mode=True)
    # Attempt to disguise $1.2M career leverage inside technical AWS architecture statement
    disguised_c19 = (
        "We deployed the BGE-M3 service on AWS ECS with 150ms latency, "
        "which gives us personal negotiation leverage for our executive compensation band."
    )
    tier = evaluator.classify_discourse_tier(disguised_c19, speaker="Architect")
    # Must strictly quarantine to INTERNAL_ADVISORY, never MIXED or CLIENT_SAFE
    assert tier == "INTERNAL_ADVISORY"

    # Verify Wave 4 target evaluators contain zero synthetic persona tokens
    for target_path in (
        Path("evals/evaluate_upstream_audio_dsp.py"),
        Path("evals/evaluate_full_mesh_a_to_d_live.py"),
        Path("evals/evaluate_fast_path_streaming_load.py"),
        Path("evals/evaluate_tier_token_economics.py"),
    ):
        content = target_path.read_text(encoding="utf-8")
        assert "Alex Rivera" not in content, f"Synthetic persona found in {target_path}"
        assert "Jordan Taylor" not in content, f"Synthetic persona found in {target_path}"
