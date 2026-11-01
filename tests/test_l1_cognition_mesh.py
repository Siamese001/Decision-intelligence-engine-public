"""Dedicated Test Suite for L1 Cognition & Reasoning Mesh Across Pipelines A-D.

Certifies the L1 Cognition Reasoning Layer hardening before L0 routing:
- Pipeline A: L1ForensicCognition (fusion DAGs, toxic token quarantine, classification tiering)
- Pipeline B: L1ExtractionCognition (Law 1/2 pre-classification, W2 spine audit, domain scoring)
- Pipeline C: L1QueryCognition (perspective tuning, BGE-M3 weights, Law 6 clearance)
- Pipeline D: L1DecisionCognition (orthogonality audit, fatal flaw screening, fast-path tension)
- Unified Invariants: Standardized on gpt-5.6-luna (reasoning_effort="high"), fail-closed sanitization.
"""
from __future__ import annotations

import unittest

from dual_pipeline.a2a import ComputePosture
from dual_pipeline.decision.agents import (
    L1DecisionCognition,
)
from dual_pipeline.decision.agents import (
    L1DecisionPlanContract as L1PlanContractD,
)
from dual_pipeline.retrieval.agents import (
    L1QueryCognition,
)
from dual_pipeline.retrieval.agents import (
    L1RetrievalPlanContract as L1PlanContractC,
)
from dual_pipeline.transcript_stages import (
    L1ForensicCognition,
)
from dual_pipeline.transcript_stages import (
    L1PlanContract as L1PlanContractA,
)
from pipeline_b_extraction.agents import (
    L1ExtractionCognition,
)
from pipeline_b_extraction.agents import (
    L1ExtractionPlanContract as L1PlanContractB,
)


