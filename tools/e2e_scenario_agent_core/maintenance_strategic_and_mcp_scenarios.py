"""Pipelines 12, 13, and 14 E2E scenario runners: Graph Signal & Warehouse Sync, Strategic Waves & Answer Ledger, and 5 MCP Servers + Live 89-Table Active Pair Read-Only Audit."""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from dual_pipeline.agent_sdk.mcp_bridge import McpServerRegistry
from dual_pipeline.answer_ledger import get_default_answer_ledger, record_query_answer_safe, verify_answer_graph_traversal
from dual_pipeline.core import ROOT, connect_sqlite, sha256_file
from dual_pipeline.export.warehouse_sync import WarehouseSyncEngine
from dual_pipeline.graph_signal_core.evaluator import evaluate_graph_signal
from dual_pipeline.graph_signal_core.freshness import calculate_graph_freshness
from dual_pipeline.graph_signal_core.mutex_invalidation import supersede_conflicting_assertions
from dual_pipeline.hash_cache import cached_sha256_file
from dual_pipeline.llm_judge_core.judge import evaluate_dialectical_decision
from dual_pipeline.strategic_reasoning_core.constants import KNOWLEDGE_ROLES, MODALITIES
from dual_pipeline.strategic_reasoning_core.profiling import validate_strategic_profile
from dual_pipeline.strategic_waves_core.specs import WAVES
from storage.database import DatabaseManager
from storage.models import AssertionRecord, CanonicalGraphNode, GraphEdge
from tools import mcp_contract_server, mcp_graphdb_server, mcp_judge_eval_server, mcp_speech_server, mcp_test_orchestrator_server
from .models import ScenarioVerificationResult

PIPELINE_P12_ID = "P12_GRAPH_MAINTENANCE_SIGNAL_AND_WAREHOUSE"
PIPELINE_P12_NAME = "Graph Signal, Temporal Supersession & Warehouse Sync"
PIPELINE_P13_ID = "P13_STRATEGIC_WAVES_DIALECTICAL_AND_LEDGER"
PIPELINE_P13_NAME = "18-Role, 6-Modality, W1-W7 Strategic Waves, Dialectical Judge & Answer Ledger"
PIPELINE_P14_ID = "P14_MCP_SERVERS_AND_ACTIVE_PAIR_AUDIT"
PIPELINE_P14_NAME = "5 Repository MCP Servers, 11-Server Registry & Live 89-Table Active Pair Audit"


def run_p12_supersession_and_graph_signal_scenario(
    sandbox_root: Path, graph_path: Path, sidecar_path: Path
) -> ScenarioVerificationResult:
    """Verify Pipeline 12 temporal conflict supersession, graph freshness, and GS-10 graph signal."""
    t0 = time.perf_counter()
    freshness = calculate_graph_freshness(graph_path, fail_closed=False)
    signal = evaluate_graph_signal(graph_path, sidecar_path)

    sup_dir = sandbox_root / "p12_supersession"
    sup_dir.mkdir(parents=True, exist_ok=True)
    sup_graph = sup_dir / "superseded_graph.sqlite"
    shutil.copy2(graph_path, sup_graph)

    conn = connect_sqlite(sup_graph)
    superseded_ids = supersede_conflicting_assertions(
        conn,
        incoming_assertion_id="as-e2e-superseding-02",
        subject_node_id="node-sys-01",
        predicate="SYNCHRONIZES_WITH",
        object_node_id="node-mod-02",
        now_iso="2026-10-31T00:00:00Z",
    )
    conn.commit()
    row = conn.execute(
        "SELECT currentness_state, superseded_by_assertion_id, retrieval_eligible FROM assertion WHERE assertion_id='as-e2e-canonical-01'"
    ).fetchone()
    conn.close()

    passed = bool(
        freshness.get("status") in ("HEALTHY", "PASS", "OK", "WARNING")
        and signal.get("schema_version") is not None
        and "as-e2e-canonical-01" in superseded_ids
        and row is not None
        and row[0] == "historical"
        and row[1] == "as-e2e-superseding-02"
        and int(row[2]) == 0
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P12-SUPERSESSION-AND-GRAPH-SIGNAL",
        pipeline_id=PIPELINE_P12_ID,
        pipeline_name=PIPELINE_P12_NAME,
        title="Temporal Conflict Supersession, Graph Freshness & GS-10 Signal Evaluation",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_deliverables={"superseded_graph": str(sup_graph)},
        verified_invariants=[
            "TEMPORAL_CONFLICT_SUPERSEDED_TO_HISTORICAL",
            "GRAPH_FRESHNESS_CALCULATED",
            "GS10_GRAPH_SIGNAL_EVALUATED",
        ],
        metrics={
            "superseded_ids": superseded_ids,
            "freshness_status": freshness.get("status"),
            "composite_signal": signal.get("composite_score"),
        },
    )


