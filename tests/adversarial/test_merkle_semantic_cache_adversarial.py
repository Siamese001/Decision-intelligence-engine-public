"""Adversarial tests for Merkle Subgraph Semantic Caching, W3 Temporal Staleness, and ALR Replay Protection.

Validates the architectural fixes from the PhD Council of 6 debate:
- Dr. Silas: Unrelated graph mutations do NOT invalidate cache (Merkle root bound to evidence envelope).
- Dr. Thorne: Replayed outputs strip historical ALR operator signatures to prevent Gate 1.7 replay attacks.
- W3 Temporal Invariant: stale_after dates force cache misses on expired knowledge.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest
from dual_pipeline.semantic_cache import (
    SemanticCache,
    build_merkle_cache_context,
    compute_subgraph_merkle_root,
    configure_semantic_cache,
    extract_earliest_stale_after,
    is_temporally_stale,
    sanitize_output_for_cache_replay,
)
from dual_pipeline.telemetry import RunRecorder
from tests.embedding_fixture import BgeM3TestBackend

CORRELATION = {
    "query_id": "cache-test",
    "source_id": None,
    "candidate_id": None,
    "graph_operation_id": None,
    "hitl_item_id": None,
}


class TestMerkleSemanticCacheAdversarial(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = BgeM3TestBackend()
        cls.intent_vector = cls.backend.encode("How should database sharding be configured?")["dense"]

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-merkle-cache-adv-")
        self.runtime_root = Path(self.temp_dir.name)
        configure_semantic_cache(self.runtime_root, "read_write")
        self.cache = SemanticCache(self.runtime_root)
        self.cache.bind_embedding_backend(self.backend)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _create_sealed_run(self, run_id: str, trace_id: str, entry_id: str) -> dict:
        recorder = RunRecorder(
            self.runtime_root,
            run_id=run_id,
            trace_id=trace_id,
            use_case="query",
            graph_snapshot="snapshot",
        )
        recorder.record(
            stage="L0",
            event_type="route_decision",
            payload={"route": "test"},
            correlation=CORRELATION,
            route="test",
        )
        recorder.record(
            stage="L4",
            event_type="pipeline_event",
            payload={"entry_id": entry_id},
            correlation=CORRELATION,
        )
        return recorder.seal(["L0", "L4"], CORRELATION)

    def _make_context(self, generation_id: str, graph_sha256: str) -> dict:
        return {
            "use_case": "query",
            "cache_payload_kind": "sealed_query_output_v4",
            "destination": "NEE_DECISION_INTELLIGENCE",
            "eligibility_policy_digest": "1" * 64,
            "assurance_profile": "standard",
            "delivery_profile": "internal_technical",
            "artifact_type": "answer",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "allowed_lanes": ["approved"],
            "active_pair": {
                "generation_id": generation_id,
                "graph_sha256": graph_sha256,
            },
            "coverage_requirements_digest": "2" * 64,
            "l2_policy_digest": "3" * 64,
            "x1_policy_digest": "4" * 64,
            "provider_egress_allowed": True,
            "query_plan_version": "2.0.0",
            "l1_controller_mode": "enforced",
            "l1_controller_policy_digest": "5" * 64,
            "l1_action_policy_digest": "6" * 64,
            "l1_budget_profile": "standard",
        }

    def test_merkle_root_topological_order_invariance(self) -> None:
        """Adversarial check: Merkle root calculation must be invariant to retrieval order."""
        node_a = {"id": "node_1", "predicate": "has_sharding", "value": "hash_ring"}
        node_b = {"id": "node_2", "predicate": "replication_factor", "value": 3}
        node_c = {"id": "node_3", "predicate": "consensus", "value": "raft"}

        root_order_1 = compute_subgraph_merkle_root([node_a, node_b, node_c])
        root_order_2 = compute_subgraph_merkle_root([node_c, node_a, node_b])
        root_order_3 = compute_subgraph_merkle_root([node_b, node_c, node_a])

        self.assertEqual(root_order_1, root_order_2)
        self.assertEqual(root_order_2, root_order_3)
        self.assertEqual(len(root_order_1), 64)

    def test_unrelated_graph_mutation_preserves_cache_hit_dr_silas(self) -> None:
        """Dr. Silas Theorem: Unrelated node mutation in HR policies changes global graph_sha256,

        but the DB sharding query evidence Merkle root remains identical -> CACHE HITS.
        """
        evidence_db = [
            {"assertion_id": "assert_db_1", "literal": "Use consistent hashing for shards", "domain": "database"},
            {"assertion_id": "assert_db_2", "literal": "Set replication factor to 3", "domain": "database"},
        ]
        merkle_db = compute_subgraph_merkle_root(evidence_db)

        # Baseline graph state G0
        base_ctx = self._make_context(generation_id="gen-001", graph_sha256="global_hash_000000000000000000000000000000000000000000000000000000000001")
        merkle_ctx_g0 = build_merkle_cache_context(base_ctx, merkle_db)

        # Stage and activate cached output for DB query
        output_db = {"answer": "Consistent hashing with replication factor 3", "evidence": evidence_db}
        staged = self.cache.stage(
            context=merkle_ctx_g0,
            intent_digest=digest("db_query"),
            intent_vector=list(self.intent_vector),
            output=output_db,
            outcome="X3D_ALLOW_FINISH",
            source_run_id="run-001",
            source_trace_id="0" * 32,
            replay_eligible=True,
        )
        self.assertEqual(staged.status, "PENDING")
        seal_001 = self._create_sealed_run("run-001", "0" * 32, staged.entry.entry_id)
        activated = self.cache.activate(
            staged.entry,
            source_event_count=seal_001["event_count"],
            source_event_chain_digest=seal_001["event_chain_digest"],
        )
        self.assertEqual(activated.status, "ACTIVE")

        # Now an unrelated mutation occurs in the graph: An HR policy document is ingested!
        # Global graph hash changes from 000...001 to 999...999
        mutated_global_ctx = self._make_context(
            generation_id="gen-002",
            graph_sha256="global_hash_999999999999999999999999999999999999999999999999999999999999",
        )
        merkle_ctx_g1 = build_merkle_cache_context(mutated_global_ctx, merkle_db)

        # Look up using the Merkle context: Should STILL HIT despite global graph hash mutation!
        lookup = self.cache.lookup(context=merkle_ctx_g1, intent_vector=list(self.intent_vector))
        self.assertTrue(lookup.hit, f"Expected CACHE HIT under Dr. Silas Merkle isolation, got: {lookup.reason}")
        self.assertEqual(lookup.output["answer"], output_db["answer"])

        # Now, mutate an assertion INSIDE the DB evidence envelope itself!
        mutated_evidence_db = [
            {"assertion_id": "assert_db_1", "literal": "Use consistent hashing for shards", "domain": "database"},
            {"assertion_id": "assert_db_2", "literal": "Set replication factor to 5", "domain": "database"},  # 3 -> 5!
        ]
        mutated_merkle_db = compute_subgraph_merkle_root(mutated_evidence_db)
        self.assertNotEqual(merkle_db, mutated_merkle_db)

        merkle_ctx_mutated = build_merkle_cache_context(mutated_global_ctx, mutated_merkle_db)
        lookup_miss = self.cache.lookup(context=merkle_ctx_mutated, intent_vector=list(self.intent_vector))
        self.assertFalse(lookup_miss.hit)
        self.assertEqual(lookup_miss.reason, "CACHE_MISS_NO_COMPATIBLE_INTENT")

    def test_w3_temporal_staleness_enforces_cache_invalidation(self) -> None:
        """W3 Temporal Invariant: If cached assertion passes stale_after, lookup forces a cache miss."""
        evidence_with_expiry = [
            {
                "assertion_id": "assert_temporal_1",
                "literal": "Current rate limit is 100 req/sec",
                "observed_at": "10/01/2026",
                "stale_after": "12/31/2026",
            }
        ]
        merkle_root = compute_subgraph_merkle_root(evidence_with_expiry)
        base_ctx = self._make_context("gen-temp", "h" * 64)
        earliest_stale = extract_earliest_stale_after({"evidence": evidence_with_expiry})
        self.assertEqual(earliest_stale, "2026-12-31T23:59:59Z")

        ctx = build_merkle_cache_context(base_ctx, merkle_root)
        output = {
            "answer": "Rate limit 100 req/sec",
            "evidence": evidence_with_expiry,
            "stale_after": "12/31/2026",
        }

        staged = self.cache.stage(
            context=ctx,
            intent_digest=digest("temporal_query"),
            intent_vector=list(self.intent_vector),
            output=output,
            outcome="X3D_ALLOW_FINISH",
            source_run_id="run-temporal",
            source_trace_id="0" * 32,
            replay_eligible=True,
            stale_after=earliest_stale,
        )
        seal_temporal = self._create_sealed_run("run-temporal", "0" * 32, staged.entry.entry_id)
        self.cache.activate(
            staged.entry,
            source_event_count=seal_temporal["event_count"],
            source_event_chain_digest=seal_temporal["event_chain_digest"],
        )

        # Query as of 10/15/2026 (Before stale_after) -> HIT
        lookup_fresh = self.cache.lookup(
            context=ctx,
            intent_vector=list(self.intent_vector),
            as_of="10/15/2026",
        )
        self.assertTrue(lookup_fresh.hit)

        # Query as of 01/15/2027 (After stale_after) -> MISS with CACHE_MISS_W3_TEMPORAL_EXPIRED!
        lookup_stale = self.cache.lookup(
            context=ctx,
            intent_vector=list(self.intent_vector),
            as_of="01/15/2027",
        )
        self.assertFalse(lookup_stale.hit)
        self.assertEqual(lookup_stale.reason, "CACHE_MISS_W3_TEMPORAL_EXPIRED")

    def test_cryptographic_alr_replay_stripping_dr_thorne(self) -> None:
        """Dr. Thorne Theorem: Replaying cached output strips historical operator signatures

        and wraps in SyntheticCacheReplay, preventing Gate 1.7 UNAUTHENTICATED_OPERATOR_VERIFICATION_CLAIM.
        """
        cached_output_with_claims = {
            "title": "Architecture Review",
            "transcript_summary": "Alex Rivera affirmed sharding strategy (Operator Verified).",
            "ambiguity_lock_register": [
                {
                    "case_id": "case_001",
                    "operator_verified": True,
                    "status": "OPERATOR_VERIFIED",
                    "ed25519_signature": "3a4b" * 32,
                    "verified_entry_id": "entry-historical-1234",
                    "hitl_role_receipts": {"Alex Rivera": "Lead Architect"},
                }
            ],
            "attendee_roles_confirmed_by_operator": True,
        }

        # Simulate cache replay into a new pipeline run
        current_run_id = "run-pipeline-2026-replay"
        sanitized = sanitize_output_for_cache_replay(
            cached_output_with_claims,
            current_run_id=current_run_id,
        )

        # 1. Historical text claim is neutralized
        self.assertNotIn("(Operator Verified)", sanitized["transcript_summary"])
        self.assertIn("(Cached Projection - Reverification Required)", sanitized["transcript_summary"])

        # 2. Raw cryptographic signature and receipts are stripped
        alr_entry = sanitized["ambiguity_lock_register"][0]
        self.assertFalse(alr_entry["operator_verified"])
        self.assertNotIn("ed25519_signature", alr_entry)
        self.assertNotIn("verified_entry_id", alr_entry)
        self.assertNotIn("hitl_role_receipts", alr_entry)
        self.assertFalse(sanitized["attendee_roles_confirmed_by_operator"])

        # 3. Governed SyntheticCacheReplay envelope is attached
        self.assertIn("synthetic_cache_replay", sanitized)
        replay_envelope = sanitized["synthetic_cache_replay"]
        self.assertEqual(replay_envelope["status"], "SYNTHETIC_CACHE_REPLAY")
        self.assertEqual(replay_envelope["replay_run_id"], current_run_id)
        self.assertTrue(replay_envelope["operator_verification_stripped"])
        self.assertTrue(replay_envelope["requires_operator_reverification"])

    def test_empty_and_malformed_evidence_graceful_handling(self) -> None:
        """Boundary test: empty, None, or malformed evidence handles cleanly without exception."""
        root_none = compute_subgraph_merkle_root(None)
        root_empty = compute_subgraph_merkle_root([])
        self.assertEqual(root_none, root_empty)
        self.assertEqual(len(root_none), 64)

        # Malformed stale dates don't crash
        self.assertIsNone(extract_earliest_stale_after({}))
        self.assertIsNone(extract_earliest_stale_after({"stale_after": "not-a-valid-date"}))
        self.assertFalse(is_temporally_stale(None))
        self.assertTrue(is_temporally_stale("invalid_date"))  # Fail-closed per SSOT


if __name__ == "__main__":
    unittest.main()
