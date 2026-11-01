"""End-to-end verification of MultiHopQueryNavigatorAgent, CompetitiveQuerySwarm fusion, and query planning."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from dual_pipeline.l2 import load_l2_config
from dual_pipeline.query_core.navigator_agent import MultiHopQueryNavigatorAgent
from dual_pipeline.query_core.planning import build_query_plan
from dual_pipeline.query_swarm import CompetitiveQuerySwarm


def _create_three_hop_sqlite_graph(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE assertions (
                assertion_id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object_id TEXT NOT NULL,
                claim_text TEXT NOT NULL,
                source_id TEXT NOT NULL,
                namespace TEXT DEFAULT 'CANONICAL',
                is_quarantined INTEGER DEFAULT 0,
                stealth_score REAL DEFAULT 0.0
            )
            """
        )
        rows = [
            (
                "asrt-hop1",
                "Capability_Gateway",
                "ADDRESSES",
                "PainPoint_Latency",
                "Capability_Gateway addresses PainPoint_Latency in intake routing.",
                "src:doc:arch_01",
                "CANONICAL",
                0,
                0.0,
            ),
            (
                "asrt-hop2",
                "PainPoint_Latency",
                "DEPENDS_ON",
                "Initiative_Batching",
                "PainPoint_Latency remediation depends on Initiative_Batching.",
                "src:doc:arch_02",
                "CANONICAL",
                0,
                0.0,
            ),
            (
                "asrt-hop3",
                "Initiative_Batching",
                "CONTRIBUTES_TO",
                "Workstream_Velocity",
                "Initiative_Batching contributes to Workstream_Velocity milestone.",
                "src:doc:arch_03",
                "CANONICAL",
                0,
                0.0,
            ),
        ]
        conn.executemany(
            "INSERT INTO assertions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def test_multihop_navigator_resolves_three_hop_chain_over_readonly_sqlite_e2e(tmp_path: Path) -> None:
    """Verifies 3-hop bridge traversal over read-only SQLite with zero file mutation."""
    db_file = tmp_path / "canonical_test_graph.sqlite"
    _create_three_hop_sqlite_graph(db_file)
    before_sha = hashlib.sha256(db_file.read_bytes()).hexdigest()

    navigator = MultiHopQueryNavigatorAgent(max_steps=3, max_tool_calls=6)
    result = navigator.navigate(
        query="How does Capability_Gateway connect to Workstream_Velocity across initiatives?",
        seed_entities=["Capability_Gateway"],
        subquestions=[
            "Which pain point does Capability_Gateway address?",
            "Which initiative does PainPoint_Latency depend on?",
            "How does Initiative_Batching contribute to Workstream_Velocity?",
        ],
        db_path=db_file,
        readonly=True,
    )

    after_sha = hashlib.sha256(db_file.read_bytes()).hexdigest()
    assert before_sha == after_sha

    assert result.terminal_outcome == "COMPLETED"
    assert result.steps_executed == 3
    assert result.tool_calls_used == 6
    assert result.coverage_ratio == 1.0
    assert "Workstream_Velocity" in result.expanded_entities
    assert len(result.expanded_assertions) == 3
    assert len(result.claim_checks) == 3
    assert all(len(c["content_digest"]) == 64 for c in result.claim_checks)


def test_competitive_query_swarm_graph_probe_and_speculative_fusion_e2e() -> None:
    """Verifies graph density probing and dual-route speculative fusion when margin <= 0.20."""
    swarm = CompetitiveQuerySwarm()

    # High-margin exact quote query -> AUTONOMOUS single route
    exact_res = swarm.route_query(
        "id:cap_gateway",
        graph_stats={"anchor_node_matches": 2},
    )
    assert exact_res["route_id"] == "ROUTE_1_EXACT_MATCH"
    assert exact_res["escalation_required"] is False
    assert exact_res["autonomy_decision"]["outcome_type"] == "AUTONOMOUS"
    assert exact_res["fused_routes"] == ["ROUTE_1_EXACT_MATCH"]
    assert "graph_probe:density_verified" in exact_res["evidence_refs"]

    # Multi-intent query producing narrow margin <= 0.20 -> DUAL_ROUTE_FUSION_AND_ESCALATE
    ambig_res = swarm.route_query(
        "How does the strategic decision risk connect to the graph network topology?",
        graph_stats={"multi_hop_edges": 4},
    )
    assert ambig_res["escalation_required"] is True
    assert len(ambig_res["fused_routes"]) == 2
    assert ambig_res["autonomy_decision"]["outcome_type"] == "DUAL_ROUTE_FUSION_AND_ESCALATE"


def test_build_query_plan_embeds_navigator_extension_e2e() -> None:
    """Verifies that build_query_plan populates com.partner_advisory.nee.navigator in extensions."""
    request_id = "req_nav_e2e_00000000000001"
    run_id = "run_nav_e2e_00000000000001"
    trace_id = "a" * 32
    dest = {
        "destination": "NEE_DECISION_INTELLIGENCE",
        "canonical_digest": {"value": "b" * 64},
        "delivery_profile": "internal_technical",
        "artifact_type": "answer",
    }
    plan = build_query_plan(
        question="Which governed paths connect PDLC capabilities to workstream risks?",
        query_type="relationship_explanation",
        request_id=request_id,
        run_id=run_id,
        current_trace_id=trace_id,
        l2_config=load_l2_config(),
        provider_egress_allowed=True,
        destination_decision=dest,
        assurance_profile="standard",
    )
    nav_ext = plan["extensions"]["com.partner_advisory.nee.navigator"]
    assert nav_ext["enabled"] is True
    assert nav_ext["max_steps"] >= 1
    assert nav_ext["max_tool_calls"] == 6
    assert "RELATED_TO" in nav_ext["bridge_predicates"]