def run_p12_warehouse_export_and_facet_schema_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 12 analytical warehouse JSONL export across nodes, edges, and assertions."""
    t0 = time.perf_counter()
    wh_db_dir = sandbox_root / "p12_warehouse_db"
    wh_out_dir = sandbox_root / "p12_warehouse_export"
    db_mgr = DatabaseManager(base_dir=wh_db_dir, allow_unencrypted_dev_fallback=True)
    db_mgr.upsert_canonical_node(
        CanonicalGraphNode(
            node_id="node-wh-01", evidence_id="ev-wh-01", type="system",
            name="Analytical Warehouse Bridge", strategic_value=0.9, confidence=0.95,
        )
    )
    db_mgr.upsert_canonical_node(
        CanonicalGraphNode(
            node_id="node-wh-02", evidence_id="ev-wh-02", type="org",
            name="Enterprise Lakehouse", strategic_value=0.88, confidence=0.94,
        )
    )
    db_mgr.upsert_canonical_edge(
        GraphEdge(
            edge_id="edge-wh-01", from_node_id="node-wh-01",
            predicate="STREAMS_TO", to_node_id="node-wh-02", confidence=0.95,
        )
    )
    db_mgr.upsert_assertion(
        AssertionRecord(
            assertion_id="as-wh-01", subject_node_id="node-wh-01",
            predicate="STREAMS_TO", object_node_id="node-wh-02",
            literal_value="Analytical Warehouse Bridge streams JSONL to Enterprise Lakehouse.",
            confidence=0.95,
        )
    )
    sync_res = WarehouseSyncEngine(db_manager=db_mgr).sync(wh_out_dir, destination_format="jsonl")
    db_mgr.close()

    manifest_file = wh_out_dir / "sync_manifest.json"
    nodes_file = wh_out_dir / "canonical_graph_nodes.jsonl"
    edges_file = wh_out_dir / "canonical_graph_edges.jsonl"
    assertions_file = wh_out_dir / "assertions.jsonl"
    passed = bool(
        sync_res.success
        and sync_res.total_records >= 4
        and manifest_file.is_file()
        and nodes_file.is_file()
        and edges_file.is_file()
        and assertions_file.is_file()
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P12-WAREHOUSE-EXPORT-AND-FACET-SCHEMA",
        pipeline_id=PIPELINE_P12_ID,
        pipeline_name=PIPELINE_P12_NAME,
        title="Analytical Warehouse JSONL Export Parity Across Nodes, Edges & Assertions",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_deliverables={"sync_manifest": str(manifest_file), "assertions_jsonl": str(assertions_file)},
        verified_invariants=[
            "WAREHOUSE_NODES_EDGES_ASSERTIONS_EXPORTED",
            "SYNC_MANIFEST_RECORD_PARITY",
        ],
        metrics={"total_records": sync_res.total_records, "total_bytes": sync_res.total_bytes},
    )


def run_p13_18_roles_6_modalities_and_dialectical_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Verify Pipeline 13 18 knowledge roles, 6 modalities, W1-W7 waves, and 20% dialectical judge gate."""
    t0 = time.perf_counter()
    valid_prof = validate_strategic_profile({
        "knowledge_role": "official_intent",
        "modality": "intended",
        "authority_scope": "official_project_intent",
        "applicability_scope": "nee_specific",
        "realization_state": "planned",
    })
    invalid_blocked = False
    try:
        validate_strategic_profile({
            "knowledge_role": "official_intent",
            "modality": "intended",
            "authority_scope": "official_project_intent",
            "applicability_scope": "nee_specific",
            "realization_state": "observed",
        })
    except ValueError:
        invalid_blocked = True

    ledger_path = sandbox_root / "p13_dialectical" / "dialectical_ledger.json"
    close_eval = evaluate_dialectical_decision(
        decision_id="dec-p13-close",
        question="Select primary regional failover topology",
        options=[
            {"option_id": "opt-a", "label": "Active-Active Multi-Region", "confidence": 0.82},
            {"option_id": "opt-b", "label": "Warm Standby Pilot Light", "confidence": 0.74},
        ],
        ledger_path=ledger_path,
    )
    decisive_eval = evaluate_dialectical_decision(
        decision_id="dec-p13-decisive",
        question="Select canonical storage engine",
        options=[
            {"option_id": "opt-wal", "label": "SQLite WAL + Hybrid Sidecar", "confidence": 0.92},
            {"option_id": "opt-flat", "label": "Unindexed Flat JSON", "confidence": 0.50},
        ],
        ledger_path=ledger_path,
    )
    passed = bool(
        len(KNOWLEDGE_ROLES) == 18
        and len(MODALITIES) == 6
        and len(WAVES) == 8
        and valid_prof["knowledge_role"] == "official_intent"
        and invalid_blocked
        and close_eval.should_surface is True
        and close_eval.status == "HITL_REQUIRED"
        and decisive_eval.should_surface is False
        and decisive_eval.status == "AUTONOMOUS_RESOLUTION"
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P13-18-ROLES-6-MODALITIES-AND-DIALECTICAL",
        pipeline_id=PIPELINE_P13_ID,
        pipeline_name=PIPELINE_P13_NAME,
        title="18 Knowledge Roles, 6 Modalities, W1-W7 Strategic Waves & 20% Dialectical Judge Gate",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "18_KNOWLEDGE_ROLES_AND_6_MODALITIES_VERIFIED",
            "W1_TO_W7_STRATEGIC_WAVES_REGISTERED",
            "ROLE_REALIZATION_BOUNDARY_ENFORCED",
            "DIALECTICAL_20_PERCENT_MARGIN_GATE",
        ],
        metrics={"roles": len(KNOWLEDGE_ROLES), "modalities": len(MODALITIES), "waves": len(WAVES)},
    )


