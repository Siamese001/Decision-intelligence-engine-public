#!/usr/bin/env python3
"""E2E test for shared transcript collateral guard and LESSONS_LEARNED entity preservation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.attachment_core.detector import discover_attachments
from dual_pipeline.transcript_stages_core.graph_post_extractor import (
    _enforce_mutation_budget,
    _materialize_missing_endpoints_and_evidence,
)


class TestLessonsLearnedCollateralGuardE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="collateral_guard_e2e_")
        self.root = Path(self.tmp.name)

        # Structure: inputs/transcripts/
        self.transcripts_dir = self.root / "inputs" / "transcripts"
        self.transcripts_dir.mkdir(parents=True, exist_ok=True)

        # Target meeting transcript
        self.target_transcript = (
            self.transcripts_dir / "conduit_lessons_learned_michael_partner_advisory_08-31-2026.txt"
        )
        self.target_transcript.write_text(
            "Michael Maione, Siara Montgelas, and Mike Homorody debriefed the P&C Conduit pause with Lori Bartlett.",
            encoding="utf-8",
        )

        # Unrelated sibling files in the shared transcripts directory
        (self.transcripts_dir / "dociq_q4_scope_09-23-2026.txt").write_text("DocIQ notes", encoding="utf-8")
        (self.transcripts_dir / "google_partner_advisory_apex_global_weekly_07-24-2026.txt").write_text("Weekly notes", encoding="utf-8")
        (self.transcripts_dir / "jarvis_kickoff_metadata.json").write_text("{}", encoding="utf-8")

        # Meeting output dir with authentic referenced_artifacts
        self.meeting_out = self.root / "output" / "conduit" / "08-31-2026" / "Conduit_Lessons_Learned_Michael_PartnerAdvisory"
        self.meeting_ref = self.meeting_out / "referenced_artifacts"
        self.meeting_ref.mkdir(parents=True, exist_ok=True)
        self.authentic_artifact = self.meeting_ref / "conduit_access_blockers.pdf"
        self.authentic_artifact.write_bytes(b"%PDF-1.4 test")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_e2e_shared_directory_collateral_poisoning_blocked(self) -> None:
        """discover_attachments on shared inputs/transcripts/ attaches ZERO unrelated sibling files."""
        candidates = discover_attachments(transcript_path=self.target_transcript)
        # Must NOT sweep sibling files from inputs/transcripts/
        candidate_names = {c.name for c in candidates}
        self.assertNotIn("dociq_q4_scope_09-23-2026.txt", candidate_names)
        self.assertNotIn("google_partner_advisory_apex_global_weekly_07-24-2026.txt", candidate_names)
        self.assertNotIn("jarvis_kickoff_metadata.json", candidate_names)
        self.assertEqual(len(candidates), 0)

    def test_e2e_authentic_meeting_collateral_still_discovered(self) -> None:
        """Legitimate referenced_artifacts in meeting output dir are correctly discovered."""
        candidates = discover_attachments(
            meeting_output_dir=self.meeting_out,
            transcript_path=self.target_transcript,
        )
        candidate_names = {c.name for c in candidates}
        self.assertIn("conduit_access_blockers.pdf", candidate_names)
        # Sibling transcript files still blocked
        self.assertNotIn("dociq_q4_scope_09-23-2026.txt", candidate_names)

    def test_e2e_lessons_learned_entity_and_debrief_preservation(self) -> None:
        """Stage 03 LESSONS_LEARNED mutations preserve Lori Bartlett and Conduit pause entities."""
        mutations = {
            "meeting_archetype": "LESSONS_LEARNED",
            "entity_upserts": [
                {
                    "id": "person:lori_bartlett",
                    "type": "PERSON",
                    "name": "Lori Bartlett",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev_1"],
                },
                {
                    "id": "person:michael_maione",
                    "type": "PERSON",
                    "name": "Michael Maione",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev_1"],
                },
                {
                    "id": "workstream:pd_conduit",
                    "type": "WORKSTREAM",
                    "name": "PD Conduit",
                    "mutation": "CREATE",
                    "evidence_ids": ["ev_2"],
                },
            ],
            "relationship_signals": [],
            "decision_updates": [
                {
                    "id": "dec_conduit_pause",
                    "description": "Paused P&C Conduit active development due to Elements system access blockers",
                    "status": "APPROVED",
                    "evidence_ids": ["ev_1", "ev_2"],
                }
            ],
        }

        metadata = {"meeting_archetype": "LESSONS_LEARNED", "meeting_title": "Conduit Lessons Learned"}
        ev_index = {"ev_1": {"quote": "Lori debrief"}, "ev_2": {"quote": "Conduit pause"}}

        # Run post-extractor budget enforcement
        _enforce_mutation_budget(
            mutations=mutations,
            max_budget=30,
            is_document=False,
            ev_index=ev_index,
            metadata=metadata,
            text="Lori Bartlett Conduit pause debrief",
        )

        _materialize_missing_endpoints_and_evidence(mutations, ev_index)

        # Assert entities are NOT wiped out
        surviving_ids = {e["id"] for e in mutations["entity_upserts"]}
        self.assertIn("person:lori_bartlett", surviving_ids)
        self.assertIn("person:michael_maione", surviving_ids)
        self.assertIn("workstream:pd_conduit", surviving_ids)
        self.assertEqual(len(mutations["decision_updates"]), 1)


if __name__ == "__main__":
    unittest.main()
