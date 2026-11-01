"""
tests/adversarial/test_meeting_archetype_routing_adversarial.py

Adversarial and edge-case verification for Unified Meeting Archetype SSOT routing:
Covers mandatory edge-case categories per e2e-and-edge-case-coverage.md:
1. Boundary payloads: Empty titles, empty transcripts, whitespace strings, empty metadata.
2. Malformed inputs: Unsupported enums, corrupt types, legacy enum names, mixed-case whitespace.
3. Concurrency & race conditions: Multi-threaded concurrent detection and healing stress.
4. Invariant bypass attempts: Self-reported candidate archetype downgrades blocked.
5. Rollback & graceful recovery: Non-destructive fallback under corrupted metadata.
"""
from __future__ import annotations

import concurrent.futures
import json
import unittest

from dual_pipeline.llm_judge_core.judges.graph import _detect_meeting_archetype
from dual_pipeline.llm_judge_core.judges.recap import ExecutiveRecapJudge
from dual_pipeline.prompts import (
    OUTPUT1_CONTRACT,
    OUTPUT2_CONTRACT,
    OUTPUT3_CONTRACT,
    MeetingArchetype,
)
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_qa_core.graph_ontology_healer import heal_ontology_and_predicates


class TestMeetingArchetypeRoutingAdversarial(unittest.TestCase):
    """Adversarial stress test suite for archetype SSOT invariants."""

    # -------------------------------------------------------------------------
    # 1. Boundary Payloads
    # -------------------------------------------------------------------------
    def test_boundary_empty_and_whitespace_inputs(self) -> None:
        """Verifies boundary payloads (empty strings, whitespace) fallback cleanly to GENERAL."""
        boundary_cases = [
            ("", ""),
            ("   ", "   \n\t  "),
            ("...", "???"),
            (None, None),
        ]
        for title, text in boundary_cases:
            arch = OUTPUT1_CONTRACT.detect_archetype(title=title, text=text)
            self.assertEqual(arch, MeetingArchetype.GENERAL)
            arch2 = OUTPUT2_CONTRACT.detect_archetype(title=title, text=text)
            self.assertEqual(arch2, MeetingArchetype.GENERAL)
            arch3 = OUTPUT3_CONTRACT.detect_archetype(title=title, text=text)
            self.assertEqual(arch3, MeetingArchetype.GENERAL)

    def test_boundary_empty_metadata_in_judges(self) -> None:
        """Verifies judges handle empty or None metadata without throwing exceptions."""
        judge = ExecutiveRecapJudge()
        # Empty text and empty metadata
        res = judge.evaluate(f2_text="", f1_text="", metadata={}, mock=True)
        self.assertIn("meeting_archetype", res)
        self.assertEqual(res["meeting_archetype"], MeetingArchetype.GENERAL.value)

        # Null metadata in graph judge detector
        detected = _detect_meeting_archetype(f3_parsed={}, f1_text="", explicit=None, metadata=None)
        self.assertEqual(detected, MeetingArchetype.GENERAL.value)

    # -------------------------------------------------------------------------
    # 2. Malformed Inputs
    # -------------------------------------------------------------------------
    def test_malformed_unsupported_archetype_string(self) -> None:
        """Verifies invalid or unknown archetype strings cleanly map to GENERAL."""
        malformed_strings = [
            "UNKNOWN_RANDOM_TYPE",
            "COFFEE_CHAT",
            "12345",
            "__NULL__",
        ]
        for s in malformed_strings:
            arch = MeetingArchetype.from_string(s)
            self.assertEqual(arch, MeetingArchetype.GENERAL)

    def test_legacy_enum_normalization(self) -> None:
        """Verifies legacy archetype enums resolve gracefully to canonical equivalents."""
        legacy_mappings = [
            ("INTERNAL_TECHNICAL_SYNC", MeetingArchetype.ARCHITECTURE_DEEP_DIVE),
            ("EXECUTIVE_DECISION_GATE", MeetingArchetype.GOVERNANCE_REVIEW),
            ("RELEASE_PLANNING", MeetingArchetype.TACTICAL_ALIGNMENT),
            ("RETROSPECTIVE", MeetingArchetype.LESSONS_LEARNED),
        ]
        for legacy_str, expected in legacy_mappings:
            resolved = MeetingArchetype.from_string(legacy_str)
            self.assertEqual(resolved, expected)

    def test_mixed_case_and_padded_whitespace(self) -> None:
        """Verifies case-insensitive and whitespace-tolerant parsing."""
        cases = [
            ("  architecture_deep_dive \n", MeetingArchetype.ARCHITECTURE_DEEP_DIVE),
            ("Strategic_Discovery", MeetingArchetype.STRATEGIC_DISCOVERY),
            ("tactical_alignment", MeetingArchetype.TACTICAL_ALIGNMENT),
            ("  GOVERNANCE_REVIEW  ", MeetingArchetype.GOVERNANCE_REVIEW),
            ("lessons_learned", MeetingArchetype.LESSONS_LEARNED),
        ]
        for s, expected in cases:
            self.assertEqual(MeetingArchetype.from_string(s), expected)

    # -------------------------------------------------------------------------
    # 3. Concurrency & Race Conditions
    # -------------------------------------------------------------------------
    def test_concurrent_detection_stress_under_multi_threading(self) -> None:
        """Verifies 16 concurrent workers invoking detect_archetype simultaneously suffer no corruption."""
        scenarios = [
            ("Client_Architecture_Sync", "Reviewing AWS schema and streaming state.", MeetingArchetype.ARCHITECTURE_DEEP_DIVE),
            ("Daily_Standup", "Reviewing sprint tasks and Jira blockers.", MeetingArchetype.TACTICAL_ALIGNMENT),
            ("Roadmap_Alignment", "Executive strategic portfolio discovery.", MeetingArchetype.STRATEGIC_DISCOVERY),
            ("Steering_Board", "Governance decision gate review.", MeetingArchetype.GOVERNANCE_REVIEW),
            ("Release_Postmortem", "Retrospective and lessons learned.", MeetingArchetype.LESSONS_LEARNED),
        ] * 10  # 50 total executions

        def _worker(item: tuple) -> bool:
            title, text, expected = item
            res1 = OUTPUT1_CONTRACT.detect_archetype(title=title, text=text)
            res2 = OUTPUT2_CONTRACT.detect_archetype(title=title, text=text)
            res3 = OUTPUT3_CONTRACT.detect_archetype(title=title, text=text)
            return res1 == expected and res2 == expected and res3 == expected

        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(_worker, scenarios))

        self.assertTrue(all(results))
        self.assertEqual(len(results), 50)

    # -------------------------------------------------------------------------
    # 4. Invariant Bypass Attempts
    # -------------------------------------------------------------------------
    def test_adversarial_candidate_archetype_spoofing_rejected(self) -> None:
        """Verifies candidate Output 03 mutations cannot spoof or downgrade their archetype in judge evaluation."""
        metadata = {
            "meeting_name": "Core_Architecture_Deep_Dive_APEX",
            "archetype": MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value,
        }
        # Adversarial candidate payload attempts to downgrade its own archetype to TACTICAL_ALIGNMENT
        # in an attempt to relax evaluation rubrics and dimension floors
        f3_spoofed = {
            "meeting_metadata": {"meeting_archetype": "TACTICAL_ALIGNMENT"},
            "graph_mutations": {"meeting_archetype": "TACTICAL_ALIGNMENT"},
        }
        transcript = "[00:00:10] Technical architecture review of streaming engine."

        # The judge must strictly ignore candidate f3 mutations and enforce metadata/transcript SSOT
        resolved = _detect_meeting_archetype(
            f3_parsed=f3_spoofed,
            f1_text=transcript,
            explicit=None,
            metadata=metadata,
        )
        self.assertEqual(
            resolved,
            MeetingArchetype.ARCHITECTURE_DEEP_DIVE.value,
            "Adversarial archetype spoofing succeeded in corrupting judge evaluation!",
        )

    # -------------------------------------------------------------------------
    # 5. Rollback & Recovery
    # -------------------------------------------------------------------------
    def test_healer_graceful_recovery_when_metadata_archetype_missing(self) -> None:
        """Verifies healers fall back cleanly to detect_archetype when metadata lacks archetype."""
        payload = {
            "graph_mutations": {
                "relationship_signals": [],
                "entity_upserts": [],
            },
            "meeting_archetype": "GENERAL",
        }
        meta_no_arch = {
            "meeting_name": "Sprint_Daily_Scrum",
            "source_text": "[00:00:05] Yesterday I completed the auth service bugfix, today working on API tests.",
        }
        from dual_pipeline.transcript_qa_core.graph_ontology_healer import _heal_workstreams_and_archetype
        changes = []
        _heal_workstreams_and_archetype(payload, [], [], changes, metadata=meta_no_arch)
        self.assertEqual(payload.get("meeting_archetype"), MeetingArchetype.TACTICAL_ALIGNMENT.value)

        # Also verify deterministic graph_healer fallback
        h_changes = []
        healed_data = heal_graph_projection(payload, h_changes, metadata=meta_no_arch)
        self.assertEqual(healed_data.get("meeting_archetype"), MeetingArchetype.TACTICAL_ALIGNMENT.value)


if __name__ == "__main__":
    unittest.main()