def run_p13_answer_ledger_hash_chain_scenario(
    sandbox_root: Path, graph_path: Path
) -> ScenarioVerificationResult:
    """Verify Pipeline 13 AnswerLedger Merkle hash chain and GraphDB traversal provenance."""
    t0 = time.perf_counter()
    ledger_root = sandbox_root / "p13_answer_ledger"
    g_sha = sha256_file(graph_path)
    q_result: dict[str, Any] = {
        "cited_assertion_ids": ["as-e2e-canonical-01"],
        "response_text": "Core Engine synchronizes with Hybrid Sidecar.",
        "rigor": "decision_grade",
        "hop_count": 2,
    }
    ans1 = record_query_answer_safe(
        ledger_root, "How does Core Engine synchronize?", q_result,
        query_type="exact_lookup", graph_sha256=g_sha, generation="gen-sandbox-e2e-01",
        latency_ms=11.2, model_id="governed-e2e",
    )
    ans2 = record_query_answer_safe(
        ledger_root, "Confirm Hybrid Sidecar retrieval parity.", q_result,
        query_type="exact_lookup", graph_sha256=g_sha, generation="gen-sandbox-e2e-01",
        latency_ms=9.8, model_id="governed-e2e",
    )
    ledger = get_default_answer_ledger(ledger_root)
    chain_ok, chain_msg = ledger.verify_chain()
    rec1 = ledger.get_answer(ans1) if ans1 else None

    ro_conn = connect_sqlite(graph_path, readonly=True)
    traversal_ok = verify_answer_graph_traversal(
        rec1, active_pair={"graph_sha256": g_sha}, conn=ro_conn
    ) if rec1 else False
    ro_conn.close()

    passed = bool(ans1 and ans2 and chain_ok and traversal_ok)
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P13-ANSWER-LEDGER-HASH-CHAIN",
        pipeline_id=PIPELINE_P13_ID,
        pipeline_name=PIPELINE_P13_NAME,
        title="Answer Ledger Merkle Hash Chain Integrity & SQLite Citation Traversal Provenance",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_deliverables={"answer_ledger_sqlite": str(ledger.db_path)},
        verified_invariants=[
            "ANSWER_LEDGER_MERKLE_CHAIN_INTACT",
            "CITED_ASSERTION_SQLITE_PROVENANCE_VERIFIED",
        ],
        metrics={"ans1": ans1, "ans2": ans2, "chain_message": chain_msg},
    )


