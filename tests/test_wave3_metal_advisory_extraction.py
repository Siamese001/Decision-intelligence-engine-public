"""
tests/test_wave3_metal_advisory_extraction.py

Wave 3 Acceptance Test Suite: Pipeline B Metal MLX Advisory Extraction,
Rule C-19 Career Firewall Certification, and Longitudinal Evals.

Validates:
1. 4-Tier Advisory Classification: INTERNAL_ADVISORY, CLIENT_SAFE, MIXED, UNCLASSIFIED.
2. Advisory precision (>=80%), recall (>=75%), and accuracy (>=85%) on golden corpus.
3. Physical Compute Fork Separation: Sanitizes text before cloud dispatch, isolates advisory to local Metal.
4. Rule C-19 Career Leverage Firewall: Strict veto on executive compensation band or executive leverage in Outputs 01-03.
5. Shadow Graph DB Storage: namespace='INTERNAL_ADVISORY', stealth_score=1.0, zero canonical leakage.
6. Two-Dimensional Longitudinal Evaluation Framework (ALR-03): Pipeline upgrade vs Rubric adaptation attribution.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge

from evals.evaluate_metal_advisory_extraction import (
    MetalAdvisoryExtractionEvaluator,
)
from evals.longitudinal_evaluation_tracker import (
    LongitudinalEvaluationTracker,
)
from pipeline_b_extraction.extractors import (
    ClientSafeExtractor,
    ComputeForkCoordinator,
    InternalAdvisoryExtractor,
)
from pipeline_b_extraction.pre_egress_sanitizer import (
    default_sanitizer,
)
from storage.database import (
    AdvisoryCategory,
    DatabaseManager,
    EvidenceRecord,
    InternalAdvisoryNode,
)


class TestWave3MetalAdvisoryExtraction(unittest.IsolatedAsyncioTestCase):
    """Wave 3 Acceptance Suite: Local Metal Advisory Extraction & Career Firewall."""

    def setUp(self) -> None:
        self.evaluator = MetalAdvisoryExtractionEvaluator(mock_mode=True)
        self.sanitizer = default_sanitizer

    def test_01_advisory_classification_metrics_and_accuracy(self) -> None:
        """Gate 1: Verify accuracy >= 85%, precision >= 80%, recall >= 75% on golden corpus."""
        passed, details = self.evaluator.evaluate_gate_1_classification_accuracy_and_metrics()
        self.assertTrue(passed)
        self.assertGreaterEqual(details["accuracy"], 0.85)
        self.assertGreaterEqual(details["advisory_precision"], 0.80)
        self.assertGreaterEqual(details["advisory_recall"], 0.75)

    def test_02_four_tier_advisory_classification_taxonomy(self) -> None:
        """Verify distinct classification of all 4 tiers: INTERNAL_ADVISORY, CLIENT_SAFE, MIXED, UNCLASSIFIED."""
        # 1. Pure INTERNAL_ADVISORY
        tier_adv = self.evaluator.classify_discourse_tier(
            "The VP of Infrastructure is privately hesitant and creating political friction.",
            "Sponsor"
        )
        self.assertEqual(tier_adv, "INTERNAL_ADVISORY")

        # 2. Pure CLIENT_SAFE
        tier_safe = self.evaluator.classify_discourse_tier(
            "We are deploying the BGE-M3 embedding service on AWS ECS with a 150ms latency target.",
            "Architect"
        )
        self.assertEqual(tier_safe, "CLIENT_SAFE")

        # 3. MIXED
        tier_mixed = self.evaluator.classify_discourse_tier(
            "We committed to AWS S3 migration by 06/16/2026, but the VP is using asymmetric leverage to delay.",
            "Director"
        )
        self.assertEqual(tier_mixed, "MIXED")

        # 4. UNCLASSIFIED
        tier_unc = self.evaluator.classify_discourse_tier(
            "Good morning everyone, can you hear me okay? Let's wait two more minutes.",
            "Attendee"
        )
        self.assertEqual(tier_unc, "UNCLASSIFIED")

    async def test_03_compute_fork_physical_separation(self) -> None:
        """Gate 2: Verify compute fork isolates advisory to local Metal and emits sanitized client-safe facts."""
        coord = ComputeForkCoordinator(
            client_safe_extractor=ClientSafeExtractor(mock_mode=True),
            internal_advisory_extractor=InternalAdvisoryExtractor(mock_mode=True),
        )
        ev = EvidenceRecord(
            evidence_id="ev-fork-test",
            meeting_id="meet-fork-01",
            speaker="Director",
            text="We finalized the schema for LAMS, but corporate IT is creating political friction over database ownership.",
            start_timestamp=0.0,
        )
        fork_res = await coord.execute_fork(ev)

        # Canonical nodes must be CLIENT_SAFE
        self.assertGreater(len(fork_res.canonical_nodes), 0)
        for node in fork_res.canonical_nodes:
            self.assertEqual(node.namespace, "CLIENT_SAFE")

        # Advisory nodes must be INTERNAL_ADVISORY
        self.assertGreater(len(fork_res.advisory_nodes), 0)
        for adv in fork_res.advisory_nodes:
            self.assertEqual(adv.namespace, "INTERNAL_ADVISORY")
            self.assertGreaterEqual(adv.stealth_score, 0.8)

    def test_04_rule_c19_career_firewall_veto_on_leaky_projection(self) -> None:
        """Gate 3: Verify GraphExtractionJudge strictly fails Output 03 containing career leverage leaks."""
        judge = GraphExtractionJudge(mock=True)
        leaky_projection = json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:client_executive",
                        "type": "person",
                        "mutation": "CREATE",
                        "description": "Executive target for $1.2M career trajectory positioning and advisory wedge",
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [],
            }
        })
        res = judge.evaluate(leaky_projection, f1_text="Spoken meeting dialogue", mock=True)
        self.assertEqual(res["verdict"], "FAIL")
        self.assertIn("RULE C-19 VIOLATION", res["forensic_rationale"])
        self.assertLessEqual(res["composite_score"], 30)

    def test_05_rule_c19_career_firewall_passes_clean_projection(self) -> None:
        """Gate 3: Verify GraphExtractionJudge passes clean enterprise architecture Output 03."""
        judge = GraphExtractionJudge(mock=True)
        clean_projection = json.dumps({
            "schema_version": "5.7",
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:bge-m3-service",
                        "type": "system",
                        "mutation": "CREATE",
                        "description": "BGE-M3 dense and sparse retrieval service for Apex Global Enterprises",
                    }
                ],
                "relationship_signals": [],
                "decision_updates": [],
            }
        })
        res = judge.evaluate(clean_projection, f1_text="Spoken meeting dialogue", mock=True)
        self.assertEqual(res["verdict"], "PASS")
        self.assertNotIn("RULE C-19 VIOLATION", res["forensic_rationale"])

    def test_06_pre_egress_sanitizer_redacts_career_and_secrets(self) -> None:
        """Verify LocalEgressSanitizer scrubs credentials, rate cards, and career leverage before egress."""
        raw_text = (
            "Our key is sk_test_redacted_placeholder and we charge $350/hr blended rate. "
            "We must position this architecture for our executive compensation band and shadow political map."
        )
        res = self.sanitizer.sanitize(raw_text)
        self.assertTrue(res.has_redactions)
        self.assertNotIn("sk-1234567890", res.sanitized_text)
        self.assertNotIn("$350/hr", res.sanitized_text)
        self.assertNotIn("$1.2M", res.sanitized_text)
        self.assertNotIn("shadow political map", res.sanitized_text)
        self.assertIn("[REDACTED_API_KEY]", res.sanitized_text)
        self.assertIn("[REDACTED_COMMERCIAL_TERM]", res.sanitized_text)
        self.assertIn("[REDACTED_CAREER_LEVERAGE]", res.sanitized_text)

    def test_07_shadow_graph_db_storage_isolation(self) -> None:
        """Gate 4: Verify shadow graph DB persists advisory nodes while canonical DB remains untouched."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            db_mgr = DatabaseManager(base_dir=temp_dir, allow_unencrypted_dev_fallback=True)
            node = InternalAdvisoryNode(
                node_id="adv-isolation-01",
                evidence_id="ev-iso-01",
                type="STAKEHOLDER_DYNAMICS",
                name="Sponsor Political Capital",
                category=AdvisoryCategory.STAKEHOLDER_DYNAMICS,
                properties={"context": "Executive alignment vector"},
                strategic_value=8.5,
                tacticality=0.0,
                confidence=0.92,
                is_quarantined=False,
                stealth_score=1.0,
                namespace="INTERNAL_ADVISORY",
            )
            db_mgr.upsert_internal_advisory_node(node)

            # Query shadow graph
            shadow_nodes = db_mgr.query_internal_advisory_nodes(limit=10)
            self.assertEqual(len(shadow_nodes), 1)
            self.assertEqual(shadow_nodes[0].node_id, "adv-isolation-01")
            self.assertEqual(shadow_nodes[0].namespace, "INTERNAL_ADVISORY")
            self.assertEqual(shadow_nodes[0].stealth_score, 1.0)

            # Verify canonical DB has 0 records of this node
            c_conn = db_mgr.get_canonical_conn(readonly=True)
            try:
                row = c_conn.execute(
                    "SELECT node_id FROM canonical_graph_nodes WHERE node_id = ?",
                    ("adv-isolation-01",)
                ).fetchone()
                self.assertIsNone(row)
            finally:
                c_conn.close()
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_08_two_dimensional_longitudinal_matrix_attribution(self) -> None:
        """ALR-03: Disentangles systemic pipeline improvements from evaluation rubric adaptation."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            ledger_path = temp_dir / "test_longitudinal_ledger.json"
            tracker = LongitudinalEvaluationTracker(ledger_path=ledger_path)
            tracker.seed_baseline_anchors()

            # Add evaluation record for (P=2.0.0, R=2.0.0)
            tracker.record_run(
                run_id="run-wave3-test",
                pipeline_version="2.0.0",
                rubric_version="2.0.0",
                benchmark_suite="Pipeline_B_Advisory_Extraction",
                model_id="mlx-community/Meta-Llama-3.1-8B-Instruct-4bit",
                reasoning_effort="medium",
                metrics={"composite_score": 100.0, "accuracy": 1.0, "precision": 1.0, "recall": 1.0},
                gates={"advisory_precision": True, "c19_firewall": True},
            )

            res = tracker.compute_two_dimensional_attribution(
                suite_name="Pipeline_B_Advisory_Extraction",
                metric_key="composite_score",
                baseline_p="1.0.0",
                target_p="2.0.0",
                baseline_r="1.0.0",
                target_r="2.0.0",
            )

            self.assertEqual(res["status"], "DISENTANGLED")
            # Systemic pipeline delta holding rubric at 1.0.0: 93.60 - 71.50 = +22.10
            self.assertAlmostEqual(res["systemic_pipeline_delta"], 22.10, places=2)
            # Rubric adaptation delta holding pipeline at 1.0.0: 67.80 - 71.50 = -3.70 (harden rubric)
            self.assertAlmostEqual(res["rubric_adaptation_delta"], -3.70, places=2)
            # Total observed delta: 100.0 - 71.50 = +28.50
            self.assertAlmostEqual(res["total_observed_delta"], 28.50, places=2)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
