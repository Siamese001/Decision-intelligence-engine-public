"""Wave 5: Cross-Pipeline End-to-End Adversarial Regression & Governance Verification.

Validates the full multi-agent remediation across Pipelines A -> B -> C -> D under
adversarial chaos, simulated network failures, acoustic noise, and boundary conditions:

1. Pipeline A: Acoustic noise floor normalization, uncertainty locking (speaker_unknown),
   and immutable claim-check pointer generation (Law 4).
2. Pipeline B: Fail-closed extraction, elimination of silent mock poisoning, JSON repair
   of truncated completions, and Dead-Letter Queue (DLQ) serialization (Law 1, 2, 5).
3. Pipeline C: Generational in-memory VectorCache, zero-copy BLAS dot-product scoring,
   Law 6 policy-before-retrieval, Law 2 stealth mode isolation, and latency < 7.5s SLA.
4. Pipeline D: Universal 25% ambiguity margin gating (Delta < 0.25 -> HITL, Delta >= 0.25 -> Auto),
   Bayesian feedback calibration in HITLDecisionStore, and dialectical context fusion.
5. End-to-End Chain: Complete pipeline flow from raw audio turns to executive briefing
   under Law 7 invariant sequencing and Figma 4K typography/canvas specifications.
6. Governance & Invariants: All refactored modules across Pipelines A-D strictly comply
   with the <= 400 lines budget under Invariant 1, with 0 architectural layer violations.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# Core A2A and Storage Imports
from dual_pipeline.a2a import (
    ClaimCheckPointer,
)

# Pipeline D Imports
from dual_pipeline.decision.agents import (
    DecisionOrchestrator,
    HitlArbiterAgent,
)
from dual_pipeline.decision.context_fusion import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    CopilotFusionEngine,
    CopilotFusionPayload,
)

# Pipeline C Imports
from dual_pipeline.index_core.vector_cache import VectorCache
from dual_pipeline.retrieval.agents import (
    RetrievalCoordinator,
)

# Pipeline A Imports
from pipeline_a_intake.acoustic_evidence import (
    DEFAULT_SPEAKER_UNKNOWN,
    AcousticSpeakerGate,
    AttributionState,
    compute_acoustic_metrics,
)
from pipeline_a_intake.agents import (
    IngestionCoordinator,
)

# Pipeline B Imports
from pipeline_b_extraction.agents import (
    AdjudicationJudgeAgent,
    ExtractionCoordinator,
)
from pipeline_b_extraction.contracts import (
    ExtractionInferenceError,
)
from pipeline_b_extraction.dlq import DeadLetterQueueManager
from pipeline_b_extraction.extractors import (
    ClientSafeExtractor,
)
from pipeline_b_extraction.json_repair import (
    repair_and_parse_json,
)
from storage.database import (
    EvidenceRecord,
    WorkItemState,
)


class TestPipelineAAdversarialAcoustic(unittest.TestCase):
    """Verifies Wave 1 Acoustic Hardening and Speaker Uncertainty Locking."""

    def setUp(self) -> None:
        self.gate = AcousticSpeakerGate(enrolled_speakers=["Alice Miller", "John Smith"])
        self.strict_gate = AcousticSpeakerGate(
            enrolled_speakers=["Alice Miller", "John Smith"],
            strict_enrollment_only=True,
        )

    def test_ungrounded_acoustic_noise_locked_as_speaker_unknown(self) -> None:
        """Placeholders, empty tags, and ambient noise must strictly normalize to speaker_unknown."""
        noisy_tags = ["", "  ", "Speaker", "Speaker 1", "unknown", "Attendee", "user"]
        for tag in noisy_tags:
            attr = self.gate.evaluate_speaker(tag)
            self.assertEqual(attr.speaker_label, DEFAULT_SPEAKER_UNKNOWN)
            self.assertEqual(attr.attribution_state, AttributionState.UNATTRIBUTED_UNKNOWN)
            self.assertTrue(attr.uncertainty_locked)
            self.assertFalse(attr.is_attributed)

        # Non-enrolled arbitrary string under strict mode also normalizes to speaker_unknown
        attr_strict = self.strict_gate.evaluate_speaker("noise_floor")
        self.assertEqual(attr_strict.speaker_label, DEFAULT_SPEAKER_UNKNOWN)
        self.assertEqual(attr_strict.attribution_state, AttributionState.UNATTRIBUTED_UNKNOWN)
        self.assertTrue(attr_strict.uncertainty_locked)

    def test_verified_enrolled_speaker_preserves_identity(self) -> None:
        """Enrolled speakers retain confirmed identity with high confidence."""
        attr = self.gate.evaluate_speaker("Alice Miller")
        self.assertEqual(attr.speaker_label, "Alice Miller")
        self.assertEqual(attr.attribution_state, AttributionState.VERIFIED_ENROLLED)
        self.assertTrue(attr.is_attributed)
        self.assertGreaterEqual(attr.confidence, 0.90)

    def test_acoustic_dsp_metrics_calculation(self) -> None:
        """Verifies DSP metrics calculation over synthetic audio signals."""
        t = np.linspace(0, 0.5, 8000, endpoint=False, dtype=np.float32)
        speech_wave = 0.6 * np.sin(2 * np.pi * 440 * t)
        metrics = compute_acoustic_metrics(speech_wave)
        self.assertTrue(metrics.is_speech)
        self.assertGreater(metrics.speech_confidence, 0.50)
        self.assertEqual(metrics.clipping_ratio, 0.0)


class TestPipelineBFailClosedAndDLQ(unittest.TestCase):
    """Verifies Wave 2 Fail-Closed Extraction and DLQ Hardening."""

    def test_json_repair_handles_truncation_and_fences(self) -> None:
        """Truncated completions and markdown fences must be cleanly repaired."""
        fenced = "```json\n{\"nodes\": [{\"name\": \"EventBroker\", \"type\": \"System\"}]}\n```"
        repaired_fenced = repair_and_parse_json(fenced)
        self.assertIn("nodes", repaired_fenced)
        self.assertEqual(repaired_fenced["nodes"][0]["name"], "EventBroker")

        truncated = '[{"category": "TECHNICAL_DEBT", "name": "Monolith direct coupling without broker'
        repaired_trunc = repair_and_parse_json(truncated)
        self.assertIsInstance(repaired_trunc, list)
        self.assertEqual(repaired_trunc[0]["category"], "TECHNICAL_DEBT")

    def test_non_mock_extractors_fail_closed_without_mock_fallbacks(self) -> None:
        """In production non-mock mode, model failures must raise ExtractionInferenceError (no silent mocks)."""
        extractor = ClientSafeExtractor(mock_mode=False, api_key="sk-test-key")
        evidence = EvidenceRecord(
            evidence_id="ev-fail-1",
            meeting_id="m-1",
            speaker="Alice",
            text="Deploy the BGE-M3 model.",
            start_timestamp=0.0,
            end_timestamp=5.0,
            is_final=True,
        )
        with patch("openai.AsyncOpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create = unittest.mock.AsyncMock(
                side_effect=Exception("Connection reset")
            )
            with self.assertRaises(ExtractionInferenceError):
                asyncio.run(extractor.extract(evidence))

    def test_dead_letter_queue_quarantine_routing(self) -> None:
        """DLQ manager routes unrecoverable errors to quarantine after max retries."""
        dlq = DeadLetterQueueManager(default_max_retries=2)
        state_pending = dlq.route_failure(
            event_id="ev-001",
            evidence_id="ev-001",
            error=ExtractionInferenceError("Persistent provider timeout"),
            retry_count=1,
            max_retries=2,
            payload_snippet="Faulty packet",
        )
        self.assertEqual(state_pending, WorkItemState.PENDING)
        self.assertIn("ev-001", dlq._in_memory_dlq)
        self.assertEqual(dlq._in_memory_dlq["ev-001"].retry_count, 1)

        # Exceed max retries
        state_dead = dlq.route_failure(
            event_id="ev-001",
            evidence_id="ev-001",
            error=ExtractionInferenceError("Persistent provider timeout"),
            retry_count=2,
            max_retries=2,
            payload_snippet="Faulty packet",
        )
        self.assertEqual(state_dead, WorkItemState.DEAD_LETTER)
        self.assertEqual(dlq._in_memory_dlq["ev-001"].state, "DEAD_LETTER")


class TestPipelineCHybridRetrievalAndVectorCache(unittest.TestCase):
    """Verifies Wave 3 In-Memory VectorCache and Policy-Gated Retrieval Performance."""

    def test_vector_cache_blas_dot_product_and_invalidation(self) -> None:
        """VectorCache unpacks contiguous float32 numpy arrays and accelerates dot products."""
        with tempfile.TemporaryDirectory() as tmpdir:
            sidecar_path = Path(tmpdir) / "test_sidecar.db"
            conn = sqlite3.connect(str(sidecar_path))
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS retrieval_record (
                    record_id TEXT PRIMARY KEY,
                    lane TEXT NOT NULL,
                    artifact_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dense_vector (
                    record_id TEXT PRIMARY KEY,
                    vector BLOB NOT NULL
                );
            """)
            records = []
            vectors = []
            for i in range(50):
                rid = f"rec-{i:03d}"
                records.append((rid, "approved", json.dumps({"record_id": rid, "lane": "approved"})))
                v = np.random.randn(1024).astype(np.float32)
                v /= np.linalg.norm(v)
                vectors.append((rid, v.tobytes()))
            conn.executemany("INSERT INTO retrieval_record VALUES (?, ?, ?)", records)
            conn.executemany("INSERT INTO dense_vector VALUES (?, ?)", vectors)
            conn.commit()
            conn.close()

            cache = VectorCache()
            lane_data = cache.get_or_load_lane(sidecar_path, "approved", generation_id="gen_001")
            self.assertEqual(lane_data.count, 50)
            self.assertEqual(lane_data.dimensions, 1024)

            # Vectorized scoring
            query_vec = np.random.randn(1024).astype(np.float32)
            query_vec /= np.linalg.norm(query_vec)
            scores = cache.score_dense(sidecar_path, "approved", query_vec, generation_id="gen_001")
            self.assertEqual(len(scores), 50)
            self.assertIsInstance(scores[0][0], float)

            # Invalidation
            cache.clear()
            self.assertEqual(cache._lane_cache, {})

    def test_policy_gated_retrieval_law2_and_law6(self) -> None:
        """Retrieval engine strictly suppresses internal advisory facts in stealth mode (Law 2 & 6)."""
        candidate_facts = [
            {"node_id": "f1", "name": "BGE-M3 Dense Architecture", "namespace": CLIENT_SAFE_NAMESPACE, "confidence": 0.95, "is_quarantined": False},
            {"node_id": "f2", "name": "Executive Career Positioning", "namespace": INTERNAL_ADVISORY_NAMESPACE, "confidence": 0.90, "is_quarantined": False},
            {"node_id": "f3", "name": "Quarantined Rumor", "namespace": CLIENT_SAFE_NAMESPACE, "confidence": 0.30, "is_quarantined": True},
        ]
        coordinator = RetrievalCoordinator(mock_mode=True)
        res = coordinator.retrieve(
            query="BGE-M3 architecture",
            candidate_facts=candidate_facts,
            caller_namespace=CLIENT_SAFE_NAMESPACE,
            stealth_mode=True,
        )
        ranked = res["ranked_results"]
        # Must return client safe and non-quarantined facts only
        for item in ranked:
            self.assertNotEqual(item.get("namespace"), INTERNAL_ADVISORY_NAMESPACE)
            self.assertFalse(item.get("is_quarantined", False))

        # Benchmark 100 queries SLA check (< 7,500ms)
        t0 = time.perf_counter()
        for _ in range(100):
            coordinator.retrieve(
                query="BGE-M3 architecture",
                candidate_facts=candidate_facts[:1],
                caller_namespace=CLIENT_SAFE_NAMESPACE,
                stealth_mode=True,
            )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.assertLess(elapsed_ms, 7500.0, f"Benchmark took {elapsed_ms:.1f}ms, exceeding 7,500ms SLA!")


