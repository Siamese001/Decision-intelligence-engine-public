"""End-to-end lifecycle tests for Merkle Semantic Cache Boundary, W3 Freshness, and ALR Replay Protection.

Exercises the full pipeline lifecycle:
- Cold invocation: cache miss -> provider execution -> staging -> activation.
- Warm invocation: cache hit -> provider bypass (0 provider invocations).
- Subgraph Merkle boundary: permutation invariant, isolated from unrelated mutations, invalidates on evidence changes.
- Temporal W3 awareness: invalidates on stale_after expiration.
- ALR replay safety: strips unauthenticated signatures and re-envelopes into SyntheticCacheReplay.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import digest
from dual_pipeline.semantic_cache import (
    MerkleSemanticCacheBoundary,
    SemanticCache,
    configure_semantic_cache,
)
from dual_pipeline.telemetry import RunRecorder
from tests.embedding_fixture import BgeM3TestBackend

CORRELATION = {
    "query_id": "e2e-cache-test",
    "source_id": None,
    "candidate_id": None,
    "graph_operation_id": None,
    "hitl_item_id": None,
}


class CountingProviderEvaluator:
    """Simulated expensive LLM provider evaluator tracking invocation count."""

    def __init__(self) -> None:
        self.call_count = 0

    def evaluate(self, question: str, evidence: list[dict]) -> dict:
        self.call_count += 1
        return {
            "answer": f"Evaluated response for: {question}",
            "evidence": evidence,
            "stale_after": "12/31/2026",
            "provider_cost_units": 100,
        }


class TestMerkleSemanticCacheE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.backend = BgeM3TestBackend()
        cls.intent_vector = cls.backend.encode("What is the architectural boundary for semantic cache?")["dense"]

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-merkle-cache-e2e-")
        self.runtime_root = Path(self.temp_dir.name)
        configure_semantic_cache(self.runtime_root, "read_write")
        self.raw_cache = SemanticCache(self.runtime_root)
        self.raw_cache.bind_embedding_backend(self.backend)
        self.boundary = MerkleSemanticCacheBoundary(self.raw_cache)
        self.provider = CountingProviderEvaluator()

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

    def _base_context(self, graph_sha: str = "global_graph_001") -> dict:
        return {
            "use_case": "query",
            "cache_payload_kind": "sealed_query_output_v4",
            "destination": "NEE_DECISION_INTELLIGENCE",
            "eligibility_policy_digest": "a" * 64,
            "assurance_profile": "standard",
            "delivery_profile": "internal_technical",
            "artifact_type": "answer",
            "classification": "internal",
            "audiences": ["nee-engineering"],
            "allowed_lanes": ["approved"],
            "active_pair": {
                "generation_id": "gen-alpha",
                "graph_sha256": graph_sha,
            },
            "coverage_requirements_digest": "b" * 64,
            "l2_policy_digest": "c" * 64,
            "x1_policy_digest": "d" * 64,
            "provider_egress_allowed": True,
            "query_plan_version": "2.0.0",
            "l1_controller_mode": "enforced",
            "l1_controller_policy_digest": "e" * 64,
            "l1_action_policy_digest": "f" * 64,
            "l1_budget_profile": "standard",
        }

    def test_full_lifecycle_cold_miss_warm_hit_mutation_and_staleness(self) -> None:
        """Full lifecycle test demonstrating provider bypass, Merkle isolation, and staleness gating."""
        evidence_subgraph = [
            {"assertion_id": "assert_1", "subject": "SemanticCache", "predicate": "is_isolated_from", "object": "GlobalGraph"},
            {"assertion_id": "assert_2", "subject": "MerkleRoot", "predicate": "computed_over", "object": "EvidenceEnvelope"},
        ]
        context_base = self._base_context(graph_sha="graph_state_v1")

        # 1. Cold Call: Should Miss and invoke Provider
        cold_lookup = self.boundary.lookup(
            context=context_base,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=evidence_subgraph,
            as_of="10/15/2026",
        )
        self.assertFalse(cold_lookup.hit)
        self.assertEqual(self.provider.call_count, 0)

        # Provider evaluates
        evaluation_result = self.provider.evaluate("What is the architectural boundary for semantic cache?", evidence_subgraph)
        self.assertEqual(self.provider.call_count, 1)

        # Stage and activate
        staged = self.boundary.stage(
            context=context_base,
            intent_digest=digest("query_intent"),
            intent_vector=list(self.intent_vector),
            output=evaluation_result,
            outcome="X3D_ALLOW_FINISH",
            source_run_id="run-pipeline-01",
            source_trace_id="0" * 32,
            replay_eligible=True,
            subgraph_nodes=evidence_subgraph,
        )
        self.assertEqual(staged.status, "PENDING")
        seal = self._create_sealed_run("run-pipeline-01", "0" * 32, staged.entry.entry_id)
        activated = self.boundary.activate(
            staged.entry,
            source_event_count=seal["event_count"],
            source_event_chain_digest=seal["event_chain_digest"],
        )
        self.assertEqual(activated.status, "ACTIVE")

        # 2. Warm Call: Identical query & subgraph -> HIT, 0 additional provider calls
        warm_lookup = self.boundary.lookup(
            context=context_base,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=evidence_subgraph,
            as_of="10/15/2026",
        )
        self.assertTrue(warm_lookup.hit)
        self.assertEqual(self.provider.call_count, 1)  # Provider was NOT called again!
        self.assertEqual(warm_lookup.output["answer"], evaluation_result["answer"])

        # 3. Order Invariance: Retrieval returns same evidence in reversed order -> Still HIT!
        reversed_subgraph = list(reversed(evidence_subgraph))
        warm_lookup_reversed = self.boundary.lookup(
            context=context_base,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=reversed_subgraph,
            as_of="10/15/2026",
        )
        self.assertTrue(warm_lookup_reversed.hit)
        self.assertEqual(self.provider.call_count, 1)  # Provider untouched!

        # 4. Global Graph Mutation Isolation: Global graph hash mutates, but subgraph remains identical
        mutated_global_context = self._base_context(graph_sha="graph_state_v2_unrelated_mutation")
        isolated_lookup = self.boundary.lookup(
            context=mutated_global_context,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=evidence_subgraph,
            as_of="10/15/2026",
        )
        self.assertTrue(isolated_lookup.hit, "Unrelated graph mutation must not evict cached evaluation!")
        self.assertEqual(self.provider.call_count, 1)

        # 5. Evidence Envelope Mutation: Modifying an assertion in the evidence subgraph causes a MISS
        mutated_subgraph = [
            {"assertion_id": "assert_1", "subject": "SemanticCache", "predicate": "is_isolated_from", "object": "GlobalGraph"},
            {"assertion_id": "assert_2", "subject": "MerkleRoot", "predicate": "computed_over", "object": "MutatedEnvelope"},
        ]
        miss_lookup = self.boundary.lookup(
            context=context_base,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=mutated_subgraph,
            as_of="10/15/2026",
        )
        self.assertFalse(miss_lookup.hit)

        # 6. W3 Temporal Staleness: Advancing time past stale_after ("12/31/2026") causes a MISS
        stale_lookup = self.boundary.lookup(
            context=context_base,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=evidence_subgraph,
            as_of="01/15/2027",  # After 12/31/2026!
        )
        self.assertFalse(stale_lookup.hit)
        self.assertEqual(stale_lookup.reason, "CACHE_MISS_W3_TEMPORAL_EXPIRED")

    def test_alr_replay_sanitization_in_boundary(self) -> None:
        """Verify boundary strips ALR operator verification claims when replaying into a new run."""
        evidence = [{"assertion_id": "assert_sec_1", "statement": "All access tokens require HSM rotation"}]
        context = self._base_context()

        output_with_alr = {
            "title": "Security Architecture",
            "executive_recap": "Approved with zero trust boundaries (Operator Verified).",
            "ambiguity_lock_register": [
                {
                    "case_id": "c1",
                    "operator_verified": True,
                    "ed25519_signature": "beef" * 32,
                    "verified_entry_id": "entry-99",
                }
            ],
            "attendee_roles_confirmed_by_operator": True,
            "stale_after": "12/31/2026",
        }

        # Stage and activate
        staged = self.boundary.stage(
            context=context,
            intent_digest=digest("sec_intent"),
            intent_vector=list(self.intent_vector),
            output=output_with_alr,
            outcome="X3D_ALLOW_FINISH",
            source_run_id="run-original-sec",
            source_trace_id="0" * 32,
            replay_eligible=True,
            subgraph_nodes=evidence,
        )
        seal = self._create_sealed_run("run-original-sec", "0" * 32, staged.entry.entry_id)
        self.boundary.activate(staged.entry, source_event_count=seal["event_count"], source_event_chain_digest=seal["event_chain_digest"])

        # Replay into a new run
        replayed_lookup = self.boundary.lookup(
            context=context,
            intent_vector=list(self.intent_vector),
            subgraph_nodes=evidence,
            current_run_id="run-replayed-2026",
            as_of="10/15/2026",
        )
        self.assertTrue(replayed_lookup.hit)
        output = replayed_lookup.output

        # Verify ALR sanitization
        self.assertNotIn("(Operator Verified)", output["executive_recap"])
        self.assertIn("(Cached Projection - Reverification Required)", output["executive_recap"])
        self.assertFalse(output["ambiguity_lock_register"][0]["operator_verified"])
        self.assertNotIn("ed25519_signature", output["ambiguity_lock_register"][0])
        self.assertNotIn("verified_entry_id", output["ambiguity_lock_register"][0])
        self.assertFalse(output["attendee_roles_confirmed_by_operator"])

        # Verify SyntheticCacheReplay envelope
        self.assertIn("synthetic_cache_replay", output)
        envelope = output["synthetic_cache_replay"]
        self.assertEqual(envelope["status"], "SYNTHETIC_CACHE_REPLAY")
        self.assertEqual(envelope["source_run_id"], "run-original-sec")
        self.assertEqual(envelope["replay_run_id"], "run-replayed-2026")
        self.assertTrue(envelope["operator_verification_stripped"])


if __name__ == "__main__":
    unittest.main()
