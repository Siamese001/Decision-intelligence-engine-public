"""End-to-end verification of master WAV and ASR cache preservation across worktree teardown."""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup import (
    teardown_ingest_worktree,
)
from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import (
    seed_master_asr_from_recorder,
)
from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
    finalize_diarization_and_seal,
    reconcile_master_wav_and_receipt,
)
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import (
    finalize_worktree_materialization,
    repoint_recorder_paths,
)
from tools.pr_governance_core.worktree import cmd_finish


def _write_pcm_wav(
    path: Path,
    *,
    sample_rate: int = 16000,
    duration_sec: float = 0.25,
    freq_hz: float = 440.0,
) -> str:
    """Writes a deterministic 16-bit mono PCM WAV file and returns its SHA-256 digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_sec)
    frames = bytearray()
    for idx in range(num_samples):
        val = int(14000.0 * math.sin(2.0 * math.pi * freq_hz * (idx / sample_rate)))
        frames.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestWavPreservationPostWorktreeTeardownE2E(unittest.TestCase):
    """End-to-end lifecycle test: worktree start -> live capture/stitch -> PR merge -> worktree teardown."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="eval_sandbox_wav_e2e_")
        self.repo_root = Path(self.tmp.name) / "primary_repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.email", "operator@example.com"])
        self._git(["config", "user.name", "Operator"])
        (self.repo_root / ".gitignore").write_text(
            "*.wav\n*.mp3\noutput/transcript_cache/\n",
            encoding="utf-8",
        )
        self._git(["add", ".gitignore"])
        self._git(["commit", "-m", "chore: initialize primary repository"])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, args: list[str], cwd: Path | None = None) -> str:
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_COMMON_DIR",
                "GIT_PREFIX",
            )
        }
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd or self.repo_root),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()

    def test_full_live_recording_pr_merge_and_worktree_teardown_preserves_wav_in_root(
        self,
    ) -> None:
        """
        Reproduces the exact October 1, 2026 DocIQ Jack O'Shea lifecycle:
        1. Provision isolated worktree `.worktrees/feat_live_dociq_jack_amit`.
        2. Stage initial capture in primary root, then materialize/repoint into worktree.
        3. Stitch master WAV (`00_10-01-2026_Dociq_Jack_Amit_master_110726.wav`) and seed ASR sidecars.
        4. Commit markdown deliverables (`01_`, `02_`, `03_`) in worktree and merge into `main`.
        5. Execute `cmd_finish` to tear down the worktree and delete the feature branch.
        6. Verify the worktree is gone while the master WAV and ASR sidecars remain byte-identical
           in `<primary_root>/output/transcript_cache/project_nexus/10-01-2026/`.
        """
        branch = "feat/live-dociq-jack-amit"
        wt_dir = self.repo_root / ".worktrees" / "feat_live_dociq_jack_amit"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])

        rel_out = Path("output/project_nexus/10-01-2026")
        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        root_out = self.repo_root / rel_out
        root_cache = self.repo_root / rel_cache
        root_chunks = root_cache / ".chunks"
        wt_out = wt_dir / rel_out
        wt_cache = wt_dir / rel_cache
        wt_chunks = wt_cache / ".chunks"

        for d in (root_out, root_cache, root_chunks, wt_out, wt_cache, wt_chunks):
            d.mkdir(parents=True, exist_ok=True)

        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        recorder = SimpleNamespace(
            date_dirname="10-01-2026",
            clean_slug="Dociq_Jack_Amit",
            session_tag="110726",
            output_dir=root_out,
            cache_dir=root_cache,
            chunks_dir=root_chunks,
            _target_output_dir=wt_out,
            _target_cache_dir=wt_cache,
            _target_chunks_dir=wt_chunks,
            worktree_dir=wt_dir,
            repo_root=self.repo_root,
            capture_manager=None,
            spooler=None,
            chat_attributions=None,
            speaker_hint_manager=None,
            speaker_hint_queue=None,
            collateral_watcher=None,
            _acoustic_diarization_complete=True,
            _acoustic_diarization_error=None,
            asr_engine=SimpleNamespace(model_id="mlx-community/whisper-large-v3-turbo"),
            attendees=["Jack O'Shea", "Amit Ayer"],
            session_id="sess_110726",
            project_slug="project_nexus",
            meeting_name="Dociq Jack Amit",
            segments=[
                {
                    "start": 0.0,
                    "end": 1.25,
                    "speaker": "Jack O'Shea",
                    "text": "Let us review the DocIQ architecture.",
                }
            ],
            speaker_Registry={},
        )
        recorder._migrate_staged_chunks = lambda is_final=False: None
        recorder._migrate_staged_metadata = lambda is_final=False: None
        recorder._repoint_recorder_paths_to_worktree = lambda: repoint_recorder_paths(
            recorder, wt_dir
        )
        recorder._run_post_recording_diarization = lambda: None
        recorder._cleanup_empty_session_or_seal = lambda: None
        recorder._release_slug_lock = lambda: None

        # Step 2: Materialize worktree and repoint active recorder paths to worktree
        finalize_worktree_materialization(recorder, is_final=False, root_dir=self.repo_root)
        self.assertEqual(recorder.master_wav, wt_cache / wav_name)

        # Step 3: Stitch master WAV in worktree, reconcile, seal, and seed ASR sidecar
        expected_sha = _write_pcm_wav(recorder.master_wav, freq_hz=440.0)
        receipt = reconcile_master_wav_and_receipt(recorder, None)
        recorder.acoustic_evidence_receipt_path.write_text(
            '{"status": "VERIFIED", "frames": 4000}',
            encoding="utf-8",
        )
        recorder.segments_jsonl.write_text(
            '{"start_sec": 0.0, "end_sec": 1.25, "speaker": "Jack O\'Shea", "verbatim": "Let us review the DocIQ architecture."}\n',
            encoding="utf-8",
        )
        finalize_diarization_and_seal(recorder)
        seed_master_asr_from_recorder(recorder, receipt)

        # Confirm tracked deliverables committed in worktree and merged into main
        (wt_out / "01_transcript.md").write_text("# Verbatim Transcript\n", encoding="utf-8")
        (wt_out / "02_recap.md").write_text("# Executive Recap\n", encoding="utf-8")
        (wt_out / "03_decisions.md").write_text("# Decision Log\n", encoding="utf-8")
        self._git(["add", str(rel_out)], cwd=wt_dir)
        self._git(["commit", "-m", "feat: complete dociq synthesis artifacts"], cwd=wt_dir)
        self._git(["merge", "--no-ff", branch, "-m", f"Merge branch '{branch}' into main"])

        # Step 5: Execute governed post-merge worktree teardown
        args = argparse.Namespace(branch=branch, force=False, ingest=False, delete_branch=True)
        with patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            rc = cmd_finish(args)

        self.assertEqual(rc, 0)
        self.assertFalse(wt_dir.exists(), "Worktree directory must be removed after cmd_finish")

        # Step 6: Verify master WAV and ASR sidecars survive in primary root with exact SHA-256
        primary_wav = root_cache / wav_name
        self.assertTrue(
            primary_wav.is_file(),
            "Master WAV must persist in primary root after worktree deletion",
        )
        self.assertEqual(
            hashlib.sha256(primary_wav.read_bytes()).hexdigest(),
            expected_sha,
        )
        with wave.open(str(primary_wav), "rb") as wf:
            self.assertEqual(wf.getnchannels(), 1)
            self.assertEqual(wf.getframerate(), 16000)
            self.assertEqual(wf.getnframes(), 4000)

        sidecars = (
            list(root_cache.glob("*.raw_asr.json"))
            + list(root_cache.glob("*_acoustic_evidence_receipt.json"))
            + list(root_cache.glob("*_segments.jsonl"))
        )
        self.assertGreaterEqual(len(sidecars), 2)

    def test_unmirrored_worktree_wav_repatriated_on_ingest_worktree_teardown(self) -> None:
        """
        Even if a WAV was placed directly inside an ingest worktree without prior mirroring,
        teardown_ingest_worktree repatriates it to primary root before deleting the worktree.
        """
        branch = "ingest/direct-wav-session"
        wt_dir = self.repo_root / ".worktrees" / "ingest_direct_wav_session"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])

        rel_cache = Path("output/transcript_cache/project_nexus/10-02-2026")
        wav_name = "00_10-02-2026_Direct_Session_master_093000.wav"
        expected_sha = _write_pcm_wav(wt_dir / rel_cache / wav_name, freq_hz=587.33)
        raw_asr = wt_dir / rel_cache / "00_10-02-2026_Direct_Session_master_093000.raw_asr.json"
        raw_asr.write_text('{"segments": [{"text": "Preserved across teardown"}]}', encoding="utf-8")

        ok = teardown_ingest_worktree(wt_dir, branch, repo_root=self.repo_root, force=True)
        self.assertTrue(ok)
        self.assertFalse(wt_dir.exists())

        primary_wav = self.repo_root / rel_cache / wav_name
        self.assertTrue(primary_wav.is_file())
        self.assertEqual(hashlib.sha256(primary_wav.read_bytes()).hexdigest(), expected_sha)
        self.assertTrue((self.repo_root / rel_cache / raw_asr.name).is_file())


if __name__ == "__main__":
    unittest.main()
