"""
Acceptance test suite for Wave 3: Upstream Multimodal Audio DSP & Diarization Benchmark.
Evaluated under OpenAI gpt-5.6-luna (reasoning_effort="high").
"""

import pytest

from dual_pipeline.audio_contracts import (
    AcousticQualityMetrics,
    AmbiguityLock,
    ForensicSignalEnvelope,
    ForensicTurnSignal,
    SampleSpan,
)
from dual_pipeline.upstream_audio_qa import UpstreamAudioQAGatekeeper
from evals.evaluate_upstream_audio_dsp import (
    UpstreamAudioDSPBenchmarkEvaluator,
    compute_diarization_error_rate,
    compute_word_error_rate,
)


def test_levenshtein_wer_exact_and_degraded():
    """Verify WER computation on exact, perturbed, and thresholded texts."""
    ref = "Apex Energy Transmission technical services post trip analysis."
    hyp_identical = "Apex Energy Transmission technical services post trip analysis."
    assert compute_word_error_rate(ref, hyp_identical) == 0.0

    # 1 substitution out of 8 words -> 1/8 = 0.125
    hyp_sub = "Apex Energy Transmission technical operations post trip analysis."
    assert pytest.approx(compute_word_error_rate(ref, hyp_sub), rel=1e-3) == 0.125

    # Benchmark test against 8.5% threshold
    long_ref = (
        "Alex Rivera leads technology evaluations while Jordan Taylor oversees "
        "technical services to ensure we do not experience repeated system outages."
    )
    long_hyp = (
        "Alex Rivera leads technology evaluations while Jordan Taylor oversees "
        "technical services to ensure we do not experience repeated system outages."
    )
    wer = compute_word_error_rate(long_ref, long_hyp)
    assert wer <= 0.085, f"WER {wer} exceeded allowable threshold 0.085"


def test_diarization_error_rate_exact_and_confused():
    """Verify sample-accurate DER calculation for perfect and confused speaker turns."""
    sample_rate = 16000
    total_samples = sample_rate * 30

    ref_turns = [
        (0, sample_rate * 10, "Speaker 1"),
        (sample_rate * 10, sample_rate * 20, "Speaker 2"),
        (sample_rate * 20, sample_rate * 30, "Speaker 1"),
    ]
    hyp_turns_identical = list(ref_turns)

    der_perf = compute_diarization_error_rate(ref_turns, hyp_turns_identical, total_samples)
    assert der_perf == 0.0
    assert der_perf <= 0.05, f"DER {der_perf} exceeded allowable threshold 0.05"

    # Confused speaker for 5 seconds (5/30 = 0.1667)
    hyp_turns_confused = [
        (0, sample_rate * 10, "Speaker 1"),
        (sample_rate * 10, sample_rate * 15, "Speaker 1"),  # confusion: Spk1 instead of Spk2
        (sample_rate * 15, sample_rate * 20, "Speaker 2"),
        (sample_rate * 20, sample_rate * 30, "Speaker 1"),
    ]
    der_confused = compute_diarization_error_rate(ref_turns, hyp_turns_confused, total_samples)
    assert der_confused > 0.10


