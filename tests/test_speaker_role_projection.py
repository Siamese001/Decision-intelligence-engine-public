#!/usr/bin/env python3
"""
tests/test_speaker_role_projection.py

Unit test suite for Wave 2: Carrying required role facts through Output 03 and normalization.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    ResolutionStatus,
    SpeakerRoleResolution,
    create_speaker_role_resolution,
)
from dual_pipeline.intake_core.planning import build_ingestion_plan
from dual_pipeline.intake_core.routing import _route_for_candidate
from dual_pipeline.normalization_core.meeting_entities import (
    _build_single_entity_candidate,
    _build_single_relationship_candidate,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
)


class TestSpeakerRoleProjectionUnit(unittest.TestCase):
    """Unit tests for speaker role projection through Output 03 and normalization."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.sqlite_path = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_path = self.work_dir / "hitl_decision_ledger.json"
        self.metadata = {
            "run_id": "test_run_20261008_w2",
            "meeting_id": "meet_w2_001",
            "title": "Architecture Review",
        }

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_reconcile_resolutions_restores_dropped_title(self) -> None:
        """Dropping or altering a title in model output is deterministically restored."""
        res = create_speaker_role_resolution(
            person_name="Alex Rivera",
            resolved_role="VP of Architecture",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {"Alex Rivera": res}

        # Model output dropped role and replaced with generic placeholder
        projection: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:mitchell_simmons",
                        "name": "Alex Rivera",
                        "type": "person",
                        "description": "Alex Rivera is a meeting attendee.",
                        "role": "Participant",
                    }
                ],
                "relationship_signals": [],
            },
            "evidence_index": {},
        }

        reconciled = reconcile_speaker_role_resolutions(projection, self.metadata)
        entities = reconciled["graph_mutations"]["entity_upserts"]
        mitch = next(e for e in entities if e["id"] == "person:mitchell_simmons")

        self.assertEqual(mitch["role"], "VP of Architecture")
        self.assertTrue(mitch["is_human_confirmed"])
        self.assertEqual(mitch["resolution_id"], res.resolution_id)
        self.assertIn("VP of Architecture", mitch["description"])

        # Check HAS_ROLE relationship
        signals = reconciled["graph_mutations"]["relationship_signals"]
        role_sig = next(
            s for s in signals
            if s["from_id"] == "person:mitchell_simmons" and s["relationship"] in ("HAS_ROLE", "HAS_ROLE_IN")
        )
        self.assertEqual(role_sig["to_id"], "role:vp_of_architecture")
        self.assertTrue(role_sig["is_human_confirmed"])

    def test_reconcile_resolutions_creates_minimal_proposal_for_novel_identity(self) -> None:
        """Novel identities produce exact minimal proposal: person node, role node, and HAS_ROLE_IN edge."""
        res = create_speaker_role_resolution(
            person_name="Sarah Connor",
            resolved_role="Security Lead",
            organization=None,
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {"Sarah Connor": res}

        projection: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }

        reconciled = reconcile_speaker_role_resolutions(projection, self.metadata)
        entities = reconciled["graph_mutations"]["entity_upserts"]
        signals = reconciled["graph_mutations"]["relationship_signals"]

        sarah = next(e for e in entities if e["id"] == "person:sarah_connor")
        self.assertEqual(sarah["name"], "Sarah Connor")
        self.assertEqual(sarah["role"], "Security Lead")
        self.assertTrue(sarah["is_human_confirmed"])

        role_node = next(e for e in entities if e["id"] == "role:security_lead")
        self.assertEqual(role_node["type"], "role")

        has_role = next(s for s in signals if s["relationship"] in ("HAS_ROLE", "HAS_ROLE_IN"))
        self.assertEqual(has_role["from_id"], "person:sarah_connor")
        self.assertEqual(has_role["to_id"], "role:security_lead")

    def test_omitted_organization_is_not_invented(self) -> None:
        """When organization is omitted or unknown, zero organization nodes or MEMBER_OF edges are invented."""
        res = create_speaker_role_resolution(
            person_name="Alex Mercer",
            resolved_role="Systems Architect",
            organization=None,
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {"Alex Mercer": res}

        projection: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }

        reconciled = reconcile_speaker_role_resolutions(projection, self.metadata)
        entities = reconciled["graph_mutations"]["entity_upserts"]
        signals = reconciled["graph_mutations"]["relationship_signals"]

        org_nodes = [e for e in entities if e.get("type") == "org" or str(e.get("id", "")).startswith("org:")]
        member_signals = [s for s in signals if s.get("relationship") == "MEMBER_OF"]

        self.assertEqual(len(org_nodes), 0)
        self.assertEqual(len(member_signals), 0)

    def test_provided_organization_creates_member_of_signal(self) -> None:
        """When organization is provided, org entity and MEMBER_OF relationship signal are created."""
        res = create_speaker_role_resolution(
            person_name="David Chen",
            resolved_role="Cloud Engineer",
            organization="Google Cloud",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {"David Chen": res}

        projection: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }

        reconciled = reconcile_speaker_role_resolutions(projection, self.metadata)
        entities = reconciled["graph_mutations"]["entity_upserts"]
        signals = reconciled["graph_mutations"]["relationship_signals"]

        org_node = next(e for e in entities if e["id"] == "org:google_cloud")
        self.assertEqual(org_node["name"], "Google Cloud")

        member_sig = next(s for s in signals if s["relationship"] == "MEMBER_OF")
        self.assertEqual(member_sig["from_id"], "person:david_chen")
        self.assertEqual(member_sig["to_id"], "org:google_cloud")

    def test_evidence_cites_human_clarification_not_transcript(self) -> None:
        """Evidence item in evidence_index cites human operator verification, not misattributed transcript speech."""
        res = create_speaker_role_resolution(
            person_name="Elena Rostova",
            resolved_role="Data Strategist",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Verified"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )
        self.metadata["hitl_role_resolutions"] = {"Elena Rostova": res}

        projection: Dict[str, Any] = {
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
            "evidence_index": {},
        }

        reconciled = reconcile_speaker_role_resolutions(projection, self.metadata)
        ev_item = reconciled["evidence_index"]["ev_hitl_role_elena_rostova"]

        self.assertEqual(ev_item["speaker"], "Operator")
        self.assertEqual(ev_item["source"], "TRANSCRIPT_HITL")
        self.assertIn("Human operator verified", ev_item["text"])
        self.assertEqual(ev_item["resolution_id"], res.resolution_id)

    def test_normalization_candidate_preserves_role_facts(self) -> None:
        """Entity and relationship normalization preserves typed role facts and provenance."""
        entity = {
            "id": "person:mitchell_simmons",
            "name": "Alex Rivera",
            "type": "person",
            "role": "VP of Architecture",
            "is_human_confirmed": True,
            "provenance": "HUMAN_OPERATOR",
            "resolution_id": "res-role-test-12345",
        }
        cand, mention_id, profile, raw_id, node_type = _build_single_entity_candidate(
            entity=entity,
            idx=0,
            run_id="run-1",
            meeting_title="Architecture Review",
            classification="internal",
            audiences=["internal"],
            default_evidence_id="ev-1",
            default_locator={"kind": "section", "value": "## Transcript"},
            evidence_by_id={"ev-1": {"locator": {"kind": "section", "value": "## Transcript"}}},
            evidence_by_identifier={},
        )
        self.assertTrue(cand["has_role_resolution"])
        self.assertTrue(cand["has_role_change"])
        self.assertEqual(cand["role"], "VP of Architecture")
        self.assertTrue(cand["is_human_confirmed"])
        self.assertEqual(cand["provenance"], "HUMAN_OPERATOR")
        self.assertEqual(cand["resolution_id"], "res-role-test-12345")
        self.assertEqual(profile["role"], "VP of Architecture")

        rel = {
            "from_id": "person:mitchell_simmons",
            "to_id": "role:vp_of_architecture",
            "relationship": "HAS_ROLE_IN",
            "is_human_confirmed": True,
            "provenance": "HUMAN_OPERATOR",
            "resolution_id": "res-role-test-12345",
        }
        rel_cand, trait = _build_single_relationship_candidate(
            rel=rel,
            idx=0,
            run_id="run-1",
            meeting_title="Architecture Review",
            classification="internal",
            audiences=["internal"],
            first_ev_id="ev-1",
            first_loc={"kind": "section", "value": "## Transcript"},
            evidence_by_id={"ev-1": {"locator": {"kind": "section", "value": "## Transcript"}}},
            evidence_by_identifier={},
            profile_index={},
            entity_intent_profiles={},
        )
        self.assertEqual(rel_cand["assertion_type"], "speaker_role")
        self.assertTrue(rel_cand["has_role_change"])
        self.assertTrue(rel_cand["has_role_resolution"])
        self.assertTrue(rel_cand["is_human_confirmed"])

    def test_planning_routes_role_change_to_add_assertion(self) -> None:
        """Role changes in candidates are planned as add_assertion rather than none/noop."""
        bundle = {
            "run_id": "run-test-role-plan",
            "trace_id": "a" * 32,
            "candidates": [
                {
                    "candidate_id": "cand-role-1",
                    "candidate_type": "entity_mention",
                    "has_role_change": True,
                    "has_role_resolution": True,
                    "assertion_type": "speaker_role",
                    "materiality": "high",
                    "classification": "internal",
                    "entity_mentions": [{"reported_text": "Alex Rivera"}],
                }
            ],
        }
        plan = build_ingestion_plan(bundle)
        cand_plan = plan["candidates"][0]
        self.assertEqual(cand_plan["expected_graph_operation"], "add_assertion")
        self.assertEqual(cand_plan["expected_evidence_lane"], "candidate")
        self.assertEqual(cand_plan["expected_retrieval_record_family"], "assertion")

    def test_routing_never_routes_role_change_to_noop(self) -> None:
        """Deterministic routing sends role changes to NEW_ASSERTION even with exact content match."""
        candidate = {
            "candidate_id": "cand-role-1",
            "candidate_type": "entity_mention",
            "has_role_change": True,
            "has_role_resolution": True,
            "assertion_type": "speaker_role",
        }
        # Simulate exact match in graph
        comparison = {"exact_matches": [{"source_id": "s1", "object_id": "ass-existing"}]}
        resolutions = [{"outcome": "AUTO_LINK_EXACT"}]

        route, reasons = _route_for_candidate(candidate, resolutions, comparison)
        self.assertEqual(route, "NEW_ASSERTION")
        self.assertIn("explicit supported role assertion change for identity", reasons)


if __name__ == "__main__":
    unittest.main()
