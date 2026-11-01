"""End-to-End Multi-Agent Mesh Integration Test.

Validates the full A2A distributed mesh across Pipelines A through D,
certifying the 7 Non-Negotiable Engineering Laws:
- Law 1: Physical compute fork before extraction.
- Law 2: Governed INTERNAL_ADVISORY namespace (Apple Metal local only, zero egress).
- Law 3: Untrusted Fast Path (ephemeral live STT context; read-only, non-canonical, [UNTRUSTED_FAST_PATH] badge).
- Law 4: Immutable provenance first via Claim-Check bounded pointers (ClaimCheckPointer).
- Law 5: Multi-dimensional soft-quarantine tagging (no destructive deletion).
- Law 6: Policy before retrieval (filter namespace, ACL, and quarantine before vector search).
- Law 7: Invariant pipeline sequencing (Evidence -> Canonical Graph Delta -> Executive Briefing).
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from dual_pipeline.a2a import (
    ClaimCheckPointer,
)
from dual_pipeline.decision.agents import (
    FAST_PATH_BADGE,
    DecisionOrchestrator,
    HitlArbiterAgent,
)
from dual_pipeline.retrieval.agents import (
    RetrievalCoordinator,
)
from pipeline_a_intake.agents import (
    IngestionCoordinator,
    RosterReconcilerAgent,
)
from pipeline_b_extraction.agents import (
    AdjudicationJudgeAgent,
    ExtractionCoordinator,
)


class TestA2AEndToEndMesh(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "e2e_hitl_ledger.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_complete_end_to_end_mesh_and_7_laws(self) -> None:
        """Executes full pipeline A -> B -> C -> D flow verifying all 7 laws."""
        # -------------------------------------------------------------
        # Step 1: Pipeline A - Multimodal Ingestion & Forensic Normalization
        # -------------------------------------------------------------
        ingestion_coord = IngestionCoordinator(mock_mode=True)

        raw_transcript = (
            "[00:00:05] Alice M: We need to adopt the BGE-M3 embedding model and deploy parallel A2A mesh.\n"
            "[00:00:15] Bob: Ag-agreed. John from Sla-lom will lead the architecture.\n"
            "[00:00:25] Alice M: We commit to delivery by 09/12/2026."
        )
        known_roster = ["Alice Miller", "Bob Vance", "John Smith"]
        slide_decks = [{
            "slide_deck_id": "deck-arch-v1",
            "slide_number": 4,
            "title": "Project Beacon A2A Architecture",
            "extracted_text": "Project Beacon Technical Architecture: Multi-Agent Parallel Event Mesh",
            "diagram_type": "technical_architecture",
            "entities": ["BGE-M3", "A2A Mesh", "Project Beacon"],
        }]

        intake_res = ingestion_coord.run_ingestion_pipeline(
            meeting_name="A2A Architecture Alignment",
            raw_transcript_text=raw_transcript,
            known_roster=known_roster,
            slide_decks=slide_decks,
        )

        # Law 4: Immutable provenance first via ClaimCheckPointer
        claim_pointer: ClaimCheckPointer = intake_res["claim_check_pointer"]
        self.assertIsInstance(claim_pointer, ClaimCheckPointer)
        self.assertEqual(claim_pointer.source_id, "src-meeting-transcript")
        self.assertTrue(len(claim_pointer.content_digest) == 64)

        # Check normalization output (Artifact 01)
        normalized_transcript = intake_res["artifact_01_normalized_transcript"]
        self.assertIn("Bob Vance", normalized_transcript)
        self.assertIn("Forensic", normalized_transcript)

        # L1 Cognition Verification: Pipeline A
        self.assertIn("l1_plan", intake_res)
        l1_plan_a = intake_res["l1_plan"]
        self.assertEqual(l1_plan_a.model_binding["model_id"], "gpt-5.6-luna")
        self.assertEqual(l1_plan_a.model_binding["reasoning_effort"], "high")
        self.assertTrue(l1_plan_a.sanitization_passed)

        # -------------------------------------------------------------
        # Step 2: Pipeline B - Dual-Compute Extraction & Law 1 Fork
        # -------------------------------------------------------------
        extraction_coord = ExtractionCoordinator(mock_mode=True)

        # Execute Law 1 physical compute fork
        extraction_res = extraction_coord.coordinate_extraction(
            source_id="src-meeting-transcript",
            evidence_id="ev-transcript-01",
            content_digest=claim_pointer.content_digest,
            clean_transcript_text=normalized_transcript,
            stealth_mode=True,
        )

        # Law 1 & Law 2 validation:
        # Client-safe facts (Cloud GPT-5.6-sol)
        client_safe_facts = extraction_res["client_safe_facts"]
        self.assertGreater(len(client_safe_facts), 0)
        for f in client_safe_facts:
            self.assertEqual(f["namespace"], "CLIENT_SAFE")

        # Internal advisory facts (Local Apple Metal only, zero external egress)
        advisory_facts = extraction_res["internal_advisory_facts"]
        self.assertGreater(len(advisory_facts), 0)
        for f in advisory_facts:
            self.assertEqual(f["namespace"], "INTERNAL_ADVISORY")
            self.assertEqual(f["compute_posture"], "INTERNAL_ADVISORY")

        # L1 Cognition Verification: Pipeline B
        self.assertIn("l1_plan", extraction_res)
        l1_plan_b = extraction_res["l1_plan"]
        self.assertEqual(l1_plan_b.model_binding["model_id"], "gpt-5.6-luna")
        self.assertEqual(l1_plan_b.model_binding["reasoning_effort"], "high")
        self.assertTrue(l1_plan_b.sanitization_passed)

        # Law 5: Multi-dimensional soft-quarantine tagging (no destructive deletion)
        # Create candidate fact with low confidence to test soft quarantine
        quarantine_test_fact = {
            "node_id": "candidate-dubious-claim",
            "content": "Unverified rumor about cloud budget cut",
            "confidence": 0.35,  # below 0.60 floor
            "evidence_id": "ev-transcript-01",
            "source_type": "transcript",
            "namespace": "CLIENT_SAFE",
        }
        judge = AdjudicationJudgeAgent()
        adjudication = judge.adjudicate(quarantine_test_fact)
        self.assertTrue(adjudication["is_quarantined"])
        self.assertIn("LOW_CONFIDENCE_BELOW_THRESHOLD", adjudication["quarantine_reasons"])
        # Verify non-destructive preservation
        self.assertEqual(adjudication["retained_node_id"], "candidate-dubious-claim")

        # -------------------------------------------------------------
        # Step 3: Pipeline C - Multi-Perspective Hybrid Retrieval & Law 6 Policy Gate
        # -------------------------------------------------------------
        retrieval_coord = RetrievalCoordinator(mock_mode=True)

        # Combine all extracted facts for retrieval indexing
        all_candidate_facts = list(client_safe_facts) + [f if isinstance(f, dict) else f.to_dict() for f in advisory_facts]

        # Law 6: Pre-retrieval policy gatekeeper
        # A CLIENT_SAFE query in stealth mode must NEVER leak INTERNAL_ADVISORY facts
        retrieval_res = retrieval_coord.retrieve(
            query="What are the strategic commitments and delivery timeline for BGE-M3?",
            candidate_facts=all_candidate_facts,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
            target_latency_ms=120.0,
        )

        ranked_results = retrieval_res["ranked_results"]
        self.assertGreater(len(ranked_results), 0)
        for item in ranked_results:
            self.assertNotEqual(item.get("namespace"), "INTERNAL_ADVISORY", "Law 6 Leak: Advisory leaked to client safe query!")

        # Verify 11 release gates passed
        self.assertTrue(retrieval_res["release_passed"])
        self.assertLess(retrieval_res["actual_latency_ms"], 7500.0)

        # L1 Cognition Verification: Pipeline C
        self.assertIn("l1_plan", retrieval_res)
        l1_plan_c = retrieval_res["l1_plan"]
        self.assertEqual(l1_plan_c.model_binding["model_id"], "gpt-5.6-luna")
        self.assertEqual(l1_plan_c.model_binding["reasoning_effort"], "high")
        self.assertTrue(l1_plan_c.sanitization_passed)

        # -------------------------------------------------------------
        # Step 4: Pipeline D - Dialectical Decision Intelligence & Law 7 Synthesis
        # -------------------------------------------------------------
        decision_coord = DecisionOrchestrator(
            hitl_arbiter=HitlArbiterAgent(ledger_path=self.ledger_path),
        )

        # Law 3: Live ephemeral STT context injection
        live_meeting_turns = [{
            "speaker": "Alice Miller",
            "text": "The executive committee is aligned on the parallel event mesh approach.",
            "timestamp": "00:35:10",
        }]

        # Candidate architectural options for dialectical debate
        decision_options = [
            {
                "key": "opt-event-mesh",
                "title": "Decoupled parallel A2A event broker with GPT-5.6-sol",
                "description": "Modular decoupled architecture with automated deployment, turnkey execution, and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-shared-db",
                "title": "Direct database coupling without broker",
                "description": "Tightly coupled direct database writes with architectural debt and contention",
                "estimated_roi": 0.40,
            },
        ]

        canonical_evidence = [{"evidence_id": "ev-transcript-01", "digest": claim_pointer.content_digest}]
        canonical_delta = {
            "nodes": client_safe_facts,
            "assertions": [{"assertion_id": "as-bge-m3-commit", "status": "CONFIRMED"}],
        }

        # Run dialectical decision
        decision_result = decision_coord.orchestrate_decision(
            decision_id="decision-a2a-rollout",
            question="Select multi-agent inter-process communication mesh",
            options=decision_options,
            canonical_evidence=canonical_evidence,
            canonical_graph_delta=canonical_delta,
            live_turns=live_meeting_turns,
            margin_threshold=0.25,
        )

        # Verify terminal state and HITL margin gate
        self.assertEqual(decision_result["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(decision_result["should_surface_hitl"])
        self.assertGreaterEqual(decision_result["margin"], 0.25)

        # Verify Law 3: Fast-path context had [UNTRUSTED_FAST_PATH] badge
        fast_path_turns = decision_coord.fast_path.get_ephemeral_context()
        self.assertEqual(len(fast_path_turns), 1)
        self.assertIn(FAST_PATH_BADGE, fast_path_turns[0]["badged_text"])
        self.assertTrue(fast_path_turns[0]["is_untrusted_fast_path"])

        # Verify Law 7: Invariant Pipeline Sequencing produced verified executive recap
        executive_recap = decision_result["executive_recap"]
        self.assertIsNotNone(executive_recap)
        self.assertIn("Executive Briefing", executive_recap)
        self.assertIn("09/12/2026", executive_recap)
        self.assertIn("Actionable Next Steps", executive_recap)
        self.assertIn("Law 7 Invariant Sequencing", executive_recap)

        # Verify Figma 4K specifications generated
        figma_spec = decision_result["figma_4k_spec"]
        self.assertIsNotNone(figma_spec)
        self.assertEqual(figma_spec["canvas"]["width"], 3840)
        self.assertEqual(figma_spec["canvas"]["height"], 2160)
        self.assertEqual(figma_spec["typography"]["font_family"], "Inter")

        # L1 Cognition Verification: Pipeline D
        self.assertIn("l1_plan", decision_result)
        l1_plan_d = decision_result["l1_plan"]
        self.assertIn(l1_plan_d["model_binding"]["model_id"], ("gpt-5.6-sol", "gpt-5.6-luna"))
        self.assertEqual(l1_plan_d["model_binding"]["reasoning_effort"], "high")
        self.assertTrue(l1_plan_d["sanitization_passed"])

    def test_complete_end_to_end_mesh_20_percent_hitl_and_ledger_persistence(self) -> None:
        """Verifies Pipelines A through D follow 20% margin HITL and autonomous ledger persistence under gpt-5.6-luna."""
        from pathlib import Path

        from dual_pipeline.hitl_governance import HitlSurfacingMode
        from dual_pipeline.hitl_ui_bridge import AntigravityUIBridge

        arbiter = HitlArbiterAgent(
            ledger_path=self.ledger_path,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        ui_bridge = AntigravityUIBridge(arbiter=arbiter)

        # -----------------------------------------------------------------
        # 1. Pipeline A: Speaker Disambiguation under 20% Margin Rule
        # -----------------------------------------------------------------
        roster_agent = RosterReconcilerAgent(
            mock_mode=True,
            hitl_arbiter=arbiter,
            ui_bridge=ui_bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(roster_agent.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(roster_agent.card.model_binding.reasoning_effort, "high")

        # Close candidate scores (Delta <= 0.20) -> auto-records in ledger
        candidates_close = [
            {"key": "user-1", "label": "Marcus Balk (PartnerAdvisory)", "confidence_score": 0.65},
            {"key": "user-2", "label": "Marcus Blake (NEE)", "confidence_score": 0.60},
        ]
        res_a_close = ui_bridge.arbitrate_and_present(
            pipeline_id="pipeline-a",
            decision_id="dec-e2e-roster-close",
            category="roster_reconciliation",
            question="Disambiguate speaker label 'Marcus'",
            options=candidates_close,
            margin_threshold=0.20,
        )
        self.assertEqual(res_a_close["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(res_a_close["should_surface"])
        self.assertLessEqual(res_a_close["margin"], 0.20)
        self.assertIsNotNone(res_a_close["ledger_record"])

        # Divergent scores (Delta > 0.20) -> surfaces mandatory UI card
        candidates_div = [
            {"key": "user-1", "label": "Marcus Balk (PartnerAdvisory)", "confidence_score": 0.90},
            {"key": "user-3", "label": "Mark Ball (External)", "confidence_score": 0.50},
        ]
        res_a_div = ui_bridge.arbitrate_and_present(
            pipeline_id="pipeline-a",
            decision_id="dec-e2e-roster-div",
            category="roster_reconciliation",
            question="Disambiguate speaker label 'Marcus'",
            options=candidates_div,
            margin_threshold=0.20,
        )
        self.assertEqual(res_a_div["status"], "HITL_REQUIRED")
        self.assertTrue(res_a_div["should_surface"])
        self.assertGreater(res_a_div["margin"], 0.20)
        self.assertIsNotNone(res_a_div["atomic_card"])

        # -----------------------------------------------------------------
        # 2. Pipeline B: Fact Adjudication Disposition under 20% Margin Rule
        # -----------------------------------------------------------------
        judge_agent = AdjudicationJudgeAgent(
            hitl_arbiter=arbiter,
            ui_bridge=ui_bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(judge_agent.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(judge_agent.card.model_binding.reasoning_effort, "high")

        # Divergent fact (confidence = 0.85, Delta = 0.85 - 0.15 = 0.70 > 0.20) -> surfaces HITL
        res_b_div = judge_agent.arbitrate_disposition(
            {"node_id": "fact-bge-m3", "content": "Adopt BGE-M3 model", "confidence": 0.85},
            margin_threshold=0.20,
        )
        self.assertEqual(res_b_div["adjudication_decision"]["status"], "HITL_REQUIRED")
        self.assertTrue(res_b_div["adjudication_decision"]["should_surface"])

        # Close fact (confidence = 0.58, Delta = 0.58 - 0.42 = 0.16 <= 0.20) -> auto-records in ledger
        res_b_close = judge_agent.arbitrate_disposition(
            {"node_id": "fact-borderline", "content": "Borderline architectural assertion", "confidence": 0.58},
            margin_threshold=0.20,
        )
        self.assertEqual(res_b_close["adjudication_decision"]["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(res_b_close["adjudication_decision"]["should_surface"])
        self.assertIsNotNone(res_b_close["adjudication_decision"]["ledger_record"])

        # -----------------------------------------------------------------
        # 3. Pipeline C: Retrieval Ranking under 20% Margin Rule
        # -----------------------------------------------------------------
        retrieval_coord = RetrievalCoordinator(
            mock_mode=True,
            hitl_arbiter=arbiter,
            ui_bridge=ui_bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(retrieval_coord.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(retrieval_coord.card.model_binding.reasoning_effort, "high")

        # Divergent retrieval ranking (Delta = 0.85 - 0.50 = 0.35 > 0.20) -> surfaces HITL
        retrieval_div = [
            {"node_id": "fact-ret-1", "name": "BGE-M3 Multi-Vector Spec", "confidence": 0.85},
            {"node_id": "fact-ret-2", "name": "General Embeddings", "confidence": 0.50},
        ]
        res_c_div = retrieval_coord.arbitrate_ranking(
            fused_facts=retrieval_div,
            query="BGE-M3 Architecture",
            margin_threshold=0.20,
        )
        self.assertEqual(res_c_div["status"], "HITL_REQUIRED")
        self.assertTrue(res_c_div["should_surface"])

        # Close retrieval ranking (Delta = 0.75 - 0.68 = 0.07 <= 0.20) -> auto-records in ledger
        retrieval_close = [
            {"node_id": "fact-ret-3", "name": "Kafka Event Bus Deployment", "confidence": 0.75},
            {"node_id": "fact-ret-4", "name": "RabbitMQ Event Broker", "confidence": 0.68},
        ]
        res_c_close = retrieval_coord.arbitrate_ranking(
            fused_facts=retrieval_close,
            query="Event Broker Architecture",
            margin_threshold=0.20,
        )
        self.assertEqual(res_c_close["status"], "AUTONOMOUS_APPROVED")
        self.assertFalse(res_c_close["should_surface"])
        self.assertIsNotNone(res_c_close["ledger_record"])

        # -----------------------------------------------------------------
        # 4. Pipeline D: Dialectical Decision under 20% Margin Rule
        # -----------------------------------------------------------------
        decision_coord = DecisionOrchestrator(
            hitl_arbiter=arbiter,
            ui_bridge=ui_bridge,
            model_id="gpt-5.6-luna",
            reasoning_effort="high",
        )
        self.assertEqual(decision_coord.card.model_binding.model_id, "gpt-5.6-luna")
        self.assertEqual(decision_coord.card.model_binding.reasoning_effort, "high")

        # Divergent options (Delta > 0.20) -> surfaces mandatory HITL in UI
        options_divergent = [
            {"key": "opt-mesh", "title": "Turnkey Event Mesh", "description": "High ROI", "estimated_roi": 0.95},
            {"key": "opt-monolith", "title": "Monolith Patch", "description": "High debt", "estimated_roi": 0.30},
        ]
        res_d_div = decision_coord.orchestrate_decision(
            decision_id="dec-mesh-div",
            question="Select multi-agent mesh architecture",
            options=options_divergent,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
        )
        self.assertEqual(res_d_div["terminal_state"], "ESCALATED_HITL")
        self.assertTrue(res_d_div["should_surface_hitl"])
        self.assertIsNotNone(res_d_div["atomic_card"])

        # Close options (Delta <= 0.20) -> auto-records in ledger and resolves
        options_close = [
            {"key": "opt-grpc", "title": "gRPC Streaming Mesh", "description": "Standard", "estimated_roi": 0.75},
            {"key": "opt-rest", "title": "REST JSON Mesh", "description": "Standard", "estimated_roi": 0.72},
        ]
        res_d_close = decision_coord.orchestrate_decision(
            decision_id="dec-mesh-close",
            question="Select transport protocol",
            options=options_close,
            margin_threshold=0.20,
            surfacing_mode=HitlSurfacingMode.DIVERGENCE_GT_THRESHOLD,
        )
        self.assertEqual(res_d_close["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(res_d_close["should_surface_hitl"])
        self.assertIsNotNone(res_d_close["ledger_record"])

        # -----------------------------------------------------------------
        # 5. Persistent Ledger Integrity Check
        # -----------------------------------------------------------------
        ledger_file = Path(self.ledger_path)
        self.assertTrue(ledger_file.exists())
        history = arbiter.store.get_history()
        # 4 autonomous decisions should be recorded (Pipeline A, B, C, D)
        self.assertEqual(len(history), 4)
        for entry in history:
            self.assertEqual(entry["operator_id"], "autonomous_engine")
            self.assertTrue(entry["metadata"]["autonomous"])
            self.assertLessEqual(entry["metadata"]["margin"], 0.20)


if __name__ == "__main__":
    unittest.main()

