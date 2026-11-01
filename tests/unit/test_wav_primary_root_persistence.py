"""Unit tests for Wave 1: Primary Root Dual-Persistence of Master WAV & ASR Cache."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
import wave

from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
    finalize_diarization_and_seal,
    mirror_master_wav_to_primary_root,
    reconcile_master_wav_and_receipt,
)
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import (
    cleanup_staging_directories,
    finalize_worktree_materialization,
    repoint_recorder_paths,
)


def _write_test_pcm_wav(path: Path, freq_hz: float = 440.0, num_samples: int = 8000) -> str:
    """Writes a deterministic 16kHz 16-bit mono PCM WAV and returns its SHA-256 hex digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        samples = [
            int(2000 * math.sin(2.0 * math.pi * freq_hz * i / 16000.0))
            for i in range(num_samples)
        ]
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestWavPrimaryRootPersistence(unittest.TestCase):
    """Verifies master WAV and durable ASR cache persistence in primary repository root."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo_root"
        self.wt = self.root / ".worktrees" / "live_dociq_jack_amit"
        self.rel_cache = Path("output") / "transcript_cache" / "project_nexus" / "10-01-2026"
        self.rel_out = Path("output") / "project_nexus" / "10-01-2026" / "Dociq_Jack_Amit"

        self.root_cache = self.root / self.rel_cache
        self.root_chunks = self.root_cache / ".chunks_Dociq_Jack_Amit"
        self.root_out = self.root / self.rel_out

        self.wt_cache = self.wt / self.rel_cache
        self.wt_chunks = self.wt_cache / ".chunks_Dociq_Jack_Amit"
        self.wt_out = self.wt / self.rel_out

        for d in (
            self.root_cache,
            self.root_chunks,
            self.root_out,
            self.wt_cache,
            self.wt_chunks,
            self.wt_out,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cleanup_staging_directories_preserves_master_wav_and_durable_cache(self) -> None:
        """cleanup_staging_directories must preserve master WAV and ASR cache in primary root while unlinking chunks."""
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        expected_sha = _write_test_pcm_wav(self.root_cache / wav_name)
        _write_test_pcm_wav(self.wt_cache / wav_name)

        # Durable sidecars present in both staging (root) and target (worktree)
        durable_names = [
            "00_10-01-2026_Dociq_Jack_Amit_master_110726_abc123.raw_asr.json",
            "00_10-01-2026_Dociq_Jack_Amit_master_110726_abc123.segments.json",
            "00_10-01-2026_Dociq_Jack_Amit_master_110726_abc123.utterances.json",
            "00_10-01-2026_Dociq_Jack_Amit_master_110726_abc123.transcript_cache.json",
            "00_10-01-2026_Dociq_Jack_Amit_master_110726_abc123.cache_seal.json",
            "00_10-01-2026_Dociq_Jack_Amit_110726_manifest.json",
            "00_10-01-2026_Dociq_Jack_Amit_110726_acoustic_evidence_receipt.json",
        ]
        for name in durable_names:
            (self.root_cache / name).write_text('{"ok": true}', encoding="utf-8")
            (self.wt_cache / name).write_text('{"ok": true}', encoding="utf-8")

        # Rolling chunks and ephemeral output files that SHOULD be unlinked from staging
        _write_test_pcm_wav(self.root_chunks / "chunk_00000.wav")
        _write_test_pcm_wav(self.wt_chunks / "chunk_00000.wav")
        (self.root_out / "01_live_transcript.md").write_text("# Live", encoding="utf-8")
        (self.wt_out / "01_live_transcript.md").write_text("# Live", encoding="utf-8")

        cleanup_staging_directories(
            staging_out=self.root_out,
            staging_cache=self.root_cache,
            staging_chunks=self.root_chunks,
            target_out=self.wt_out,
            target_cache=self.wt_cache,
            target_chunks=self.wt_chunks,
            root_dir=self.root,
        )

        # Master WAV and durable ASR sidecars must survive in primary root
        root_master = self.root_cache / wav_name
        self.assertTrue(root_master.is_file(), "Primary root master WAV was unlinked!")
        self.assertEqual(
            hashlib.sha256(root_master.read_bytes()).hexdigest(),
            expected_sha,
        )
        for name in durable_names:
            self.assertTrue(
                (self.root_cache / name).is_file(),
                f"Durable cache artifact {name} was unlinked from primary root!",
            )

        # Ephemeral chunks and staged output md in primary root must be unlinked
        self.assertFalse((self.root_chunks / "chunk_00000.wav").exists())
        self.assertFalse((self.root_out / "01_live_transcript.md").exists())

    def test_reconcile_master_wav_mirrors_stitched_wav_to_primary_root(self) -> None:
        """Stitched master WAV inside worktree is atomically mirrored to primary root with identical SHA-256."""
        _write_test_pcm_wav(self.wt_chunks / "chunk_00000.wav", freq_hz=440.0)
        _write_test_pcm_wav(self.wt_chunks / "chunk_00001.wav", freq_hz=550.0)

        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        wt_master = self.wt_cache / wav_name
        stitch_receipt = AudioMasterStitcher.stitch_master_wav(
            self.wt_chunks, wt_master, pad_missing_or_corrupt=True
        )
        self.assertTrue(wt_master.is_file())

        # Remove primary root cache directory first to prove reconcile creates and mirrors it
        if (self.root_cache / wav_name).exists():
            (self.root_cache / wav_name).unlink()

        recorder = SimpleNamespace(
            master_wav=wt_master,
            spooler=None,
            processed_chunks=[0, 1],
            repo_root=self.root,
            worktree_dir=self.wt,
            _staging_cache_dir=self.root_cache,
        )

        reconciled = reconcile_master_wav_and_receipt(recorder, stitch_receipt)
        self.assertIsNotNone(reconciled)

        root_master = self.root_cache / wav_name
        self.assertTrue(root_master.is_file(), "Primary root master WAV was not created by reconcile!")
        root_sha = hashlib.sha256(root_master.read_bytes()).hexdigest()
        wt_sha = hashlib.sha256(wt_master.read_bytes()).hexdigest()
        self.assertEqual(root_sha, wt_sha)
        self.assertEqual(root_sha, reconciled.master_wav_sha256)

    def test_mirror_resolves_via_worktree_path_without_staging_attr(self) -> None:
        """mirror_master_wav_to_primary_root resolves primary root directly from .worktrees/<slug> path."""
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        wt_master = self.wt_cache / wav_name
        expected_sha = _write_test_pcm_wav(wt_master, freq_hz=660.0)
        sidecar = self.wt_cache / "00_10-01-2026_Dociq_Jack_Amit_110726_acoustic_evidence_receipt.json"
        sidecar.write_text('{"verified": true}', encoding="utf-8")

        recorder = SimpleNamespace(master_wav=wt_master)
        mirrored_path = mirror_master_wav_to_primary_root(
            recorder, wt_master, expected_sha256=expected_sha
        )
        self.assertIsNotNone(mirrored_path)
        self.assertEqual(mirrored_path.resolve(), (self.root_cache / wav_name).resolve())
        self.assertEqual(
            hashlib.sha256(mirrored_path.read_bytes()).hexdigest(),
            expected_sha,
        )
        self.assertTrue((self.root_cache / sidecar.name).is_file())

    def test_finalize_worktree_materialization_and_seal_end_to_end_mirror(self) -> None:
        """Full materialization + stitch + seal lifecycle preserves master WAV in primary root."""
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        recorder = SimpleNamespace(
            date_dirname="10-01-2026",
            clean_slug="Dociq_Jack_Amit",
            session_tag="110726",
            output_dir=self.root_out,
            cache_dir=self.root_cache,
            chunks_dir=self.root_chunks,
            _target_output_dir=self.wt_out,
            _target_cache_dir=self.wt_cache,
            _target_chunks_dir=self.wt_chunks,
            worktree_dir=self.wt,
            repo_root=self.root,
            capture_manager=None,
            spooler=None,
            chat_attributions=None,
            speaker_hint_manager=None,
            speaker_hint_queue=None,
            collateral_watcher=None,
            _acoustic_diarization_complete=True,
            _acoustic_diarization_error=None,
        )
        recorder._migrate_staged_chunks = lambda is_final=False: None
        recorder._migrate_staged_metadata = lambda is_final=False: None
        recorder._repoint_recorder_paths_to_worktree = lambda: repoint_recorder_paths(
            recorder, self.wt
        )
        recorder._run_post_recording_diarization = lambda: None
        recorder._cleanup_empty_session_or_seal = lambda: None
        recorder._release_slug_lock = lambda: None

        finalize_worktree_materialization(recorder, is_final=False, root_dir=self.root)
        self.assertEqual(recorder._staging_cache_dir.resolve(), self.root_cache.resolve())

        # Write stitched WAV and acoustic receipt inside the worktree cache dir
        expected_sha = _write_test_pcm_wav(recorder.master_wav, freq_hz=500.0)
        recorder.acoustic_evidence_receipt_path.write_text('{"status": "PASS"}', encoding="utf-8")

        finalize_diarization_and_seal(recorder)

        root_master = self.root_cache / wav_name
        self.assertTrue(root_master.is_file())
        self.assertEqual(
            hashlib.sha256(root_master.read_bytes()).hexdigest(),
            expected_sha,
        )
        self.assertTrue(
            (self.root_cache / recorder.acoustic_evidence_receipt_path.name).is_file()
        )


class TestWave2AudioRepatriationGate(unittest.TestCase):
    """Verifies Wave 2 pre-teardown audio and ASR cache repatriation across worktree removal paths."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.tmp.name) / "git_repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.email", "test@example.com"])
        self._git(["config", "user.name", "Tester"])
        (self.repo_root / ".gitignore").write_text("*.wav\noutput/transcript_cache/\n", encoding="utf-8")
        self._git(["add", ".gitignore"])
        self._git(["commit", "-m", "chore: initial commit"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, args: list[str], cwd: Path | None = None) -> None:
        import os
        import subprocess

        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_PREFIX")
        }
        subprocess.run(
            ["git"] + args,
            cwd=str(cwd or self.repo_root),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_cmd_finish_repatriates_wav_and_sidecars_before_removing_worktree(self) -> None:
        """cmd_finish repatriates ignored .wav and ASR sidecars to primary root and removes worktree cleanly."""
        import argparse
        from unittest.mock import patch
        from tools.pr_governance_core.worktree import cmd_finish

        branch = "feat/live-dociq-jack-amit"
        wt_dir = self.repo_root / ".worktrees" / "feat_live_dociq_jack_amit"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])

        # Add a tracked deliverable and merge into main
        recap = wt_dir / "02_recap.md"
        recap.write_text("# Executive Recap\n", encoding="utf-8")
        self._git(["add", "02_recap.md"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: deliver recap"], cwd=wt_dir)
        self._git(["merge", "--no-ff", branch, "-m", f"Merge branch '{branch}' into main"])

        # Place ignored master WAV, rolling chunk, and ASR sidecars ONLY in the worktree
        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        wt_cache = wt_dir / rel_cache
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        expected_sha = _write_test_pcm_wav(wt_cache / wav_name, freq_hz=440.0)
        _write_test_pcm_wav(wt_cache / ".chunks" / "chunk_00000.wav", freq_hz=440.0)
        raw_asr = wt_cache / "00_10-01-2026_Dociq_Jack_Amit_master_110726_abc.raw_asr.json"
        raw_asr.write_text('{"segments": []}', encoding="utf-8")

        args = argparse.Namespace(branch=branch, force=False, ingest=False, delete_branch=True)
        with patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            rc = cmd_finish(args)

        self.assertEqual(rc, 0)
        self.assertFalse(wt_dir.exists(), "Merged worktree must be removed after repatriation")

        root_wav = self.repo_root / rel_cache / wav_name
        self.assertTrue(root_wav.is_file(), "Master WAV must survive in primary root after worktree teardown")
        self.assertEqual(hashlib.sha256(root_wav.read_bytes()).hexdigest(), expected_sha)
        self.assertTrue((self.repo_root / rel_cache / raw_asr.name).is_file())

    def test_teardown_ingest_worktree_repatriates_wav_under_force(self) -> None:
        """teardown_ingest_worktree repatriates WAV to primary root before git worktree remove --force."""
        from dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup import (
            teardown_ingest_worktree,
        )

        branch = "ingest/dociq-jack-amit"
        wt_dir = self.repo_root / ".worktrees" / "ingest_dociq_jack_amit"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])

        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        expected_sha = _write_test_pcm_wav(wt_dir / rel_cache / wav_name, freq_hz=520.0)

        ok = teardown_ingest_worktree(wt_dir, branch, repo_root=self.repo_root, force=True)
        self.assertTrue(ok)
        self.assertFalse(wt_dir.exists())

        root_wav = self.repo_root / rel_cache / wav_name
        self.assertTrue(root_wav.is_file())
        self.assertEqual(hashlib.sha256(root_wav.read_bytes()).hexdigest(), expected_sha)

    def test_repatriation_checksum_mismatch_aborts_teardown_fail_closed(self) -> None:
        """Simulated destination corruption during repatriation blocks worktree deletion fail-closed."""
        from tools.pr_governance_core.audio_repatriation import (
            repatriate_worktree_audio_artifacts,
        )

        branch = "feat/corrupt-copy-test"
        wt_dir = self.repo_root / ".worktrees" / "feat_corrupt_copy_test"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])

        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        wt_wav = wt_dir / rel_cache / wav_name
        expected_sha = _write_test_pcm_wav(wt_wav, freq_hz=600.0)

        def _corrupting_copy(src: Path, dst: Path) -> None:
            data = bytearray(src.read_bytes())
            data[-1] ^= 0xFF
            dst.write_bytes(bytes(data))

        res = repatriate_worktree_audio_artifacts(wt_dir, self.repo_root, copy_fn=_corrupting_copy)
        self.assertFalse(res.success)
        self.assertIn("SHA-256 mismatch", res.error or "")
        # Source WAV in worktree must remain untouched
        self.assertTrue(wt_wav.is_file())
        self.assertEqual(hashlib.sha256(wt_wav.read_bytes()).hexdigest(), expected_sha)


if __name__ == "__main__":
    unittest.main()