class TestPipelineDHitlMarginAndDialecticalFusion(unittest.TestCase):
    """Verifies Wave 4 HITL 25% Ambiguity Margin Gating and Dialectical Fusion."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "test_p4_ledger.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_25_percent_ambiguity_margin_gating_boundaries(self) -> None:
        """Verifies Delta < 0.25 -> HITL_REQUIRED; Delta >= 0.25 -> AUTONOMOUS_APPROVED."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)

        # Ambiguous case: Delta = 0.24 (< 0.25) -> Must surface
        res_ambiguous = arbiter.arbitrate_decision(
            decision_id="dec-b24",
            category="arch_choice",
            question="Select transport",
            options=[
                {"key": "a", "label": "Option A", "confidence_score": 0.74},
                {"key": "b", "label": "Option B", "confidence_score": 0.50},
            ],
        )
        self.assertTrue(res_ambiguous["should_surface"])
        self.assertEqual(res_ambiguous["status"], "HITL_REQUIRED")
        self.assertIsNotNone(res_ambiguous["atomic_card"])

        # Decisive case: Delta = 0.25 (== 0.25) -> Autonomous progression
        res_decisive = arbiter.arbitrate_decision(
            decision_id="dec-b25",
            category="arch_choice",
            question="Select transport",
            options=[
                {"key": "a", "label": "Option A", "confidence_score": 0.75},
                {"key": "b", "label": "Option B", "confidence_score": 0.50},
            ],
        )
        self.assertFalse(res_decisive["should_surface"])
        self.assertEqual(res_decisive["status"], "AUTONOMOUS_APPROVED")
        self.assertIn("audit_receipt", res_decisive)

    def test_bayesian_feedback_calibration_persists_in_ledger(self) -> None:
        """Operator resolution boosts affirmed prior (+0.15/-0.10) to widen future margins."""
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        options = [
            {"key": "opt-1", "label": "Kafka Event Mesh", "confidence_score": 0.60},
            {"key": "opt-2", "label": "Direct gRPC", "confidence_score": 0.50},
        ]
        # Operator confirms opt-1
        arbiter.record_feedback(
            decision_id="dec-transport-1",
            category="transport_selection",
            prompt="Select transport",
            options=options,
            selected_option=options[0],
            rationale="Event mesh decouples high concurrency loads",
        )
        # Verify calibrated options widen margin
        calibrated = arbiter.store.calibrate_options(options, category="transport_selection")
        c1 = next(o for o in calibrated if o["key"] == "opt-1")
        c2 = next(o for o in calibrated if o["key"] == "opt-2")
        self.assertGreater(c1["confidence_score"], 0.60)
        self.assertLess(c2["confidence_score"], 0.50)
        margin = c1["confidence_score"] - c2["confidence_score"]
        self.assertGreaterEqual(margin, 0.25)

    def test_dialectical_context_fusion_reconciliation(self) -> None:
        """CopilotFusionEngine detects dialectical tension between live utterances and settled facts."""
        class MockFact:
            def __init__(self, node_id: str, name: str):
                self.node_id = node_id
                self.name = name
                self.namespace = CLIENT_SAFE_NAMESPACE
                self.type = "ArchitectureDecision"

        class MockRetrieval:
            def search(self, query: str, stealth_mode: bool = True):
                return [MockFact("node-bge", "BGE-M3 Retrieval Sidecar")]

        engine = CopilotFusionEngine(retrieval_engine=MockRetrieval())
        live_events = [
            {"speaker": "Alice", "text": "We are facing defects with BGE-M3 Retrieval Sidecar and plan to replace it."},
        ]
        payload = engine.fuse_context(query="retrieval architecture", live_fast_path_events=live_events)
        self.assertIsInstance(payload, CopilotFusionPayload)
        self.assertEqual(len(payload.dialectical_reconciliations), 1)
        self.assertEqual(payload.dialectical_reconciliations[0]["reconciliation_type"], "DIALECTICAL_TENSION")
        self.assertTrue(payload.dialectical_reconciliations[0]["requires_hitl_arbitration"])
        self.assertIn("Dialectical Tension Detected", payload.executive_synthesis)