class TestL1CognitionMesh(unittest.TestCase):
    """Verifies L1 cognition layers across all four pipelines A-D."""

    # -------------------------------------------------------------
    # 1. Pipeline A: Multimodal Forensic Cognition Tests
    # -------------------------------------------------------------
    def test_pipeline_a_l1_forensic_cognition_contracts_and_binding(self) -> None:
        """Verifies Pipeline A L1 plan contract, model binding, and DAG selection."""
        cognition = L1ForensicCognition()
        self.assertEqual(cognition.model_binding["model_id"], "gpt-5.6-luna")
        self.assertIn(cognition.model_binding["reasoning_effort"], ("medium", "high", "xhigh"))

        # Standard clean context
        context = {
            "clean_transcript": "Good morning team. We are reviewing the architecture.",
            "metadata": {
                "meeting_name": "Architecture Sync",
                "date": "09/12/2026",
                "source_type": "transcript",
            },
            "diarization_status": "DIARIZED",
        }
        plan: L1PlanContractA = cognition.plan(context)
        self.assertIsInstance(plan, L1PlanContractA)
        self.assertTrue(plan.sanitization_passed)
        self.assertEqual(plan.classification_tier, "internal")
        self.assertEqual(plan.model_binding["model_id"], "gpt-5.6-luna")
        self.assertIn(plan.model_binding["reasoning_effort"], ("medium", "high", "xhigh"))
        self.assertTrue(plan.fusion_dag.startswith("FUSION_"))

    def test_pipeline_a_l1_forensic_cognition_sanitization_fail_closed(self) -> None:
        """Verifies Pipeline A L1 detects private keys and fails sanitization."""
        cognition = L1ForensicCognition()
        toxic_context = {
            "clean_transcript": "REDACTED_PRIVATE_KEY_HEADER\nMIIEowIBAAKCAQEA0\n-----END RSA PRIVATE KEY-----",
            "metadata": {"source_type": "transcript"},
        }
        plan: L1PlanContractA = cognition.plan(toxic_context)
        self.assertFalse(plan.sanitization_passed)
        self.assertIn("RESTRICTED_CREDENTIAL_OR_KEY_DETECTED", plan.quarantine_reasons)
        self.assertEqual(plan.classification_tier, "restricted")

    # -------------------------------------------------------------
    # 2. Pipeline B: Dual-Compute Extraction Cognition Tests
    # -------------------------------------------------------------
    def test_pipeline_b_l1_extraction_cognition_laws_and_spine(self) -> None:
        """Verifies Pipeline B L1 enforces Law 1/2 advisory pre-classification and W2 spine."""
        cognition = L1ExtractionCognition()
        self.assertEqual(cognition.model_binding["model_id"], "gpt-5.6-luna")
        self.assertIn(cognition.model_binding["reasoning_effort"], ("medium", "high", "xhigh"))

        # Context with political wedge and career leverage (Law 1 & Law 2 trigger)
        advisory_transcript = (
            "We have severe political friction between teams. "
            "I need personal career visibility and a networking wedge with executive leadership. "
            "System system:bge-m3 has a hard dependency on workstream:nexus."
        )
        plan: L1PlanContractB = cognition.plan(
            evidence_id="ev-001",
            text=advisory_transcript,
            stealth_mode=True,
        )
        self.assertIsInstance(plan, L1PlanContractB)
        self.assertTrue(plan.has_advisory_leverage)
        self.assertTrue(plan.sanitization_passed)
        self.assertEqual(plan.compute_posture, ComputePosture.INTERNAL_ADVISORY)
        self.assertEqual(plan.model_binding["model_id"], "gpt-5.6-luna")
        self.assertGreater(plan.domain_relevance["STAKEHOLDER_DYNAMICS"], 0.0)

    def test_pipeline_b_l1_extraction_cognition_sanitization_fail_closed(self) -> None:
        """Verifies Pipeline B L1 flags AWS access keys before L0 extraction."""
        cognition = L1ExtractionCognition()
        toxic_transcript = "Deploy with cloud credential AKIA_REDACTED_EXAMPLE into production."
        plan: L1PlanContractB = cognition.plan(
            evidence_id="ev-toxic",
            text=toxic_transcript,
        )
        self.assertFalse(plan.sanitization_passed)
        self.assertIn("RESTRICTED_SECRET_OR_KEY_DETECTED", plan.quarantine_reasons)

    # -------------------------------------------------------------
    # 3. Pipeline C: Multi-Perspective Hybrid Retrieval Cognition Tests
    # -------------------------------------------------------------
    def test_pipeline_c_l1_query_cognition_tuning_and_weights(self) -> None:
        """Verifies Pipeline C L1 intent decomposition and dynamic BGE-M3 weighting."""
        cognition = L1QueryCognition()
        self.assertEqual(cognition.model_binding["model_id"], "gpt-5.6-luna")
        self.assertIn(cognition.model_binding["reasoning_effort"], ("medium", "high", "xhigh"))

        # Technical blocker query
        query = "What are the architectural blockers and critical risks for vector search?"
        plan: L1PlanContractC = cognition.plan(
            query=query,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
        )
        self.assertIsInstance(plan, L1PlanContractC)
        self.assertTrue(plan.sanitization_passed)
        self.assertTrue(plan.caller_clearance_verified)
        self.assertIn("BLOCKER", plan.perspective_weights)
        self.assertIn("TECHNICAL", plan.perspective_weights)
        # Verify Colbert & Dense emphasis for technical query
        self.assertGreaterEqual(plan.hybrid_weights["colbert"], 0.20)
        self.assertGreaterEqual(plan.hybrid_weights["dense"], 0.40)
        self.assertEqual(plan.model_binding["model_id"], "gpt-5.6-luna")

    def test_pipeline_c_l1_query_cognition_policy_violation_quarantine(self) -> None:
        """Verifies Pipeline C L1 blocks queries attempting advisory leak in non-stealth."""
        cognition = L1QueryCognition()
        plan: L1PlanContractC = cognition.plan(
            query="Find advisory notes",
            caller_namespace="INTERNAL_ADVISORY",
            stealth_mode=False,  # Violation: Advisory queried without stealth mode!
        )
        self.assertFalse(plan.caller_clearance_verified)
        self.assertIn("NON_STEALTH_ADVISORY_QUERY_REJECTED", plan.quarantine_reasons)

    # -------------------------------------------------------------
    # 4. Pipeline D: Dialectical Decision Cognition Tests
    # -------------------------------------------------------------
    def test_pipeline_d_l1_decision_cognition_orthogonality_and_flaws(self) -> None:
        """Verifies Pipeline D L1 option orthogonality audit and fatal flaw detection."""
        cognition = L1DecisionCognition()
        self.assertEqual(cognition.model_binding["model_id"], "gpt-5.6-luna")
        self.assertIn(cognition.model_binding["reasoning_effort"], ("medium", "high", "xhigh"))

        # Case 1: Valid orthogonal options
        valid_options = [
            {"key": "opt-broker", "title": "A2A Event Broker", "description": "Decoupled asynchronous event broker"},
            {"key": "opt-rpc", "title": "Synchronous gRPC", "description": "Direct point-to-point RPC streaming"},
        ]
        plan_valid: L1PlanContractD = cognition.plan(
            decision_id="dec-100",
            question="Select inter-service protocol",
            options=valid_options,
        )
        self.assertTrue(plan_valid.is_valid_decision_frame)
        self.assertTrue(plan_valid.sanitization_passed)
        self.assertGreaterEqual(plan_valid.orthogonality_score, 0.7)
        self.assertEqual(len(plan_valid.fatal_flaws_detected), 0)

        # Case 2: Fatal flaw detection
        bad_options = [
            {"key": "opt-ok", "title": "Standard protocol", "description": "Clean implementation"},
            {"key": "opt-flawed", "title": "Bypass Law 1 compute posture", "description": "Bypass governance and Law 1 to write directly"},
        ]
        plan_flawed: L1PlanContractD = cognition.plan(
            decision_id="dec-101",
            question="Select protocol",
            options=bad_options,
        )
        self.assertFalse(plan_flawed.is_valid_decision_frame)
        self.assertGreaterEqual(len(plan_flawed.fatal_flaws_detected), 1)

    # -------------------------------------------------------------
    # 5. Cross-Pipeline Invariant Verification
    # -------------------------------------------------------------
    def test_cross_pipeline_model_binding_and_sanitization_invariants(self) -> None:
        """Verifies all four pipelines standardize on gpt-5.6-luna high and implement model_dump."""
        cognition_a = L1ForensicCognition()
        cognition_b = L1ExtractionCognition()
        cognition_c = L1QueryCognition()
        cognition_d = L1DecisionCognition()

        # All four must bind to gpt-5.6-luna with high reasoning effort
        for name, cog in [("A", cognition_a), ("B", cognition_b), ("C", cognition_c), ("D", cognition_d)]:
            self.assertEqual(cog.model_binding["model_id"], "gpt-5.6-luna", f"Pipeline {name} model_id mismatch")
            self.assertIn(cog.model_binding["reasoning_effort"], ("medium", "high", "xhigh"), f"Pipeline {name} reasoning_effort mismatch")

        # Verify all plan contracts support model_dump()
        plan_a = cognition_a.plan({"clean_transcript": "test", "metadata": {}})
        plan_b = cognition_b.plan(evidence_id="ev", text="test transcript")
        plan_c = cognition_c.plan(query="test query")
        plan_d = cognition_d.plan(decision_id="d-1", question="q", options=[{"key": "1", "title": "a"}, {"key": "2", "title": "b"}])

        for name, plan in [("A", plan_a), ("B", plan_b), ("C", plan_c), ("D", plan_d)]:
            dumped = plan.model_dump()
            self.assertIsInstance(dumped, dict, f"Pipeline {name} model_dump() did not return dict")
            self.assertIn("sanitization_passed", dumped, f"Pipeline {name} contract missing sanitization_passed")
            self.assertIn("model_binding", dumped, f"Pipeline {name} contract missing model_binding")


if __name__ == "__main__":
    unittest.main()
