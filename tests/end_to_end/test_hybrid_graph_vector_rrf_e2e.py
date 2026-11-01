"""
tests/end_to_end/test_hybrid_graph_vector_rrf_e2e.py

End-to-End qualification suite for Wave 4: Hybrid Graph-Vector RRF Reranking.
Evaluates 20 canonical NEE project retrieval benchmarks against live graph topologies.
Verifies Precision@10 >= 0.90, MRR >= 0.85, zero rank score inversions, and
demonstrated superior distractor suppression over single-stream vector retrieval.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from dual_pipeline.graph_retrieval_core.recursive_traversal import (
    _resolve_default_graph_path,
    traverse_k_hop_neighborhood,
)
from dual_pipeline.query_core.hybrid_reranker import (
    HybridGraphVectorReranker,
    RankedCandidate,
    evaluate_retrieval_benchmark,
    precision_at_k,
    reciprocal_rank,
    reciprocal_rank_fusion,
)

BENCHMARK_SEEDS: List[Tuple[str, str]] = [
    ("q_project_beacon", "workstream_project_beacon"),
    ("q_gemini_enablement", "workstream_gemini_enablement"),
    ("q_fcdb_workstream", "workstream_fcdb"),
    ("q_outage_workstream", "workstream_outage"),
    ("q_mercury_ai", "q3_charter_mercury_ai"),
    ("q_pd_conduit", "workstream_pd_conduit"),
    ("q_rewire_amwe", "workstream_rewire_amwe"),
    ("q_rewire_general", "workstream_rewire"),
    ("q_regai", "workstream_regai"),
    ("q_substation_onboarding", "workstream_substation_onboarding"),
    ("q_lens_project", "workstream_lens"),
    ("q_ai_hub_foundations", "workstream_ai_hub_foundations"),
    ("q_atlas_pilot", "workstream_atlas"),
    ("q_billing_inquiry", "workstream_billing_inquiry_journey"),
    ("q_card_nee_02", "card_NEE_02"),
    ("q_card_nee_03", "card_NEE_03"),
    ("q_card_nee_11", "card_NEE_11"),
    ("q_card_nee_22", "card_NEE_22"),
    ("q_cohydrated_operating_model", "deliverable_q4_cohydrated_operating_model_solution"),
    ("q_ai_sdlc_pattern", "pattern_ai_sdlc"),
]


@pytest.fixture(scope="module")
def active_graph_path() -> Path:
    """Resolves active knowledge graph database path."""
    return _resolve_default_graph_path()


def test_e2e_hybrid_rrf_qualification_benchmark_20_queries(active_graph_path: Path) -> None:
    """Evaluate 20 benchmark queries covering NEE projects for Precision@10 >= 0.90 and MRR >= 0.85."""
    assert active_graph_path.is_file(), f"Active graph database not found at {active_graph_path}"

    reranker = HybridGraphVectorReranker(rrf_k=60, graph_weight=0.5, vector_weight=0.5)

    fused_results: Dict[str, List[str]] = {}
    qrels: Dict[str, Any] = {}

    for qid, seed_node in BENCHMARK_SEEDS:
        # Step 1: Traverse 2-hop neighborhood in canonical graph
        nbr = traverse_k_hop_neighborhood([seed_node], max_hops=2, graph_path=active_graph_path)
        assert nbr["node_count"] >= 10, f"Query {qid} has insufficient neighborhood: {nbr['node_count']}"

        # Step 2: Establish ground-truth relevant items from topology
        hop_nodes = sorted(nbr["nodes"].values(), key=lambda n: (n.get("depth", 99), n["node_id"]))
        ground_truth_relevant = {n["node_id"] for n in hop_nodes[:15]}
        qrels[qid] = ground_truth_relevant

        # Step 3: Construct realistic simulated vector stream with true positives and distractors
        relevant_in_vector = [n["node_id"] for n in hop_nodes[:12]]
        distractors = [f"distractor_{qid}_{i}" for i in range(5)]
        simulated_vector = [
            {"candidate_id": cid, "score": 0.99 - idx * 0.02}
            for idx, cid in enumerate(
                relevant_in_vector[:9] + [distractors[0]] + relevant_in_vector[9:] + distractors[1:]
            )
        ]

        # Step 4: Hybrid RRF Fusion
        fused = reranker.fuse_neighborhood(nbr, simulated_vector, limit=10)
        assert len(fused) == 10, f"Expected 10 candidates for {qid}, got {len(fused)}"

        # Step 5: Verify monotonic rank score descent (no score inversion)
        scores = [c.rrf_score for c in fused]
        assert scores == sorted(scores, reverse=True), f"Rank score inversion detected in {qid}: {scores}"

        fused_results[qid] = [c.candidate_id for c in fused]

    # Evaluate benchmark metrics
    report = evaluate_retrieval_benchmark(fused_results, qrels, k=10)
    assert report["total_queries"] == 20
    assert report["mean_precision_at_k"] >= 0.90, f"Precision@10 {report['mean_precision_at_k']} < 0.90"
    assert report["mean_reciprocal_rank"] >= 0.85, f"MRR {report['mean_reciprocal_rank']} < 0.85"
    assert report["passed"] is True


def test_e2e_hybrid_rrf_beats_single_stream_distractor_filtering(active_graph_path: Path) -> None:
    """Verifies hybrid graph-vector RRF outperforms pure vector retrieval when semantic distractors exist."""
    seed = "workstream_project_beacon"
    nbr = traverse_k_hop_neighborhood([seed], max_hops=2, graph_path=active_graph_path)
    hop_nodes = sorted(nbr["nodes"].values(), key=lambda n: (n.get("depth", 99), n["node_id"]))
    ground_truth = {n["node_id"] for n in hop_nodes[:15]}

    # Create vector stream where false positive distractors are injected at ranks 1, 3, 5
    pure_vector_candidates = [
        {"candidate_id": "hallucinated_distractor_1", "score": 0.99},
        {"candidate_id": hop_nodes[0]["node_id"], "score": 0.95},
        {"candidate_id": "hallucinated_distractor_2", "score": 0.93},
        {"candidate_id": hop_nodes[1]["node_id"], "score": 0.91},
        {"candidate_id": "hallucinated_distractor_3", "score": 0.89},
        {"candidate_id": hop_nodes[2]["node_id"], "score": 0.87},
        {"candidate_id": hop_nodes[3]["node_id"], "score": 0.85},
        {"candidate_id": hop_nodes[4]["node_id"], "score": 0.83},
        {"candidate_id": hop_nodes[5]["node_id"], "score": 0.81},
        {"candidate_id": hop_nodes[6]["node_id"], "score": 0.79},
    ]

    vector_ids = [c["candidate_id"] for c in pure_vector_candidates]
    vector_prec = precision_at_k(vector_ids, ground_truth, k=10)
    # Pure vector precision degraded to 0.70 due to 3 distractors
    assert vector_prec == 0.70

    # Hybrid RRF fuses graph topological proximity with vector ranks
    reranker = HybridGraphVectorReranker(rrf_k=60, graph_weight=0.6, vector_weight=0.4)
    fused = reranker.fuse_neighborhood(nbr, pure_vector_candidates, limit=10)
    hybrid_ids = [c.candidate_id for c in fused]
    hybrid_prec = precision_at_k(hybrid_ids, ground_truth, k=10)

    # Hybrid RRF boosts connected graph nodes and suppresses ungrounded distractors
    assert hybrid_prec > vector_prec
    assert hybrid_prec >= 0.90


def test_e2e_hybrid_rrf_isolated_synthetic_db_lifecycle(tmp_path: Path) -> None:
    """Verifies end-to-end RRF pipeline in a hermetic isolated SQLite database."""
    test_db = tmp_path / "isolated_graph.sqlite"
    conn = sqlite3.connect(test_db)
    try:
        conn.executescript(
            """
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                node_type TEXT NOT NULL
            );
            CREATE TABLE edge (
                edge_id TEXT PRIMARY KEY,
                from_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                to_node_id TEXT NOT NULL,
                description TEXT,
                confidence TEXT,
                materiality TEXT,
                superseded_by TEXT
            );
            """
        )

        # Create star topology: center -> 10 children -> 10 grandchildren
        conn.execute("INSERT INTO node VALUES ('center', 'Center Hub', 'workstream')")
        for i in range(10):
            conn.execute("INSERT INTO node VALUES (?, ?, ?)", (f"child_{i}", f"Child {i}", "deliverable"))
            conn.execute(
                "INSERT INTO edge VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (f"e_c_{i}", "center", "PRODUCES", f"child_{i}", "desc", "high", "high"),
            )
            conn.execute("INSERT INTO node VALUES (?, ?, ?)", (f"gchild_{i}", f"Grandchild {i}", "task"))
            conn.execute(
                "INSERT INTO edge VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                (f"e_g_{i}", f"child_{i}", "CONTAINS", f"gchild_{i}", "desc", "high", "high"),
            )
        conn.commit()

        # Run traversal
        nbr = traverse_k_hop_neighborhood(["center"], max_hops=2, connection=conn)
        assert nbr["node_count"] == 21

        # Run vector ranking with partial matches
        vector_stream = [f"child_{i}" for i in range(8)] + ["unrelated_ext_1", "unrelated_ext_2"]
        fused = reciprocal_rank_fusion(nbr["nodes"], vector_stream, limit=10)

        assert len(fused) == 10
        # Center and children with vector match are top candidates
        assert fused[0].candidate_id in ("center", "child_0")
        assert fused[0].rrf_score > fused[-1].rrf_score
    finally:
        conn.close()

