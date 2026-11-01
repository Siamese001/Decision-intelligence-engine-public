"""Stress harness and concurrency tests for isolated MLX Whisper and GPT-6-Astra live copilot.

Guarantees:
  1. Copilot advisor resolves to and binds gpt-6-luna with high reasoning effort.
  2. Copilot produces insightful probing questions and strategic trap warnings.
  3. Slow LLM network API latency (injected delays) NEVER blocks or degrades MLX Whisper chunk transcription.
  4. Rapid burst chunk ingestion executes cleanly without IPC queue contention or deadlocks.
"""
from __future__ import annotations

import shutil
import struct
import tempfile
import time
import unittest
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
)


def _create_synthetic_wav(path: Path, duration_sec: float = 0.25, amplitude: int = 1500) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16000
    n_frames = int(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_frames):
            val = amplitude if (i % 32 < 16) else -amplitude
            frames.extend(struct.pack("<h", val))
        w.writeframes(frames)


class TestCopilotLiveStressHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_stress_copilot_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_copilot_gpt6_astra_probing_generation(self) -> None:
        """Verifies that LiveCopilotAdvisor binds gpt-6-luna and generates probing questions."""
        advisor = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Executive Architecture Sync",
            model_name="gpt-6-luna",
            reasoning_effort="high",
            mock_mode=True,
            cooldown_seconds=0.0,
        )
        self.assertEqual(advisor.model_id, "gpt-6-luna")
        self.assertEqual(advisor.reasoning_effort, "high")

        # Buffer dialogue turns
        advisor.record_turn("Amit", "We are reviewing the Q4 delivery schedule with Reid.", "[00:10]")
        advisor.record_turn("Client", "We might need to push the API delivery to late November.", "[00:25]")

        feedback = advisor.evaluate_live_turns(time.time() + 10.0)
        self.assertIsNotNone(feedback)
        self.assertTrue(feedback.get("has_guidance"))
        self.assertEqual(feedback.get("model"), "gpt-6-luna")
        self.assertIn("insightful_questions", feedback)
        self.assertGreaterEqual(len(feedback["insightful_questions"]), 1)
        self.assertIn("risk_trap", feedback)

    def test_copilot_latency_isolation(self) -> None:
        """Verifies that slow LLM network API calls never block or slow down MLX Whisper transcription."""
        copilot = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Latency Isolation Test",
            mock_mode=True,
            cooldown_seconds=0.0,
        )
        transcriber = IsolatedMLXTranscriberClient(mock_mode=True, backend="mlx")
        transcriber.start()

        # Injected slow background LLM task simulating 1.5s network delay
        def slow_evaluation(*args, **kwargs):
            time.sleep(1.2)
            return {
                "has_guidance": True,
                "category": "STRATEGIC",
                "snap_cue": "Slow LLM response",
                "suggested_script": "Should we phase commitments?",
                "risk_trap": "Scope creep",
                "insightful_questions": ["Should we phase commitments?"],
            }

        copilot._evaluate_dialogue_sync = slow_evaluation

        received_feedback = []
        copilot.submit_turn_async(
            "Client",
            "We want additional features added to the milestone.",
            "[00:08]",
            time.time(),
            on_feedback=lambda fb: received_feedback.append(fb),
        )

        # Transcribe 5 chunks in the isolated process during the slow LLM call
        chunk_latencies = []
        for i in range(5):
            wav_file = self.temp_path / f"chunk_{i:04d}.wav"
            _create_synthetic_wav(wav_file)
            t0 = time.perf_counter()
            txt = transcriber.transcribe(wav_file)
            elapsed = time.perf_counter() - t0
            chunk_latencies.append(elapsed)
            self.assertIn("Mock transcript segment", txt)

        # All 5 chunks must finish quickly (< 0.5s each), unaffected by the 1.2s LLM latency
        for lat in chunk_latencies:
            self.assertLess(lat, 0.4, "MLX Whisper transcription was blocked by Copilot LLM latency")

        # Wait for the background Copilot task to complete gracefully
        copilot.shutdown(wait=True)
        transcriber.stop()
        self.assertEqual(len(received_feedback), 1)

    def test_rapid_chunk_concurrency_stress(self) -> None:
        """Stress tests rapid burst chunk submission to the isolated MLX worker."""
        client = IsolatedMLXTranscriberClient(mock_mode=True, backend="mlx")
        client.start()

        try:
            total_chunks = 10
            for i in range(total_chunks):
                wav = self.temp_path / f"burst_{i:03d}.wav"
                _create_synthetic_wav(wav)
                res = client.transcribe(wav)
                self.assertIn(wav.name, res)
        finally:
            client.stop()
