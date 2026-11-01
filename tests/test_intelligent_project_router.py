"""Comprehensive tests for intelligent project routing, partner alliances resolution,
and elimination of hardcoded project_nexus fallbacks.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.document_pipeline_core.metadata_helper import (
    scaffold_document_metadata,
)
from dual_pipeline.exceptions import ProjectResolutionRequiredError
from dual_pipeline.parameters import resolve_project_name
from dual_pipeline.query_router_core.project_router import (
    _resolve_project_from_roster,
    determine_project_directory,
)


class IntelligentProjectRouterTests(unittest.TestCase):
    """Test suite for intelligent multi-tenant project routing and partner alliances."""

    def test_roster_domain_routing_google_and_partner_advisory(self) -> None:
        """When attendees belong to Google and PartnerAdvisory, resolve to partner_alliances."""
        metadata = {
            "meeting_name": "PartnerAdvisory / Google - apex_global Alignment",
            "attendees": [
                {"name": "Alex Brannan", "org": "Google"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory"},
                {"name": "Dianne Eldridge", "org": "Google"},
                {"name": "Drew Oakley", "org": "PartnerAdvisory"},
            ],
        }
        res = determine_project_directory(metadata)
        self.assertEqual(res, "partner_alliances")

    def test_roster_domain_routing_from_email_strings(self) -> None:
        """When attendee strings contain @google.com and @consulting.example.com, route to partner_alliances."""
        metadata = {
            "meeting_name": "Weekly Architecture Sync",
            "attendees": ["alex@google.com", "amit@consulting.example.com"],
        }
        res = determine_project_directory(metadata)
        self.assertEqual(res, "partner_alliances")

    def test_partner_alliances_aliases(self) -> None:
        """Verify alias resolution for partner alliances variants."""
        aliases = [
            "partner gcp",
            "partner_gcp",
            "partner google",
            "partner_google",
            "gcp partner",
            "partner sync",
            "alliances",
            "partner_alliances",
        ]
        for alias in aliases:
            self.assertEqual(
                resolve_project_name(alias),
                "partner_alliances",
                f"Alias '{alias}' did not resolve to 'partner_alliances'",
            )

    def test_dialogue_mention_of_nexus_does_not_misroute_partner_meeting(self) -> None:
        """Passing mention of 'nexus' in transcript snippet should not hijack partner routing."""
        metadata = {
            "meeting_name": "GCP PartnerAdvisory Cadence",
            "attendees": [
                {"name": "Alex Brannan", "org": "Google"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory"},
            ],
        }
        snippet = "We also talked briefly about the apex_global client and Nexus project status."
        res = determine_project_directory(metadata, transcript_snippet=snippet)
        self.assertEqual(res, "partner_alliances")

    def test_unknown_meeting_does_not_default_to_project_nexus(self) -> None:
        """Unknown meeting with no project indicators must fail-closed to None, NOT project_nexus."""
        metadata = {
            "meeting_name": "Random Unrelated Standup",
            "attendees": [{"name": "John Doe", "org": "Acme"}],
        }
        res = determine_project_directory(metadata)
        self.assertIsNone(res)
        self.assertNotEqual(res, "project_nexus")

    def test_client_presence_preempts_pure_partner_alliances(self) -> None:
        """If apex_global or APEX is an explicit client attendee, do not classify as purely partner_alliances."""
        metadata = {
            "meeting_name": "apex_global Nexus Core Sync",
            "roster": [
                {"name": "Alex Brannan", "org": "Google"},
                {"name": "Amit Ayer", "org": "PartnerAdvisory"},
                {"name": "Client Lead", "org": "apex_global"},
            ],
        }
        # _resolve_project_from_roster should return None so client-specific routing takes over
        res = _resolve_project_from_roster(metadata, ["partner_alliances", "project_nexus"])
        self.assertIsNone(res)

    def test_scaffold_document_metadata_fails_closed_without_defaulting_to_nexus(self) -> None:
        """When document project cannot be resolved, scaffold_document_metadata must raise ProjectResolutionRequiredError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "generic_memo.md"
            fpath.write_text("# General Operational Update\n\nRoutine update with no project markers.", encoding="utf-8")
            with self.assertRaises(ProjectResolutionRequiredError):
                scaffold_document_metadata(fpath)

    def test_scaffold_document_metadata_resolves_project_from_content(self) -> None:
        """When document content contains project markers, scaffold_document_metadata correctly classifies it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "lens_architecture_memo.md"
            fpath.write_text("# Project Lens Architecture Memo\n\nTechnical details for project_lens.", encoding="utf-8")
            meta = scaffold_document_metadata(fpath)
            self.assertEqual(meta["project"], "project_lens")


if __name__ == "__main__":
    unittest.main()
