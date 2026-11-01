"""Wave 7 E2E Tests: Semantic Cache Promotion Purge, Live BGE-M3 Gate Auditor & EvidencePack Escalation."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dual_pipeline.core import digest
from dual_pipeline.l0.contracts import (
    CLIENT_SAFE_NAMESPACE,
    INTERNAL_ADVISORY_NAMESPACE,
    PolicyContext,
)
from dual_pipeline.query_core.cache import _cached_query_output, _delivery_evidence_ready
from dual_pipeline.retrieval.agents.retrieval_perspective_healer import (
    RetrievalPerspectiveHealerAgent,
)
from dual_pipeline.retrieval.bge_reranker_service import BgeRerankerService
from dual_pipeline.retrieval.evidence_pack import assemble_decision_evidence_pack
from dual_pipeline.semantic_cache import SemanticCache, configure_semantic_cache
from tools.audit_retrieval_gates import evaluate_inference_similarity


class _FakeBgeM3Backend:
    retrieval_encoder = "real-bge-m3"
    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = "a" * 40
    model_manifest_digest = "b" * 64

    def encode(self, text: str) -> dict[str, list[float]]:
        if "DocIQ" in text:
            return {"dense": [1.0, 0.0, 0.0, 0.0]}
        return {"dense": [0.0, 1.0, 0.0, 0.0]}


class SemanticCachePromotionAndRetrievalGatesE2ETests(unittest.TestCase):
    def test_rebuild_from_validated_active_pair_purges_entries_on_generation_change(self) -> None:
        """CAT6-1000-P0-1: Promoting a new graph generation with identical BGE-M3 encoder purges stale cache entries."""
        with tempfile.TemporaryDirectory(prefix="wave7-e2e-cache-") as tmp:
            runtime = Path(tmp) / "runtime"
            configure_semantic_cache(runtime, "read_write")
            cache = SemanticCache(runtime)
            backend = _FakeBgeM3Backend()
            pair_v1 = {
                "embedding_backend": backend.backend_id,
                "model_id": backend.model_id,
                "model_revision": backend.model_revision,
                "model_manifest_digest": backend.model_manifest_digest,
                "generation_id": "gen-001",
                "graph_sha256": "1" * 64,
                "sidecar_sha256": "2" * 64,
            }
            res1 = cache.rebuild_from_validated_active_pair(pair_v1)
            self.assertEqual("BOUND", res1["status"])

            staged = cache.stage(
                context={"use_case": "query", "route": "exact_lookup"},
                intent_digest="c" * 64,
                intent_vector=[0.5, 0.5, 0.5, 0.5],
                output={"answer": "v1"},
                outcome="X3D_ALLOW_FINISH",
                source_run_id="run-v1",
                source_trace_id="d" * 32,
                replay_eligible=True,
            )
            self.assertEqual("PENDING", staged.status)
            self.assertEqual(1, cache.status()["entry_counts"]["PENDING"])

            # Same pair -> no purge
            same_res = cache.rebuild_from_validated_active_pair(pair_v1)
            self.assertEqual(0, same_res["purged_entry_count"])
            self.assertEqual(1, cache.status()["entry_counts"]["PENDING"])

            # Promoted generation_id / graph_sha256 with same encoder -> purges entries
            pair_v2 = dict(pair_v1, generation_id="gen-002", graph_sha256="3" * 64)
            res2 = cache.rebuild_from_validated_active_pair(pair_v2)
            self.assertEqual("REBUILT", res2["status"])
            self.assertEqual(1, res2["purged_entry_count"])
            self.assertEqual({"PENDING": 0, "ACTIVE": 0}, cache.status()["entry_counts"])

    def test_evaluate_inference_similarity_computes_live_vectors_and_rejects_unattributed(self) -> None:
        """CAT6-1000-P0-3: evaluate_inference_similarity computes real cosine similarity and rejects source='None'."""
        backend = _FakeBgeM3Backend()
        inferences = [
            {
                "claim": "DocIQ completed migration.",
                "source_text": "DocIQ completed migration in Q3.",
                "source": "Slide 3",
            },
            {
                "claim": "DocIQ completed migration.",
                "source_text": "Unrelated topic.",
                "source": "Slide 4",
            },
            {
                "claim": "Hallucinated claim with high fake score.",
                "bge_m3_similarity": 0.99,
                "source": "None",
            },
        ]
        approved, quarantined = evaluate_inference_similarity(
            inferences, min_score=0.85, backend=backend
        )
        self.assertEqual(1, len(approved))
        self.assertEqual("Slide 3", approved[0]["source"])
        self.assertEqual(2, len(quarantined))

    def test_evidence_pack_escalates_namespace_on_internal_advisory_facts(self) -> None:
        """CAT6-1000-P1-3: EvidencePack escalates namespace to INTERNAL_ADVISORY when any fact is INTERNAL_ADVISORY."""
        ctx = PolicyContext(
            tenant_id="partner_advisory",
            allowed_namespaces=[CLIENT_SAFE_NAMESPACE, INTERNAL_ADVISORY_NAMESPACE],
            clearance_level="INTERNAL_ADVISORY",
            allow_quarantined=False,
        )
        candidates = [
            {
                "node_id": "n_safe",
                "evidence_id": "ev_safe",
                "name": "Public Roadmap",
                "snippet": "Milestone 1 on track.",
                "tenant_id": "partner_advisory",
                "namespace": CLIENT_SAFE_NAMESPACE,
            },
            {
                "node_id": "n_adv",
                "evidence_id": "ev_adv",
                "name": "Executive Alignment Friction",
                "snippet": "VP expressed concern.",
                "tenant_id": "partner_advisory",
                "namespace": INTERNAL_ADVISORY_NAMESPACE,
            },
        ]
        pack = assemble_decision_evidence_pack(
            inquiry_id="inq_w7",
            query_text="Assess roadmap and alignment risks",
            candidates=candidates,
            policy_context=ctx,
        )
        self.assertEqual(2, len(pack.canonical_facts))
        self.assertEqual(INTERNAL_ADVISORY_NAMESPACE, pack.namespace)

    def test_perspective_healer_rejects_empty_mlx_output_and_falls_back_to_service(self) -> None:
        """CAT6-1000-P0-4: RetrievalPerspectiveHealerAgent raises on empty MLX healed_query and falls back to healer_service."""
        agent = RetrievalPerspectiveHealerAgent(
            api_key="dummy-key",
            mock_mode=False,
            use_local_model=True,
        )
        fake_runtime = MagicMock()
        fake_runtime.generate.return_value = MagicMock(parsed_json={}, content="{}")
        with patch(
            "dual_pipeline.runtime.model_runtime.get_model_runtime",
            return_value=fake_runtime,
        ), patch.object(
            agent.healer_service,
            "execute_healing",
             wraps=agent.healer_service.execute_healing,
        ) as spy_exec:
            agent.healer_service.mock_mode = True
            res = asyncio.run(
                agent.heal_starved_perspective(
                    query="DocIQ architecture",
                    starved_lens="FINANCIAL",
                )
            )
            self.assertEqual(1, spy_exec.call_count)
            self.assertTrue(res.is_success)


if __name__ == "__main__":
    unittest.main()
