"""
tests/end_to_end/test_meeting_archetype_routing_e2e.py

End-to-End verification of Unified Meeting Archetype SSOT routing and judge alignment:
1. Multi-archetype detection across all 5 canonical archetypes (including client technical sessions).
2. Thread-safe parallel Stage 02 / Stage 03 execution with zero archetype divergence.
3. Cross-stage agreement across metadata.json, Output 02 evaluation, Output 03 JSON, and Output 03 evaluation.
4. Tone Arc preservation for STRATEGIC_DISCOVERY and suppression for ARCHITECTURE_DEEP_DIVE.
"""
from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
import unittest

from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
from dual_pipeline.llm_judge_core.judges.graph import _detect_meeting_archetype
from dual_pipeline.prompts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
)
from dual_pipeline.transcript_metadata_helper import scaffold_meeting_metadata
from dual_pipeline.transcript_stages_core.recap_executor import _process_meeting_tone_arc
from dual_pipeline.transcript_stages_core.graph_executor import post_process_stage03_projection


class TestMeetingArchetypeRoutingE2E(unittest.TestCase):
    """End-to-end verification of archetype propagation and judge consensus."""

    def test_all_five_archetypes_end_to_end_detection(self) -> None:
        """Verifies that all 5 canonical archetypes resolve cleanly from title + text."""
        test_cases = [
            (
                "APEX_Apex Global_Core_Architecture_Deep_Dive",
                "[00:00:10] Alice: Reviewing AWS lambda microservice extraction logic and DynamoDB schemas.",
                MeetingArchetype.ARCHITECTURE_DEEP_DIVE,
            ),
            (
                "Weekly_Sprint_Standup_and_Blockers",
                "[00:00:05] Bob: Today's standup covers yesterday's PRs, backlog tasks, and blocked sprint cards.",
                MeetingArchetype.TACTICAL_ALIGNMENT,
            ),
            (
                "Executive_Strategic_Roadmap_and_Partnerships",
                "[00:00:15] Carol: Discussing enterprise AI value hypothesis and executive stakeholder adoption.",
                MeetingArchetype.STRATEGIC_DISCOVERY,
            ),
            (
                "Steering_Committee_Governance_Review",
                "[00:00:20] Dave: The governance board is reviewing project stage gates and audit compliance.",
                MeetingArchetype.GOVERNANCE_REVIEW,
            ),
            (
                "Quarterly_Release_Retrospective_and_Lessons_Learned",
                "[00:00:30] Eve: Welcome to our post-mortem and retrospective to document what went wrong and lessons learned.",
                MeetingArchetype.LESSONS_LEARNED,
            ),
        ]
        for title, text, expected_arch in test_cases:
            arch_1 = OUTPUT1_CONTRACT.detect_archetype(title=title, text=text)
            arch_2 = OUTPUT2_CONTRACT.detect_archetype(title=title, text=text)
            arch_3 = OUTPUT3_CONTRACT.detect_archetype(title=title, text=text)
            self.assertEqual(arch_1, expected_arch, f"Output 1 failed for {title}")
            self.assertEqual(arch_2, expected_arch, f"Output 2 failed for {title}")
            self.assertEqual(arch_3, expected_arch, f"Output 3 failed for {title}")

    def test_tone_arc_preservation_for_strategic_and_suppression_for_architecture(self) -> None:
        """Verifies Tone Arc is preserved on STRATEGIC_DISCOVERY and suppressed on ARCHITECTURE_DEEP_DIVE."""
        recap_with_tone = (
            "# Subject: Meeting Recap\n\n"
            "## Meeting Objective\nAnalyze strategic alignment.\n\n"
            "## Meeting Tone Arc\n- 00:00 - Collaborative opening\n- 15:00 - Constructive debate\n\n"
            "## Key Discussion Points\nStrategic overview."
        )

        # 1. Architecture deep dive -> Tone Arc should be stripped
        meta_arch = {"archetype": MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value}
        healed_arch = _process_meeting_tone_arc(recap_with_tone, meta_arch, forbid_sections=[])
        self.assertNotIn("Meeting Tone Arc", healed_arch)

        # 2. Strategic discovery -> Tone Arc should be retained
        meta_strat = {"archetype": MeetingArchetype.STRATEGIC_DISCOVERY.value}
        healed_strat = _process_meeting_tone_arc(recap_with_tone, meta_strat, forbid_sections=[])
        self.assertIn("Meeting Tone Arc", healed_strat)

    def test_parallel_thread_safety_stage02_and_stage03_consensus(self) -> None:
        """Verifies concurrent Stage 02 and Stage 03 execution maintains identical archetype SSOT."""
        raw_transcript = (
            "Meeting: APEX_Architecture_Review\n"
            "Date: 10/14/2026\n"
            "[00:01:00] Alice: We are profiling the data ingest throughput and schema constraints.\n"
        )
        meta = {
            "meeting_name": "APEX_Architecture_Review",
            "date": "10/14/2026",
            "source_text": raw_transcript,
        }

        # Resolve archetype upstream
        detected_arch = OUTPUT1_CONTRACT.detect_archetype(
            title=meta["meeting_name"], text=raw_transcript
        )
        meta["archetype"] = detected_arch.value
        meta["meeting_type"] = detected_arch.value

        def run_stage_02_eval(m: dict) -> str:
            recap_text = (
                f"# Subject: {m['meeting_name']} | 10/14/2026\n\n"
                "## Meeting Objective\nEvaluate architecture.\n\n"
                "## Action & Governance Ledger\n| # | Type | Description | Owner | Target Timing | Status |\n"
                "| 1 | Action | Test worker pool | Alice | 10/20/2026 | Pending |\n\n"
                "# Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n\n"
                "### Alice (Architect, PartnerAdvisory)\n- Technical design perspective."
            )
            judge = ExecutiveRecapJudge()
            res = judge.evaluate(f2_text=recap_text, f1_text=raw_transcript, metadata=m, mock=True)
            return res.get("meeting_archetype", "")

        def run_stage_03_eval(m: dict) -> str:
            return _detect_meeting_archetype(
                f3_parsed={}, f1_text=raw_transcript, explicit=None, metadata=m
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            fut2 = executor.submit(run_stage_02_eval, dict(meta))
            fut3 = executor.submit(run_stage_03_eval, dict(meta))
            res2 = fut2.result()
            res3 = fut3.result()

        self.assertEqual(res2, MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value)
        self.assertEqual(res3, MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value)
        self.assertEqual(res2, res3)

    def test_stage03_projection_propagates_canonical_archetype(self) -> None:
        """Verifies post_process_stage03_projection stamps meeting_metadata.meeting_archetype."""
        mock_raw_json = json.dumps({
            "meeting_id": "test_meeting_001",
            "meeting_metadata": {"meeting_name": "Sprint_Sync_1014"},
            "graph_mutations": {"relationship_signals": [], "entity_upserts": [], "action_updates": []},
            "proposal_updates": [],
            "risk_updates": [],
            "evidence_index": {},
        })
        metadata = {
            "meeting_name": "Sprint_Sync_1014",
            "archetype": MeetingArchetype.TACTICAL_ALIGNMENT.value,
            "meeting_type": MeetingArchetype.TACTICAL_ALIGNMENT.value,
        }
        processed = post_process_stage03_projection(
            mock_raw_json, metadata=metadata, forensic_transcript="[00:00:10] Standup meeting."
        )
        parsed = json.loads(processed)
        self.assertEqual(
            parsed.get("meeting_metadata", {}).get("meeting_archetype"),
            MeetingArchetype.TACTICAL_ALIGNMENT.value,
        )


if __name__ == "__main__":
    unittest.main()
