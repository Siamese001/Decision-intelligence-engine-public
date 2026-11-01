"""
tests/test_meeting_deconfliction_pipeline_integration.py

Integration tests certifying pre-flight collision detection inside RawTranscriptPipeline
and CLI argument enforcement.
"""
from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.cli.handlers import _ingest_meeting
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.meeting_deconfliction import MeetingDeconflictionError
from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline


class TestMeetingDeconflictionPipelineIntegration(unittest.TestCase):
    """Certifies the pre-flight deconfliction gate in pipeline execution and CLI."""

    def test_pipeline_preflight_blocks_colliding_candidate(self) -> None:
        """Verify that RawTranscriptPipeline.run() raises MeetingDeconflictionError when colliding."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        candidate_text = (
            "Reid Schenk and Amit Ayer discussed labor hour validation with Dan and Sharon. "
            "Taylor Mercer noted $12 million in capital savings across 40 sites. "
            "Matt Martinello and Raisa Weiser handle financial endorsements in the app. "
            "Chris Go/No-Go funding review on Wednesday."
        )
        metadata = {
            "project": "project_nexus",
            "date": "09/14/2026",
            "time": "10:00 AM - 10:15 AM ET",
            "meeting_name": "Nexus_Reid_NEA",
            "attendees": ["Reid Schenk", "Amit Ayer"],
            "allow_duplicate": False,
        }

        # Mock U0 execution to verify blocking before U0
        with self.assertRaises(MeetingDeconflictionError) as ctx:
            pipeline.run(
                candidate_text,
                metadata=metadata,
                output_dir="auto",
            )

        err_msg = str(ctx.exception)
        self.assertIn("[DUPLICATE_MEETING_COLLISION]", err_msg)
        self.assertIn("Nexus_Scope", err_msg)
        self.assertIn("collides with existing meeting", err_msg)

    def test_pipeline_preflight_allows_override_with_rationale(self) -> None:
        """Verify that passing allow_duplicate=True bypasses the blocking gate."""
        pipeline = RawTranscriptPipeline(provider_type="offline")
        candidate_text = (
            "Reid Schenk and Amit Ayer discussed labor hour validation with Dan and Sharon.\n"
            "Taylor Mercer noted $12 million in capital savings across 40 sites.\n"
        )
        metadata = {
            "project": "project_nexus",
            "date": "09/14/2026",
            "time": "10:00 AM - 10:15 AM ET",
            "meeting_name": "Nexus_Reid_NEA",
            "attendees": ["Reid Schenk", "Amit Ayer"],
            "allow_duplicate": True,
            "collision_override_rationale": "Intentional parallel session authorized by steering committee",
        }

        # Mock U0 with sentinel exception to prove preflight passed without blocking
        with patch.object(pipeline.u0, "execute", side_effect=RuntimeError("SentinelPastPreflight")):
            with self.assertRaises(RuntimeError) as ctx:
                pipeline.run(
                    candidate_text,
                    metadata=metadata,
                    output_dir="auto",
                )
            self.assertEqual(str(ctx.exception), "SentinelPastPreflight")

    def test_cli_parser_has_deconfliction_arguments(self) -> None:
        """Verify that the ingest-meeting parser accepts deconfliction flags."""
        parser = build_parser()
        args = parser.parse_args([
            "ingest-meeting",
            "--input", "test.txt",
            "--project", "project_nexus",
            "--meeting", "Nexus_Scope",
            "--date", "09/14/2026",
            "--allow-duplicate",
            "--collision-override-rationale", "Executive approval granted",
            "--augment-existing",
        ])
        self.assertTrue(args.allow_duplicate)
        self.assertEqual(args.collision_override_rationale, "Executive approval granted")
        self.assertTrue(args.augment_existing)

    def test_cli_handler_enforces_rationale_when_allow_duplicate(self) -> None:
        """Verify that CLI handler rejects --allow-duplicate without --collision-override-rationale."""
        args = argparse.Namespace(
            input=Path("test.txt"),
            metadata=None,
            project="project_nexus",
            meeting="Nexus_Scope",
            date="09/14/2026",
            time=None,
            attendees=None,
            classification="confidential",
            platform=None,
            transcript_cache=None,
            notes=False,
            copilot_notes=False,
            allow_duplicate=True,
            collision_override_rationale=None,
            augment_existing=False,
            model=None,
            judge_model=None,
            reasoning_effort="medium",
            provider="offline",
            output_dir="auto",
            presentation=None,
            max_judge_retries=2,
            mutate_graph=False,
            directive_id=None,
            reviewer=None,
            rationale=None,
            document=False,
            output3_only=False,
        )
        with patch("dual_pipeline.transcript_metadata_helper.scaffold_meeting_metadata", return_value={"project": "project_nexus"}):
            with self.assertRaises(ValueError) as ctx:
                _ingest_meeting(args)
            self.assertIn("--collision-override-rationale is required", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
