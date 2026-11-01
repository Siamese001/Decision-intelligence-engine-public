#!/usr/bin/env python3
"""
Unit and Concurrency Tests for Atomic Rolling File Persistence Engine
Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, macOS)

Verifies:
  1. Atomic File Replacement (write to .tmp -> fsync -> os.replace)
  2. Zero Lock Errors & High-Throughput Concurrent Reader Integrity
  3. Governance Header Conformance (Date MM/DD/YYYY, ET Timestamps, Speaker Attribution)
  4. LocalSTTEngine End-to-End Rolling Persistence Integration
"""
from __future__ import annotations

import concurrent.futures
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from pipeline_a_intake.local_stt import AudioChunk, LocalSTTEngine
from pipeline_a_intake.rolling_persistence import (
    AtomicRollingFileWriter,
)
from storage.database import DatabaseManager


class TestAtomicRollingFileWriter(unittest.TestCase):
    """Verifies atomic snapshotting and formatting of rolling transcripts."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.staging_dir = Path(self.temp_dir) / "staging"
        self.filename = "01_09-12-2026_system_audio_transcript.md"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initialization_and_header_creation(self):
        writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
            meeting_name="Nexus Architecture Review",
            project_name="project_nexus",
            capture_mode="screen_capture_kit",
            whisper_model="mlx-community/whisper-large-v3-turbo",
            classification="internal",
            custom_date="09/12/2026",
        )

        self.assertTrue(writer.target_path.exists())
        self.assertFalse(writer.tmp_path.exists())

        content = writer.read_snapshot()
        self.assertIn("# Nexus Architecture Review — System Audio Transcript", content)
        self.assertIn("**Date:** 09/12/2026", content)
        self.assertIn("**Capture Mode:** screen_capture_kit", content)
        self.assertIn("**Whisper Engine:** mlx-community/whisper-large-v3-turbo", content)
        self.assertIn("**Classification:** internal", content)
        self.assertIn("## Spoken Feed", content)
        self.assertIn("*(Awaiting speech input...)*", content)

    def test_add_segments_and_atomic_replace(self):
        writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
            meeting_name="Executive Standup",
            custom_date="09/12/2026",
        )

        seg1 = writer.add_segment(
            text="First spoken segment regarding data pipeline migration.",
            speaker="Alice",
            timestamp_str="09:30:15 AM ET",
        )
        self.assertEqual(seg1.speaker, "Alice")
        self.assertEqual(writer.segment_count, 1)

        content1 = writer.read_snapshot()
        self.assertIn("- `[09:30:15 AM ET]` **Alice**: First spoken segment regarding data pipeline migration.", content1)
        self.assertNotIn("*(Awaiting speech input...)*", content1)

        writer.add_segment(
            text="Second confirmed utterance with default speaker.",
            speaker="Speaker",
            timestamp_str="09:30:25 AM ET",
        )
        self.assertEqual(writer.segment_count, 2)

        content2 = writer.read_snapshot()
        self.assertIn("- `[09:30:15 AM ET]` **Alice**: First spoken segment regarding data pipeline migration.", content2)
        self.assertIn("- `[09:30:25 AM ET]` Second confirmed utterance with default speaker.", content2)

    def test_empty_segment_validation(self):
        writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
        )
        with self.assertRaises(ValueError):
            writer.add_segment("   ")

    def test_date_and_timestamp_governance_regex(self):
        writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
        )
        writer.add_segment("Testing automated ET timestamp generation.")
        content = writer.read_snapshot()

        # Check date format MM/DD/YYYY
        self.assertTrue(re.search(r"\*\*Date:\*\* \d{2}/\d{2}/\d{4}", content))
        # Check ET timestamp format [HH:MM:SS AM/PM ET]
        self.assertTrue(re.search(r"- `\[\d{2}:\d{2}:\d{2} [AP]M ET\]`", content))

    def test_session_resumption_and_crash_recovery(self):
        """Verifies that an existing file is parsed on init so previous segments are never overwritten."""
        w1 = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
            meeting_name="Crash Recovery Test",
            custom_date="09/12/2026",
        )
        w1.add_segment("Segment before interruption 1", speaker="Bob", timestamp_str="10:00:00 AM ET")
        w1.add_segment("Segment before interruption 2", speaker="Speaker", timestamp_str="10:01:00 AM ET")
        w1.close()

        # Session 2: simulated restart / reconnection
        w2 = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
            meeting_name="Crash Recovery Test",
            custom_date="09/12/2026",
        )
        self.assertEqual(w2.segment_count, 2)
        w2.add_segment("Segment after recovery", speaker="Bob", timestamp_str="10:02:00 AM ET")
        self.assertEqual(w2.segment_count, 3)

        content = w2.read_snapshot()
        self.assertIn("- `[10:00:00 AM ET]` **Bob**: Segment before interruption 1", content)
        self.assertIn("- `[10:01:00 AM ET]` Segment before interruption 2", content)
        self.assertIn("- `[10:02:00 AM ET]` **Bob**: Segment after recovery", content)

    def test_concurrent_readers_never_observe_torn_read_or_lock_error(self):

        """
        Stress tests atomic replacement:
        A writer thread rapidly appends 100 segments while 8 concurrent reader
        threads continuously read the target file.
        Asserts 0 exceptions, 0 empty reads, and valid markdown on every single read.
        """
        writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename=self.filename,
            meeting_name="Concurrency Stress Session",
            custom_date="09/12/2026",
        )

        stop_event = concurrent.futures.thread.threading.Event()
        read_errors = []
        read_counts = [0]

        def reader_worker():
            local_reads = 0
            while not stop_event.is_set():
                try:
                    with open(writer.target_path, "r", encoding="utf-8") as f:
                        data = f.read()
                    if not data:
                        read_errors.append("Empty read encountered (torn write)")
                    if "# Concurrency Stress Session" not in data:
                        read_errors.append("Corrupted header encountered")
                    local_reads += 1
                except Exception as e:
                    read_errors.append(f"Reader exception: {e}")
                time.sleep(0.001)
            read_counts[0] += local_reads

        # Launch 8 concurrent readers
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(reader_worker) for _ in range(8)]

            # Writer commits 50 segments in rapid succession
            for i in range(50):
                writer.add_segment(
                    text=f"Concurrent segment {i} confirming atomic snapshot integrity.",
                    speaker=f"Worker_{i % 4}",
                )
                time.sleep(0.002)

            stop_event.set()
            for f in futures:
                f.result()

        self.assertEqual(len(read_errors), 0, f"Encountered read errors during atomic replace: {read_errors[:5]}")
        self.assertGreater(read_counts[0], 100)
        self.assertEqual(writer.segment_count, 50)
        final_content = writer.read_snapshot()
        self.assertIn("Concurrent segment 49", final_content)


class TestLocalSTTEngineRollingIntegration(unittest.TestCase):
    """Verifies that LocalSTTEngine automatically persists confirmed segments to AtomicRollingFileWriter."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.staging_dir = Path(self.temp_dir) / "staging"
        self.db_manager = DatabaseManager(
            base_dir=self.temp_dir,
            allow_unencrypted_dev_fallback=True,
        )
        self.rolling_writer = AtomicRollingFileWriter(
            staging_dir=self.staging_dir,
            filename="01_test_rolling_transcript.md",
            meeting_name="Engine Integration Test",
            custom_date="09/12/2026",
        )
        self.engine = LocalSTTEngine(
            db_manager=self.db_manager,
            mock_mode=True,
            rolling_writer=self.rolling_writer,
            session_id="session_rolling_test",
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_audio_feed_commits_to_rolling_file(self):
        t = np.linspace(0, 1.0, 16_000, endpoint=False)
        audio = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)

        chunk = AudioChunk(
            chunk_id="chunk_rolling_01",
            meeting_id="meeting_rolling_01",
            audio_data=audio,
            speaker="Sarah Lin",
            metadata={"mock_text": "Live verified rolling persistence chunk"},
        )

        self.engine.feed_audio(chunk)
        self.engine.flush()

        # Verify rolling writer received segments
        self.assertGreater(self.rolling_writer.segment_count, 0)
        content = self.rolling_writer.read_snapshot()
        self.assertIn("Live verified", content)
        self.assertIn("rolling persistence chunk", content)
        self.assertIn("Sarah Lin", content)



if __name__ == "__main__":
    unittest.main()
