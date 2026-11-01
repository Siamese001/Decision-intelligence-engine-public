"""
tests/test_copilot_strategic_reasoning.py

Unit test suite for Wave 3: Grounded Energy/Utility Strategic Reasoning & Domain Rigor.
Verifies:
1. In-memory graph acronym expansion & anchor mapping (ROCC, SCADA, EMS, Pano AI, Substation).
2. Domain validation guards in copilot_validator (SCADA NERC-CIP CIP-005, 2027 timeline spike, Pano AI SaaS).
3. Flash snap domain dilemma detection and executive script synthesis.
"""
from __future__ import annotations

import unittest
from unittest import mock

from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    DOMAIN_ACRONYM_MAP,
    CopilotGraphRetriever,
)
from dual_pipeline.live_meeting_recorder_core.copilot_validator import (
    AGREE_AND_BOUND_2027_RISK,
    AGREE_AND_BOUND_2027_SCRIPT,
    AGREE_AND_BOUND_2027_SNAP,
    PANO_AI_REVERSE_PROBING_SCRIPT,
    PANO_AI_REVERSE_PROBING_SNAP,
    PANO_AI_TOOL_FATIGUE_RISK,
    SCADA_DECOUPLING_RISK,
    SCADA_DECOUPLING_SCRIPT,
    SCADA_DECOUPLING_SNAP,
    validate_copilot_feedback,
)
from dual_pipeline.live_meeting_recorder_core.flash_snap import FlashSnapEngine


class TestEnergyDomainRetrieval(unittest.TestCase):
    """Tests energy transmission and utility domain acronym retrieval grounding."""

    def setUp(self) -> None:
        self.mock_topology = mock.MagicMock()
        self.mock_topology.nodes = {
            "anchor_edo_rfp_scada_tracker": {
                "canonical_name": "EDO SCADA Ingress Tracker",
                "display_name": "SCADA Tracker",
                "node_type": "system",
                "organization": "EDO",
                "aliases": ["scada_tracker", "ems_feed"],
            },
            "anchor_edo_rfp_rocc_rso_greensheet": {
                "canonical_name": "ROCC RSO Greensheet Playbook",
                "display_name": "ROCC Greensheet",
                "node_type": "policy",
                "organization": "Apex Global",
                "aliases": ["rso_playbook", "greensheet"],
            },
            "workstream_substation_onboarding": {
                "canonical_name": "Substation Onboarding Workstream",
                "display_name": "Substation Onboarding",
                "node_type": "workstream",
                "organization": "APEX_TRANS",
                "aliases": ["substation_telemetry"],
            },
        }
        self.mock_topology.assertions = {
            "assert_scada_pi": {
                "subject_node_id": "anchor_edo_rfp_scada_tracker",
                "predicate": "DEPENDS_ON",
                "object_literal": "PI Historian replication layer",
                "canonical_status": "ACTIVE",
            },
            "assert_rocc_ops": {
                "subject_node_id": "anchor_edo_rfp_rocc_rso_greensheet",
                "predicate": "GOVERNS",
                "object_literal": "Control room operator handoffs",
                "canonical_status": "ACTIVE",
            },
        }
        self.retriever = CopilotGraphRetriever(
            topology=self.mock_topology,
            attendees=[{"name": "Drew Oakley", "role": "VP", "org": "Apex Global"}],
        )

    def test_domain_acronym_mapping_presence(self) -> None:
        self.assertIn("scada", DOMAIN_ACRONYM_MAP)
        self.assertIn("rocc", DOMAIN_ACRONYM_MAP)
        self.assertIn("ems", DOMAIN_ACRONYM_MAP)
        self.assertIn("pano", DOMAIN_ACRONYM_MAP)
        self.assertIn("substation", DOMAIN_ACRONYM_MAP)

    def test_scada_dialogue_retrieves_scada_tracker(self) -> None:
        dialogue = "Dan: We have concerns regarding direct SCADA telemetry access from external tools."
        context = self.retriever.retrieve_context(dialogue)
        self.assertIn("Verified Graph Knowledge:", context)
        self.assertIn("EDO SCADA Ingress Tracker", context)
        self.assertIn("PI Historian replication layer", context)

    def test_rocc_dialogue_retrieves_greensheet_playbook(self) -> None:
        dialogue = "Amit: How do operators in the ROCC handle desk handoffs during storm conditions?"
        context = self.retriever.retrieve_context(dialogue)
        self.assertIn("Verified Graph Knowledge:", context)
        self.assertIn("ROCC RSO Greensheet Playbook", context)
        self.assertIn("Control room operator handoffs", context)

    def test_substation_dialogue_retrieves_workstream(self) -> None:
        dialogue = "Aaron: What is the timeline for substation sensor integration?"
        context = self.retriever.retrieve_context(dialogue)
        self.assertIn("Verified Graph Knowledge:", context)
        self.assertIn("Substation Onboarding Workstream", context)


