"""Unit tests for Output 3 Knowledge Graph Ambiguity Core (Domains G1-G4)."""
import unittest

from dual_pipeline.graph_ambiguity_core import (
    G1EntityIdentityDetector,
    G2PredicateDirectionalityDetector,
    G3EpistemicClaimDetector,
    G4BipartiteC19Detector,
    GraphAmbiguityDomain,
    GraphAmbiguityEngine,
    GraphAmbiguityRecord,
    GraphCandidateOption,
)


class TestGraphAmbiguityCore(unittest.TestCase):
    def test_models_margin_delta_and_gate_eligibility(self):
        c1 = GraphCandidateOption(key="opt1", label="Option 1", description="Desc 1", confidence=0.55, rationale="Rat 1")
        c2 = GraphCandidateOption(key="opt2", label="Option 2", description="Desc 2", confidence=0.45, rationale="Rat 2")
        rec = GraphAmbiguityRecord(
            ambiguity_id="G1-TEST-1",
            domain=GraphAmbiguityDomain.G1_ENTITY_IDENTITY_NAMESPACE,
            title="Test Ambiguity",
            context_snippet="Context snippet",
            candidates=[c1, c2],
        )
        self.assertAlmostEqual(rec.margin_delta, 0.10, places=2)
        self.assertTrue(rec.is_gate_eligible(margin_threshold=0.20))
        self.assertFalse(rec.is_gate_eligible(margin_threshold=0.05))

        # Test resolution
        rec.resolve(chosen_key="opt1", rationale="Operator selected option 1", timestamp_iso="2026-09-13T07:00:00Z")
        self.assertEqual(rec.status, "RESOLVED")
        self.assertEqual(rec.selected_key, "opt1")
        self.assertIsNotNone(rec.selected_option)
        self.assertFalse(rec.is_gate_eligible(margin_threshold=0.20))

    def test_g1_entity_identity_detector(self):
        detector = G1EntityIdentityDetector()
        candidate_f3 = {
            "graph_mutations": {
                "entity_upserts": [
                    {"entity_id": "vendor:snowflake", "name": "Snowflake Inc", "type": "ORGANIZATION"},
                    {"entity_id": "system:billing_engine", "name": "Billing Engine", "type": "SYSTEM"},
                    {"entity_id": "database_oracle", "name": "Oracle DB", "type": "SYSTEM"},
                ]
            }
        }
        recs = detector.detect("transcript text", candidate_f3)
        self.assertEqual(len(recs), 2)
        self.assertTrue(any("vendor" in r.ambiguity_id for r in recs))
        self.assertTrue(any("NOPREFIX" in r.ambiguity_id for r in recs))

    def test_g2_predicate_directionality_detector(self):
        detector = G2PredicateDirectionalityDetector()
        candidate_f3 = {
            "graph_mutations": {
                "relationship_signals": [
                    {"from_id": "system:core_sap", "to_id": "system:salesforce", "relationship": "RELATED_TO"},
                    {"from_id": "system:auth_service", "to_id": "system:portal", "relationship": "DEPENDS_ON"},
                ]
            }
        }
        recs = detector.detect("transcript text", candidate_f3)
        # Should flag RELATED_TO for non-canonical predicate, and DEPENDS_ON for directionality check
        self.assertGreaterEqual(len(recs), 2)
        pred_rec = [r for r in recs if "PREDICATE" in r.ambiguity_id][0]
        self.assertIn("RELATED_TO", pred_rec.title)
        self.assertTrue(pred_rec.is_gate_eligible(0.20))

    def test_g3_epistemic_claim_detector(self):
        detector = G3EpistemicClaimDetector()
        candidate_f3 = {
            "graph_mutations": {
                "decision_updates": [
                    {
                        "decision_id": "decision:1",
                        "statement": "We could consider migrating to Snowflake next quarter.",
                        "rationale": "Thinking about cloud cost optimization.",
                    }
                ],
                "proposal_updates": [
                    {
                        "proposal_id": "proposal:1",
                        "statement": "We approved and finalized the new enterprise security policy.",
                        "rationale": "Committed to implementation by Q4.",
                    }
                ],
            }
        }
        recs = detector.detect("transcript text", candidate_f3)
        self.assertEqual(len(recs), 2)
        self.assertTrue(any("DEC" in r.ambiguity_id for r in recs))
        self.assertTrue(any("PROP" in r.ambiguity_id for r in recs))

    def test_g4_bipartite_c19_detector(self):
        detector = G4BipartiteC19Detector()
        leaked_f3 = {
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "entity_id": "person:executive_candidate",
                        "name": "Target Lead",
                        "description": "Executive target on executive compensation band with personal leverage.",
                    }
                ]
            }
        }
        recs = detector.detect("transcript text", leaked_f3)
        self.assertGreaterEqual(len(recs), 1)
        leak_rec = [r for r in recs if "FIREWALL" in r.ambiguity_id][0]
        self.assertEqual(leak_rec.domain, GraphAmbiguityDomain.G4_BIPARTITE_C19_PARTITION)
        self.assertIn("Rule C-19", leak_rec.title)

    def test_graph_ambiguity_engine_orchestration_and_apply_resolutions(self):
        engine = GraphAmbiguityEngine(margin_threshold=0.20)
        candidate_f3 = {
            "graph_mutations": {
                "entity_upserts": [
                    {"entity_id": "vendor:databricks", "name": "Databricks", "description": "Cloud Lakehouse"}
                ],
                "relationship_signals": [
                    {"from_id": "vendor:databricks", "to_id": "system:warehouse", "relationship": "RELATED_TO"}
                ],
                "decision_updates": [
                    {"decision_id": "decision:10", "statement": "We might evaluate vector databases."}
                ],
                "proposal_updates": [],
            }
        }
        recs = engine.scan_all("transcript", candidate_f3)
        self.assertGreaterEqual(len(recs), 3)

        gate_recs = engine.scan("transcript", candidate_f3)
        self.assertGreaterEqual(len(gate_recs), 2)

        # Resolve G1 and G3
        for r in gate_recs:
            if r.domain == GraphAmbiguityDomain.G1_ENTITY_IDENTITY_NAMESPACE:
                r.resolve("org:databricks", "Resolved to org")
            elif r.domain == GraphAmbiguityDomain.G3_EPISTEMIC_CLAIM_STATUS:
                r.resolve("PROPOSAL", "Demoted to proposal")

        updated = engine.apply_resolutions(candidate_f3, gate_recs)
        self.assertEqual(updated["graph_mutations"]["entity_upserts"][0]["entity_id"], "org:databricks")
        self.assertEqual(len(updated["graph_mutations"]["decision_updates"]), 0)
        self.assertEqual(len(updated["graph_mutations"]["proposal_updates"]), 1)


if __name__ == "__main__":
    unittest.main()
