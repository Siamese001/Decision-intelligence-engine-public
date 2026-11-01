#!/usr/bin/env python3
"""Adversarial and boundary tests for attachment discovery and statement filtering."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.attachment_core.detector import discover_attachments
from dual_pipeline.transcript_pipeline_core.statement_filter import (
    _filter_turn_dialogue,
    _is_turn_banter,
    _score_sentence,
)


class TestLessonsLearnedCollateralAdversarial(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="collateral_adv_")
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_adversarial_shared_containers_never_scanned(self) -> None:
        """Every shared container name is immune to inadvertent sibling scanning."""
        shared_names = [
            "intake", "inputs", "transcripts", "raw_transcripts",
            "transcript_cache", "audio", "recordings", "output",
        ]
        for name in shared_names:
            container = self.root / name
            container.mkdir(parents=True, exist_ok=True)
            target = container / "target_meeting.txt"
            target.write_text("Target meeting content", encoding="utf-8")
            sibling = container / "unrelated_meeting.txt"
            sibling.write_text("Unrelated content", encoding="utf-8")

            candidates = discover_attachments(transcript_path=target)
            self.assertEqual(
                len(candidates), 0,
                f"Container '{name}' failed collateral isolation; found {candidates}"
            )

    def test_adversarial_debrief_cues_score_positively(self) -> None:
        """Turns containing debrief, pause, access, or connector cues are never classified as banter."""
        debrief_sentences = [
            "Lori debriefed on the P&C Conduit pause with Michael Maione.",
            "The Conduit platform was paused due to Elements system access blockers.",
            "SharePoint connector permission friction delayed the demo progress.",
            "Lessons learned debrief confirmed no delivery performance defect.",
        ]
        for s in debrief_sentences:
            score, is_intro = _score_sentence(s)
            self.assertGreater(score, 0, f"Sentence scored 0: {s}")
            dialogue, total_score, _ = _filter_turn_dialogue(s)
            self.assertTrue(len(dialogue) > 0, f"Dialogue filtered out: {s}")
            self.assertGreater(total_score, 0, f"Total score 0: {s}")
            self.assertFalse(_is_turn_banter(s), f"Incorrectly classified as banter: {s}")

    def test_adversarial_pure_banter_correctly_classified(self) -> None:
        """Pure conversational banter without technical/debrief keywords is dropped."""
        banter_turns = [
            "Nice pocket square today, good afternoon everybody.",
            "The weather in Miami is just gnarly today.",
            "How are you doing today, love Minnesota.",
        ]
        for b in banter_turns:
            self.assertTrue(_is_turn_banter(b), f"Failed to detect banter: {b}")

    def test_adversarial_nonexistent_or_none_paths_do_not_crash(self) -> None:
        """Nonexistent paths, empty strings, and None arguments return empty lists safely."""
        self.assertEqual(discover_attachments(transcript_path=None), [])
        self.assertEqual(discover_attachments(transcript_path=""), [])
        self.assertEqual(discover_attachments(transcript_path=self.root / "nonexistent.txt"), [])
        self.assertEqual(discover_attachments(attachment_dir=self.root / "nonexistent_dir"), [])


if __name__ == "__main__":
    unittest.main()