class TestEnergyDomainValidationGuards(unittest.TestCase):
    """Tests copilot_validator post-synthesis guards for energy domain constraints."""

    def test_direct_scada_ingress_intercepted(self) -> None:
        parsed = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "Let's configure direct SCADA access to pull real-time transformer telemetry.",
            "snap_cue": "Direct SCADA connection",
            "confidence": 0.9,
        }
        result = validate_copilot_feedback(parsed)
        self.assertIsNotNone(result)
        self.assertEqual(result["suggested_script"], SCADA_DECOUPLING_SCRIPT)
        self.assertEqual(result["snap_cue"], SCADA_DECOUPLING_SNAP)
        self.assertEqual(result["risk_trap"], SCADA_DECOUPLING_RISK)
        self.assertEqual(result["category"], "TECHNICAL")

    def test_safe_scada_with_pi_historian_allowed(self) -> None:
        parsed = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "We ingest telemetry from the PI Historian replica rather than live SCADA.",
            "snap_cue": "Read from PI Historian",
            "confidence": 0.95,
        }
        result = validate_copilot_feedback(parsed)
        self.assertIsNotNone(result)
        self.assertEqual(
            result["suggested_script"],
            "We ingest telemetry from the PI Historian replica rather than live SCADA.",
        )
        self.assertEqual(result["snap_cue"], "Read from PI Historian")

    def test_2027_timeline_pushback_intercepted(self) -> None:
        parsed = {
            "has_guidance": True,
            "category": "STRATEGIC",
            "suggested_script": "I propose we wait until 2027 for the enterprise rollout across all substations.",
            "snap_cue": "Wait until 2027",
            "confidence": 0.88,
        }
        result = validate_copilot_feedback(parsed)
        self.assertIsNotNone(result)
        self.assertEqual(result["suggested_script"], AGREE_AND_BOUND_2027_SCRIPT)
        self.assertEqual(result["snap_cue"], AGREE_AND_BOUND_2027_SNAP)
        self.assertEqual(result["risk_trap"], AGREE_AND_BOUND_2027_RISK)
        self.assertEqual(result["category"], "STRATEGIC")

    def test_custom_camera_build_intercepted(self) -> None:
        parsed = {
            "has_guidance": True,
            "category": "TECHNICAL",
            "suggested_script": "We should build our own camera detection algorithm internally.",
            "snap_cue": "Build custom camera CV",
            "confidence": 0.85,
        }
        result = validate_copilot_feedback(parsed)
        self.assertIsNotNone(result)
        self.assertEqual(result["suggested_script"], PANO_AI_REVERSE_PROBING_SCRIPT)
        self.assertEqual(result["snap_cue"], PANO_AI_REVERSE_PROBING_SNAP)
        self.assertEqual(result["risk_trap"], PANO_AI_TOOL_FATIGUE_RISK)
        self.assertEqual(result["category"], "TECHNICAL")


class TestFlashSnapEnergyDilemmas(unittest.TestCase):
    """Tests FlashSnapEngine zero-latency heuristic matching on energy domain dilemmas."""

    def setUp(self) -> None:
        self.engine = FlashSnapEngine()

    def test_flash_snap_scada_dilemma(self) -> None:
        snap = self.engine.evaluate_utterance(
            "Dan: We cannot grant live access to SCADA networks without breaking regulatory rules."
        )
        self.assertIsNotNone(snap)
        self.assertEqual(snap["snap_cue"], "Decouple via PI Historian replica")
        self.assertIn("PI Historian", snap["suggested_script"])
        self.assertEqual(snap["track"], "TRACK_1_FLASH_SNAP")

    def test_flash_snap_2027_timeline_dilemma(self) -> None:
        snap = self.engine.evaluate_utterance(
            "Aaron: If we wait for the 2027 roadmap release, we lose the operational season."
        )
        self.assertIsNotNone(snap)
        self.assertEqual(snap["snap_cue"], "Anchor to grounded-contract proof spike")
        self.assertIn("grounded-contract spike", snap["suggested_script"])

    def test_flash_snap_pano_ai_dilemma(self) -> None:
        snap = self.engine.evaluate_utterance(
            "Dan: We already have 10 tools and Pano AI cameras on the towers. No new custom software."
        )
        self.assertIsNotNone(snap)
        self.assertEqual(snap["snap_cue"], "Integrate Pano API, avoid rebuild")
        self.assertIn("Pano AI", snap["suggested_script"])


if __name__ == "__main__":
    unittest.main()
