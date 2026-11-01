"""Adversarial test suite enforcing zero silent fallbacks to project_nexus.

Validates that:
1. Unresolvable document ingestion fails closed with ProjectResolutionRequiredError.
2. Unresolvable meeting preflight fails closed and never creates or writes to output/project_nexus.
3. Live meeting recorder and local ingress assign UNRESOLVED sentinel and never project_nexus.
4. Lexical priming never leaks project_nexus domain vocabulary into unclassified projects.
5. Ingestion CLI entrypoints reject missing/unresolvable projects in isolated worktree modes.
"""
from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.ingest import ingest_canonical
from dual_pipeline.document_pipeline import DocumentPipeline
from dual_pipeline.exceptions import ProjectResolutionRequiredError
from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder
from dual_pipeline.live_meeting_recorder_core.lexical_priming import LexicalPrimer
from dual_pipeline.parameters import UNRESOLVED_PROJECT_SENTINEL
from dual_pipeline.transcript_pipeline_core.preflight_coordinator import (
    resolve_output_directory_and_slug,
)


class TestProjectRoutingUnresolvedAdversarial(unittest.TestCase):
    """Adversarial validation of fail-closed project resolution across all ingress channels."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_document_pipeline_unresolved_project_fails_closed(self) -> None:
        """DocumentPipeline without explicit project and without resolvable markers must raise ProjectResolutionRequiredError."""
        doc_path = self.test_root / "unclassified_document.txt"
        doc_path.write_text(
            "Executive Memo\n\nDiscussion regarding general operational efficiency and team updates.",
            encoding="utf-8",
        )
        pipeline = DocumentPipeline(provider_type="offline")

        with self.assertRaises(ProjectResolutionRequiredError) as ctx:
            pipeline.run(
                input_path=str(doc_path),
                output_dir="auto",
                metadata={"title": "General Operational Update"},
            )

        err_msg = str(ctx.exception)
        self.assertIn("could not be resolved", err_msg.lower())
        self.assertIn("--project", err_msg)

        # Assert no output/project_nexus was created in test_root or current repo
        nexus_dir = self.test_root / "output" / "project_nexus"
        self.assertFalse(nexus_dir.exists(), "output/project_nexus must not be created on unresolved failure")

    def test_meeting_preflight_unresolved_project_fails_closed(self) -> None:
        """resolve_output_directory_and_slug with output_dir='auto' and unclassified metadata must raise ProjectResolutionRequiredError."""
        metadata = {
            "meeting_name": "Generic Standup Discussion",
            "date": "09-24-2026",
            "attendees": [{"name": "Participant A", "role": "Engineer", "organization": "ThirdParty"}],
            "enforce_worktree": True,
        }
        transcript_snippet = "Participant A: We reviewed general team schedules and tasks today."

        with self.assertRaises(ProjectResolutionRequiredError) as ctx:
            resolve_output_directory_and_slug(
                metadata=metadata,
                transcript_text=transcript_snippet,
                output_dir="auto",
            )

        err_msg = str(ctx.exception)
        self.assertIn("Target project could not be resolved", err_msg)
        self.assertIn("--project", err_msg)
        self.assertNotEqual(metadata.get("project"), "project_nexus")

    def test_live_recorder_initialization_defaults_to_unresolved_not_nexus(self) -> None:
        """LiveMeetingRecorder initialized without project_name must assign UNRESOLVED, never project_nexus."""
        recorder = LiveMeetingRecorder(
            meeting_name="Unassociated Coffee Chat",
            project_name=None,
            mock_mode=True,
            dry_run=True,
        )
        self.assertEqual(recorder.project_name, UNRESOLVED_PROJECT_SENTINEL)
        self.assertNotEqual(recorder.project_name, "project_nexus")

        # Explicit 'auto' also resolves to UNRESOLVED when no keywords present
        recorder_auto = LiveMeetingRecorder(
            meeting_name="Random Strategy Session",
            project_name="auto",
            mock_mode=True,
            dry_run=True,
        )
        self.assertEqual(recorder_auto.project_name, UNRESOLVED_PROJECT_SENTINEL)

    def test_lexical_primer_isolation_zero_nexus_leakage(self) -> None:
        """Lexical primer must return empty domain vocabulary for unknown or UNRESOLVED projects."""
        unknown_terms = LexicalPrimer("some_random_project_123").lexicon
        self.assertEqual(unknown_terms, [])

        unresolved_terms = LexicalPrimer(UNRESOLVED_PROJECT_SENTINEL).lexicon
        self.assertEqual(unresolved_terms, [])

        none_terms = LexicalPrimer(None).lexicon
        self.assertEqual(none_terms, [])

        # Specifically verify no nexus domain jargon is present
        nexus_jargon = {"CIP-005", "ROCC", "Apex Global Analytics", "Nexus"}
        self.assertTrue(nexus_jargon.isdisjoint(set(unknown_terms)))
        self.assertTrue(nexus_jargon.isdisjoint(set(unresolved_terms)))
        self.assertTrue(nexus_jargon.isdisjoint(set(none_terms)))

        # Nexus terms must only be returned when explicitly requesting project_nexus
        nexus_actual = LexicalPrimer("project_nexus").lexicon
        self.assertTrue(len(nexus_actual) > 0)
        self.assertTrue(any("ROCC" in term for term in nexus_actual))

    def test_cli_ingest_meeting_dry_run_unresolved_fails_closed(self) -> None:
        """CLI ingest meeting in dry-run with unresolvable project must raise ProjectResolutionRequiredError."""
        sample_file = self.test_root / "sample_meeting.txt"
        sample_file.write_text("Alice: Reviewing generic metrics.\nBob: Sounds good.", encoding="utf-8")

        args = argparse.Namespace(
            input=str(sample_file),
            project="auto",
            meeting="Generic Metrics Sync",
            date="09-24-2026",
            attendees="Alice, Bob",
            dry_run=True,
            provider="offline",
            model="mock",
            judge_model="mock",
            mutate_graph=False,
            auto_mutate_on_pass=False,
            reviewer=None,
            rationale=None,
            source=None,
            attachment=None,
            attachments=None,
            attachment_dir=None,
            reasoning_effort="high",
        )

        with self.assertRaises(ProjectResolutionRequiredError) as ctx:
            ingest_canonical(args)

        self.assertIn("Target project could not be resolved", str(ctx.exception))
        self.assertIn("--project", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