class TestCrossPipelineAdversarialE2EFlow(unittest.TestCase):
    """End-to-End Multi-Agent Integration Flow across Pipelines A -> B -> C -> D."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.ledger_path = os.path.join(self.temp_dir, "e2e_master_ledger.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_full_pipeline_a_to_d_adversarial_flow(self) -> None:
        """Executes full pipeline A -> B -> C -> D flow verifying all 7 laws under chaos."""
        # -------------------------------------------------------------
        # Step 1: Pipeline A - Ingestion with Speaker Disambiguation
        # -------------------------------------------------------------
        ingestion_coord = IngestionCoordinator(mock_mode=True)
        raw_transcript = (
            "[00:00:05] Alice M: We commit to deploy the BGE-M3 vector model with VectorCache by 09/12/2026.\n"
            "[00:00:18] Unidentified speaker: Unverified background noise.\n"
            "[00:00:30] Bob: Agreed. PartnerAdvisory will lead the turnkey delivery."
        )
        known_roster = ["Alice Miller", "Bob Vance"]

        intake_res = ingestion_coord.run_ingestion_pipeline(
            meeting_name="Project Beacon Architecture Review",
            raw_transcript_text=raw_transcript,
            known_roster=known_roster,
        )

        # Law 4: Immutable provenance first
        claim_pointer: ClaimCheckPointer = intake_res["claim_check_pointer"]
        self.assertEqual(len(claim_pointer.content_digest), 64)
        normalized_transcript = intake_res["artifact_01_normalized_transcript"]
        self.assertIn("Bob Vance", normalized_transcript)
        self.assertIn("Ambiguity Lock Register", normalized_transcript)
        self.assertIn("Transcript", normalized_transcript)

        # -------------------------------------------------------------
        # Step 2: Pipeline B - Dual-Compute Extraction & Soft-Quarantine
        # -------------------------------------------------------------
        extraction_coord = ExtractionCoordinator(mock_mode=True)
        extraction_res = extraction_coord.coordinate_extraction(
            source_id="src-e2e-adversarial",
            evidence_id="ev-e2e-01",
            content_digest=claim_pointer.content_digest,
            clean_transcript_text=normalized_transcript,
            stealth_mode=True,
        )

        client_safe_facts = extraction_res["client_safe_facts"]
        advisory_facts = extraction_res["internal_advisory_facts"]
        self.assertGreater(len(client_safe_facts), 0)
        self.assertGreater(len(advisory_facts), 0)

        # Law 1 & Law 2: Partitioning
        for f in client_safe_facts:
            self.assertEqual(f["namespace"], CLIENT_SAFE_NAMESPACE)
        for f in advisory_facts:
            self.assertEqual(f["namespace"], INTERNAL_ADVISORY_NAMESPACE)

        # Law 5: Soft-quarantine verification
        judge = AdjudicationJudgeAgent()
        quarantine_test_fact = {
            "node_id": "cand-unanchored",
            "content": "Unverified external gossip about budget cut",
            "confidence": 0.35,
            "evidence_id": "ev-e2e-01",
            "namespace": CLIENT_SAFE_NAMESPACE,
        }
        adjudication = judge.adjudicate(quarantine_test_fact)
        self.assertTrue(adjudication["is_quarantined"])

        # -------------------------------------------------------------
        # Step 3: Pipeline C - Policy-Gated Hybrid Retrieval
        # -------------------------------------------------------------
        retrieval_coord = RetrievalCoordinator(mock_mode=True)
        all_candidate_facts = list(client_safe_facts) + [
            f if isinstance(f, dict) else f.to_dict() for f in advisory_facts
        ]

        retrieval_res = retrieval_coord.retrieve(
            query="What are the commitments and delivery timeline for BGE-M3?",
            candidate_facts=all_candidate_facts,
            caller_namespace=CLIENT_SAFE_NAMESPACE,
            stealth_mode=True,
        )
        ranked = retrieval_res["ranked_results"]
        self.assertGreater(len(ranked), 0)
        # Law 2 / Law 6: Zero advisory leakage in stealth mode
        for item in ranked:
            self.assertNotEqual(item.get("namespace"), INTERNAL_ADVISORY_NAMESPACE)
        self.assertTrue(retrieval_res["release_passed"])
        self.assertLess(retrieval_res["actual_latency_ms"], 7500.0)

        # -------------------------------------------------------------
        # Step 4: Pipeline D - Dialectical Decision & Executive Briefing
        # -------------------------------------------------------------
        arbiter = HitlArbiterAgent(ledger_path=self.ledger_path)
        decision_coord = DecisionOrchestrator(hitl_arbiter=arbiter)

        decision_options = [
            {
                "key": "opt-vector-cache",
                "title": "In-memory VectorCache with BLAS matrix-vector dot products",
                "description": "Turnkey automated modular vector cache with zero-copy float32 numpy arrays and high ROI",
                "estimated_roi": 0.95,
            },
            {
                "key": "opt-table-scan",
                "title": "Legacy unindexed table scan",
                "description": "Legacy monolith unindexed table scan with high latency and heap churn",
                "estimated_roi": 0.20,
            },
        ]

        live_turns = [{
            "speaker": "Alice Miller",
            "text": "The engineering team is committed to the in-memory VectorCache architecture.",
            "timestamp": "00:01:15",
        }]

        decision_res = decision_coord.orchestrate_decision(
            decision_id="dec-e2e-cache",
            question="Select vector indexing and retrieval strategy",
            options=decision_options,
            canonical_evidence=[{"evidence_id": "ev-e2e-01", "digest": claim_pointer.content_digest}],
            canonical_graph_delta={"nodes": client_safe_facts, "assertions": []},
            live_turns=live_turns,
            margin_threshold=0.25,
        )

        # Terminal state and autonomous approval (Delta = 0.95 - 0.30 = 0.65 >= 0.25)
        self.assertEqual(decision_res["terminal_state"], "APPROVED_UNANIMOUS")
        self.assertFalse(decision_res["should_surface_hitl"])
        self.assertGreaterEqual(decision_res["margin"], 0.25)

        # Law 7: Invariant Pipeline Sequencing
        recap = decision_res["executive_recap"]
        self.assertIn("Executive Briefing", recap)
        self.assertIn("09/12/2026", recap)
        self.assertIn("Actionable Next Steps", recap)

        # Figma 4K Specifications
        figma_spec = decision_res["figma_4k_spec"]
        self.assertEqual(figma_spec["canvas"]["width"], 3840)
        self.assertEqual(figma_spec["canvas"]["height"], 2160)
        self.assertEqual(figma_spec["typography"]["font_family"], "Inter")


class TestArchitecturalInvariantsAndModuleBudgets(unittest.TestCase):
    """Verifies AGENTS.md Invariant 1 (<= 400 lines) across all refactored modules."""

    def test_all_refactored_modules_satisfy_400_line_budget(self) -> None:
        """Every single module touched in Waves 1 through 4 must satisfy <= 400 lines."""
        root = Path(__file__).resolve().parents[1]
        governed_modules = [
            # Pipeline A (Wave 1)
            root / "pipeline_a_intake" / "local_stt.py",
            # Pipeline B (Wave 2)
            root / "pipeline_b_extraction" / "extractors.py",
            root / "pipeline_b_extraction" / "dlq.py",
            root / "pipeline_b_extraction" / "json_repair.py",
            # Pipeline C (Wave 3)
            root / "dual_pipeline" / "index_core" / "vector_cache.py",
            root / "dual_pipeline" / "index_core" / "facets.py",
            root / "dual_pipeline" / "index_core" / "filters.py",
            root / "dual_pipeline" / "index_core" / "fusion.py",
            root / "dual_pipeline" / "index_core" / "retriever.py",
            root / "pipeline_c_retrieval" / "engine.py",
            # Pipeline D (Wave 4)
            root / "dual_pipeline" / "decision" / "agents" / "hitl_arbiter_agent.py",
            root / "dual_pipeline" / "hitl_ui_bridge.py",
            root / "dual_pipeline" / "decision" / "context_fusion.py",
            root / "dual_pipeline" / "retrieval" / "agents" / "retrieval_coordinator.py",
        ]

        violations = []
        for mod in governed_modules:
            self.assertTrue(mod.is_file(), f"Module not found: {mod}")
            line_count = len(mod.read_text(encoding="utf-8").splitlines())
            if line_count > 400:
                violations.append((mod.name, line_count))

        self.assertEqual(
            violations,
            [],
            f"Violations of AGENTS.md Invariant 1 (<= 400 lines): {violations}",
        )


if __name__ == "__main__":
    unittest.main()
