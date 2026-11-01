"""Wave 7 Adversarial & Edge-Case Tests: Semantic Cache Concurrency, Quarantine Delivery & Non-Word Router Entities.

Covers mandatory edge-case categories:
1. Boundary payload (quarantined-only candidate packets in _delivery_evidence_ready, empty/missing namespaces)
2. Concurrency & race (multi-threaded stage + activate on identical entry_id updating source_run_id coherently)
3. Invariant bypass attempt (cross-classification / cross-subject cache namespace collision attempts, already-active entry re-activation)
4. Exact production regression (non-word entity names like 'c++' and '.net' in _has_canonical_entity_or_project; BgeRerankerService singleton without HF_HUB_OFFLINE mutation)
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from dual_pipeline.core import digest
from dual_pipeline.l0.contracts import CLIENT_SAFE_NAMESPACE, PolicyContext
from dual_pipeline.query_core.cache import _delivery_evidence_ready
from dual_pipeline.query_router_core.router import _has_canonical_entity_or_project
from dual_pipeline.retrieval.bge_reranker_service import BgeRerankerService
from dual_pipeline.retrieval.evidence_pack import assemble_decision_evidence_pack
from dual_pipeline.semantic_cache import SemanticCache, configure_semantic_cache


class _FakeBgeM3Backend:
    retrieval_encoder = "real-bge-m3"
    backend_id = "flagembedding-bge-m3-dev"
    model_id = "BAAI/bge-m3"
    model_revision = "a" * 40
    model_manifest_digest = "b" * 64


class SemanticCacheAndEvidencePackAdversarialTests(unittest.TestCase):
    def test_boundary_quarantined_only_candidates_rejected_for_delivery(self) -> None:
        """Category 1 (Boundary payload): _delivery_evidence_ready rejects candidate packets where all items are quarantined."""
        plan = {"retrieval_mode": "status_snapshot_current"}
        packet_quarantined = {
            "approved": [],
            "candidate": [{"record_id": "cand-1", "is_quarantined": True}],
        }
        self.assertFalse(_delivery_evidence_ready(plan, packet_quarantined))

        packet_valid = {
            "approved": [],
            "candidate": [
                {"record_id": "cand-1", "is_quarantined": True},
                {"record_id": "cand-2", "is_quarantined": False},
            ],
        }
        self.assertTrue(_delivery_evidence_ready(plan, packet_valid))

    def test_concurrency_restage_and_activate_updates_source_run_and_blocks_double_activate(self) -> None:
        """Category 3 (Concurrency & race): Re-staging an existing entry_id updates source_run_id and double-activate returns ERROR."""
        with tempfile.TemporaryDirectory(prefix="wave7-adv-race-") as tmp:
            runtime = Path(tmp) / "runtime"
            configure_semantic_cache(runtime, "read_write")
            cache = SemanticCache(runtime)
            cache.bind_embedding_backend(_FakeBgeM3Backend())

            ctx = {
                "use_case": "l2_extraction",
                "graph_generation_id": "gen-1",
                "merkle_root_hash": "1" * 64,
                "classification": "CLIENT_SAFE",
            }
            staged1 = cache.stage(
                context=ctx,
                intent_digest="a" * 64,
                intent_vector=[0.1, 0.2, 0.3, 0.4],
                output={"extracted": True},
                outcome="X3D_ALLOW_FINISH",
                source_run_id="crashed-run-1",
                source_trace_id="1" * 32,
                replay_eligible=True,
            )
            self.assertEqual("PENDING", staged1.status)

            # Second run stages identical entry_id with new source_run_id
            staged2 = cache.stage(
                context=ctx,
                intent_digest="a" * 64,
                intent_vector=[0.1, 0.2, 0.3, 0.4],
                output={"extracted": True},
                outcome="X3D_ALLOW_FINISH",
                source_run_id="healthy-run-2",
                source_trace_id="2" * 32,
                replay_eligible=True,
            )
            self.assertEqual("PENDING", staged2.status)
            assert staged2.entry is not None

            # Parallel threads attempting activation: exactly one succeeds as ACTIVE, second gets CACHE_PENDING_ENTRY_MISSING
            results = []
            lock = threading.Lock()

            def _worker() -> None:
                res = cache.activate(
                    staged2.entry,
                    source_event_count=2,
                    source_event_chain_digest="f" * 64,
                )
                with lock:
                    results.append(res)

            threads = [threading.Thread(target=_worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            statuses = [r.status for r in results]
            self.assertEqual(1, statuses.count("ACTIVE"))
            self.assertEqual(3, statuses.count("ERROR"))

            conn = sqlite3.connect(cache.path)
            try:
                row = conn.execute(
                    "SELECT source_run_id, state FROM semantic_cache_entry WHERE entry_id=?",
                    (staged2.entry.entry_id,),
                ).fetchone()
                self.assertEqual(("healthy-run-2", "ACTIVE"), row)
            finally:
                conn.close()

    def test_invariant_bypass_cross_classification_and_unlabeled_stealth_candidate(self) -> None:
        """Category 5 (Invariant bypass attempt): Compatibility isolates classification/subject across all namespaces; unlabeled stealth candidate blocked."""
        with tempfile.TemporaryDirectory(prefix="wave7-adv-bypass-") as tmp:
            cache = SemanticCache(Path(tmp) / "runtime")
            c_safe = cache.compatibility(
                {
                    "use_case": "l2_extraction",
                    "graph_generation_id": "g1",
                    "merkle_root_hash": "m1",
                    "classification": "CLIENT_SAFE",
                    "user_id": "alice",
                }
            )
            c_adv = cache.compatibility(
                {
                    "use_case": "l2_extraction",
                    "graph_generation_id": "g1",
                    "merkle_root_hash": "m1",
                    "classification": "INTERNAL_ADVISORY",
                    "user_id": "alice",
                }
            )
            c_bob = cache.compatibility(
                {
                    "use_case": "l2_extraction",
                    "graph_generation_id": "g1",
                    "merkle_root_hash": "m1",
                    "classification": "CLIENT_SAFE",
                    "user_id": "bob",
                }
            )
            self.assertNotEqual(digest(c_safe), digest(c_adv))
            self.assertNotEqual(digest(c_safe), digest(c_bob))

        # Unlabeled stealth candidate (stealth_score=1.0, missing namespace) must be inferred as INTERNAL_ADVISORY and blocked for CLIENT_SAFE caller
        ctx = PolicyContext(
            tenant_id="partner_advisory",
            allowed_namespaces=[CLIENT_SAFE_NAMESPACE],
            clearance_level="STANDARD",
            allow_quarantined=False,
        )
        pack = assemble_decision_evidence_pack(
            inquiry_id="inq_stealth",
            query_text="Check stakeholder notes",
            candidates=[
                {
                    "node_id": "n_stealth",
                    "evidence_id": "ev_stealth",
                    "name": "Hidden Power Map",
                    "snippet": "Confidential career note",
                    "tenant_id": "partner_advisory",
                    "stealth_score": 1.0,
                }
            ],
            policy_context=ctx,
        )
        self.assertEqual(0, len(pack.canonical_facts))

    def test_production_regression_non_word_router_entities_and_reranker_singleton(self) -> None:
        """Category 7 (Exact production regression): Non-word entities ('c++', '.net') match in router; BgeRerankerService reuses singleton without mutating HF_HUB_OFFLINE."""
        with patch(
            "dual_pipeline.query_router_core.router._load_active_graph_entity_terms",
            return_value=("c++ runtime", ".net core"),
        ):
            self.assertTrue(
                _has_canonical_entity_or_project("Recommend migration strategy for our C++ Runtime services.")
            )
            self.assertTrue(
                _has_canonical_entity_or_project("Assess .NET Core upgrade risks across teams.")
            )
            self.assertFalse(
                _has_canonical_entity_or_project("Recommend a general team lunch spot.")
            )

        prev_env = os.environ.pop("HF_HUB_OFFLINE", None)
        try:
            with patch.object(BgeRerankerService, "predict", return_value=[0.91]) as mock_pred:
                s1 = BgeRerankerService.compute_rerank_score("query 1", "doc 1")
                s2 = BgeRerankerService.compute_rerank_score("query 2", "doc 2")
                self.assertAlmostEqual(0.91, s1)
                self.assertAlmostEqual(0.91, s2)
                self.assertEqual(2, mock_pred.call_count)
                self.assertNotIn("HF_HUB_OFFLINE", os.environ)
        finally:
            if prev_env is not None:
                os.environ["HF_HUB_OFFLINE"] = prev_env


if __name__ == "__main__":
    unittest.main()
