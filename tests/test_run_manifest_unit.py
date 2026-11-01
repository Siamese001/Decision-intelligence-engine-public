#!/usr/bin/env python3
"""Unit tests for dual_pipeline.run_manifest module."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.run_manifest import build_parser, main


class TestRunManifestUnit(unittest.TestCase):
    """Tests for run_manifest CLI parser and invocation."""

    def test_build_parser_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--manifest", "manifest.json"])
        self.assertEqual(args.manifest, "manifest.json")
        self.assertIsNone(args.model)
        self.assertIsNone(args.reasoning_effort)
        self.assertFalse(args.dry_run_qa)
        self.assertFalse(args.no_publish)

    def test_build_parser_all_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--manifest", "custom_manifest.json",
            "--model", "gpt-6-luna",
            "--reasoning-effort", "max",
            "--dry-run-qa",
            "--no-publish",
            "--fresh-eval",
        ])
        self.assertEqual(args.manifest, "custom_manifest.json")
        self.assertEqual(args.model, "gpt-6-luna")
        self.assertEqual(args.reasoning_effort, "max")
        self.assertTrue(args.dry_run_qa)
        self.assertTrue(args.no_publish)
        self.assertTrue(args.fresh_eval)

    def test_main_execution_delegation(self) -> None:
        with patch("dual_pipeline.run_manifest.IngestionMeetingManifest.from_json") as mock_from_json, \
             patch("dual_pipeline.run_manifest.ManifestPipelineRunner.run_manifest") as mock_run_manifest:

            mock_manifest = MagicMock()
            mock_manifest.meeting_title = "Test Meeting"
            mock_manifest.human_date = "09/17/2026"
            mock_from_json.return_value = mock_manifest
            mock_run_manifest.return_value = {"status": "SUCCESS"}

            main(["--manifest", "dummy.json", "--dry-run-qa"])

            mock_from_json.assert_called_once_with(Path("dummy.json"))
            mock_run_manifest.assert_called_once_with(
                manifest=mock_manifest,
                model=None,
                reasoning_effort=None,
                dry_run_qa=True,
                publish_to_graph=True,
                fresh_eval=False,
            )


if __name__ == "__main__":
    unittest.main()
