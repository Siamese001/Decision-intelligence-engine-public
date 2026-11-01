"""Adversarial and counterfactual tests for retrieval surface functionality gaps and high-ROI enhancements.

Covers mandatory edge-case categories:
1. Boundary payloads (empty queries, 0-candidate starvation, max_subgraph_depth=0)
2. Malformed / heterogeneous inputs (dict vs RetrievedFact records, out-of-range scores)
3. Invariant bypass attempts (unauthorized allowed_roles, forbidden_leakage candidates, strict 11-gate audit)
4. Overflow & exhaustion (BgeRerankerService _PAIR_SCORE_CACHE > 10,000 entries eviction cliff)
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.agent_sdk.tools.retrieval_tools import (
    compute_hybrid_scoring,
    evaluate_candidate_retrieval,
)
from dual_pipeline.ptc.hybrid_query import (
    PTCHybridQueryExecutor,
    PTCHybridQueryPlan,
)
from dual_pipeline.retrieval.agents.release_gate_auditor import ReleaseGateAuditor
from dual_pipeline.retrieval.agents.retrieval_coordinator import RetrievalCoordinator
from dual_pipeline.retrieval.bge_reranker_service import (
    _MAX_PAIR_CACHE_ENTRIES,
    _PAIR_SCORE_CACHE,
    _RERANKER_MODEL_CACHE,
    BgeRerankerService,
)
from pipeline_c_retrieval.contracts import RetrievedFact


class _StubEngineWithRolesAndDicts:
    def __init__(self, items: List[Any]) -> None:
        self._items = items

    def search(self, **kwargs: Any) -> List[Any]:
        return list(self._items)


class _DeterministicRerankerBackend:
    def compute_score(self, pairs: List[tuple[str, str]], normalize: bool = True) -> List[float]:
        return [0.75 for _ in pairs]


class RetrievalSurfaceGapsAdversarialTests(unittest.TestCase):
    def test_retrieval_coordinator_perspectives_healer_and_strict_11_gates(self) -> None:
        """Counterfactual: RetrievalCoordinator ranks per perspective, heals starved lanes, and passes strict 11-gate audit."""
        strict_auditor = ReleaseGateAuditor(mock_mode=True, strict_payload=True)
        coord = RetrievalCoordinator(mock_mode=True, auditor_agent=strict_auditor)
        candidates = [
            {
                "node_id": "node-tech-1",
                "name": "BGE-M3 Vector Architecture",
                "content": "Technical architecture system dependencies and data contract for sidecar.",
                "confidence": 0.94,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
            {
                "node_id": "node-comm-1",
                "name": "Q4 SOW Milestone Commitment",
                "content": "Commercial delivery milestone SOW commitment and target date.",
                "confidence": 0.89,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
            {
                "node_id": "node-block-1",
                "name": "WAL Lock Bottleneck",
                "content": "Critical blocker constraint dependency and bottleneck on SQLite.",
                "confidence": 0.85,
                "classification": "CLIENT_SAFE",
                "is_quarantined": 0,
            },
        ]
        res = coord.retrieve(
            query="LENS release readiness",
            candidate_facts=candidates,
            caller_namespace="CLIENT_SAFE",
            stealth_mode=True,
        )
        self.assertTrue(res["all_gates_passed"], f"Strict 11-gate audit failed: {res}")
        self.assertEqual(res["qualification_status"], "DELIVERY_READY")
        self.assertIn("healed_perspectives", res)
        self.assertIn("ADOPTION", res["healed_perspectives"])

    def test_ptc_hybrid_query_enforces_allowed_roles_depth_and_dict_records(self) -> None:
        """Counterfactual: PTCHybridQueryExecutor filters by allowed_roles, respects max_subgraph_depth=0, and accepts dict records."""
        mixed_records: List[Any] = [
            RetrievedFact(
                node_id="n-exec",
                evidence_id="ev-1",
                namespace="CLIENT_SAFE",
                type="Decision",
                name="Executive Grid Strategy",
                snippet="Approved by executive sponsor",
                strategic_value=0.9,
                confidence=0.95,
                tacticality=0.8,
                is_quarantined=False,
                relevance_score=0.92,
                properties={"role": "executive"},
                connected_edges=[{"from_node_id": "n-exec", "to_node_id": "n-2", "relationship": "GOVERNS"}],
            ),
            {
                "node_id": "n-restricted",
                "name": "Internal Analyst Draft",
                "type": "Note",
                "snippet": "Draft note",
                "strategic_value": 0.4,
                "confidence": 0.6,
                "relevance_score": 0.5,
                "evidence_id": "ev-2",
                "role": "internal_analyst",
                "connected_edges": [{"from_node_id": "n-restricted", "to_node_id": "n-3", "relationship": "LINKS"}],
            },
        ]
        executor = PTCHybridQueryExecutor(_StubEngineWithRolesAndDicts(mixed_records))
        plan = PTCHybridQueryPlan(
            query="grid strategy",
            expand_subgraph=True,
            max_subgraph_depth=0,
            allowed_roles=["executive"],
        )
        pack = executor.execute(plan)
        self.assertEqual(len(pack.top_facts), 1)
        self.assertEqual(pack.top_facts[0]["node_id"], "n-exec")
        self.assertEqual(len(pack.subgraph_edges), 0)

    def test_agent_sdk_retrieval_tools_3modality_and_empirical_candidate_gates(self) -> None:
        """Counterfactual: compute_hybrid_scoring supports 3-modality BGE-M3 and evaluate_candidate_retrieval checks candidate leakage/metrics."""
        score_2m = compute_hybrid_scoring(0.80, 0.60, dense_weight=0.5)
        self.assertAlmostEqual(score_2m, 0.70)
        score_3m = compute_hybrid_scoring(0.80, 0.60, colbert_score=0.90)
        self.assertAlmostEqual(score_3m, round(0.80 * 0.50 + 0.60 * 0.30 + 0.90 * 0.20, 4))
        score_zero_w = compute_hybrid_scoring(0.80, 0.60, dense_weight=0.0, colbert_score=0.90, sparse_weight=0.0, colbert_weight=0.0)
        self.assertAlmostEqual(score_zero_w, score_3m)

        empty_q = evaluate_candidate_retrieval("   ")
        self.assertFalse(empty_q["passed_11_gates"])
        self.assertEqual(empty_q["status"], "REJECTED")

        default_eval = evaluate_candidate_retrieval("executive roadmap", top_k=5)
        self.assertTrue(default_eval["passed_11_gates"])
        self.assertEqual(default_eval["status"], "QUALIFIED")

        empty_cands = evaluate_candidate_retrieval("executive roadmap", top_k=5, candidates=[])
        self.assertFalse(empty_cands["passed_11_gates"])
        self.assertEqual(empty_cands["gates"]["coverage_complete"], "FAIL")
        self.assertEqual(empty_cands["gates"]["core_thresholds"], "FAIL")

        leaking_eval = evaluate_candidate_retrieval(
            "executive roadmap",
            top_k=5,
            candidates=[
                {"id": "c1", "relevant": True, "score": 0.95, "forbidden_leakage": True},
                {"id": "c2", "relevant": True, "score": 0.85, "forbidden_leakage": False},
            ],
        )
        self.assertFalse(leaking_eval["passed_11_gates"])
        self.assertEqual(leaking_eval["status"], "REJECTED")
        self.assertEqual(leaking_eval["gates"]["forbidden_leakage"], "FAIL")

        clean_eval = evaluate_candidate_retrieval(
            "executive roadmap",
            top_k=5,
            candidates=[
                {"id": "c1", "relevant": True, "score": 0.95, "forbidden_leakage": False},
                {"id": "c2", "relevant": True, "score": 0.88, "forbidden_leakage": False},
            ],
        )
        self.assertTrue(clean_eval["passed_11_gates"])
        self.assertEqual(clean_eval["status"], "QUALIFIED")
        self.assertEqual(clean_eval["mean_reciprocal_rank"], 1.0)

    def test_bge_reranker_bounded_lru_eviction_prevents_full_cache_flush_cliff(self) -> None:
        """Counterfactual: BgeRerankerService evicts oldest entries at capacity instead of clearing all 10,000 entries."""
        _PAIR_SCORE_CACHE.clear()
        model_name = "test-bounded-lru-reranker"
        _RERANKER_MODEL_CACHE[(model_name, False)] = _DeterministicRerankerBackend()
        try:
            svc = BgeRerankerService(model_name=model_name, use_fp16=False)
            for i in range(_MAX_PAIR_CACHE_ENTRIES):
                _PAIR_SCORE_CACHE[(model_name, "q", f"doc-{i}")] = 0.5
            # Touch doc-0 so it becomes most-recently used
            svc.predict([("q", "doc-0")])
            # Insert 2 new pairs pushing cache past _MAX_PAIR_CACHE_ENTRIES
            svc.predict([("q", "doc-new-1"), ("q", "doc-new-2")])
            self.assertEqual(len(_PAIR_SCORE_CACHE), _MAX_PAIR_CACHE_ENTRIES)
            self.assertIn((model_name, "q", "doc-0"), _PAIR_SCORE_CACHE)
            self.assertNotIn((model_name, "q", "doc-1"), _PAIR_SCORE_CACHE)
        finally:
            _PAIR_SCORE_CACHE.clear()
            _RERANKER_MODEL_CACHE.pop((model_name, False), None)

    def test_gap01_non_stealth_advisory_blocked_and_healer_rescores(self) -> None:
        """Adversarial: Non-stealth INTERNAL_ADVISORY query cannot bypass L1 clearance, and healer query rescores zero-hit lanes."""
        coord = RetrievalCoordinator(mock_mode=True)
        with self.assertRaises(ValueError) as ctx:
            coord.retrieve(
                query="board governance strategy",
                candidate_facts=[],
                caller_namespace="INTERNAL_ADVISORY",
                stealth_mode=False,
            )
        self.assertIn("NON_STEALTH_ADVISORY_QUERY_REJECTED", str(ctx.exception))

        from dual_pipeline.retrieval.agents.retrieval_perspective_healer import get_retrieval_perspective_healer_card

        class _CustomHealer:
            card = get_retrieval_perspective_healer_card(use_local_model=True)

            async def handle_task(self, task: Any) -> Dict[str, Any]:
                return {
                    "task_id": task.task_id,
                    "status": "COMPLETED",
                    "healed_artifact": {"query": "remediated latency throughput", "starved_lens": "TECHNICAL"},
                }

        coord_heal = RetrievalCoordinator(mock_mode=True, healer_agent=_CustomHealer())
        res = coord_heal.retrieve(
            query="unmatchedtoken",
            candidate_facts=[
                {
                    "node_id": "fact-irrelevant-high-conf",
                    "name": "Catering Menu",
                    "content": "Lunch catering order details",
                    "confidence": 0.90,
                    "classification": "CLIENT_SAFE",
                    "is_quarantined": 0,
                },
                {
                    "node_id": "fact-healed-match",
                    "name": "Remediated Latency Throughput",
                    "content": "Remediated latency throughput metrics",
                    "confidence": 0.60,
                    "classification": "CLIENT_SAFE",
                    "is_quarantined": 0,
                },
            ],
            caller_namespace="CLIENT_SAFE",
            stealth_mode=False,
        )
        self.assertEqual(res["ranked_results"][0]["node_id"], "fact-healed-match")

    def test_gap02_ptc_hybrid_query_blocks_missing_roles_and_enforces_subgraph_depth(self) -> None:
        """Adversarial: Candidate facts with None or empty role cannot bypass non-empty allowed_roles, and hop > max_subgraph_depth is pruned."""
        records = [
            {
                "node_id": "n-none-role",
                "name": "Missing Role Node",
                "type": "Fact",
                "snippet": "Missing role",
                "strategic_value": 0.95,
                "confidence": 0.95,
                "relevance_score": 0.95,
                "evidence_id": "ev-none",
                "role": None,
                "connected_edges": [],
            },
            {
                "node_id": "n-valid-role",
                "name": "Valid Role Node",
                "type": "Fact",
                "snippet": "Valid role",
                "strategic_value": 0.80,
                "confidence": 0.85,
                "relevance_score": 0.85,
                "evidence_id": "ev-valid",
                "role": "security_lead",
                "connected_edges": [
                    {"from_node_id": "n-valid-role", "to_node_id": "n-hop1", "relationship": "LINKS", "hop": 1},
                    {"from_node_id": "n-hop1", "to_node_id": "n-hop2", "relationship": "LINKS", "hop": 2},
                ],
            },
        ]
        executor = PTCHybridQueryExecutor(_StubEngineWithRolesAndDicts(records))
        pack = executor.execute(
            PTCHybridQueryPlan(
                query="role check",
                expand_subgraph=True,
                max_subgraph_depth=1,
                allowed_roles=["security_lead"],
            )
        )
        self.assertEqual([f["node_id"] for f in pack.top_facts], ["n-valid-role"])
        self.assertEqual(len(pack.subgraph_edges), 1)
        self.assertEqual(pack.subgraph_edges[0]["to_node_id"], "n-hop1")
        self.assertIn("n-hop1", {n["node_id"] for n in pack.subgraph_nodes})

    def test_gap03_copilot_multi_query_secondary_lanes_expand_acronyms_and_roles(self) -> None:
        """Adversarial: Secondary ask and topic lanes must expand domain acronyms and role synonyms instead of dropping them."""
        from dual_pipeline.live_meeting_recorder_core.copilot_retriever_ranking import collect_multi_query_rankings

        seen: Dict[str, set[str]] = {}

        class _StubCopilotRetriever:
            def _retrieve_lexical_ranking(self, q_toks, seeds, q_text, priority_only, max_results):
                seen[q_text] = set(q_toks)
                return [("aid-1", 1.0)]

        collect_multi_query_rankings(
            retriever=_StubCopilotRetriever(),
            multi_queries={
                "primary": "status",
                "ask": "scada tracker update",
                "topic": "productivity review",
            },
            query_tokens={"status"},
            direct_seed_nodes=set(),
            priority_only=False,
            max_results=5,
        )
        self.assertIn("historian", seen["scada tracker update"])
        self.assertIn("velocity", seen["productivity review"])

    def test_enh01_copilot_mmr_blends_dense_cosine_and_lexical_jaccard(self) -> None:
        """Counterfactual: compute_assertion_similarity blends dense cosine similarity with lexical Jaccard to suppress semantic paraphrases."""
        import numpy as np
        from dual_pipeline.live_meeting_recorder_core.copilot_retrieval_fusion import compute_assertion_similarity

        a1 = {"assertion_id": "a1", "claim_text": "substation transformer thermal overload"}
        a2 = {"assertion_id": "a2", "claim_text": "grid equipment overheating failure"}
        # Lexical overlap is 0.0 (completely disjoint vocabulary), but dense embeddings are identical (cosine = 1.0)
        dense_matrix = np.array([[1.0, 0.0], [1.0, 0.0]], dtype=np.float32)
        record_id_to_idx = {"a1": 0, "a2": 1}

        sim_with_dense = compute_assertion_similarity(a1, a2, dense_matrix=dense_matrix, record_id_to_idx=record_id_to_idx)
        sim_lex_only = compute_assertion_similarity(a1, a2, dense_matrix=None, record_id_to_idx=None)
        self.assertEqual(sim_lex_only, 0.0)
        self.assertAlmostEqual(sim_with_dense, 0.5, places=4)

    def test_enh02_colbert_cache_lru_promotion_and_query_encoding_cache_bound(self) -> None:
        """Counterfactual: PrePaddedColbertCache promotes hit keys to MRU and QueryEncodingCache bounds in-memory _entries."""
        import tempfile
        from pathlib import Path
        import numpy as np
        from dual_pipeline.index_core.retriever_cache import PrePaddedColbertCache
        from dual_pipeline.retrieval_release_core.query_encoding_cache import QueryEncodingCache

        cache = PrePaddedColbertCache(max_entries=2, max_bytes=1024 * 1024)
        arr = np.zeros((2, 4), dtype=np.float32)
        lens = np.array([2, 2], dtype=np.int32)
        cache.set(("k1",), arr, lens, None)
        cache.set(("k2",), arr, lens, None)
        # Touch ("k1",) so it becomes MRU and ("k2",) becomes LRU
        self.assertIsNotNone(cache.get(("k1",)))
        # Adding ("k3",) at capacity=2 must evict ("k2",), keeping hot ("k1",)
        cache.set(("k3",), arr, lens, None)
        self.assertIsNotNone(cache.get(("k1",)))
        self.assertIsNone(cache.get(("k2",)))

        class _StubBackend:
            backend_id = "stub-bge"
            model_name = "stub-model"

        with tempfile.TemporaryDirectory() as tmpdir:
            q_cache = QueryEncodingCache(Path(tmpdir) / "q_cache.sqlite", max_entries=3)
            backend = _StubBackend()
            q_cache.set(backend, "q1", {"dense": [1.0]})
            q_cache.set(backend, "q2", {"dense": [2.0]})
            q_cache.set(backend, "q3", {"dense": [3.0]})
            # Touch q1 so it becomes MRU in memory
            self.assertEqual(q_cache.get(backend, "q1"), {"dense": [1.0]})
            # Insert q4 -> in-memory _entries must stay bounded at 3 and evict LRU (q2) from _entries
            q_cache.set(backend, "q4", {"dense": [4.0]})
            self.assertEqual(len(q_cache._entries), 3)

    def test_wave1_adversarial_zero_score_rrf_pollution_and_json_hydration_bound(self) -> None:
        """Adversarial: Zero-score facts in specialized perspectives must not outrank matching facts via RRF rank credit, and JSON cache hydration must respect max_entries."""
        import json
        import tempfile
        from pathlib import Path
        from dual_pipeline.retrieval.agents.retrieval_coordinator import _score_fact_for_perspective
        from dual_pipeline.retrieval_release_core.query_encoding_cache import CACHE_SCHEMA_VERSION, QueryEncodingCache

        # 1. Zero-overlap fact on GENERAL perspective must score 0.0 instead of raw confidence
        irrelevant_fact = {"node_id": "f-irr", "name": "Cafeteria Menu", "content": "soup and salad", "confidence": 0.99}
        self.assertEqual(_score_fact_for_perspective(irrelevant_fact, "GENERAL", "substation breaker telemetry"), 0.0)

        # 2. JSON fallback hydration in QueryEncodingCache._load() must also cap _entries at max_entries
        with tempfile.TemporaryDirectory() as tmpdir:
            json_cache_path = Path(tmpdir) / "legacy_cache.json"
            json_cache_path.write_text(
                json.dumps(
                    {
                        "schema_version": CACHE_SCHEMA_VERSION,
                        "entries": {
                            "k1": {"query": "q1", "encoded": {"d": [1]}},
                            "k2": {"query": "q2", "encoded": {"d": [2]}},
                            "k3": {"query": "q3", "encoded": {"d": [3]}},
                            "k4": {"query": "q4", "encoded": {"d": [4]}},
                        },
                    }
                ),
                encoding="utf-8",
            )
            q_cache = QueryEncodingCache(json_cache_path, max_entries=2)
            self.assertEqual(len(q_cache._entries), 2)

    def test_wave2_adversarial_vector_cache_subset_and_session_context_synonym_recency(self) -> None:
        """Adversarial: LaneDenseVectors.record_id_set handles empty/partial authorized sets and search_session_context handles short/empty turns."""
        import numpy as np
        from dual_pipeline.index_core.vector_cache import LaneDenseVectors
        from dual_pipeline.live_meeting_recorder_core.copilot_retriever import search_session_context

        lane = LaneDenseVectors(
            lane="assertion",
            record_ids=["r1", "r2"],
            matrix=np.eye(2, dtype=np.float32),
            id_to_index={"r1": 0, "r2": 1},
        )
        self.assertEqual(lane.record_id_set, frozenset({"r1", "r2"}))

        turns = [
            {"timestamp": "09:00", "speaker": "Lead", "text": "Engineering velocity and throughput increased"},
            {"timestamp": "09:15", "speaker": "PM", "text": "Team velocity and throughput targets met"},
            {"timestamp": "09:20", "speaker": "A", "text": "w1"},
            {"timestamp": "09:21", "speaker": "A", "text": "w2"},
            {"timestamp": "09:22", "speaker": "A", "text": "w3"},
            {"timestamp": "09:23", "speaker": "A", "text": "w4"},
        ]
        res = search_session_context(turns, "productivity", top_k=2)
        lines = res.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("09:15 PM", lines[1])
        self.assertIn("09:00 Lead", lines[2])


if __name__ == "__main__":
    unittest.main()
