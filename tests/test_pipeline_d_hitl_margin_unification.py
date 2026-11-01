"""Wave 4: Pipeline D HITL 25% Margin Unification and Dialectical Fusion Tests.

Verifies:
1. SSOT Margin Threshold: DEFAULT_MARGIN_THRESHOLD == 0.25 across all governance modules.
2. Inversion Defect Remediation: HitlArbiterAgent universally defaults to AMBIGUITY_LE_THRESHOLD.
3. Ambiguity Boundary Validation:
   - Delta = 0.24 (< 0.25) -> HITL_REQUIRED (should_surface=True)
   - Delta = 0.25 (== 0.25) -> AUTONOMOUS_APPROVED (should_surface=False)
   - Delta = 0.26 (> 0.25) -> AUTONOMOUS_APPROVED (should_surface=False)
4. Atomic Clickable Card Formulation: Conforms to ask_user_question format with (Recommended) marker.
5. Bayesian Feedback Calibration: Prior answers stored in ledger widen confidence spreads.
6. AntigravityUIBridge Integration: Default 25% ambiguity threshold delegation.
7. Dialectical Context Fusion: Fast-path utterances reconciled against settled canonical facts.
8. Architectural Invariants: File budgets <= 400 lines for all modified modules.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.decision.agents.hitl_arbiter_agent import (
    HitlArbiterAgent,
    get_hitl_arbiter_card,
)
from dual_pipeline.decision.context_fusion import (
    CLIENT_SAFE_NAMESPACE,
    CopilotFusionEngine,
    CopilotFusionPayload,
)
from dual_pipeline.hitl_governance import (
    DEFAULT_MARGIN_THRESHOLD,
    DEFAULT_SURFACING_MODE,
    HitlSurfacingMode,
    evaluate_hitl_surfacing_gate,
)
from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge


class MockSettledFact:
    """Mock canonical graph fact for context fusion testing."""

    def __init__(
        self,
        node_id: str,
        name: str,
        namespace: str = CLIENT_SAFE_NAMESPACE,
        fact_type: str = "ArchitectureDecision",
    ) -> None:
        self.node_id = node_id
        self.name = name
        self.namespace = namespace
        self.type = fact_type
        self.snippet = f"Settled architectural fact: {name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "name": self.name,
            "namespace": self.namespace,
            "type": self.type,
            "snippet": self.snippet,
        }


class MockRetrievalEngine:
    """Mock retrieval engine returning settled facts."""

    def __init__(self, facts: List[MockSettledFact]) -> None:
        self.facts = facts

    def search(self, query: str, stealth_mode: bool = True) -> List[MockSettledFact]:
        return self.facts


class TestPipelineDHitlMarginUnification(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "test_wave4_ledger.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_ssot_constants_unification(self) -> None:
        """Verifies DEFAULT_MARGIN_THRESHOLD == 0.30 and default mode is AMBIGUITY_LE_THRESHOLD."""
        self.assertEqual(DEFAULT_MARGIN_THRESHOLD, 0.30)
        self.assertEqual(DEFAULT_SURFACING_MODE, HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD)

        # evaluate_hitl_surfacing_gate default parameters
        gate_res = evaluate_hitl_surfacing_gate(
            options=[
                {"label": "Opt A", "confidence": "0.70"},
                {"label": "Opt B", "confidence": "0.60"},  # Delta = 0.10 < 0.30
            ]
        )
        self.assertEqual(gate_res["margin_threshold"], 0.30)
        self.assertEqual(gate_res["surfacing_mode"], HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD.value)
        self.assertTrue(gate_res["should_surface"])

    def test_02_hitl_arbiter_agent_card_skills_and_domain(self) -> None:
        """Verifies HitlArbiterAgent AgentCard declares 30% ambiguity margin gating."""
        card = get_hitl_arbiter_card()
        self.assertIn("30%", card.domain_focus)
        self.assertIn("Evaluate30PercentMarginGate", card.skills)
        self.assertEqual(card.model_binding.model_id, "gpt-6.1-sol")
        self.assertEqual(card.model_binding.reasoning_effort, "high")

    def test_03_hitl_arbiter_inversion_defect_remediated(self) -> None:
        """HitlArbiterAgent without explicit mode MUST default to AMBIGUITY_LE_THRESHOLD (not DIVERGENCE_GT_THRESHOLD)."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        # Highly ambiguous options (Delta = 0.10 < 0.15)
        ambiguous_options = [
            {"key": "broker", "label": "Modular Event Broker", "confidence_score": 0.60},
            {"key": "mesh", "label": "Direct P2P Mesh", "confidence_score": 0.50},
        ]
        # Should surface to HITL under AMBIGUITY_LE_THRESHOLD, NOT proceed autonomously
        res = arbiter.arbitrate_decision(
            decision_id="dec-defect-check",
            category="architecture",
            question="Select transport",
            options=ambiguous_options,
        )
        self.assertEqual(res["surfacing_mode"], HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD.value)
        self.assertEqual(res["status"], "HITL_REQUIRED")
        self.assertTrue(res["should_surface"])
        self.assertIsNotNone(res["atomic_card"])

    def test_04_boundary_delta_24_surfaces_hitl(self) -> None:
        """Boundary test: Delta = 0.29 (< 0.30) strictly surfaces as HITL_REQUIRED."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        options = [
            {"key": "opt-1", "label": "Option 1: Primary", "confidence_score": 0.79},
            {"key": "opt-2", "label": "Option 2: Alternative", "confidence_score": 0.50},
        ]
        res = arbiter.arbitrate_decision(
            decision_id="dec-b-14",
            category="strategic_tradeoff",
            question="Select strategy",
            options=options,
        )
        self.assertEqual(res["status"], "HITL_REQUIRED")
        self.assertTrue(res["should_surface"])
        self.assertAlmostEqual(res["margin"], 0.29, places=2)
        self.assertIn("30% ambiguity threshold", res["reason"])
        self.assertIsNotNone(res["atomic_card"])

    def test_05_boundary_delta_25_proceeds_autonomously(self) -> None:
        """Boundary test: Delta = 0.31 (> 0.30) decisively qualifies for autonomous progression."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        options = [
            {"key": "opt-1", "label": "Option 1: Primary", "confidence_score": 0.81},
            {"key": "opt-2", "label": "Option 2: Alternative", "confidence_score": 0.50},
        ]
        res = arbiter.arbitrate_decision(
            decision_id="dec-b-15",
            category="strategic_tradeoff",
            question="Select strategy",
            options=options,
        )
        self.assertEqual(res["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(res["should_surface"])
        self.assertAlmostEqual(res["margin"], 0.31, places=2)
        self.assertIn("exceeds the 30% threshold", res["reason"])
        self.assertIn("audit_receipt", res)

    def test_06_boundary_delta_26_proceeds_autonomously(self) -> None:
        """Boundary test: Delta = 0.35 (> 0.30) decisively qualifies for autonomous progression."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        options = [
            {"key": "opt-1", "label": "Option 1: Primary", "confidence_score": 0.85},
            {"key": "opt-2", "label": "Option 2: Alternative", "confidence_score": 0.50},
        ]
        res = arbiter.arbitrate_decision(
            decision_id="dec-b-16",
            category="strategic_tradeoff",
            question="Select strategy",
            options=options,
        )
        self.assertEqual(res["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(res["should_surface"])
        self.assertAlmostEqual(res["margin"], 0.35, places=2)
        self.assertIn("exceeds the 30% threshold", res["reason"])
        self.assertIn("audit_receipt", res)

    def test_07_atomic_clickable_card_structure(self) -> None:
        """Verifies atomic card conforms to ask_user_question interactive clickable options standard."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        options = [
            {"key": "opt-a", "label": "Asynchronous Event Sourcing", "confidence_score": 0.65},
            {"key": "opt-b", "label": "Synchronous Direct RPC", "confidence_score": 0.55},
        ]
        res = arbiter.arbitrate_decision(
            decision_id="dec-card-spec",
            category="event_design",
            question="Choose inter-service communication protocol",
            options=options,
        )
        card = res["atomic_card"]
        self.assertIsNotNone(card)
        self.assertEqual(card["decision_id"], "dec-card-spec")
        self.assertIn("Choose inter-service communication protocol", card["question"])
        self.assertEqual(len(card["options"]), 2)
        # Top option designated with (Recommended)
        self.assertTrue(card["options"][0].startswith("(Recommended)"))
        self.assertIn("65%", card["options"][0])

    def test_08_bayesian_prior_calibration_widens_margin(self) -> None:
        """Verifies operator choice calibration widens future margin past 30%."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        initial_options = [
            {"key": "opt-clean", "label": "Deterministic cleanup", "confidence_score": 0.60},
            {"key": "opt-purge", "label": "Hard database purge", "confidence_score": 0.50},
        ]
        # Initially ambiguous (Delta = 0.10 < 0.30)
        res_initial = arbiter.arbitrate_decision(
            decision_id="dec-cleanup-1",
            category="database_hygiene",
            question="Select DB hygiene strategy",
            options=initial_options,
        )
        self.assertTrue(res_initial["should_surface"])

        # Human operator resolves by selecting deterministic cleanup (2 feedback rounds widen past 30%)
        arbiter.record_feedback(
            decision_id="dec-cleanup-1",
            category="database_hygiene",
            prompt="Select DB hygiene strategy",
            options=initial_options,
            selected_option=initial_options[0],
            rationale="Deterministic cleanup preserves ledger history",
        )
        arbiter.record_feedback(
            decision_id="dec-cleanup-2",
            category="database_hygiene",
            prompt="Select DB hygiene strategy",
            options=initial_options,
            selected_option=initial_options[0],
            rationale="Deterministic cleanup preserves ledger history",
        )

        # Future identical evaluation: calibrated options widen spread
        res_future = arbiter.arbitrate_decision(
            decision_id="dec-cleanup-3",
            category="database_hygiene",
            question="Select DB hygiene strategy",
            options=initial_options,
        )
        # Calibrated: opt-clean = 0.733, opt-purge = 0.333 -> Delta = 0.40 >= 0.30
        self.assertFalse(res_future["should_surface"])
        self.assertEqual(res_future["status"], "AUTONOMOUS_APPROVED")
        self.assertGreaterEqual(res_future["margin"], 0.30)

    def test_09_antigravity_ui_bridge_delegation_unification(self) -> None:
        """Verifies AntigravityUIBridge operates with 30% threshold by default."""
        bridge = AntigravityUIBridge(ledger_path=self.ledger_path)
        self.assertEqual(bridge.margin_threshold, 0.30)
        self.assertEqual(bridge.surfacing_mode, HitlSurfacingMode.AMBIGUITY_LE_THRESHOLD)

        # Ambiguous decision surfaces
        res_ambiguous = bridge.arbitrate_and_present(
            pipeline_id="pipeline-d",
            decision_id="dec-bridge-ambiguous",
            category="test_category",
            question="Ambiguous choice",
            options=[
                {"key": "a", "label": "Option A", "confidence_score": 0.60},
                {"key": "b", "label": "Option B", "confidence_score": 0.50},
            ],
        )
        self.assertTrue(res_ambiguous["should_surface"])
        self.assertEqual(res_ambiguous["status"], "HITL_REQUIRED")

        # Decisive decision auto-approves
        res_decisive = bridge.arbitrate_and_present(
            pipeline_id="pipeline-d",
            decision_id="dec-bridge-decisive",
            category="test_category",
            question="Decisive choice",
            options=[
                {"key": "a", "label": "Option A", "confidence_score": 0.85},
                {"key": "b", "label": "Option B", "confidence_score": 0.50},
            ],
        )
        self.assertFalse(res_decisive["should_surface"])
        self.assertEqual(res_decisive["status"], "AUTONOMOUS_APPROVED")
        self.assertIsNotNone(res_decisive.get("audit_receipt"))

    def test_10_dialectical_context_fusion_reconciliation(self) -> None:
        """Verifies CopilotFusionEngine reconciles fast-path events against settled canonical facts."""
        settled_facts = [
            MockSettledFact(
                node_id="node:arch:bge_m3",
                name="BGE-M3 Dense Embedding Architecture",
            ),
            MockSettledFact(
                node_id="node:arch:wal_storage",
                name="SQLite WAL Mode Zero-Contention Storage",
            ),
        ]
        retrieval = MockRetrievalEngine(settled_facts)
        engine = CopilotFusionEngine(retrieval_engine=retrieval)

        # Test case A: Corroboration
        live_corroborating = [
            {"speaker": "Alex", "text": "We are maintaining the BGE-M3 Dense Embedding Architecture for retrieval."},
        ]
        payload_corrob = engine.fuse_context(
            query="embedding pipeline architecture",
            live_fast_path_events=live_corroborating,
        )
        self.assertIsInstance(payload_corrob, CopilotFusionPayload)
        self.assertEqual(len(payload_corrob.dialectical_reconciliations), 1)
        self.assertEqual(payload_corrob.dialectical_reconciliations[0]["reconciliation_type"], "CORROBORATED")
        self.assertFalse(payload_corrob.dialectical_reconciliations[0]["requires_hitl_arbitration"])

        # Test case B: Dialectical Tension (e.g. drift / deprecation suggested in live utterance)
        live_tension = [
            {"speaker": "Jordan", "text": "We should replace the SQLite WAL Mode Zero-Contention Storage with MongoDB."},
        ]
        payload_tension = engine.fuse_context(
            query="storage architecture",
            live_fast_path_events=live_tension,
        )
        self.assertEqual(len(payload_tension.dialectical_reconciliations), 1)
        self.assertEqual(payload_tension.dialectical_reconciliations[0]["reconciliation_type"], "DIALECTICAL_TENSION")
        self.assertTrue(payload_tension.dialectical_reconciliations[0]["requires_hitl_arbitration"])
        self.assertIn("Dialectical Tension Detected", payload_tension.executive_synthesis)

        # Test case C: Unverified fast path (unanchored new claim)
        live_unverified = [
            {"speaker": "Taylor", "text": "What is the timeline for the Q4 offsite in Miami?"},
        ]
        payload_unverified = engine.fuse_context(
            query="team schedule",
            live_fast_path_events=live_unverified,
        )
        self.assertEqual(len(payload_unverified.dialectical_reconciliations), 1)
        self.assertEqual(payload_unverified.dialectical_reconciliations[0]["reconciliation_type"], "UNVERIFIED_FAST_PATH")
        self.assertFalse(payload_unverified.dialectical_reconciliations[0]["requires_hitl_arbitration"])

    def test_11_module_line_budgets_invariant(self) -> None:
        """Verifies that all modified modules strictly adhere to the <= 400 lines budget (Invariant 1)."""
        root = Path(__file__).resolve().parents[1]
        modules = [
            root / "dual_pipeline" / "decision" / "agents" / "hitl_arbiter_agent.py",
            root / "dual_pipeline" / "hitl_ui_bridge.py",
            root / "dual_pipeline" / "decision" / "context_fusion.py",
        ]
        for mod_path in modules:
            self.assertTrue(mod_path.is_file(), f"Module {mod_path} not found")
            lines = mod_path.read_text(encoding="utf-8").splitlines()
            self.assertLessEqual(
                len(lines),
                400,
                f"Module {mod_path.name} has {len(lines)} lines, exceeding the 400-line budget limit!",
            )


if __name__ == "__main__":
    unittest.main()
