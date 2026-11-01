"""Pipeline A Specialized Adversarial Evaluator.

Evaluates:
1. Acoustic Quality & Noise Floor Resilience: DSP metric computation across silence, clipping, low SNR.
2. Speaker Attribution Uncertainty Locking: 100% normalization of ungrounded caller tags to 'speaker_unknown'.
3. Enrolled Speaker Identity Preservation: High-confidence attribution for verified roster members.
4. Streaming Intake Latency SLA: Low-latency chunk processing (<500ms SLA).
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

from dual_pipeline.evals.pipelines.models import PipelineAEvalScorecard
from pipeline_a_intake.acoustic_evidence import (
    DEFAULT_SPEAKER_UNKNOWN,
    AcousticAttributionResult,
    AcousticSpeakerGate,
    AttributionState,
    compute_acoustic_metrics,
)


class PipelineAEvaluator:
    """Specialized adversarial evaluator for Pipeline A acoustic and intake contracts."""

    def __init__(
        self,
        enrolled_speakers: Optional[Sequence[str]] = None,
        max_chunk_latency_ms: float = 500.0,
        strict_enrollment_only: bool = False,
    ) -> None:
        self.enrolled_speakers = list(enrolled_speakers) if enrolled_speakers else ["Alice Smith", "Drew Oakley", "Elena Rostova"]
        self.strict_enrollment_only = strict_enrollment_only
        self.gate = AcousticSpeakerGate(
            enrolled_speakers=self.enrolled_speakers,
            strict_enrollment_only=strict_enrollment_only,
        )
        self.max_chunk_latency_ms = max_chunk_latency_ms


    def evaluate_waveform_resilience(
        self,
        waveforms: Optional[Dict[str, np.ndarray]] = None,
    ) -> Dict[str, Any]:
        """Test DSP quality metrics across 4 distinct acoustic challenge regimes.
        Conforms strictly to PIPELINES_AD_HIGH_SIGNAL_METRIC_SPEC_V1 Metric A.1 (S_noise).
        """
        if waveforms is None:
            rng = np.random.default_rng(42)
            t = np.linspace(0, 1, 16000, endpoint=False, dtype=np.float32)
            # 1. True silence: very low amplitude ambient baseline
            silence_wave = rng.normal(0, 1e-5, 16000).astype(np.float32)
            # 2. Overdriven clipped signal: high amplitude sine pushed past -1.0/1.0
            clipped_wave = np.clip(2.5 * np.sin(2 * np.pi * 440 * t), -1.0, 1.0).astype(np.float32)
            # 3. Clean nominal speech tone: 440Hz + harmonics at safe -6dBFS
            clean_wave = (0.5 * np.sin(2 * np.pi * 440 * t) + 0.25 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
            # 4. Low-SNR speech: tone mixed with high ambient noise floor (SNR ~ 3-6dB)
            speech_signal = 0.3 * np.sin(2 * np.pi * 300 * t)
            noise_floor = rng.normal(0, 0.2, 16000).astype(np.float32)
            low_snr_wave = (speech_signal + noise_floor).astype(np.float32)

            waveforms = {
                "silence": silence_wave,
                "clipping": clipped_wave,
                "clean_speech": clean_wave,
                "low_snr": low_snr_wave,
            }

        results: Dict[str, Any] = {}
        # Regime 1: Silence detection
        silence_m = compute_acoustic_metrics(waveforms["silence"])
        results["silence_detected"] = (not silence_m.is_speech) or (silence_m.speech_confidence < 0.30)
        results["silence_rms_below_floor"] = silence_m.rms_dbfs < -45.0

        # Regime 2: Hard clipping detection
        clip_m = compute_acoustic_metrics(waveforms["clipping"])
        results["clipping_detected"] = clip_m.clipping_ratio >= 0.05

        # Regime 3: Clean nominal signal validity
        clean_m = compute_acoustic_metrics(waveforms["clean_speech"])
        results["clean_signal_valid"] = clean_m.rms_dbfs > -35.0
        results["clean_speech_detected"] = clean_m.is_speech and clean_m.speech_confidence >= 0.50

        # Regime 4: Low SNR estimation robustness
        low_snr_m = compute_acoustic_metrics(waveforms["low_snr"])
        results["low_snr_computed"] = low_snr_m.snr_estimate_db is not None

        passed_count = sum(1 for v in results.values() if v)
        score = passed_count / len(results) if results else 0.0

        return {
            "score": score,
            "passed": score >= 0.75,
            "metrics": {
                "silence_rms": silence_m.rms_dbfs,
                "clip_ratio": clip_m.clipping_ratio,
                "clean_rms": clean_m.rms_dbfs,
                "clean_conf": clean_m.speech_confidence,
                "low_snr_est": low_snr_m.snr_estimate_db,
            },
            "regimes_tested": len(results),
            "subtests": results,
        }


    def evaluate_speaker_locking(
        self,
        ungrounded_tags: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Verify ungrounded and placeholder tags strictly lock to speaker_unknown."""
        if ungrounded_tags is None:
            ungrounded_tags = [
                "", "   ", "Speaker", "speaker", "Speaker A", "SPEAKER B",
                "Speaker 1", "Speaker 2", "unknown", "Attendee", "user", "Unassigned",
            ]

        locked_count = 0
        failures: List[str] = []

        for tag in ungrounded_tags:
            res: AcousticAttributionResult = self.gate.evaluate_speaker(tag)
            is_locked = (
                res.speaker_label == DEFAULT_SPEAKER_UNKNOWN
                and res.attribution_state == AttributionState.UNATTRIBUTED_UNKNOWN
                and not res.is_attributed
                and res.confidence == 0.0
                and res.uncertainty_locked
            )
            if is_locked:
                locked_count += 1
            else:
                failures.append(tag)

        accuracy = locked_count / len(ungrounded_tags) if ungrounded_tags else 0.0
        return {
            "accuracy": accuracy,
            "passed": accuracy == 1.0,
            "tested_count": len(ungrounded_tags),
            "locked_count": locked_count,
            "failures": failures,
        }

    def evaluate_enrolled_preservation(
        self,
        enrolled_speakers: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Verify enrolled roster members maintain verified attribution."""
        speakers = list(enrolled_speakers) if enrolled_speakers else self.enrolled_speakers

        preserved_count = 0
        failures: List[str] = []

        for spk in speakers:
            res: AcousticAttributionResult = self.gate.evaluate_speaker(spk)
            is_preserved = (
                res.speaker_label == spk
                and res.attribution_state == AttributionState.VERIFIED_ENROLLED
                and res.is_attributed
                and res.confidence >= 0.85
                and not res.uncertainty_locked
            )
            if is_preserved:
                preserved_count += 1
            else:
                failures.append(spk)

        rate = preserved_count / len(speakers) if speakers else 0.0
        return {
            "preservation_rate": rate,
            "passed": rate == 1.0,
            "tested_count": len(speakers),
            "preserved_count": preserved_count,
            "failures": failures,
        }

    def evaluate_chunk_streaming_latency(
        self,
        chunk_count: int = 50,
    ) -> Dict[str, Any]:
        """Measure chunk processing latency against SLA (<500ms)."""
        rng = np.random.default_rng(123)
        chunk_latencies_ms: List[float] = []

        for i in range(chunk_count):
            # 100ms audio chunk at 16kHz
            chunk = rng.normal(0, 0.1, 1600).astype(np.float32)
            tag = self.enrolled_speakers[i % len(self.enrolled_speakers)] if i % 2 == 0 else f"Speaker {i}"

            start_t = time.perf_counter()
            _ = compute_acoustic_metrics(chunk)
            _ = self.gate.evaluate_speaker(tag)
            elapsed_ms = (time.perf_counter() - start_t) * 1000.0

            chunk_latencies_ms.append(elapsed_ms)

        avg_ms = float(np.mean(chunk_latencies_ms))
        max_ms = float(np.max(chunk_latencies_ms))
        sla_passed = max_ms < self.max_chunk_latency_ms

        return {
            "sla_passed": sla_passed,
            "avg_latency_ms": avg_ms,
            "max_latency_ms": max_ms,
            "chunks_tested": chunk_count,
        }

    def run_evaluation(self) -> PipelineAEvalScorecard:
        """Run all Pipeline A evaluations and return consolidated scorecard."""
        waveform_res = self.evaluate_waveform_resilience()
        locking_res = self.evaluate_speaker_locking()
        enrolled_res = self.evaluate_enrolled_preservation()
        latency_res = self.evaluate_chunk_streaming_latency()

        all_passed = (
            waveform_res["passed"]
            and locking_res["passed"]
            and enrolled_res["passed"]
            and latency_res["sla_passed"]
        )

        summary = (
            f"Pipeline A Evaluation: {'PASS' if all_passed else 'FAIL'} | "
            f"Noise Resilience: {waveform_res['score']:.2f} | "
            f"Speaker Locking: {locking_res['accuracy'] * 100:.1f}% | "
            f"Enrolled Preservation: {enrolled_res['preservation_rate'] * 100:.1f}% | "
            f"Avg Chunk Latency: {latency_res['avg_latency_ms']:.2f}ms (Max: {latency_res['max_latency_ms']:.2f}ms)"
        )

        return PipelineAEvalScorecard(
            passed=all_passed,
            noise_floor_resilience_score=waveform_res["score"],
            speaker_locking_accuracy=locking_res["accuracy"],
            enrolled_speaker_preservation=enrolled_res["preservation_rate"],
            chunk_latency_sla_passed=latency_res["sla_passed"],
            avg_chunk_latency_ms=latency_res["avg_latency_ms"],
            max_chunk_latency_ms=latency_res["max_latency_ms"],
            chunks_evaluated=latency_res["chunks_tested"],
            summary=summary,
            details={
                "waveform_eval": waveform_res,
                "speaker_locking_eval": locking_res,
                "enrolled_preservation_eval": enrolled_res,
                "latency_eval": latency_res,
            },
        )


# Canonical domain benchmark alias
AcousticBenchmarkEvaluator = PipelineAEvaluator

__all__ = [
    "AcousticBenchmarkEvaluator",
    "PipelineAEvaluator",
]