def test_acoustic_dsp_floors_pass_and_fail():
    """Verify UpstreamAudioQAGatekeeper enforces SNR, clipping, and noise floor gates."""
    sample_rate = 16000

    # Passing acoustic quality
    valid_acoustic = AcousticQualityMetrics(
        span=SampleSpan(start_sample=0, end_sample=sample_rate * 5),
        snr_estimate_db=24.0,
        snr_method="wada",
        flat_top_fraction=0.0005,
        noise_floor_dbfs=-48.0,
    )
    turn_valid = ForensicTurnSignal(
        turn_id="turn-valid-01",
        span=SampleSpan(start_sample=0, end_sample=sample_rate * 5),
        transcript_verbatim="Clear audio with healthy SNR and low clipping.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
        acoustic_quality=valid_acoustic,
    )
    digest_valid = ForensicSignalEnvelope.compute_signal_digest("rec-valid", ("src-1",), (turn_valid,))
    envelope_valid = ForensicSignalEnvelope(
        recording_id="rec-valid",
        source_hashes=("src-1",),
        canonical_sample_rate_hz=sample_rate,
        timeline_length_samples=sample_rate * 5,
        turns=(turn_valid,),
        signal_digest=digest_valid,
    )

    gatekeeper = UpstreamAudioQAGatekeeper()
    rep_valid = gatekeeper.evaluate(envelope_valid)
    assert rep_valid.verdict == "PASS"
    gate_evals_valid = {ge.gate_id: ge.status for ge in rep_valid.gate_evaluations}
    assert gate_evals_valid["Gate_E_Acoustic_Signal_Floor"] == "pass"

    # Failing acoustic quality (severe clipping near_full_scale_fraction > 0.10)
    degraded_acoustic = AcousticQualityMetrics(
        span=SampleSpan(start_sample=0, end_sample=sample_rate * 5),
        snr_estimate_db=6.0,
        snr_method="wada",
        flat_top_fraction=0.08,
        near_full_scale_fraction=0.15,
        noise_floor_dbfs=-20.0,
    )
    turn_degraded = ForensicTurnSignal(
        turn_id="turn-degraded-01",
        span=SampleSpan(start_sample=0, end_sample=sample_rate * 5),
        transcript_verbatim="Distorted and clipped recording below gate threshold.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1",),
        acoustic_quality=degraded_acoustic,
    )
    digest_deg = ForensicSignalEnvelope.compute_signal_digest("rec-deg", ("src-2",), (turn_degraded,))
    envelope_degraded = ForensicSignalEnvelope(
        recording_id="rec-deg",
        source_hashes=("src-2",),
        canonical_sample_rate_hz=sample_rate,
        timeline_length_samples=sample_rate * 5,
        turns=(turn_degraded,),
        signal_digest=digest_deg,
    )

    rep_degraded = gatekeeper.evaluate(envelope_degraded)
    gate_evals_deg = {ge.gate_id: ge.status for ge in rep_degraded.gate_evaluations}
    assert gate_evals_deg["Gate_E_Acoustic_Signal_Floor"] == "fail"


def test_ambiguity_lock_registry_attachment():
    """Verify Ambiguity Lock Register (Rule C-10) schema and attachment to turns."""
    sample_rate = 16000
    lock = AmbiguityLock(
        lock_id="ALR-001",
        kind="overlap",
        span=SampleSpan(start_sample=sample_rate * 10, end_sample=sample_rate * 12),
        reason_code="OVERLAPPING_SPEECH_CROSS_TALK",
        evidence_refs=("ref-001", "ref-002"),
        forbidden_actions=("assert_single_speaker", "promote_to_confirmed_commitment"),
        resolution_state="open",
    )
    assert lock.lock_id == "ALR-001"
    assert lock.kind == "overlap"
    assert "promote_to_confirmed_commitment" in lock.forbidden_actions

    turn = ForensicTurnSignal(
        turn_id="turn-lock-01",
        span=SampleSpan(start_sample=sample_rate * 10, end_sample=sample_rate * 12),
        transcript_verbatim="Cross-talk utterance requiring ambiguity quarantine.",
        identity_state="cluster_only",
        speaker_cluster_ids=("Speaker 1", "Speaker 2"),
        ambiguity_locks=(lock,),
    )
    assert len(turn.ambiguity_locks) == 1
    assert turn.ambiguity_locks[0].lock_id == "ALR-001"


def test_full_upstream_audio_dsp_benchmark_evaluator_luna_high():
    """Execute complete 5-gate benchmark suite bound to gpt-5.6-luna (high)."""
    evaluator = UpstreamAudioDSPBenchmarkEvaluator(
        target_model="gpt-5.6-luna",
        target_reasoning_effort="high",
    )
    result = evaluator.run_all()

    assert result["status"] == "PASS"
    assert result["target_model"] == "gpt-5.6-luna"
    assert result["reasoning_effort"] == "high"

    # Verify all 5 non-compensating gates passed
    gates = result["gates"]
    assert gates["1. acoustic_dsp_floors"] is True
    assert gates["2. transcription_accuracy_wer"] is True
    assert gates["3. diarization_accuracy_der"] is True
    assert gates["4. ambiguity_lock_registry"] is True
    assert gates["5. downstream_luna_high_qualification"] is True

    # Assert metric floors
    assert result["gate_details"]["gate_2"]["wer"] <= 0.085
    assert result["gate_details"]["gate_3"]["der"] <= 0.05
    assert result["gate_details"]["gate_4"]["lock_count"] >= 1
    assert result["gate_details"]["gate_5"]["judge_status"] in ("PASS", "PASS_ADVISORY")
