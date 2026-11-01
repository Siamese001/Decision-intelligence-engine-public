#!/usr/bin/env python3
"""Unit tests for dual_pipeline.archival_manager."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.archival_manager import ArchivalManager


class TestArchivalManagerUnit(unittest.TestCase):
    """Tests for ArchivalManager artifact preservation and isolation."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.root_dir = Path(self.temp_dir) / "output"
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.manager = ArchivalManager(str(self.root_dir))

    def test_target_dir_does_not_exist_returns_none(self) -> None:
        res = self.manager.archive_if_exists("project_x", "09-17-2026")
        self.assertIsNone(res)

    def test_target_dir_empty_returns_none(self) -> None:
        target = self.root_dir / "project_x" / "09-17-2026"
        target.mkdir(parents=True, exist_ok=True)
        res = self.manager.archive_if_exists("project_x", "09-17-2026")
        self.assertIsNone(res)

    def test_target_dir_with_unrelated_files_without_stem_returns_none(self) -> None:
        target = self.root_dir / "project_x" / "09-17-2026"
        target.mkdir(parents=True, exist_ok=True)
        (target / "notes.txt").write_text("random notes")
        res = self.manager.archive_if_exists("project_x", "09-17-2026")
        self.assertIsNone(res)

    def test_archive_artifacts_with_stem_matching(self) -> None:
        target = self.root_dir / "project_x" / "09-17-2026"
        target.mkdir(parents=True, exist_ok=True)
        artifact_1 = target / "01_Meeting_A_transcript.md"
        artifact_2 = target / "02_Meeting_A_recap.md"
        other_file = target / "01_Meeting_B_transcript.md"

        artifact_1.write_text("Meeting A transcript")
        artifact_2.write_text("Meeting A recap")
        other_file.write_text("Meeting B transcript")

        with patch("dual_pipeline.archival_manager.ROOT", Path(self.temp_dir)):
            archived = self.manager.archive_if_exists("project_x", "09-17-2026", meeting_stem="Meeting_A")
            self.assertIsNotNone(archived)
            self.assertTrue(Path(archived).exists())
            self.assertTrue((Path(archived) / "01_Meeting_A_transcript.md").exists())
            self.assertTrue((Path(archived) / "02_Meeting_A_recap.md").exists())
            # Other file must remain untouched
            self.assertTrue(other_file.exists())
            self.assertFalse(artifact_1.exists())
            self.assertFalse(artifact_2.exists())

    def test_archive_all_artifacts_without_stem(self) -> None:
        target = self.root_dir / "project_x" / "09-17-2026"
        target.mkdir(parents=True, exist_ok=True)
        artifact_1 = target / "01_transcript.md"
        artifact_2 = target / "02_recap.md"
        artifact_1.write_text("transcript")
        artifact_2.write_text("recap")

        with patch("dual_pipeline.archival_manager.ROOT", Path(self.temp_dir)):
            archived = self.manager.archive_if_exists("project_x", "09-17-2026")
            self.assertIsNotNone(archived)
            self.assertTrue(Path(archived).exists())
            self.assertTrue((Path(archived) / "01_transcript.md").exists())
            self.assertTrue((Path(archived) / "02_recap.md").exists())
            self.assertFalse(artifact_1.exists())
            self.assertFalse(artifact_2.exists())


if __name__ == "__main__":
    unittest.main()
