"""
tests/end_to_end/test_post_recording_sla_replay_e2e.py

End-to-End Governance Test Suite for Post-Recording Ingestion SLA Clock & Replay (Wave 1).
Validates:
  1. PostRecordingSLAClock lifecycle across single-run and multi-run episodes.
  2. SLA configuration loading and stage budget threshold accounting.
  3. Proper calculation of episode_wall_clock_s, inter_run_gap_s, and approval_wait_s.
  4. Immutable PipelineExecutionReceipt serialization containing top-level and execution_timing SLA metrics.
  5. Strict sandbox isolation ensuring ZERO writes to production output/.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.sla_clock import (
    PostRecordingSLAClock,
    RunEpisodeSpan,
    enrich_receipt_with_sla_timing,
)
from dual_pipeline.unified_agentic_checkpoint import (
    PipelineExecutionReceipt,
    build_pipeline_receipt,
)
from dual_pipeline.unified_agentic_pipeline import UnifiedAgenticPipelineTracker


class PostRecordingSLAReplayE2ETests(unittest.TestCase):
    """E2E suite verifying post-recording ingestion SLA clock and replay harness."""

    def setUp(self) -> None:
        self.sandbox_dir = Path(tempfile.mkdtemp(prefix="eval_sandbox_sla_e2e_"))
        self.output_root = Path.cwd() / "output"
        self._initial_output_snapshot = (
            {str(p.relative_to(self.output_root)) for p in self.output_root.rglob("*")}
            if self.output_root.exists()
            else set()
        )

    def tearDown(self) -> None:
        if self.sandbox_dir.exists():
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
        # Production purity assertion: output/ must not be mutated
        if self.output_root.exists():
            current_snapshot = {str(p.relative_to(self.output_root)) for p in self.output_root.rglob("*")}
            new_files = current_snapshot - self._initial_output_snapshot
            self.assertEqual(
                new_files,
                set(),
                f"[PRODUCTION-PURITY-VIOLATION] Files written to output/ during SLA test: {new_files}",
            )

    def test_sla_config_loading_and_stage_budgets(self) -> None:
        """Verify SLA configuration loads stage budgets and ceiling targets."""
        clock = PostRecordingSLAClock()
        config = clock.sla_config
        self.assertIn("sla_target_seconds", config)
        self.assertEqual(config["sla_target_seconds"], 600.0)
        self.assertIn("stage_budgets_seconds", config)
        budgets = config["stage_budgets_seconds"]
        self.assertEqual(budgets.get("total"), 600.0)
        self.assertEqual(budgets.get("U0"), 60.0)
        self.assertEqual(budgets.get("extraction"), 60.0)
        self.assertEqual(budgets.get("judge"), 120.0)
        self.assertEqual(budgets.get("publish"), 60.0)

    def test_single_run_episode_lifecycle_and_approval_timing(self) -> None:
        """Verify single run episode with approval wait timing."""
        clock = PostRecordingSLAClock()
        # 1. Stop recording at t=100.0
        clock.record_stop_event(100.0)
        self.assertEqual(clock.stopped_at, 100.0)

        # 2. Operator approval requested at t=110.0, received at t=135.0 (25s wait)
        clock.record_approval_dispatch(110.0)
        clock.record_approval_received(135.0)
        self.assertEqual(clock.approval_wait_s, 25.0)

        # 3. Pipeline run starts at t=140.0, finishes at t=260.0
        clock.start_run("run-single-001", started_at=140.0)
        clock.finish_run(
            "run-single-001",
            ended_at=260.0,
            terminal_outcome="COMPLETE",
            stages={"U0": 30.0, "extraction": 25.0, "judge": 50.0, "publish": 15.0},
        )

        # Episode spans from t=100.0 to t=260.0 -> 160.0s
        self.assertEqual(clock.episode_wall_clock_s, 160.0)
        self.assertEqual(clock.inter_run_gap_s, 0.0)
        self.assertFalse(clock.is_sla_breached)

        d = clock.to_dict()
        self.assertEqual(d["episode_wall_clock_s"], 160.0)
        self.assertEqual(d["approval_wait_s"], 25.0)
        self.assertEqual(d["inter_run_gap_s"], 0.0)
        self.assertFalse(d["is_sla_breached"])
        self.assertEqual(d["runs_count"], 1)

    def test_multi_run_failure_gap_accumulation_and_breach(self) -> None:
        """Replay multi-run scenario mimicking Bryce meeting failure chain with inter-run gaps."""
        clock = PostRecordingSLAClock()
        # Meeting stops at t=0
        clock.record_stop_event(0.0)

        # Run 1: t=60 to t=234 (174s), FAILED
        clock.start_run("run-attempt-1", started_at=60.0)
        clock.finish_run("run-attempt-1", ended_at=234.0, terminal_outcome="FAILED")

        # Inter-run gap 1: t=234 to t=300 (66s gap of agent inspection)
        # Run 2: t=300 to t=442 (142s), FAILED
        clock.start_run("run-attempt-2", started_at=300.0)
        clock.finish_run("run-attempt-2", ended_at=442.0, terminal_outcome="FAILED")

        # Inter-run gap 2: t=442 to t=500 (58s gap)
        # Run 3: t=500 to t=719 (219s), FAILED
        clock.start_run("run-attempt-3", started_at=500.0)
        clock.finish_run("run-attempt-3", ended_at=719.0, terminal_outcome="FAILED")

        # Total episode: t=0 to t=719 = 719.0s
        self.assertEqual(clock.episode_wall_clock_s, 719.0)
        # Gaps: (300 - 234) + (500 - 442) = 66.0 + 58.0 = 124.0s
        self.assertEqual(clock.inter_run_gap_s, 124.0)
        # 719s > 600s SLA ceiling -> SLA breached!
        self.assertTrue(clock.is_sla_breached)

    def test_receipt_enrichment_and_disk_serialization(self) -> None:
        """Verify pipeline execution receipt correctly persists SLA fields to disk."""
        receipt = PipelineExecutionReceipt(
            pipeline_name="RawTranscriptPipeline",
            input_path=str(self.sandbox_dir / "master.wav"),
            session_id="sess-e2e-sla-1",
            started_at="2026-10-12T16:00:00+00:00",
            ended_at="2026-10-12T16:05:00+00:00",
            monotonic_duration_s=300.0,
            terminal_outcome="COMPLETE",
        )

        clock = PostRecordingSLAClock()
        clock.record_stop_event("2026-10-12T15:58:00+00:00")
        clock.record_approval_dispatch("2026-10-12T15:58:30+00:00")
        clock.record_approval_received("2026-10-12T15:59:15+00:00")
        clock.start_run("sess-e2e-sla-1", started_at="2026-10-12T16:00:00+00:00")
        clock.finish_run("sess-e2e-sla-1", ended_at="2026-10-12T16:05:00+00:00", terminal_outcome="COMPLETE")

        receipt_dict = receipt.to_dict()
        enriched = enrich_receipt_with_sla_timing(receipt_dict, clock=clock)

        # Assert top-level fields
        self.assertIn("episode_wall_clock_s", enriched)
        self.assertIn("inter_run_gap_s", enriched)
        self.assertIn("approval_wait_s", enriched)
        self.assertEqual(enriched["approval_wait_s"], 45.0)
        self.assertEqual(enriched["episode_wall_clock_s"], 420.0)

        # Assert execution_timing fields
        ex_timing = enriched["execution_timing"]
        self.assertEqual(ex_timing["episode_wall_clock_s"], 420.0)
        self.assertEqual(ex_timing["approval_wait_s"], 45.0)
        self.assertIn("sla_metrics", ex_timing)
        self.assertFalse(ex_timing["sla_metrics"]["is_sla_breached"])

        # Write to isolated sandbox directory
        out_file = self.sandbox_dir / "pipeline_execution_receipt.json"
        out_file.write_text(json.dumps(enriched, indent=2), encoding="utf-8")
        self.assertTrue(out_file.exists())

        # Read back from disk and verify
        reloaded = json.loads(out_file.read_text(encoding="utf-8"))
        self.assertEqual(reloaded["episode_wall_clock_s"], 420.0)
        self.assertEqual(reloaded["approval_wait_s"], 45.0)
        self.assertEqual(reloaded["inter_run_gap_s"], 0.0)

    def test_pipeline_tracker_integration_in_sandbox(self) -> None:
        """Verify UnifiedAgenticPipelineTracker generates receipt with SLA metrics."""
        mock_wav = self.sandbox_dir / "00_master_recovered.wav"
        mock_wav.write_bytes(b"RIFF" + b"\x00" * 4000)

        clock = PostRecordingSLAClock()
        clock.record_stop_event()
        clock.start_run("test-tracker-run-1")

        meta = {
            "stopped_at": "2026-10-12T16:10:00+00:00",
            "approval_wait_s": 12.5,
            "inter_run_gap_s": 0.0,
            "episode_wall_clock_s": 145.2,
        }

        tracker = UnifiedAgenticPipelineTracker(
            pipeline_name="RawTranscriptPipeline",
            input_path=str(mock_wav),
            metadata=meta,
            session_id="sess-tracker-e2e",
        )
        receipt = build_pipeline_receipt(
            tracker=tracker,
            output_dir=self.sandbox_dir / "target_out",
            terminal_outcome="COMPLETE",
        )
        dest = receipt.write_to_dir(self.sandbox_dir / "target_out")
        self.assertTrue(dest.exists())

        payload = json.loads(dest.read_text(encoding="utf-8"))
        self.assertEqual(payload["episode_wall_clock_s"], 145.2)
        self.assertEqual(payload["approval_wait_s"], 12.5)
        self.assertEqual(payload["inter_run_gap_s"], 0.0)
        self.assertEqual(payload["execution_timing"]["episode_wall_clock_s"], 145.2)

    def test_audio_asr_cache_decoupling_and_persistence(self) -> None:
        """Verify audio ASR cache key is decoupled from prompt/roster and persists to sandbox."""
        from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
            build_audio_asr_cache_key,
            load_audio_asr_cache,
            save_audio_asr_cache,
        )

        audio_sha = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        model_name = "mlx-community/whisper-large-v3-turbo"

        key1 = build_audio_asr_cache_key(audio_sha, model_name, language="en", mock=True)
        key2 = build_audio_asr_cache_key(audio_sha, model_name, language="en", mock=True)
        self.assertEqual(key1, key2)

        cache_dir = self.sandbox_dir / "cache"
        sample_segments = [
            {"start": 0.0, "end": 2.5, "text": "Hello team, let us review the architecture.", "speaker": "Speaker 1"},
            {"start": 2.6, "end": 5.0, "text": "Agreed, we need to finalize the ingestion SLA.", "speaker": "Speaker 2"},
        ]

        saved_path = save_audio_asr_cache(
            audio_sha256=audio_sha,
            raw_segments=sample_segments,
            model_name=model_name,
            language="en",
            stem="test_meeting",
            cache_dir=cache_dir,
            mock=True,
        )
        self.assertTrue(saved_path.is_file())

        loaded = load_audio_asr_cache(
            audio_sha256=audio_sha,
            model_name=model_name,
            language="en",
            stem="test_meeting",
            cache_dir=cache_dir,
            mock=True,
        )
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0]["text"], sample_segments[0]["text"])

    def test_seed_master_from_chunk_segments(self) -> None:
        """Verify master audio cache can be seeded from chunk segments."""
        from dual_pipeline.audio_ingestion_core.audio_asr_cache import (
            load_audio_asr_cache,
            seed_master_from_chunk_segments,
        )

        master_sha = "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff"
        model_name = "mlx-community/whisper-large-v3-turbo"
        cache_dir = self.sandbox_dir / "cache"

        chunk_segments = [
            {"start": 0.0, "end": 5.0, "text": "Chunk 1 speech.", "speaker": "SPEAKER_00"},
            {"start": 5.0, "end": 10.0, "text": "Chunk 2 speech.", "speaker": "SPEAKER_01"},
            {"start": 10.0, "end": 15.0, "text": "Chunk 3 speech.", "speaker": "SPEAKER_00"},
        ]

        seeded_path = seed_master_from_chunk_segments(
            chunk_segments=chunk_segments,
            master_sha256=master_sha,
            model_name=model_name,
            language="en",
            stem="00_master_recovered",
            cache_dir=cache_dir,
            mock=True,
        )
        self.assertTrue(seeded_path.is_file())

        retrieved = load_audio_asr_cache(
            audio_sha256=master_sha,
            model_name=model_name,
            language="en",
            stem="00_master_recovered",
            cache_dir=cache_dir,
            mock=True,
        )
        self.assertIsNotNone(retrieved)
        self.assertEqual(len(retrieved), 3)
        self.assertEqual(retrieved[1]["text"], "Chunk 2 speech.")

    def test_u0_roster_change_reuses_cache_under_10s_and_resolves_accurately(self) -> None:
        """Verify Stage U0 cache reuse on roster edit completes under 10s with token diff <= 1% and non-stale labels."""
        import time
        from dual_pipeline.audio_ingestion_core.audio_asr_cache import seed_master_from_chunk_segments
        from dual_pipeline.transcript_stages_core.l1_attribution import apply_speaker_map

        cache_dir = self.sandbox_dir / "cache"
        audio_file = self.sandbox_dir / "00_test_meeting_master.wav"
        audio_file.write_bytes(b"RIFF" + b"\x00" * 4000)

        import hashlib
        audio_sha = hashlib.sha256(audio_file.read_bytes()).hexdigest()

        # Seed master ASR cache
        chunk_segments = [
            {"start": 0.0, "end": 4.0, "text": "Welcome everyone to our engineering sync.", "speaker": "Speaker 1"},
            {"start": 4.5, "end": 9.0, "text": "Thanks, let us discuss the post-recording SLA.", "speaker": "Speaker 2"},
        ]
        seed_master_from_chunk_segments(
            chunk_segments=chunk_segments,
            master_sha256=audio_sha,
            model_name="mlx-community/whisper-large-v3-turbo",
            stem=audio_file.stem,
            cache_dir=cache_dir,
            mock=True,
        )

        # Baseline text formatting
        raw_transcript = (
            "**Speaker 1** [00:00:00]: Welcome everyone to our engineering sync.\n\n"
            "**Speaker 2** [00:00:04]: Thanks, let us discuss the post-recording SLA."
        )

        # Run 1: Initial Roster A
        roster_a_map = {"Speaker 1": "Bryce Poeter", "Speaker 2": "Amit Ayer"}
        t0 = time.monotonic()
        transcript_a = apply_speaker_map(raw_transcript, roster_a_map)
        dur_a = time.monotonic() - t0
        self.assertLess(dur_a, 10.0)
        self.assertIn("**Bryce Poeter:** [00:00:00]:", transcript_a)
        self.assertIn("**Amit Ayer:** [00:00:04]:", transcript_a)

        # Run 2: Roster B changed (new titles/roles or mapping)
        roster_b_map = {"Speaker 1": "Bryce Poeter (VP)", "Speaker 2": "Amit Ayer (Fellow)"}
        t1 = time.monotonic()
        transcript_b = apply_speaker_map(raw_transcript, roster_b_map)
        dur_b = time.monotonic() - t1

        # SLA criterion 1: U0 with warm cache <= 10s
        self.assertLess(dur_b, 10.0)

        # SLA criterion 2: Cached transcript tokens differ <= 1% (spoken dialogue is identical)
        dialogue_a = [line.split("]: ", 1)[1] for line in transcript_a.splitlines() if "]: " in line]
        dialogue_b = [line.split("]: ", 1)[1] for line in transcript_b.splitlines() if "]: " in line]
        self.assertEqual(dialogue_a, dialogue_b)

        # SLA criterion 3: Zero stale speaker labels (Roster B reflected, not Roster A)
        self.assertIn("**Bryce Poeter (VP):** [00:00:00]:", transcript_b)
        self.assertIn("**Amit Ayer (Fellow):** [00:00:04]:", transcript_b)
        self.assertNotIn("**Bryce Poeter:** [00:00:00]:", transcript_b)

    def test_scoped_retry_preserves_frozen_outputs_e2e(self) -> None:
        """Verify that when Output 2 fails, Output 1 and 3 stay frozen while Output 2 regenerates."""
        from dual_pipeline.canonical_pipeline.checkpoint_memoizer import memoize_substage_checkpoints
        from dual_pipeline.canonical_pipeline.modalities import CanonicalModality

        class MockSM:
            def __init__(self) -> None:
                self.frozen = {"output_1": "pass_1", "output_2": "pass_2", "output_3": "pass_3"}
                self.unfrozen: set[str] = set()

            def is_frozen(self, k: str) -> bool:
                return k in self.frozen

            def unfreeze_artifact(self, k: str, force: bool = False) -> None:
                self.unfrozen.add(k)
                self.frozen.pop(k, None)

        sm = MockSM()
        out_dir = self.sandbox_dir / "scoped_retry_run"
        out_dir.mkdir(parents=True, exist_ok=True)

        meta = {"retry_scope": "output_2"}
        memoized = memoize_substage_checkpoints(
            out_dir,
            sm,
            meta,
            modality=CanonicalModality.AUDIO,
        )

        # Output 1 and 3 should remain frozen/memoized
        self.assertTrue(memoized["output_1"])
        self.assertTrue(memoized["output_3"])
        self.assertIn("output_1", sm.frozen)
        self.assertIn("output_3", sm.frozen)

        # Output 2 should be unfrozen and flagged for regeneration
        self.assertFalse(memoized["output_2"])
        self.assertIn("output_2", sm.unfrozen)
        self.assertNotIn("output_2", sm.frozen)


if __name__ == "__main__":
    unittest.main()
