#!/usr/bin/env python3
"""
tests/end_to_end/test_speaker_role_projection_e2e.py

End-to-End Test Suite for Wave 2: Carrying required role facts through Output 03 and normalization.
Verifies the full lifecycle from captured resolution through Stage 03 projection post-processing,
candidate normalization, and intake planning/routing.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.evidence_coverage import EvidenceCoverageManifest
from dual_pipeline.hitl_governance_core.speaker_resolution_contract import (
    ResolutionProvenance,
    create_speaker_role_resolution,
)
from dual_pipeline.intake_core.planning import build_ingestion_plan
from dual_pipeline.intake_core.routing import build_routes
from dual_pipeline.normalization_core.meeting_entities import process_projection_signals
from dual_pipeline.transcript_stages_core.graph_executor import post_process_stage03_projection


@pytest.mark.e2e
class TestSpeakerRoleProjectionE2E(unittest.TestCase):
    """E2E lifecycle testing for speaker role projection through Output 03 and normalization."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name)
        self.sqlite_path = self.work_dir / "hitl_decision_ledger.sqlite"
        self.json_path = self.work_dir / "hitl_decision_ledger.json"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_full_projection_and_normalization_lifecycle(self) -> None:
        """Full lifecycle: captured resolution -> stage 03 projection reconciliation -> normalization -> planning & routing."""
        run_id = "run-e2e-proj-001"
        meeting_title = "Executive Architecture Alignment"

        # 1. Capture authentic human resolution
        res = create_speaker_role_resolution(
            person_name="Alex Rivera",
            resolved_role="VP of Architecture",
            organization="PartnerAdvisory",
            provenance=ResolutionProvenance.HUMAN_OPERATOR,
            is_human_confirmed=True,
            operator_origin={"source": "human_operator", "text": "Confirmed by operator"},
            sqlite_path=self.sqlite_path,
            json_path=self.json_path,
        )

        # 2. Prepare metadata with resolution and evidence coverage manifest
        ev_manifest = EvidenceCoverageManifest(
            manifest_id="cov-e2e-test",
            run_id=run_id,
            source_digest="a" * 64,
            items=[],
        )
        metadata = {
            "run_id": run_id,
            "meeting_id": "meet_e2e_001",
            "title": meeting_title,
            "hitl_role_resolutions": {"Alex Rivera": res},
            "evidence_manifest": ev_manifest,
        }

        # 3. Simulate Stage 03 model output where LLM dropped role and set placeholder
        raw_model_output = json.dumps({
            "meeting_id": "meet_e2e_001",
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
        })

        transcript = "Operator: Mitchell, can you confirm your title? Mitchell: Yes, VP of Architecture."

        # 4. Execute post_process_stage03_projection
        processed_json = post_process_stage03_projection(raw_model_output, metadata, transcript)
        final_projection = json.loads(processed_json)

        # Verify Stage 03 Output 03 contents
        mutations = final_projection["graph_mutations"]
        entities = mutations["entity_upserts"]
        signals = mutations["relationship_signals"]
        ev_index = final_projection["evidence_index"]

        mitch = next(e for e in entities if e["id"] == "person:mitchell_simmons")
        self.assertEqual(mitch["role"], "VP of Architecture")
        self.assertTrue(mitch["is_human_confirmed"])
        self.assertEqual(mitch["resolution_id"], res.resolution_id)
        self.assertIn("VP of Architecture", mitch["description"])

        role_sig = next(s for s in signals if s["relationship"] == "HAS_ROLE_IN")
        self.assertEqual(role_sig["from_id"], "person:mitchell_simmons")
        self.assertEqual(role_sig["to_id"], "role:vp_of_architecture")
        self.assertTrue(role_sig["is_human_confirmed"])

        member_sig = next(s for s in signals if s["relationship"] == "MEMBER_OF")
        self.assertEqual(member_sig["from_id"], "person:mitchell_simmons")
        self.assertEqual(member_sig["to_id"], "org:partner_advisory")

        ev_key = "ev_hitl_role_mitchell_simmons"
        self.assertIn(ev_key, ev_index)
        self.assertEqual(ev_index[ev_key]["source"], "TRANSCRIPT_HITL")
        self.assertEqual(ev_index[ev_key]["speaker"], "Operator")

        # 5. Normalize projection signals into candidates
        candidates = []
        evidence = [{"evidence_id": ev_key, "locator": {"kind": "section", "value": "## Transcript"}}]
        profiles, traits = process_projection_signals(
            projection=final_projection,
            candidates=candidates,
            evidence=evidence,
            evidence_by_identifier={},
            run_id=run_id,
            meeting_title=meeting_title,
            classification="internal",
            default_audiences=["internal"],
        )

        # Verify normalized candidate properties
        mitch_cand = next(c for c in candidates if any(m["stable_identifier"] == "person:mitchell_simmons" for m in c.get("entity_mentions", [])))
        self.assertTrue(mitch_cand["has_role_resolution"])
        self.assertTrue(mitch_cand["has_role_change"])
        self.assertEqual(mitch_cand["role"], "VP of Architecture")
        self.assertTrue(mitch_cand["is_human_confirmed"])

        role_cand = next(c for c in candidates if c.get("assertion_type") == "speaker_role")
        self.assertTrue(role_cand["has_role_change"])
        self.assertTrue(role_cand["has_role_resolution"])

        # 6. Intake planning
        bundle = {
            "run_id": run_id,
            "trace_id": "b" * 32,
            "candidates": candidates,
        }
        plan = build_ingestion_plan(bundle)
        for cand_plan in plan["candidates"]:
            # Neither role candidate should be marked as no-op operation="none"
            c_orig = next(c for c in candidates if c["candidate_id"] == cand_plan["candidate_id"])
            if c_orig.get("has_role_resolution"):
                self.assertEqual(cand_plan["expected_graph_operation"], "add_assertion")
                self.assertEqual(cand_plan["expected_evidence_lane"], "candidate")

        # 7. Deterministic routing against existing graph (simulating existing person entity)
        comparison = {
            "comparisons": {
                c["candidate_id"]: {
                    "exact_matches": [{"source_id": "prev-source-01", "object_id": "ass-01"}],
                    "partial_overlaps": [],
                }
                for c in candidates
            }
        }
        resolutions = [
            {"mention_id": m["mention_id"], "outcome": "AUTO_LINK_EXACT"}
            for c in candidates
            for m in c.get("entity_mentions", [])
        ]

        routes = build_routes(bundle, resolutions, comparison)
        for route_artifact in routes:
            cand_id = route_artifact["subject_id"]
            c_orig = next(c for c in candidates if c["candidate_id"] == cand_id)
            if c_orig.get("has_role_resolution"):
                # Must route as NEW_ASSERTION, NOT EXACT_DUPLICATE
                self.assertEqual(route_artifact["intake_route"], "NEW_ASSERTION")
                self.assertIn("candidate", route_artifact["allowed_lanes"])


if __name__ == "__main__":
    unittest.main()