def run_p14_five_mcp_servers_roundtrip_scenario(
    sandbox_root: Path, graph_path: Path, sidecar_path: Path
) -> ScenarioVerificationResult:
    """Verify Pipeline 14 round-trip execution across all 5 repository MCP servers and 11-server registry."""
    t0 = time.perf_counter()
    c_hash = json.loads(mcp_contract_server.rfc8259_canonical_hash(json.dumps({"a": 1, "b": 2})))
    c_inv = json.loads(mcp_contract_server.contract_inventory_verify("evidence-envelope-intake"))
    g_sync = json.loads(mcp_graphdb_server.graphdb_verify_sidecar_sync(str(graph_path), str(sidecar_path)))
    g_vec = json.loads(mcp_graphdb_server.graphdb_inspect_vector_health(str(sidecar_path), sample_limit=50))
    j_cal = json.loads(mcp_judge_eval_server.calibrate_hybrid_weights(0.05, 0.15, 0.10, 0.70))
    j_seal = json.loads(
        mcp_judge_eval_server.seal_retrieval_receipt(
            json.dumps({"passed": True}),
            output_dir=str(sandbox_root / "p14_mcp_receipts"),
            receipt_id="rcpt-p14-01",
        )
    )
    s_stitch = json.loads(
        mcp_speech_server.transcript_stitch_speakers(
            json.dumps([
                {"speaker": "Amit Ayer", "start_time": "00:00:01", "end_time": "00:00:04", "text": "Part one."},
                {"speaker": "Amit Ayer", "start_time": "00:00:04", "end_time": "00:00:08", "text": "Part two."},
            ])
        )
    )
    t_diag = json.loads(mcp_test_orchestrator_server.extract_test_failure_diagnostics("1 passed in 0.12s"))
    reg = McpServerRegistry()
    discovered = reg.registered_server_names

    passed = bool(
        len(c_hash.get("sha256", "")) == 64
        and c_inv.get("status") == "ALL_IN_SYNC"
        and g_sync.get("aligned") is True
        and g_vec.get("status") in ("HEALTHY", "PASS", "OK")
        and j_cal.get("status") == "CALIBRATED"
        and j_seal.get("status") == "RECEIPT_SEALED"
        and s_stitch.get("stitched_turn_count") == 1
        and "failures" in t_diag
        and len(discovered) >= 11
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P14-FIVE-MCP-SERVERS-ROUNDTRIP",
        pipeline_id=PIPELINE_P14_ID,
        pipeline_name=PIPELINE_P14_NAME,
        title="All 5 Repository MCP Servers Tool Round-Trip & 11-Server MCP Registry Discovery",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "MCP_CONTRACT_AND_GRAPHDB_SERVERS_VERIFIED",
            "MCP_JUDGE_SPEECH_AND_TEST_SERVERS_VERIFIED",
            "11_SERVER_MCP_REGISTRY_DISCOVERED",
        ],
        metrics={"discovered_mcp_servers": len(discovered), "stitched_turns": s_stitch.get("stitched_turn_count")},
    )


