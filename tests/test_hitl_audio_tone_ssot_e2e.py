"""
tests/test_hitl_audio_tone_ssot_e2e.py

End-to-end integration test exercising the full enhanced HITL lifecycle:
1. Audio / raw source input with default speaker diarization ON.
2. Diarization toggle defense verification.
3. Mandatory attendee confirmation HITL gate for audio/raw sources.
4. Word-level confidence routing & speaker pattern attribution.
5. Paralinguistic tone arc in Output 2 and tone signals in Output 3.
6. GraphDB stakeholder SSOT resolution and cross-output consistency validation.
7. Universal Write Gateway (UWG) artifact deployment.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.speaker_attribution_core import (
    DiarizationLane,
    SpeakerPatternEngine,
    WordConfidenceRouter,
)
from dual_pipeline.tone_analysis_core import (
    ParalinguisticToneAnalyzer,
)
from dual_pipeline.transcript_pipeline_core.stakeholder_validator import (
    StakeholderResolutionValidator,
    sync_stakeholder_to_graph,
)
from dual_pipeline.transcript_pipeline_core.uwg_deployer import deploy_uwg_artifacts
from dual_pipeline.transcript_stages_core.attendee_gate import (
    record_attendee_confirmation,
    verify_mandatory_attendee_confirmation,
)
from dual_pipeline.transcript_stages_core.hitl_guard import (
    validate_diarization_toggle,
)
from dual_pipeline.transcript_stages_core.models import HITLRequiredError
from dual_pipeline.transcript_stages_core.recap_executor import _post_process_recap
from storage.connection import create_sqlite_conn


class TestHITLAudioToneSSOTE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.repo_root = self.work_dir
        self.db_path = self.work_dir / "governed_graph.db"
        self.output_dir = self.work_dir / "output" / "project_alpha" / "09-24-2026" / "SSOT_Architecture_Review"

        # Initialize test GraphDB with verified stakeholder nodes
        conn = create_sqlite_conn(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
        conn.close()

        # Seed verified stakeholders
        sync_stakeholder_to_graph("Cameron Mercer", "Executive Sponsor", "Apex Global Enterprises", db_path=self.db_path)
        sync_stakeholder_to_graph("Amit Ayer", "Lead Architect", "PartnerAdvisory", db_path=self.db_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_full_hitl_audio_tone_ssot_e2e_lifecycle(self) -> None:
        """Exercises complete end-to-end flow from audio metadata to UWG deployment."""
        # Step 1: Diarization toggle defense check (Default ON, or authorized reason code)
        meta_audio = {
            "source_type": "audio_recording",
            "meeting_name": "SSOT_Architecture_Review",
            "date": "09/24/2026",
            "speaker_diarization": True,
            "participants": ["Cameron Mercer", "Amit Ayer"],
        }
        # Verify diarization toggle is allowed
        validated_meta = validate_diarization_toggle(meta_audio)
        self.assertTrue(validated_meta.get("speaker_diarization"))

        # Step 2: Mandatory Attendee Confirmation Gate
        meta_audio["_from_audio_transcription"] = True
        meta_audio["roster"] = ["Cameron Mercer", "Amit Ayer"]

        # Unconfirmed audio must trigger HITLRequiredError
        with self.assertRaises(HITLRequiredError) as exc_ctx:
            verify_mandatory_attendee_confirmation(meta_audio)
        self.assertEqual(exc_ctx.exception.hitl_type, "attendee_confirmation")
        self.assertIn("Mandatory Stage U0 Attendee Confirmation Gate", str(exc_ctx.exception))

        # Operator confirms attendees via HITL interaction
        confirmed_attendees = ["Cameron Mercer", "Amit Ayer"]
        meta_audio = record_attendee_confirmation(meta_audio, confirmed_attendees, repo_root=self.repo_root)
        # Now confirmed: should pass without exception
        verify_mandatory_attendee_confirmation(meta_audio)

        # Step 3: Word-Level Confidence Routing & Speaker Pattern Engine
        router = WordConfidenceRouter()
        turn_words = [
            {"word": "We", "confidence": 0.95},
            {"word": "agree", "confidence": 0.94},
            {"word": "to", "confidence": 0.92},
            {"word": "approve", "confidence": 0.96},
            {"word": "the", "confidence": 0.93},
            {"word": "architecture", "confidence": 0.91},
        ]
        route = router.route_segment(turn_words, segment_id="seg_0", speaker="Cameron Mercer")
        self.assertEqual(route.lane, DiarizationLane.STRAIGHT_THROUGH)
        self.assertGreaterEqual(route.mean_confidence, 0.90)

        # Pattern engine detects self/addressee cues
        pattern_engine = SpeakerPatternEngine(known_participants=["Cameron Mercer", "Amit Ayer"])
        raw_turns = [
            {"text": "Hello, this is Stefan. Amit, do you agree with the write-ahead-log storage decision?"},
            {"text": "Yes, fully support partnering on the zero-lock storage layer."},
        ]
        pattern_map = pattern_engine.analyze_and_pattern_turns(raw_turns)
        self.assertGreaterEqual(len(pattern_map.anchors), 1)
        self.assertEqual(pattern_map.assigned_turns[0]["speaker"], "Cameron Mercer")

        # Step 4: Paralinguistic Tone Analysis & Output 2 / Output 3 Enrichment
        turns = [
            {"speaker": "Cameron Mercer", "text": "Stefan here. Looking back, we must ensure high availability.", "start": 0.0, "end": 6.0},
            {"speaker": "Amit Ayer", "text": "Great idea, fully support partnering on the zero-lock storage layer.", "start": 8.0, "end": 14.0},
            {"speaker": "Cameron Mercer", "text": "The decision is approved, we commit to moving forward.", "start": 16.0, "end": 22.0},
        ]
        tone_analyzer = ParalinguisticToneAnalyzer()
        tone_arc = tone_analyzer.analyze_turns(turns)
        self.assertEqual(tone_arc.decisiveness_count, 1)
        self.assertGreaterEqual(tone_arc.engagement_score, 0.8)

        # Generate Output 1 (Forensic transcript)
        output_1_text = (
            "# SSOT Architecture Review -- HITL-Resolved Transcript\n\n"
            "**Date:** 09/24/2026\n"
            "**Participants:** Cameron Mercer (Apex Global Enterprises) -- Executive Sponsor; Amit Ayer (PartnerAdvisory) -- Lead Architect\n\n"
            "### [00:00:00]\n\n"
            "**Cameron Mercer (Apex Global Enterprises):** Stefan here. Looking back, we must ensure high availability.\n\n"
            "**Amit Ayer (PartnerAdvisory):** Great idea, fully support partnering on the zero-lock storage layer.\n\n"
            "**Cameron Mercer (Apex Global Enterprises):** The decision is approved, we commit to moving forward.\n"
        )

        # Generate Output 2 (Executive Recap) with post-processing tone arc injection
        raw_recap = (
            "# Subject: SSOT Architecture Review -- Executive Recap | 09/24/2026\n\n"
            "**Date:** 09/24/2026\n"
            "**Time:** 09:00 AM - 09:30 AM ET\n"
            "**Attendees:** Cameron Mercer (Apex Global Enterprises, Executive Sponsor); Amit Ayer (PartnerAdvisory, Lead Architect)\n\n"
            "## Meeting Objective & Executive Summary\n"
            "Review storage architecture and validate zero contention.\n\n"
            "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
            "# Cameron Mercer - Apex Global Enterprises, Executive Sponsor\n"
            "- **Strategy**: Validated storage roadmap.\n\n"
            "# Amit Ayer - PartnerAdvisory, Lead Architect\n"
            "- **Architecture**: Implemented zero contention WAL storage.\n"
        )
        output_2_text = _post_process_recap(raw_recap, meta_audio, forbid_sections=[], forensic_transcript=output_1_text)
        self.assertIn("## Meeting Tone Arc & Strategic Dynamics", output_2_text)

        # Generate Output 3 (Canonical Signal Projection)
        output_3_dict = {
            "schema_version": "5.7.0",
            "meeting_name": "SSOT_Architecture_Review",
            "date": "09/24/2026",
            "entities": [
                {"entity_id": "person:stefan_marceau", "name": "Cameron Mercer", "role": "Executive Sponsor", "organization": "Apex Global Enterprises"},
                {"entity_id": "person:amit_ayer", "name": "Amit Ayer", "role": "Lead Architect", "organization": "PartnerAdvisory"},
            ],
            "signals": [
                {"signal_type": "decision", "summary": "Approved zero-lock storage layer", "confidence": 0.98}
            ],
        }

        # Step 5: GraphDB Stakeholder SSOT Validation
        validator = StakeholderResolutionValidator(repo_root=self.repo_root)
        val_result = validator.validate_deliverables(
            output_1_text=output_1_text,
            output_2_text=output_2_text,
            output_3_payload=output_3_dict,
            fail_closed=True,
        )
        self.assertTrue(val_result.is_consistent)
        self.assertEqual(len(val_result.discrepancies), 0)

        # Step 6: Universal Write Gateway (UWG) Atomic Deployment
        artifacts = {
            "01_hitl_resolved_transcript.md": output_1_text,
            "02_executive_recap.md": output_2_text,
            "03_canonical_signal_projection.json": json.dumps(output_3_dict, indent=2),
        }
        metadata = {
            "project": "project_alpha",
            "date": "09/24/2026",
            "date_filename": "09-24-2026",
            "time": "09:00 AM - 09:30 AM ET",
            "time_filename": "0900AM_0930AM_ET",
            "meeting_name": "SSOT_Architecture_Review",
            "standardized_slug": "SSOT_Architecture_Review",
            "turns": turns,
        }

        deploy_res = deploy_uwg_artifacts(
            artifacts=artifacts,
            metadata=metadata,
            output_dir=str(self.output_dir),
        )

        # Verify deployment outputs exist on disk
        f1_path = Path(deploy_res["01_transcript"])
        f2_path = Path(deploy_res["02_recap"])
        f3_path = Path(deploy_res["03_graph"])

        self.assertTrue(f1_path.exists())
        self.assertTrue(f2_path.exists())
        self.assertTrue(f3_path.exists())

        # Verify tone arc in deployed Output 2 and tone signals in deployed Output 3
        deployed_o2 = f2_path.read_text()
        self.assertIn("## Meeting Tone Arc & Strategic Dynamics", deployed_o2)

        deployed_o3 = json.loads(f3_path.read_text())
        self.assertIn("tone_signals", deployed_o3)
        self.assertGreater(len(deployed_o3["tone_signals"]), 0)


if __name__ == "__main__":
    unittest.main()
