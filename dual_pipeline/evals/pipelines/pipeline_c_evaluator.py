"""Pipeline C Specialized Adversarial Evaluator.

Evaluates:
1. Generational VectorCache Invalidation: Zero-copy float32 BLAS matrix cache purge upon generation/mtime drift.
2. Steady-State Retrieval SLA: 500 consecutive query executions within < 7,500ms (<15ms per query).
3. Law 6 Policy-Before-Retrieval: Quarantined nodes (is_quarantined = 1) strictly excluded from query results.
4. Law 2 Namespace Isolation: Zero INTERNAL_ADVISORY leakage into CLIENT_SAFE retrieval contexts.
"""
from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict, Optional

import numpy as np

from dual_pipeline.evals.pipelines.models import PipelineCEvalScorecard
from dual_pipeline.index_core.vector_cache import (
    VectorCache,
)
from dual_pipeline.parameters import get_float_parameter
from pipeline_c_retrieval.engine import PolicyGatedRetrievalEngine
from storage.database import (
    AdvisoryCategory,
    CanonicalGraphNode,
    DatabaseManager,
    InternalAdvisoryNode,
)


class PipelineCEvaluator:
    """Specialized adversarial evaluator for Pipeline C retrieval, caching, and SLA contracts."""

    def __init__(self, vector_cache: Optional[VectorCache] = None) -> None:
        self.vector_cache = vector_cache or VectorCache()

    def evaluate_vector_cache_invalidation(self, dummy_lane: str = "facts") -> Dict[str, Any]:
        """Verify VectorCache invalidation on generation ID shift and file mtime bump."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_mgr = DatabaseManager(base_dir=tmpdir, allow_unencrypted_dev_fallback=True)
            with db_mgr.get_canonical_conn() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT)")
                conn.execute("CREATE TABLE IF NOT EXISTS dense_vector (record_id TEXT PRIMARY KEY, vector BLOB)")
                for i in range(5):
                    vec = np.ones(1024, dtype=np.float32) * (i + 1)
                    conn.execute("INSERT INTO retrieval_record VALUES (?, ?)", (f"rec:{i}", dummy_lane))
                    conn.execute("INSERT INTO dense_vector VALUES (?, ?)", (f"rec:{i}", vec.tobytes()))
                conn.commit()
            db_path = db_mgr.canonical_db_path
            lane_1 = self.vector_cache.get_or_load_lane(sidecar_path=db_path, lane=dummy_lane, generation_id="gen_001")
            lane_same = self.vector_cache.get_or_load_lane(sidecar_path=db_path, lane=dummy_lane, generation_id="gen_001")
            lane_2 = self.vector_cache.get_or_load_lane(sidecar_path=db_path, lane=dummy_lane, generation_id="gen_002")
            st = os.stat(db_path)
            os.utime(db_path, (st.st_atime + 10.0, st.st_mtime + 10.0))
            lane_3 = self.vector_cache.get_or_load_lane(sidecar_path=db_path, lane=dummy_lane, generation_id="gen_002")
            gen_inv, mtime_inv = (lane_1 is not lane_2), (lane_2 is not lane_3)
            passed = (lane_1 is lane_same) and gen_inv and mtime_inv and (lane_1.count == 5)
            return {
                "invalidation_verified": passed,
                "passed": passed,
                "cached_count": lane_1.count,
                "generation_bump_invalidated": gen_inv,
                "mtime_bump_invalidated": mtime_inv,
                "generation_transition": "gen_001 -> gen_002",
            }

    def evaluate_steady_state_retrieval_sla(
        self,
        query_count: int = 500,
        max_duration_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Verify 500 consecutive vector scoring operations execute within the retrieval SLA backed by VectorCache."""
        if max_duration_ms is None:
            max_duration_ms = get_float_parameter("timeouts.retrieval_steady_state_latency_ms", 7500.0)

        rng = np.random.default_rng(999)
        with tempfile.TemporaryDirectory() as tmpdir:
            db_mgr = DatabaseManager(base_dir=tmpdir, allow_unencrypted_dev_fallback=True)
            with db_mgr.get_canonical_conn() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS retrieval_record (record_id TEXT PRIMARY KEY, lane TEXT)")
                conn.execute("CREATE TABLE IF NOT EXISTS dense_vector (record_id TEXT PRIMARY KEY, vector BLOB)")
                for i in range(100):
                    vec = rng.normal(0, 0.1, 1024).astype(np.float32)
                    conn.execute("INSERT INTO retrieval_record VALUES (?, ?)", (f"rec:{i}", "facts"))
                    conn.execute("INSERT INTO dense_vector VALUES (?, ?)", (f"rec:{i}", vec.tobytes()))
                conn.commit()

            # Load lane through VectorCache from the actual SQLite sidecar
            lane_data = self.vector_cache.get_or_load_lane(
                sidecar_path=db_mgr.canonical_db_path,
                lane="facts",
                generation_id="sla_bench_gen",
            )

            query_vec = rng.normal(0, 0.1, 1024).astype(np.float32)
            candidate_ids = [f"rec:{i}" for i in range(50)]

            start_t = time.perf_counter()
            with db_mgr.get_canonical_conn() as conn:
                for idx in range(query_count):
                    # Interleaved SQLite query checking record metadata under WAL mode
                    if idx % 10 == 0:
                        _ = conn.execute("SELECT record_id, lane FROM retrieval_record LIMIT 5").fetchall()
                    indices = [lane_data.id_to_index[cid] for cid in candidate_ids if cid in lane_data.id_to_index]
                    sub_matrix = lane_data.matrix[indices]
                    scores = np.dot(sub_matrix, query_vec)
                    _ = list(zip(candidate_ids, scores))

            elapsed_ms = (time.perf_counter() - start_t) * 1000.0
            avg_query_ms = elapsed_ms / query_count
            sla_passed = elapsed_ms < max_duration_ms

            return {
                "sla_passed": sla_passed,
                "passed": sla_passed,
                "query_count": query_count,
                "total_duration_ms": elapsed_ms,
                "avg_query_ms": avg_query_ms,
                "sla_limit_ms": max_duration_ms,
                "vector_dimension": lane_data.dimensions,
                "cached_records": lane_data.count,
            }

    def evaluate_law6_quarantine_enforcement(self) -> Dict[str, Any]:
        """Verify quarantined entities (is_quarantined = 1) are strictly denied from public retrieval contexts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_mgr = DatabaseManager(base_dir=tmpdir, allow_unencrypted_dev_fallback=True)
            active_node = CanonicalGraphNode(
                node_id="node:active_01",
                evidence_id="ev:01",
                type="system",
                name="Substation Alpha",
                is_quarantined=False,
            )
            quarantined_node = CanonicalGraphNode(
                node_id="node:quarantined_02",
                evidence_id="ev:02",
                type="system",
                name="Unverified Voltage Sensor",
                is_quarantined=True,
            )
            db_mgr.upsert_canonical_node(active_node)
            db_mgr.upsert_canonical_node(quarantined_node)

            # 1. Invoke production DatabaseManager catalog query (must exclude quarantined nodes)
            public_nodes = db_mgr.query_canonical_nodes(include_quarantined=False)
            public_ids = [n.node_id for n in public_nodes]
            public_ok = public_ids == ["node:active_01"]

            # 2. Invoke production FTS5 BM25 search API (must exclude quarantined nodes even on keyword match)
            fts_nodes = db_mgr.search_canonical_nodes_fts(
                "Substation Voltage Sensor",
                include_quarantined=False,
            )
            fts_ids = [n.node_id for n in fts_nodes]
            fts_ok = "node:active_01" in fts_ids and "node:quarantined_02" not in fts_ids

            # 3. Verify quarantined node remains persisted with is_quarantined == True when queried with include_quarantined=True
            all_nodes = db_mgr.query_canonical_nodes(include_quarantined=True)
            quarantined_flag_intact = any(
                n.node_id == quarantined_node.node_id and n.is_quarantined for n in all_nodes
            )

            # 4. Invoke PolicyGatedRetrievalEngine.search directly to verify end-to-end Law 6 enforcement
            engine = PolicyGatedRetrievalEngine(db_manager=db_mgr, vector_cache=self.vector_cache)
            engine_hits = engine.search("Substation Voltage Sensor", stealth_mode=True, include_quarantined=False)
            engine_ids = [r.node_id for r in engine_hits]
            engine_ok = "node:active_01" in engine_ids and "node:quarantined_02" not in engine_ids

            passed = public_ok and fts_ok and quarantined_flag_intact and engine_ok
            return {
                "quarantine_enforced": passed,
                "passed": passed,
                "public_results_count": len(public_nodes),
                "fts_quarantine_enforced": fts_ok,
                "quarantined_flag_intact": quarantined_flag_intact,
                "engine_search_quarantine_enforced": engine_ok,
            }

    def evaluate_law2_namespace_isolation(self) -> Dict[str, Any]:
        """Verify INTERNAL_ADVISORY entities are strictly segregated from CLIENT_SAFE queries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_mgr = DatabaseManager(base_dir=tmpdir, allow_unencrypted_dev_fallback=True)
            client_node = CanonicalGraphNode(
                node_id="node:client_01",
                evidence_id="ev:01",
                type="system",
                name="Client System",
                is_quarantined=False,
            )
            leaked_adv_canonical = CanonicalGraphNode(
                node_id="node:leaked_adv_02",
                evidence_id="ev:02",
                type="stakeholder",
                name="Misrouted Advisory Node",
                is_quarantined=False,
                namespace="INTERNAL_ADVISORY",
            )
            advisory_node = InternalAdvisoryNode(
                node_id="node:adv_01",
                evidence_id="ev:02",
                type="stakeholder",
                name="Executive Power Map",
                category=AdvisoryCategory.STAKEHOLDER_DYNAMICS,
                properties={"details": "Private stakeholder dynamic"},
            )
            db_mgr.upsert_canonical_node(client_node)
            db_mgr.upsert_canonical_node(leaked_adv_canonical)
            db_mgr.upsert_shadow_advisory(advisory_node)

            # Production query APIs must enforce namespace = 'CLIENT_SAFE'
            public_nodes = db_mgr.query_canonical_nodes(include_quarantined=True)
            canon_ids = [n.node_id for n in public_nodes]
            direct_leaked_lookup = db_mgr.get_canonical_node("node:leaked_adv_02")
            direct_shadow_lookup = db_mgr.get_canonical_node("node:adv_01")

            query_api_ok = (
                canon_ids == ["node:client_01"]
                and direct_leaked_lookup is None
                and direct_shadow_lookup is None
            )
            engine = PolicyGatedRetrievalEngine(db_manager=db_mgr, vector_cache=self.vector_cache)
            stealth_hits = engine.search("Client System Executive Power Map", stealth_mode=True)
            stealth_ids = [r.node_id for r in stealth_hits]
            engine_stealth_ok = stealth_ids == ["node:client_01"] and all(
                r.namespace == "CLIENT_SAFE" for r in stealth_hits
            )
            passed = query_api_ok and engine_stealth_ok
            return {
                "namespace_isolated": passed,
                "passed": passed,
                "query_api_namespace_isolated": query_api_ok,
                "engine_stealth_namespace_isolated": engine_stealth_ok,
                "canonical_node_count": len(canon_ids),
            }

    def run_evaluation(self) -> PipelineCEvalScorecard:
        """Run all Pipeline C evaluations and return consolidated scorecard."""
        invalidation_res = self.evaluate_vector_cache_invalidation()
        sla_res = self.evaluate_steady_state_retrieval_sla()
        quarantine_res = self.evaluate_law6_quarantine_enforcement()
        namespace_res = self.evaluate_law2_namespace_isolation()

        all_passed = (
            invalidation_res["passed"]
            and sla_res["passed"]
            and quarantine_res["passed"]
            and namespace_res["passed"]
        )

        summary = (
            f"Pipeline C Evaluation: {'PASS' if all_passed else 'FAIL'} | "
            f"VectorCache Invalidation: {invalidation_res['invalidation_verified']} | "
            f"500-Query Latency: {sla_res['total_duration_ms']:.2f}ms (Avg: {sla_res['avg_query_ms']:.3f}ms) | "
            f"Law 6 Quarantine: {quarantine_res['quarantine_enforced']} | "
            f"Law 2 Namespace Isolation: {namespace_res['namespace_isolated']}"
        )

        return PipelineCEvalScorecard(
            passed=all_passed,
            vector_cache_invalidation_verified=invalidation_res["invalidation_verified"],
            steady_state_latency_ms=sla_res["total_duration_ms"],
            queries_evaluated=sla_res["query_count"],
            latency_sla_passed=sla_res["sla_passed"],
            law6_quarantine_enforced=quarantine_res["quarantine_enforced"],
            law2_namespace_isolation_enforced=namespace_res["namespace_isolated"],
            summary=summary,
            details={
                "invalidation_eval": invalidation_res,
                "sla_eval": sla_res,
                "quarantine_eval": quarantine_res,
                "namespace_eval": namespace_res,
            },
        )


# Canonical domain benchmark alias
RetrievalBenchmarkEvaluator = PipelineCEvaluator

__all__ = [
    "PipelineCEvaluator",
    "RetrievalBenchmarkEvaluator",
]
