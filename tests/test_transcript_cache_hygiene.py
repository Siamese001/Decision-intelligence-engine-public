"""Unit tests for transcript cache hygiene planner and non-destructive dry run."""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.archival_manager import cleanup_transcript_cache
from tools.runtime_storage_gc import run_transcript_cache_plan
from tools.storage_hygiene_core.policy import StorageRetentionPolicy, TranscriptCachePolicy
from tools.storage_hygiene_core.receipt import verify_report_digest
from tools.storage_hygiene_core.transcript_cache import plan_transcript_cache


class TestTranscriptCacheHygiene(unittest.TestCase):
    def test_dry_run_preserves_files_on_disk(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            old_wav = cache_dir / "old_audio.wav"
            old_wav.write_text("old wav audio bytes", encoding="utf-8")

            # Set mtime to 14 days ago
            old_time = time.time() - (14 * 86400)
            import os
            os.utime(old_wav, (old_time, old_time))

            summary = cleanup_transcript_cache(
                cache_dir,
                max_age_seconds=7 * 86400,
                dry_run=True,
                include_globs=("*.wav",),
            )
            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["evicted_ttl_count"], 1)
            self.assertEqual(len(summary["evicted_files"]), 1)
            self.assertEqual(summary["evicted_files"][0]["name"], "old_audio.wav")

            # Crucial check: file must still exist on disk!
            self.assertTrue(old_wav.exists())

    def test_include_globs_strictly_ignores_txt_and_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = Path(tmpdir)
            old_time = time.time() - (14 * 86400)
            import os

            old_wav = cache_dir / "audio.wav"
            old_wav.write_text("wav bytes", encoding="utf-8")
            os.utime(old_wav, (old_time, old_time))

            old_txt = cache_dir / "audio.txt"
            old_txt.write_text("reusable asr transcript", encoding="utf-8")
            os.utime(old_txt, (old_time, old_time))

            old_json = cache_dir / "metadata.json"
            old_json.write_text('{"reusable": true}', encoding="utf-8")
            os.utime(old_json, (old_time, old_time))

            pol = StorageRetentionPolicy(
                transcript_cache=TranscriptCachePolicy(wav_ttl_days=7)
            )
            plan = plan_transcript_cache(cache_dir=cache_dir, policy=pol)

            self.assertEqual(plan.total_files_count, 3)
            self.assertEqual(plan.candidate_count, 1)
            candidate_names = [c["name"] for c in plan.candidates]
            self.assertIn("audio.wav", candidate_names)
            self.assertNotIn("audio.txt", candidate_names)
            self.assertNotIn("metadata.json", candidate_names)

    def test_real_workspace_transcript_cache_plan_integrity(self):
        """Run plan on actual workspace transcript cache and verify candidates are strictly .wav."""
        report = run_transcript_cache_plan(scheduled=False)
        self.assertNotIn("error", report)
        self.assertTrue(verify_report_digest(report))
        self.assertIn("transcript_cache_analysis", report)

        analysis = report["transcript_cache_analysis"]
        for c in analysis["candidates"]:
            self.assertTrue(
                c["path"].endswith(".wav"),
                f"Non-wav file flagged as candidate: {c['path']}",
            )


if __name__ == "__main__":
    unittest.main()
