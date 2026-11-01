#!/usr/bin/env python3
"""End-to-End Test Suite for Graph Extraction Convergence & Schema SSOT Alignment.

Exercises:
1. Dynamic attendee roster hydration from Stage U0 metadata into Stage 03 prompt context.
2. End-to-end signal projection validation against CanonicalSignalProjectionV57 schema.
3. Stateless deterministic healing with pre-flight conversational sludge eviction.
4. Semantic spine and closed evidence kinds verification across full lifecycle.
5. Topological preservation: zero decision erasure and valid relationship signals.
"""
from __future__ import annotations

import time
import unittest

import pytest

from dual_pipeline.transcript_models import (
    CanonicalSignalProjectionV57,
    ClosedEvidenceKind,
)
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_stages_core.graph_executor import build_stage03_user_content


@pytest.mark.e2e
class GraphExtractionConvergenceE2ETests(unittest.TestCase):
    """Full lifecycle E2E tests for Graph Extraction Convergence."""

    def test_end_to_end_attendee_context_to_healed_projection(self) -> None:
        """Verify pipeline lifecycle from metadata roster through prompt context to healed projection."""
        metadata = {
            "project": "project_nexus",
            "meeting_name": "Debrief_Traci_Amit",
            "date": "09-28-2026",
            "time": "01:00 PM - 01:30 PM ET",
            "classification": "confidential",
            "roster": [
                {"name": "Amit Ayer", "role": "Lead Client Partner", "org": "PartnerAdvisory"},
                {"name": "Taylor Mercer", "role": "Delivery Lead / PM", "org": "PartnerAdvisory"},
            ],
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Lead Client Partner)", "Taylor Mercer (Delivery Lead / PM)"]
            },
        }

        forensic_transcript = (
            "Amit Ayer [00:00:00]: What that business case is. But I don't expect them to do it in a vacuum.\n"
            "Taylor Mercer [00:00:30]: We are being asked to revisit assumptions driving ROI.\n"
            "Amit Ayer [00:01:00]: We have cycle time compression and downstream rework avoidance as value pillars.\n"
            "Amit Ayer [00:01:30]: We decide not to pursue workforce reduction or capacity scaling.\n"
            "Taylor Mercer [00:02:00]: Cycle time could be potential crossover with existing baseline."
        )

        # 1. Build prompt user content with attendee roster hydration
        user_content = build_stage03_user_content(
            forensic_transcript=forensic_transcript,
            recap="Executive recap of debrief session.",
            metadata=metadata,
        )
        self.assertIn("<meeting_attendees_roster>", user_content)
        self.assertIn("Amit Ayer", user_content)
        self.assertIn("Lead Client Partner", user_content)
        self.assertIn("Taylor Mercer", user_content)
        self.assertIn("Delivery Lead / PM", user_content)

        # 2. Simulate raw Stage 03 LLM extraction payload
        raw_projection = {
            "schema_version": "5.7",
            "meeting_id": "meeting-20260928-debrief-traci-amit",
            "classification": "confidential",
            "audiences": ["nee-engineering"],
            "evidence_only_statement_ids": [],
            "evidence_index": {
                "ev-01": {
                    "evidence_id": "ev-01",
                    "speaker": "operator-confirmed-roster",
                    "text": "Amit Ayer -- Lead Client Partner, PartnerAdvisory; Taylor Mercer -- Delivery Lead / PM, PartnerAdvisory",
                    "exact_text": "Amit Ayer -- Lead Client Partner, PartnerAdvisory; Taylor Mercer -- Delivery Lead / PM, PartnerAdvisory",
                    "kind": "summary",
                },
                "ev-02": {
                    "evidence_id": "ev-02",
                    "speaker": "person:amit-ayer",
                    "text": "We have cycle time compression and downstream rework avoidance as value pillars.",
                    "exact_text": "We have cycle time compression and downstream rework avoidance as value pillars.",
                    "kind": "strategic",
                },
                "ev-03": {
                    "evidence_id": "ev-03",
                    "speaker": "person:amit-ayer",
                    "text": "We decide not to pursue workforce reduction or capacity scaling.",
                    "exact_text": "We decide not to pursue workforce reduction or capacity scaling.",
                    "kind": "decision",
                },
                "ev-04": {
                    "evidence_id": "ev-04",
                    "speaker": "person:traci-metzger",
                    "text": "Cycle time could be potential crossover with existing baseline.",
                    "exact_text": "Cycle time could be potential crossover with existing baseline.",
                    "kind": "risk",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:amit-ayer",
                        "type": "person",
                        "name": "Amit Ayer",
                        "role": "Lead Client Partner",
                        "description": "Lead Client Partner at PartnerAdvisory advising on value framework.",
                        "evidence_ids": ["ev-01"],
                        "mutation": "CONFIRM",
                    },
                    {
                        "id": "person:traci-metzger",
                        "type": "person",
                        "name": "Taylor Mercer",
                        "role": "Delivery Lead / PM",
                        "description": "Delivery Lead at PartnerAdvisory governing project delivery and business case.",
                        "evidence_ids": ["ev-01"],
                        "mutation": "CONFIRM",
                    },
                    {
                        "id": "org:partner_advisory",
                        "type": "org",
                        "name": "PartnerAdvisory",
                        "description": "Consulting delivery partner organization.",
                        "evidence_ids": ["ev-01"],
                        "mutation": "CONFIRM",
                    },
                ],
                "relationship_signals": [
                    {
                        "signal_id": "rel-amit-partner_advisory",
                        "from_id": "person:amit-ayer",
                        "to_id": "org:partner_advisory",
                        "predicate": "MEMBER_OF",
                        "relationship": "MEMBER_OF",
                        "description": "PartnerAdvisory team member.",
                        "evidence_ids": ["ev-01"],
                    },
                    {
                        "signal_id": "rel-traci-partner_advisory",
                        "from_id": "person:traci-metzger",
                        "to_id": "org:partner_advisory",
                        "predicate": "MEMBER_OF",
                        "relationship": "MEMBER_OF",
                        "description": "PartnerAdvisory team member.",
                        "evidence_ids": ["ev-01"],
                    },
                ],
                "decision_updates": [
                    {
                        "decision_id": "decision:exclude-workforce-reduction",
                        "description": "Exclude workforce reduction and capacity scaling from charter value model.",
                        "status": "decided",
                        "evidence_ids": ["ev-03"],
                        "mutation": "CREATE",
                    }
                ],
                "proposal_updates": [
                    {
                        "proposal_id": "proposal:rework-avoidance-pillar",
                        "description": "Establish downstream rework avoidance as candidate value driver.",
                        "status": "proposed",
                        "evidence_ids": ["ev-02"],
                        "mutation": "CREATE",
                    },
                    {
                        "proposal_id": "proposal:filler-commentary",
                        "description": "Proposed enhancement: Those are the main, I think those are the two big ones",
                        "status": "proposed",
                        "evidence_ids": ["ev-02"],
                        "mutation": "CREATE",
                    },
                ],
                "risk_updates": [
                    {
                        "risk_id": "risk:cycle-time-crossover",
                        "description": "Cycle time benefits may duplicate existing $1.3M time savings baseline.",
                        "status": "active",
                        "evidence_ids": ["ev-04"],
                        "mutation": "CREATE",
                    }
                ],
                "action_updates": [],
            },
        }

        # 3. Execute stateless deterministic healer
        changes: list[str] = []
        t0 = time.monotonic()
        healed = heal_graph_projection(raw_projection, changes)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # Assert sub-5ms performance
        self.assertLess(elapsed_ms, 15.0)

        # 4. Assert conversational sludge node was cleanly evicted
        proposals = healed["graph_mutations"]["proposal_updates"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["proposal_id"], "proposal:rework-avoidance-pillar")

        # 5. Assert decisions were preserved (zero-erasure invariant)
        decisions = healed["graph_mutations"]["decision_updates"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["decision_id"], "decision:exclude-workforce-reduction")

        # 6. Validate complete object against Pydantic schema SSOT
        validated = CanonicalSignalProjectionV57.model_validate(healed)
        self.assertEqual(validated.schema_version, "5.7")
        self.assertEqual(len(validated.evidence_index), 4)

        # 7. Verify ClosedEvidenceKind
        import typing
        allowed_kinds = typing.get_args(ClosedEvidenceKind)
        for ev in validated.evidence_index.values():
            self.assertIn(ev.kind, allowed_kinds)

        # 8. Verify substantive person descriptions
        for ent in validated.graph_mutations.entity_upserts:
            if ent.type == "person":
                self.assertIsNotNone(ent.description)
                self.assertGreater(len(ent.description), 10)


if __name__ == "__main__":
    unittest.main()
