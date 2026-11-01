"""Regression and invariant tests for SSOT drift remediation (refactor/daily-drift-fix-20261013).

Verifies that:
1. Governance schemas reference canonical tool names (ask_user_question).
2. Pipeline modules dynamically consume Layer 0 SSOT margin accessors.
3. Fallback and default parameter behaviors preserve backward compatibility.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any, Dict

import numpy as np

from dual_pipeline.core import ROOT
from dual_pipeline.l0.policy_snapshot import PolicySnapshot, create_policy_snapshot
from dual_pipeline.learning_adapters.agent_outcome_adapter import AgentOutcomeAdapter
from dual_pipeline.live_meeting_recorder_core.acoustic_features import detect_intra_chunk_switches
from dual_pipeline.parameters import (
    get_hitl_ambiguity_margin_threshold,
    get_hitl_divergence_margin_threshold,
)
from dual_pipeline.retrieval_release_core.decision_calibration import OperatingThresholds
from dual_pipeline.retrieval_release_core.evaluation_contracts import RouteCorrectnessContract
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import _handle_assertion_conflict


class SSOTDriftRemediationRegressionTests(unittest.TestCase):
    """Guarantees parity and regression resistance across remediated SSOT surfaces."""

    def test_post_recording_sla_canonical_tool_name(self) -> None:
        """Verify post_recording_sla.json uses canonical ask_user_question tool name."""
        sla_path = ROOT / "config" / "governance" / "post_recording_sla.json"
        self.assertTrue(sla_path.is_file(), f"Missing SLA config: {sla_path}")
        data = json.loads(sla_path.read_text(encoding="utf-8"))
        escalation = data.get("escalation_policy", {})
        self.assertEqual(
            escalation.get("prompt_tool"),
            "ask_user_question",
            "post_recording_sla.json must specify canonical ask_user_question tool",
        )

    def test_policy_snapshot_dynamic_margin_resolution(self) -> None:
        """Verify PolicySnapshot dynamically binds ambiguity and divergence margins from SSOT."""
        snap = create_policy_snapshot()
        self.assertEqual(snap.ambiguity_margin, get_hitl_ambiguity_margin_threshold())
        self.assertEqual(snap.divergence_margin, get_hitl_divergence_margin_threshold())

        # Test explicit override remains supported
        custom = create_policy_snapshot(ambiguity_margin=0.42, divergence_margin=0.35)
        self.assertEqual(custom.ambiguity_margin, 0.42)
        self.assertEqual(custom.divergence_margin, 0.35)

    def test_agent_outcome_adapter_margin_default(self) -> None:
        """Verify AgentOutcomeAdapter.build_decision_override_event resolves None to SSOT."""
        event = AgentOutcomeAdapter.build_decision_override_event(
            decision_id="dec_test_001",
            category="speaker_attribution",
            recommended_option="Speaker A",
            operator_chosen_option="Speaker B",
            margin_at_decision=None,
        )
        self.assertEqual(
            event.observed_outcome.get("margin_at_decision"),
            get_hitl_ambiguity_margin_threshold(),
            "Default margin_at_decision must dynamically resolve to SSOT threshold",
        )

    def test_acoustic_features_distance_threshold_default(self) -> None:
        """Verify detect_intra_chunk_switches dynamically consumes divergence threshold."""
        v1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        v2 = np.array([0.9, 0.4358, 0.0], dtype=np.float32)  # distance ~ 0.10 < 0.20
        v3 = np.array([0.0, 1.0, 0.0], dtype=np.float32)     # distance ~ 1.0 > 0.20

        # With default distance_threshold=None (resolves to divergence margin 0.20)
        switches = detect_intra_chunk_switches([v1, v2, v3])
        self.assertEqual(switches, [2])

    def test_decision_calibration_operating_thresholds_defaults(self) -> None:
        """Verify OperatingThresholds defaults min_margin to SSOT ambiguity threshold."""
        thresholds = OperatingThresholds()
        self.assertEqual(thresholds.min_margin, get_hitl_ambiguity_margin_threshold())

        # Explicit override supported
        custom = OperatingThresholds(min_margin=0.12)
        self.assertEqual(custom.min_margin, 0.12)

    def test_evaluation_contracts_route_correctness_defaults(self) -> None:
        """Verify RouteCorrectnessContract defaults ambiguity_margin_threshold to SSOT."""
        contract = RouteCorrectnessContract()
        self.assertEqual(
            contract.ambiguity_margin_threshold,
            get_hitl_ambiguity_margin_threshold(),
        )

    def test_entity_resolver_graph_margin_comparison(self) -> None:
        """Verify entity resolver margin comparison uses SSOT ambiguity threshold."""
        class DummyEntity:
            def __init__(self, role: str, confidence: float) -> None:
                self.role = role
                self.confidence = confidence
                self.canonical_name = "Test Person"
                self.display_name = "Test Person"
                self.org = "NEE"
                self.assertion_id = "asrt_000"

        def dummy_factory(**kwargs: Any) -> Any:
            return kwargs

        curr = DummyEntity(role="Lead Architect", confidence=0.85)

        # Confidence spread is 0.85 - 0.70 = 0.15 <= 0.30 -> conflict entity
        conflict = _handle_assertion_conflict(
            curr=curr,
            role_text="Staff Engineer",
            ass_id="asrt_001",
            sub_id="node_001",
            conf=0.70,
            gen_id="gen_001",
            entity_factory=dummy_factory,
        )
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict.get("status"), "AMBIGUOUS_CONFLICT")

    def test_seal_mutation_dossier_canonical_tool_name(self) -> None:
        """Verify tools/seal_mutation_dossier.py uses canonical ask_user_question."""
        tool_path = ROOT / "tools" / "seal_mutation_dossier.py"
        self.assertTrue(tool_path.is_file(), f"Missing tool: {tool_path}")
        content = tool_path.read_text(encoding="utf-8")
        self.assertIn("ask_user_question", content)
        self.assertNotIn("`ask_question`", content)

    def test_audit_judge_criteria_operator_input_required(self) -> None:
        """Verify tools/audit_judge_criteria.py uses OPERATOR_INPUT_REQUIRED instead of HITL_REQUIRED."""
        tool_path = ROOT / "tools" / "audit_judge_criteria.py"
        self.assertTrue(tool_path.is_file(), f"Missing tool: {tool_path}")
        content = tool_path.read_text(encoding="utf-8")
        self.assertIn("OPERATOR_INPUT_REQUIRED", content)
        self.assertNotIn("HITL_REQUIRED", content)

    def test_check_ask_user_question_in_deprecated_tool_exemptions(self) -> None:
        """Verify tools/check_ask_user_question.py is in DEPRECATED_TOOL_EXEMPTIONS."""
        from tools.audit_governance_harmonization_core.hitl_scanners import (
            DEPRECATED_TOOL_EXEMPTIONS,
        )
        self.assertIn("tools/check_ask_user_question.py", DEPRECATED_TOOL_EXEMPTIONS)


if __name__ == "__main__":
    unittest.main()

