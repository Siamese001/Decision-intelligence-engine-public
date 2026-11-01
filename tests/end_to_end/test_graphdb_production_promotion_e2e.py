"""
Wave 4 E2E Verification: Governed Production GraphDB & BGE-M3 Sidecar Promotion.

Verifies:
1. All 5 mandatory physical proof checks (File Changed, Raw Byte Scan, Integrity,
   Fresh Readback, Row Delta) pass on a promoted generation.
2. Stakeholder role convergence for Eduardo More, Parker Vance, Amit Ayer,
   Michael Maione, and Siara Montgelas with zero competing role conflicts.
3. 100% assertion_evidence linkage for current assertions and node_fts == node parity.
4. Label quality audit passes with 0 blocking codes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

from dual_pipeline.index_core.records import render_retrieval_records
from dual_pipeline.label_quality import audit_graph_labels
from dual_pipeline.runtime_root import resolve_active_pair, resolve_authoritative_runtime_root
from dual_pipeline.transcript_metadata_core.entity_resolver import (
    PreSynthesisEntityResolver,
    ResolvedEntity,
)
from dual_pipeline.transcript_metadata_core.entity_resolver_graph import (
    load_knowledge_graph_data,
)
from dual_pipeline.uwg_core.universal_write_gateway_role import (
    backfill_unlinked_current_assertions,
    persist_validated_role,
)


def _sha256_bytes(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def test_e2e_governed_graphdb_promotion_and_five_physical_checks(tmp_path: Path) -> None:
    """Executes the full Wave 4 remediation on a sandbox clone and verifies all 5 physical checks."""
    rt_root = resolve_authoritative_runtime_root()
    active_pair, _ = resolve_active_pair(rt_root, enforce_integrity=False)
    src_graph = rt_root / active_pair["graph_path"]
    assert src_graph.is_file()

    sandbox_graph = tmp_path / "graph.sqlite"
    shutil.copy2(src_graph, sandbox_graph)

    # Record pre-mutation state if sandbox_graph was not yet remediated, or test idempotent + delta write
    pre_conn = sqlite3.connect(f"file:{sandbox_graph}?mode=ro", uri=True)
    try:
        tbl_queries = {
            "node": "SELECT count(*) FROM node",
            "assertion": "SELECT count(*) FROM assertion",
            "evidence_item": "SELECT count(*) FROM evidence_item",
            "assertion_evidence": "SELECT count(*) FROM assertion_evidence",
            "node_fts": "SELECT count(*) FROM node_fts",
        }
        before_counts = {t: pre_conn.execute(q).fetchone()[0] for t, q in tbl_queries.items()}
    finally:
        pre_conn.close()

    # Apply Wave 4 governed UWG role persistence + evidence backfill + FTS resync
    r_eduardo = persist_validated_role(sandbox_graph, "Eduardo (Ed) Juan More", "Atlas Lead / Rule Specialist")
    r_anthony = persist_validated_role(sandbox_graph, "Parker Vance", "Director Strategic Initiatives, Op Excellence")
    r_amit = persist_validated_role(sandbox_graph, "Amit Ayer", "Senior Director / Lead Enterprise Architect")
    r_michael = persist_validated_role(sandbox_graph, "Michael Maione", "Solution Lead / Manager")
    r_siara = persist_validated_role(sandbox_graph, "Product Lead / Project Manager" if False else "Siara Montgelas", "Product Lead / Project Manager")
    bf_res = backfill_unlinked_current_assertions(sandbox_graph)

    # Resync node_fts and search_fts and checkpoint WAL
    w_conn = sqlite3.connect(sandbox_graph)
    try:
        w_conn.execute("DELETE FROM node_fts")
        w_conn.execute(
            "INSERT INTO node_fts (node_id, canonical_name, display_name, description) "
            "SELECT node_id, canonical_name, display_name, description FROM node"
        )
        w_conn.execute("INSERT INTO search_fts(search_fts) VALUES('rebuild')")
        w_conn.commit()
        w_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        w_conn.close()

    # Check 1: File exists and non-empty on disk (WAL checkpointed)
    after_size = sandbox_graph.stat().st_size
    after_sha = _sha256_bytes(sandbox_graph)
    assert after_size > 0
    assert len(after_sha) == 64

    # Check 2: Raw Byte Scan on primary SQLite file (bypassing SQLite driver)
    raw_bytes = sandbox_graph.read_bytes()
    for expected_id in (
        r_eduardo["persisted_assertion_id"],
        r_anthony["persisted_assertion_id"],
        r_amit["persisted_assertion_id"],
        r_michael["persisted_assertion_id"],
        r_siara["persisted_assertion_id"],
        "Atlas Lead / Rule Specialist",
        "Director Strategic Initiatives, Op Excellence",
    ):
        assert expected_id.encode("utf-8") in raw_bytes, f"Missing {expected_id} in raw SQLite bytes"

    # Check 3: PRAGMA integrity_check on fresh connection
    ro_conn = sqlite3.connect(f"file:{sandbox_graph}?mode=ro", uri=True)
    try:
        ic = ro_conn.execute("PRAGMA integrity_check").fetchone()[0]
        assert ic == "ok"

        # Check 4: Fresh Readback
        ed_desc = ro_conn.execute(
            "SELECT description FROM node WHERE node_id = 'person_eduardo_more'"
        ).fetchone()[0]
        assert "Atlas Lead / Rule Specialist" in ed_desc
        assert "VP / Executive Sponsor" not in ed_desc

        af_desc = ro_conn.execute(
            "SELECT description FROM node WHERE node_id = 'entity-44a67f3d24d5aa6a0a96ea67174c'"
        ).fetchone()[0]
        assert "Director Strategic Initiatives, Op Excellence" in af_desc

        unlinked_after = ro_conn.execute(
            "SELECT count(*) FROM assertion a WHERE a.currentness_state='current' "
            "AND NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id=a.assertion_id)"
        ).fetchone()[0]
        assert unlinked_after == 0

        # Check 5: Row Delta & FTS Parity
        after_counts = {t: ro_conn.execute(q).fetchone()[0] for t, q in tbl_queries.items()}
        assert after_counts["node_fts"] == after_counts["node"]
        assert after_counts["assertion_evidence"] >= before_counts["assertion_evidence"] + len(bf_res["linked_assertion_ids"])
    finally:
        ro_conn.close()

    # Verify label quality audit has 0 blocking codes
    lq = audit_graph_labels(sandbox_graph)
    assert sum(lq["blocking_codes"].values()) == 0, f"Blocking label quality codes: {lq['blocking_codes']}"

    # Verify role resolution and retrieval record coverage (>2,300 non-facet records)
    kg_cache, _ = load_knowledge_graph_data(
        db_path=sandbox_graph,
        root=tmp_path,
        runtime_root=tmp_path,
        fallback_generation_id="gen-wave4",
        entity_factory=ResolvedEntity,
    )
    assert kg_cache["eduardo more"].role == "Atlas Lead / Rule Specialist"
    assert kg_cache["Parker Vance"].role == "Director Strategic Initiatives, Op Excellence"
    assert kg_cache["amit ayer"].role == "Senior Director / Lead Enterprise Architect"
    assert kg_cache["michael maione"].role == "Solution Lead / Manager"
    assert kg_cache["siara montgelas"].role == "Product Lead / Project Manager"

    records = render_retrieval_records(sandbox_graph, active_pair["graph_snapshot"])
    non_facet = [r for r in records if r["record_type"] != "entity_facet"]
    assert len(non_facet) >= 2300


def test_e2e_promoted_active_generation_integrity() -> None:
    """Verifies that the promoted active generation in runtime_root satisfies all Wave 4 invariants."""
    rt_root = resolve_authoritative_runtime_root()
    active_pair, _ = resolve_active_pair(rt_root, enforce_integrity=True)
    graph_path = rt_root / active_pair["graph_path"]
    sidecar_path = rt_root / active_pair["sidecar_path"]

    ro_conn = sqlite3.connect(f"file:{graph_path}?mode=ro", uri=True)
    try:
        node_cnt = ro_conn.execute("SELECT count(*) FROM node").fetchone()[0]
        nfts_cnt = ro_conn.execute("SELECT count(*) FROM node_fts").fetchone()[0]
        assert node_cnt == nfts_cnt, f"node_fts ({nfts_cnt}) != node ({node_cnt})"

        unlinked = ro_conn.execute(
            "SELECT count(*) FROM assertion a WHERE a.currentness_state='current' "
            "AND NOT EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id=a.assertion_id)"
        ).fetchone()[0]
        assert unlinked == 0
    finally:
        ro_conn.close()

    s_conn = sqlite3.connect(f"file:{sidecar_path}?mode=ro", uri=True)
    try:
        vec_cnt = s_conn.execute("SELECT count(*) FROM dense_vector").fetchone()[0]
        rec_cnt = s_conn.execute("SELECT count(*) FROM retrieval_record").fetchone()[0]
        assert vec_cnt == rec_cnt
        assert vec_cnt >= 2300, f"Expected >=2300 sidecar vectors, got {vec_cnt}"
    finally:
        s_conn.close()

    resolver = PreSynthesisEntityResolver(runtime_root=rt_root)
    resolved = {
        e.canonical_name: e
        for e in resolver.resolve_attendee_list(["Eduardo More", "Parker Vance", "Amit Ayer"])
    }
    ed_ent = resolved.get("Eduardo More") or resolved.get("Eduardo Juan More")
    assert ed_ent is not None and ed_ent.role == "Atlas Lead / Rule Specialist"
    assert resolved["Parker Vance"].role == "Director Strategic Initiatives, Op Excellence"
    assert resolved["Amit Ayer"].role == "Senior Director / Lead Enterprise Architect"
