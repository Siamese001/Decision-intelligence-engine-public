"""
tests/test_post_meeting_stop_and_path_handoff_e2e.py

End-to-end test for Wave 1: Post-meeting stop lifecycle, graceful termination,
WAV stitching, and canonical path/speaker hint handoff.
"""

from __future__ import annotations

import json
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import wave
from pathlib import Path

from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
    RecordingSessionHandle,
    RecordingStartRequest,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    migrate_staged_files,
    repoint_recorder_paths,
)


def _create_dummy_wav(path: Path, num_samples: int = 16000, value: int = 1000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{num_samples}h", *[value] * num_samples))


class TestPostMeetingStopAndPathHandoffE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_stop_handoff_e2e_")
        self.temp_path = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_e2e_stop_lifecycle_and_path_canonicalization(self) -> None:
        """
        Verify that stopping an active session via the session handle:
        1. Canonicalizes alias project name ('LENS' -> 'project_lens').
        2. Drains pending chunks and stitches master WAV.
        3. Exports co-located speaker map and speaker hints to output and cache dirs.
        4. Migrates staged worktree files and cleans root staging duplicates on is_final=True.
        """
        canonical_out = self.temp_path / "canonical_output"
        staging_dir = self.temp_path / "staging_worktree"
        canonical_out.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=True)

        with patch("dual_pipeline.live_meeting_recorder.ROOT", self.temp_path):
            recorder = LiveMeetingRecorder(
                project_name="LENS",
                meeting_name="Wave1 Business Review",
                output_dir=None,
                mock_mode=True,
                attendees="Daniel Sabet, Amit Ayer",
            )

            # Check project canonicalization
            self.assertEqual(recorder.project_name, "project_lens")
            self.assertIn("project_lens", str(recorder.output_dir))

        # Provision 2 dummy chunks
        chunk0 = recorder.chunks_dir / "chunk_0000.wav"
        chunk1 = recorder.chunks_dir / "chunk_0001.wav"
        _create_dummy_wav(chunk0, num_samples=16000, value=1200)
        _create_dummy_wav(chunk1, num_samples=16000, value=1400)

        with patch.object(
            recorder,
            "_transcribe_chunk",
            side_effect=[
                "Hello world from chunk zero",
                "Next era strategy update",
            ],
        ):
            recorder.process_chunk(chunk0, 0)
            recorder.process_chunk(chunk1, 1)
            recorder.processed_chunks.add(chunk0.name)
            recorder.processed_chunks.add(chunk1.name)

        # Enqueue a speaker hint
        hint_result = recorder.apply_speaker_hint(
            quote="Hello world",
            speaker_name="Daniel Sabet",
        )
        self.assertEqual(hint_result.status, "PASS")

        # Wrap in session handle and stop
        handle = RecordingSessionHandle(
            request=RecordingStartRequest(project_name="LENS", meeting_name="Wave1 Business Review"),
            session_id=recorder.session_id,
            repo_root=self.temp_path,
        )
        handle.recorder = recorder

        stop_resp = handle.stop()
        self.assertEqual(stop_resp.status, "STOPPED")
        self.assertEqual(stop_resp.final_chunk_count, 2)

        # 1. Master WAV must be stitched
        self.assertTrue(recorder.master_wav.exists())
        self.assertGreater(recorder.master_wav.stat().st_size, 0)
        with wave.open(str(recorder.master_wav), "rb") as mw:
            self.assertEqual(mw.getnframes(), 32000)

        # 2. Co-located speaker map and hints must exist in output_dir
        speaker_hints_json = recorder.output_dir / "speaker_hints.json"
        self.assertTrue(speaker_hints_json.exists())
        hints_data = json.loads(speaker_hints_json.read_text(encoding="utf-8"))
        self.assertEqual(len(hints_data), 1)
        self.assertEqual(hints_data[0]["speaker_name"], "Daniel Sabet")

        clean_slug = recorder.clean_slug.replace(" ", "_").lower()
        speaker_map_json = recorder.output_dir / f"{clean_slug}_speaker_map.json"
        self.assertTrue(speaker_map_json.exists())
        map_data = json.loads(speaker_map_json.read_text(encoding="utf-8"))
        self.assertEqual(map_data.get("Voice A"), "Daniel Sabet")

        # 3. Test worktree migration unlinking when is_final=True
        staged_chunks_dir = staging_dir / "project_lens" / recorder.date_dirname / "chunks"
        staged_chunks_dir.mkdir(parents=True, exist_ok=True)
        staged_chunk = staged_chunks_dir / "chunk_0000.wav"
        _create_dummy_wav(staged_chunk, num_samples=16000)

        staged_manifest = staging_dir / "project_lens" / recorder.date_dirname / "session_manifest.json"
        staged_manifest.write_text(json.dumps({"session_id": "test_sess"}), encoding="utf-8")

        target_chunks_dir = canonical_out / "project_lens" / recorder.date_dirname / "chunks"
        target_meta_dir = canonical_out / "project_lens" / recorder.date_dirname
        target_chunks_dir.mkdir(parents=True, exist_ok=True)
        target_meta_dir.mkdir(parents=True, exist_ok=True)

        metadata_pairs = [(staged_manifest, target_meta_dir)]

        migrate_staged_files(
            chunks_dir=staged_chunks_dir,
            target_chunks_dir=target_chunks_dir,
            metadata_pairs=metadata_pairs,
            is_final=True,
        )

        # Staged files should be unlinked on is_final=True
        self.assertFalse(staged_chunk.exists())
        self.assertFalse(staged_manifest.exists())
        # Target files must exist
        self.assertTrue((target_chunks_dir / "chunk_0000.wav").exists())
        self.assertTrue((target_meta_dir / "session_manifest.json").exists())

    def test_adopted_spooler_lazy_worktree_multichunk_handoff_and_asr_seed(self) -> None:
        """
        Verify BUG-01..BUG-06 end-to-end:
        - Adopted InstantAudioSpooler (segment_seconds=5) syncs segment_seconds to recorder.
        - Lazy materialize_worktree() preserves capture_manager.chunks_dir == spooler.spool_dir.
        - All 10 chunks (chunk_00000..chunk_00009) migrate and transcribe into segments.jsonl.
        - Primary ROOT/output staging files and .staging_* dirs are unlinked/cleaned.
        - Queued speaker hint reconciles BEFORE master ASR cache (.raw_asr.json) is seeded.
        - StitchReceipt.master_wav_sha256 matches actual master.wav disk bytes.
        """
        import hashlib
        from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler

        spool_dir = self.temp_path / "spool_session"
        spooler = InstantAudioSpooler(
            spool_dir=spool_dir,
            segment_seconds=5,
            mock_mode=True,
            session_id="spool_wave1_e2e",
        )
        spooler._is_running = True

        fake_wt = self.temp_path / ".worktrees" / "live_wave1_spool_review"

        def _fake_provision(wt_dir: Path, branch: str, root: Path) -> None:
            wt_dir.mkdir(parents=True, exist_ok=True)
            (wt_dir / ".git").write_text("gitdir: /tmp/fake\n", encoding="utf-8")

        with (
            patch("dual_pipeline.live_meeting_recorder.ROOT", self.temp_path),
            patch("dual_pipeline.live_meeting_recorder.is_inside_isolated_worktree", return_value=False),
            patch("dual_pipeline.live_meeting_recorder.provision_live_transcription_worktree", side_effect=_fake_provision),
        ):
            recorder = LiveMeetingRecorder(
                project_name="LENS",
                meeting_name="Wave1 Spool Review",
                output_dir=None,
                mock_mode=True,
                attendees="Speaker_Alpha, Amit",
                spooler=spooler,
            )
            try:
                # BUG-06: segment_seconds synced from spooler (5s, not default 15s)
                self.assertEqual(recorder.segment_seconds, 5)
                self.assertFalse(recorder._worktree_materialized)

                staging_out = recorder.output_dir
                staging_chunks = recorder.chunks_dir
                recorder._init_transcript_file()
                recorder._write_session_manifest()
                (recorder.cache_dir / "record_start_receipt.json").write_text("{}", encoding="utf-8")

                # Adopt spooler into capture_manager
                recorder.capture_manager.adopt_spooler(spooler)
                self.assertEqual(recorder.capture_manager.chunks_dir.resolve(), spool_dir.resolve())

                # Create 10 chunks in spool_dir across poll cycles
                for idx in range(10):
                    _create_dummy_wav(spool_dir / f"chunk_{idx:05d}.wav", num_samples=16000 * 5, value=1200 + idx * 20)

                last_sizes: dict[str, int] = {}
                with patch.object(
                    recorder,
                    "_transcribe_chunk",
                    side_effect=[f"Spoken turn number {i} in meeting" for i in range(10)],
                ):
                    # Poll twice sochunks stabilize and trigger lazy materialize_worktree on chunk_00000
                    recorder._poll_and_process_chunks(last_sizes)
                    recorder._poll_and_process_chunks(last_sizes)

                    # BUG-01: capture_manager.chunks_dir must still point to spool_dir after materialize_worktree!
                    self.assertTrue(recorder._worktree_materialized)
                    self.assertEqual(recorder.capture_manager.chunks_dir.resolve(), spool_dir.resolve())

                    # BUG-05: Primary ROOT/output staging files and .staging_* directory must be cleaned up
                    self.assertFalse((staging_out / recorder.transcript_md.name).exists())
                    self.assertFalse(staging_chunks.exists())

                    # Enqueue a pending speaker hint in speaker_hints_queue.json before stop()
                    recorder.speaker_hint_queue.enqueue_hint(
                        speaker="Speaker_Alpha",
                        quote="Spoken turn number 0",
                        raw_input='Speaker_Alpha - "Spoken turn number 0"',
                    )

                    # Stop session (drains remaining chunks, reconciles hints, stitches WAV, seeds ASR cache)
                    spooler._is_running = False
                    recorder.stop()

                self.assertEqual(len(recorder.processed_chunks), 10)
                self.assertEqual(len(list(recorder.chunks_dir.glob("chunk_*.wav"))), 10)
                seg_lines = [
                    json.loads(line)
                    for line in recorder.segments_jsonl.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                self.assertEqual(len(seg_lines), 10)
                self.assertIn(seg_lines[0]["speaker"], ("Speaker_Alpha", "Speaker_Alpha Muschett"))

                # BUG-02: StitchReceipt SHA-256 matches disk bytes
                self.assertIsNotNone(recorder.stitch_receipt)
                disk_sha = hashlib.sha256(recorder.master_wav.read_bytes()).hexdigest()
                self.assertEqual(recorder.stitch_receipt.master_wav_sha256, disk_sha)

                # BUG-03: Seeded .raw_asr.json contains all 10 segments AND resolved speaker "Speaker_Alpha"
                from dual_pipeline.audio_ingestion_core.audio_asr_cache import load_audio_asr_cache
                from dual_pipeline.model_registry import resolve_acoustic_asr

                raw_asr_files = list(recorder.cache_dir.glob("*.raw_asr.json"))
                self.assertEqual(len(raw_asr_files), 1)
                cached_segments = load_audio_asr_cache(
                    audio_sha256=disk_sha,
                    model_name=resolve_acoustic_asr().model_id,
                    language="en",
                    stem=recorder.master_wav.stem,
                    cache_dir=recorder.cache_dir,
                    mock=True,
                )
                self.assertIsNotNone(cached_segments)
                self.assertEqual(len(cached_segments), 10)
                self.assertIn(cached_segments[0]["speaker"], ("Speaker_Alpha", "Speaker_Alpha Muschett"))
            finally:
                recorder._release_slug_lock()





if __name__ == "__main__":
    unittest.main()