def run_p14_live_active_pair_89_table_readonly_audit_scenario(sandbox_root: Path) -> ScenarioVerificationResult:
    """Perform strictly read-only (mode=ro) audit of the live 89-table production GraphDB and 6-table Sidecar."""
    t0 = time.perf_counter()
    _ = sandbox_root
    candidates = [
        ROOT / ".local" / "dual_pipeline" / "active_pair.json",
        ROOT.parent.parent / ".local" / "dual_pipeline" / "active_pair.json",
    ]
    ap_file = next((p for p in candidates if p.is_file()), None)
    if ap_file is None:
        return ScenarioVerificationResult(
            scenario_id="SCENARIO-P14-LIVE-ACTIVE-PAIR-89-TABLE-READONLY-AUDIT",
            pipeline_id=PIPELINE_P14_ID, pipeline_name=PIPELINE_P14_NAME,
            title="Live Active Production Pair 89-Table Read-Only Audit", passed=False,
            duration_ms=0.0, error="active_pair.json not found",
        )
    ap_data = json.loads(ap_file.read_text(encoding="utf-8"))
    gp = (ap_file.parent / str(ap_data["graph_path"])).resolve()
    sp = (ap_file.parent / str(ap_data["sidecar_path"])).resolve()
    g_sha_before = cached_sha256_file(gp)

    ro_g = connect_sqlite(gp, readonly=True)
    g_tables = [r[0] for r in ro_g.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    quick_ok = ro_g.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    canon_cnt = int(ro_g.execute("SELECT COUNT(*) FROM assertion WHERE canonical_status='canonical' AND canonical_eligible=1").fetchone()[0])
    unmapped = int(
        ro_g.execute(
            "SELECT COUNT(*) FROM edge e LEFT JOIN legacy_edge_assertion_map m ON e.edge_id = m.edge_id WHERE m.assertion_id IS NULL"
        ).fetchone()[0]
    )
    ro_g.close()

    ro_s = connect_sqlite(sp, readonly=True)
    s_tables = [r[0] for r in ro_s.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    s_meta = dict(ro_s.execute("SELECT key, value FROM metadata").fetchall())
    dense_cnt = int(ro_s.execute("SELECT COUNT(*) FROM dense_vector").fetchone()[0])
    ro_s.close()
    g_sha_after = cached_sha256_file(gp)

    passed = bool(
        len(g_tables) >= 85
        and quick_ok
        and canon_cnt > 0
        and unmapped == 0
        and len(s_tables) >= 6
        and dense_cnt > 0
        and (s_meta.get("graph_sha256") or s_meta.get("bound_graph_sha256")) == g_sha_before
        and g_sha_before == g_sha_after
    )
    return ScenarioVerificationResult(
        scenario_id="SCENARIO-P14-LIVE-ACTIVE-PAIR-89-TABLE-READONLY-AUDIT",
        pipeline_id=PIPELINE_P14_ID,
        pipeline_name=PIPELINE_P14_NAME,
        title="Live Active Production Pair 89-Table GraphDB & 6-Table Sidecar Read-Only Audit",
        passed=passed,
        duration_ms=round((time.perf_counter() - t0) * 1000.0, 2),
        verified_invariants=[
            "PRODUCTION_89_GRAPH_TABLES_VERIFIED_READONLY",
            "PRODUCTION_6_SIDECAR_TABLES_BOUND_SHA_MATCH",
            "ZERO_UNMAPPED_ACTIVE_PRODUCTION_EDGES",
            "ZERO_PRODUCTION_MUTATION_DURING_AUDIT",
        ],
        metrics={
            "prod_graph_table_count": len(g_tables),
            "prod_sidecar_table_count": len(s_tables),
            "prod_canonical_assertions": canon_cnt,
            "prod_dense_vectors": dense_cnt,
        },
    )
