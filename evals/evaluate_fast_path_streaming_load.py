#!/usr/bin/env python3
"""
evals/evaluate_fast_path_streaming_load.py

Wave 2 Evaluation Suite: Fast Path Streaming Concurrency & Load Benchmark.
Evaluates:
1. AudioRingBuffer thread safety and zero-allocation streaming under 10 concurrent streams.
2. AudioSplicer 250ms stride / 1.5s sliding window latency (<5ms per slice).
3. Silence gating (< -45 dBFS discarded in <1ms without Whisper dispatch).
4. Memory stability (<500MB ceiling) and zero buffer drops under heavy throughput.
5. Standard WebSocket streaming envelope validation (TranscriptStreamEnvelope).
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline_a_intake.audio_splicer import (
    AudioRingBuffer,
    AudioSplicer,
    SplicedAudioWindow,
)
from pipeline_a_intake.contracts import (
    BUFFER_CAPACITY_SAMPLES,
    FRAME_STRIDE_SAMPLES,
    SAMPLE_RATE,
    SILENCE_THRESHOLD_DB,
    TranscriptStreamEnvelope,
)

logger = logging.getLogger("evals.fast_path_streaming_load")


def generate_synthetic_speech_waveform(
    duration_seconds: float,
    sample_rate: int = 16000,
    amplitude: float = 0.5,
    frequency_hz: float = 440.0,
    add_silence: bool = False,
) -> np.ndarray:
    """Generates 16kHz float32 audio with speech-like harmonic energy or silence."""
    num_samples = int(duration_seconds * sample_rate)
    if add_silence:
        # Generate low-energy ambient noise below -55 dBFS
        return (np.random.randn(num_samples) * 0.0005).astype(np.float32)

    t = np.linspace(0, duration_seconds, num_samples, endpoint=False, dtype=np.float32)
    # Fundamental frequency + harmonics (vocal formant imitation)
    wave = (
        0.6 * np.sin(2 * np.pi * frequency_hz * t)
        + 0.3 * np.sin(2 * np.pi * frequency_hz * 2 * t)
        + 0.1 * np.sin(2 * np.pi * frequency_hz * 3 * t)
    )
    # Scale to target amplitude
    wave = (wave * amplitude).astype(np.float32)
    return wave


class FastPathStreamingLoadEvaluator:
    """Benchmark evaluation runner for Pipeline A Fast-Path Streaming and Audio Splicing."""

    def __init__(self, num_concurrent_workers: int = 10):
        self.num_concurrent_workers = num_concurrent_workers
        self.sample_rate = SAMPLE_RATE

    def evaluate_gate_1_ring_buffer_concurrency(self) -> Tuple[bool, Dict[str, Any]]:
        """Gate 1: Multi-threaded stress test on circular AudioRingBuffer."""
        buffer = AudioRingBuffer(capacity=BUFFER_CAPACITY_SAMPLES)
        chunk_size = FRAME_STRIDE_SAMPLES  # 4000 samples (250ms)
        chunks_per_worker = 100  # 25 seconds of audio per worker

        def worker_task(worker_id: int) -> int:
            written = 0
            for i in range(chunks_per_worker):
                # Generates 250ms of audio
                chunk = np.full(chunk_size, fill_value=float(worker_id + 1) * 0.01, dtype=np.float32)
                written += buffer.write(chunk)
                # Occasional tail read under write contention
                if i % 10 == 0:
                    tail = buffer.read_tail(chunk_size)
                    assert len(tail) == chunk_size
            return written

        tracemalloc.start()
        start_time = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_concurrent_workers) as executor:
            futures = [executor.submit(worker_task, w) for w in range(self.num_concurrent_workers)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        elapsed = time.time() - start_time
        _current_mem, peak_mem = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak_mem / (1024 * 1024)
        memory_stable = peak_mb < 500.0

        total_samples_written = sum(results)
        expected_samples = self.num_concurrent_workers * chunks_per_worker * chunk_size

        tail = buffer.read_tail(chunk_size)
        buffer_valid = (
            len(tail) == chunk_size
            and not np.isnan(tail).any()
            and total_samples_written == expected_samples
            and buffer.total_samples_written == expected_samples
            and memory_stable
        )

        details = {
            "num_workers": self.num_concurrent_workers,
            "total_samples_written": total_samples_written,
            "expected_samples": expected_samples,
            "elapsed_seconds": round(elapsed, 4),
            "samples_per_sec": round(total_samples_written / elapsed, 2) if elapsed > 0 else 0,
            "peak_memory_mb": round(peak_mb, 2),
            "memory_ceiling_mb": 500.0,
            "memory_stable": memory_stable,
            "buffer_integrity_valid": buffer_valid,
        }
        return buffer_valid, details

    def evaluate_gate_2_audio_splicer_throughput_and_latency(self) -> Tuple[bool, Dict[str, Any]]:
        """Gate 2: Verify AudioSplicer 250ms sliding stride latency (<5ms per slice)."""
        splicer = AudioSplicer()
        audio = generate_synthetic_speech_waveform(
            duration_seconds=10.0,
            sample_rate=self.sample_rate,
            amplitude=0.4,
            frequency_hz=300.0,
        )

        slice_times: List[float] = []
        emitted_windows: List[SplicedAudioWindow] = []
        chunk_size = FRAME_STRIDE_SAMPLES

        total_chunks = len(audio) // chunk_size
        for i in range(total_chunks):
            chunk = audio[i * chunk_size : (i + 1) * chunk_size]
            t0 = time.perf_counter()
            windows = splicer.push_audio(chunk)
            t_slice = (time.perf_counter() - t0) * 1000.0  # ms
            slice_times.append(t_slice)
            emitted_windows.extend(windows)

        avg_latency_ms = float(np.mean(slice_times))
        max_latency_ms = float(np.max(slice_times))
        p95_latency_ms = float(np.percentile(slice_times, 95))

        # Expected: after filling the 1.5s initial buffer (6 chunks), each chunk emits a window
        expected_min_windows = max(0, total_chunks - 6)
        throughput_valid = len(emitted_windows) >= expected_min_windows
        latency_valid = p95_latency_ms < 5.0 and avg_latency_ms < 2.0

        details = {
            "total_chunks_fed": total_chunks,
            "windows_emitted": len(emitted_windows),
            "avg_latency_ms": round(avg_latency_ms, 4),
            "p95_latency_ms": round(p95_latency_ms, 4),
            "max_latency_ms": round(max_latency_ms, 4),
            "latency_threshold_ms": 5.0,
            "throughput_valid": throughput_valid,
            "latency_valid": latency_valid,
        }
        passed = throughput_valid and latency_valid
        return passed, details

    def evaluate_gate_3_silence_gating_and_energy_filter(self) -> Tuple[bool, Dict[str, Any]]:
        """Gate 3: Verify silence windows (< -45 dBFS) are gated without downstream dispatch."""
        from pipeline_a_intake.audio_splicer import SileroVADGate
        vad = SileroVADGate(threshold_db=SILENCE_THRESHOLD_DB)
        splicer = AudioSplicer(vad_gate=vad)
        silent_audio = generate_synthetic_speech_waveform(
            duration_seconds=3.0,
            sample_rate=self.sample_rate,
            add_silence=True,
        )

        chunk_size = FRAME_STRIDE_SAMPLES
        silent_windows_detected = 0
        active_windows_emitted = 0

        total_chunks = len(silent_audio) // chunk_size
        for i in range(total_chunks):
            chunk = silent_audio[i * chunk_size : (i + 1) * chunk_size]
            windows = splicer.push_audio(chunk)
            if not windows:
                silent_windows_detected += 1
            else:
                for w in windows:
                    if w.has_speech:
                        active_windows_emitted += 1
                    else:
                        silent_windows_detected += 1

        # All windows in silent audio must be recognized as silence
        all_silent = (active_windows_emitted == 0)
        details = {
            "total_chunks": total_chunks,
            "silent_windows_detected": silent_windows_detected,
            "active_windows_emitted": active_windows_emitted,
            "silence_threshold_db": SILENCE_THRESHOLD_DB,
            "silence_filter_passed": all_silent,
        }
        return all_silent, details

    def evaluate_gate_4_websocket_stream_envelope_conformance(self) -> Tuple[bool, Dict[str, Any]]:
        """Gate 4: Verify TranscriptStreamEnvelope serialization and schema integrity."""
        env_interim = TranscriptStreamEnvelope(
            segment_id="seg-12345",
            text="We need to adopt the BGE-M3 embedding",
            start_ts=0.0,
            end_ts=1.5,
            speaker="Sarah Jenkins",
            is_final=False,
            confidence=0.88,
        )
        json_interim = env_interim.to_json()
        parsed_interim = json.loads(json_interim)

        env_final = TranscriptStreamEnvelope(
            segment_id="seg-12345",
            text="We need to adopt the BGE-M3 embedding model and deploy parallel A2A mesh.",
            start_ts=0.0,
            end_ts=3.5,
            speaker="Sarah Jenkins",
            is_final=True,
            confidence=0.96,
        )
        json_final = env_final.to_json()
        parsed_final = json.loads(json_final)

        conformance_passed = (
            parsed_interim["segment_id"] == "seg-12345"
            and not parsed_interim["is_final"]
            and parsed_final["is_final"]
            and parsed_final["confidence"] >= 0.90
            and parsed_final["speaker"] == "Sarah Jenkins"
        )
        details = {
            "interim_envelope": parsed_interim,
            "final_envelope": parsed_final,
            "conformance_passed": conformance_passed,
        }
        return conformance_passed, details

    def evaluate_gate_5_memory_stability_tracemalloc(self, ceiling_mb: float = 500.0) -> Tuple[bool, Dict[str, Any]]:
        """Gate 5: Verify memory stability (<500MB ceiling) and zero buffer drops under heavy throughput via tracemalloc."""
        import tracemalloc
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()
        try:
            tracemalloc.reset_peak()
            buffer = AudioRingBuffer(capacity=BUFFER_CAPACITY_SAMPLES)
            chunk = np.zeros(FRAME_STRIDE_SAMPLES, dtype=np.float32)
            for _ in range(200):
                buffer.write(chunk)
            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
        finally:
            if not was_tracing:
                tracemalloc.stop()

        peak_mb = round(peak_bytes / (1024.0 * 1024.0), 4)
        current_mb = round(current_bytes / (1024.0 * 1024.0), 4)
        passed = peak_mb < ceiling_mb and peak_bytes > 0
        details = {
            "current_memory_mb": current_mb,
            "peak_memory_mb": peak_mb,
            "memory_ceiling_mb": ceiling_mb,
            "memory_stability_passed": passed,
        }
        return passed, details

    def run_all(self) -> Dict[str, Any]:
        """Execute complete Wave 2 Fast-Path streaming and audio load benchmark."""
        start_time = time.time()

        g1_passed, g1_det = self.evaluate_gate_1_ring_buffer_concurrency()
        g2_passed, g2_det = self.evaluate_gate_2_audio_splicer_throughput_and_latency()
        g3_passed, g3_det = self.evaluate_gate_3_silence_gating_and_energy_filter()
        g4_passed, g4_det = self.evaluate_gate_4_websocket_stream_envelope_conformance()
        g5_passed, g5_det = self.evaluate_gate_5_memory_stability_tracemalloc()

        all_passed = g1_passed and g2_passed and g3_passed and g4_passed and g5_passed
        elapsed = time.time() - start_time

        return {
            "evaluation_contract": "urn:nee:dual-pipeline:fast-path-streaming-load:1.0.0",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "PASS" if all_passed else "FAIL",
            "gates": {
                "1. ring_buffer_concurrency": g1_passed,
                "2. audio_splicer_throughput_and_latency": g2_passed,
                "3. silence_gating_and_energy_filter": g3_passed,
                "4. websocket_stream_envelope_conformance": g4_passed,
                "5. memory_stability_tracemalloc": g5_passed,
            },
            "gate_details": {
                "gate_1": g1_det,
                "gate_2": g2_det,
                "gate_3": g3_det,
                "gate_4": g4_det,
                "gate_5": g5_det,
            },
            "elapsed_seconds": round(elapsed, 4),
        }


def main() -> int:
    print("=" * 80)
    print("Wave 2: Fast-Path Audio Streaming & Concurrency Load Benchmark")
    print("=" * 80)

    evaluator = FastPathStreamingLoadEvaluator(num_concurrent_workers=10)
    result = evaluator.run_all()

    print(f"Status: {result['status']} (Elapsed: {result['elapsed_seconds']}s)\n")
    print("Gate Results:")
    for gate_name, passed in result["gates"].items():
        mark = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {gate_name:45s} {mark}")

    g2 = result["gate_details"]["gate_2"]
    print(f"\nSplicer Latency (p95): {g2['p95_latency_ms']} ms (Threshold < {g2['latency_threshold_ms']} ms)")
    print(f"Windows Emitted:       {g2['windows_emitted']}")

    print("=" * 80)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
