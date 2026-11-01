"""Adversarial and edge-case tests for pre-teardown WAV and ASR cache repatriation."""

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
from unittest.mock import patch

from dual_pipeline.live_meeting_recorder_core.ingest_worktree_cleanup import (
    teardown_ingest_worktree,
)
from tools.pr_governance_core.audio_repatriation import (
    repatriate_worktree_audio_artifacts,
)
from tools.pr_governance_core.worktree import cmd_finish


def _write_pcm_wav(
    path: Path,
    *,
    sample_rate: int = 16000,
    duration_sec: float = 0.15,
    freq_hz: float = 440.0,
) -> str:
    """Writes a deterministic 16-bit mono PCM WAV file and returns its SHA-256 digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    num_samples = int(sample_rate * duration_sec)
    frames = bytearray()
    for idx in range(num_samples):
        val = int(12000.0 * math.sin(2.0 * math.pi * freq_hz * (idx / sample_rate)))
        frames.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestWavRepatriationAdversarial(unittest.TestCase):
    """Adversarial test suite covering boundary payloads, malformed writes, rollback, and regression."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="eval_sandbox_wav_adv_")
        self.repo_root = Path(self.tmp.name) / "adv_repo"
        self.repo_root.mkdir(parents=True, exist_ok=True)
        self._git(["init", "-b", "main"])
        self._git(["config", "user.email", "adv@example.com"])
        self._git(["config", "user.name", "Adversarial Tester"])
        (self.repo_root / ".gitignore").write_text(
            "*.wav\noutput/transcript_cache/\n",
            encoding="utf-8",
        )
        self._git(["add", ".gitignore"])
        self._git(["commit", "-m", "chore: initial commit"])

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

    def test_boundary_payload_zero_byte_wav_in_root_overwritten_by_valid_worktree_wav(
        self,
    ) -> None:
        """
        Category 1 (Boundary payload):
        1a. A 0-byte placeholder `.wav` in primary root is replaced by a valid non-empty `.wav`
            from the worktree during repatriation.
        1b. A 0-byte `.wav` inside the worktree never overwrites a valid non-empty `.wav` in primary root.
        """
        wt_dir = self.repo_root / ".worktrees" / "feat_zero_byte_boundary"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/zero-byte-boundary", "main"])

        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        wav_a = "00_10-01-2026_Valid_In_Worktree_master_110000.wav"
        wav_b = "00_10-01-2026_Valid_In_Root_master_120000.wav"

        # 1a: 0-byte file in primary root, valid PCM WAV in worktree
        root_wav_a = self.repo_root / rel_cache / wav_a
        root_wav_a.parent.mkdir(parents=True, exist_ok=True)
        root_wav_a.write_bytes(b"")
        sha_a = _write_pcm_wav(wt_dir / rel_cache / wav_a, freq_hz=440.0)

        # 1b: valid PCM WAV in primary root, 0-byte file in worktree
        sha_b = _write_pcm_wav(self.repo_root / rel_cache / wav_b, freq_hz=520.0)
        wt_wav_b = wt_dir / rel_cache / wav_b
        wt_wav_b.parent.mkdir(parents=True, exist_ok=True)
        wt_wav_b.write_bytes(b"")

        res = repatriate_worktree_audio_artifacts(wt_dir, self.repo_root)
        self.assertTrue(res.success)

        # Root wav_a must now match valid worktree WAV
        self.assertEqual(hashlib.sha256(root_wav_a.read_bytes()).hexdigest(), sha_a)
        # Root wav_b must remain untouched by 0-byte worktree file
        self.assertEqual(
            hashlib.sha256((self.repo_root / rel_cache / wav_b).read_bytes()).hexdigest(),
            sha_b,
        )

    def test_malformed_corrupt_destination_write_aborts_cmd_finish_and_teardown_fail_closed(
        self,
    ) -> None:
        """
        Category 2 & 6 (Malformed/corrupt write + Rollback & recovery):
        When a destination write flips bits or raises an OS error during repatriation,
        both `cmd_finish` and `teardown_ingest_worktree` abort fail-closed and keep the
        worktree and source `.wav` completely intact on disk.
        """
        branch = "feat/corrupt-dest-write"
        wt_dir = self.repo_root / ".worktrees" / "feat_corrupt_dest_write"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])
        (wt_dir / "note.md").write_text("merged note\n", encoding="utf-8")
        self._git(["add", "note.md"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: add note"], cwd=wt_dir)
        self._git(["merge", "--no-ff", branch, "-m", f"Merge branch '{branch}' into main"])

        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        wt_wav = wt_dir / rel_cache / wav_name
        expected_sha = _write_pcm_wav(wt_wav, freq_hz=480.0)

        def _truncated_copy(src: Path, dst: Path) -> None:
            raw = src.read_bytes()
            dst.write_bytes(raw[: len(raw) // 2])

        with patch(
            "tools.pr_governance_core.audio_repatriation.shutil.copy2",
            side_effect=_truncated_copy,
        ), patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            args = argparse.Namespace(branch=branch, force=True, ingest=True, delete_branch=True)
            rc = cmd_finish(args)
            self.assertEqual(rc, 1, "cmd_finish must abort with exit code 1 on checksum mismatch")

            ok = teardown_ingest_worktree(wt_dir, branch, repo_root=self.repo_root, force=True)
            self.assertFalse(ok, "teardown_ingest_worktree must return False on repatriation failure")

        # Worktree and source WAV must still exist uncorrupted
        self.assertTrue(wt_dir.is_dir())
        self.assertTrue(wt_wav.is_file())
        self.assertEqual(hashlib.sha256(wt_wav.read_bytes()).hexdigest(), expected_sha)

    def test_invariant_bypass_attempt_force_and_ingest_flags_still_preserve_wav_while_blocking_other_dirty_files(
        self,
    ) -> None:
        """
        Category 5 (Invariant bypass attempt):
        1. Passing `--force` and `--ingest` to `cmd_finish` still repatriates `.wav` files before removal.
        2. When `force=False`, repatriating `.wav` files does NOT bypass the cleanliness gate for
           non-audio valuable untracked/ignored files (e.g. `graph.sqlite-wal`), which still block teardown.
        """
        branch = "feat/bypass-check"
        wt_dir = self.repo_root / ".worktrees" / "feat_bypass_check"
        self._git(["worktree", "add", str(wt_dir), "-b", branch, "main"])
        (wt_dir / "code.py").write_text("x = 1\n", encoding="utf-8")
        self._git(["add", "code.py"], cwd=wt_dir)
        self._git(["commit", "-m", "feat: add code"], cwd=wt_dir)
        self._git(["merge", "--no-ff", branch, "-m", f"Merge branch '{branch}' into main"])

        rel_cache = Path("output/transcript_cache/project_nexus/10-01-2026")
        wav_name = "00_10-01-2026_Dociq_Jack_Amit_master_110726.wav"
        expected_sha = _write_pcm_wav(wt_dir / rel_cache / wav_name, freq_hz=440.0)

        # Also place an untracked valuable non-audio file in the worktree
        (wt_dir / "uncommitted_notes.md").write_text("valuable notes\n", encoding="utf-8")

        # With force=False, WAV is repatriated to primary root, AND teardown is blocked due to uncommitted_notes.md
        args_no_force = argparse.Namespace(branch=branch, force=False, ingest=False, delete_branch=True)
        with patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            rc_blocked = cmd_finish(args_no_force)

        self.assertEqual(rc_blocked, 1)
        self.assertTrue(wt_dir.exists(), "Worktree must remain when non-audio dirty file exists")
        root_wav = self.repo_root / rel_cache / wav_name
        self.assertTrue(root_wav.is_file(), "WAV is already safely repatriated to primary root")
        self.assertEqual(hashlib.sha256(root_wav.read_bytes()).hexdigest(), expected_sha)

        # Idempotent re-repatriation under --force --ingest succeeds and preserves root WAV
        args_force = argparse.Namespace(branch=branch, force=True, ingest=True, delete_branch=True)
        with patch("tools.pr_governance_core.worktree.ROOT", self.repo_root):
            rc_force = cmd_finish(args_force)

        self.assertEqual(rc_force, 0)
        self.assertFalse(wt_dir.exists())
        self.assertTrue(root_wav.is_file())
        self.assertEqual(hashlib.sha256(root_wav.read_bytes()).hexdigest(), expected_sha)

    def test_orphan_chunks_without_master_wav_are_preserved_not_pruned(self) -> None:
        """
        Category 6 & 7 (Recovery & production regression):
        If a recording crashed before stitching a `00_*_master_*.wav` file, rolling
        `.chunks/chunk_*.wav` files in the worktree must NOT be deleted as redundant;
        they must be repatriated to `<primary_root>/output/transcript_cache/.../.chunks/`
        so the session can be stitched and recovered later.
        """
        wt_dir = self.repo_root / ".worktrees" / "live_interrupted_session"
        self._git(["worktree", "add", str(wt_dir), "-b", "feat/interrupted-session", "main"])

        rel_chunks = Path("output/transcript_cache/project_nexus/10-01-2026/.chunks")
        chunk_0 = wt_dir / rel_chunks / "chunk_00000.wav"
        chunk_1 = wt_dir / rel_chunks / "chunk_00001.wav"
        sha_0 = _write_pcm_wav(chunk_0, freq_hz=400.0)
        sha_1 = _write_pcm_wav(chunk_1, freq_hz=500.0)

        res = repatriate_worktree_audio_artifacts(wt_dir, self.repo_root)
        self.assertTrue(res.success)

        root_chunk_0 = self.repo_root / rel_chunks / "chunk_00000.wav"
        root_chunk_1 = self.repo_root / rel_chunks / "chunk_00001.wav"
        self.assertTrue(root_chunk_0.is_file())
        self.assertTrue(root_chunk_1.is_file())
        self.assertEqual(hashlib.sha256(root_chunk_0.read_bytes()).hexdigest(), sha_0)
        self.assertEqual(hashlib.sha256(root_chunk_1.read_bytes()).hexdigest(), sha_1)


if __name__ == "__main__":
    unittest.main()
