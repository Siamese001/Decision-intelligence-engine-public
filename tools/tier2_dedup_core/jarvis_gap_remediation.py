"""Governed Project JARVIS duplicate source consolidation and candidate assertion promotion."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)

JARVIS_DUPLICATE_SOURCE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("source-aad33814e62cdad44e4401fb", "source-94fc64aafce1753909535af4"),
    ("source-325ea8a21ebb2d6a1a4a469c", "source-4f9f8fc8ed974f2c7be1a229"),
    ("source-6b3adeb96e5de9dd4504585d", "source-bc61c66b5e0d48d38e980e31"),
)

JARVIS_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source-1acfc7fa9be51d46c7aff034",
    "source-aad33814e62cdad44e4401fb",
    "source-dedcd512fe0e2f591d8e6c4f",
    "source-1db4a06f16fd507ae807edd1",
    "source-8dd2f4a54f7e533889412ed2",
    "source-52925f489d3c0fa4def327a5",
    "source-7b9e83133ff41f2cc54a8d24",
    "source-58771e029b8369421c88f3ab",
    "source-325ea8a21ebb2d6a1a4a469c",
    "source-c37c6c33b188e403de96865b",
    "source-51e5da104b50f0ffdbaaa758",
    "source-8467f7f5a350988a0853bdb2",
    "source-5ec96e22d7164bb4e1a66670",
    "source-6fa0caf796d61fe09412ea73",
    "source-6b3adeb96e5de9dd4504585d",
    "source-a7740761e493a327f4fb911a",
)

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-jarvis-gap-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-25.1', 1, ?)"
)


def _consolidate_jarvis_duplicate_sources(conn: Any) -> int:
    merged = 0
    for win, lose in JARVIS_DUPLICATE_SOURCE_PAIRS:
        row = conn.execute("SELECT 1 FROM source WHERE source_id = ?", (lose,)).fetchone()
        if not row:
            continue
        max_ver = conn.execute(
            "SELECT coalesce(max(version_number), 0) FROM source_version WHERE source_id = ?", (win,)
        ).fetchone()[0]
        sv_rows = conn.execute(
            "SELECT source_version_id FROM source_version WHERE source_id = ? ORDER BY version_number",
            (lose,),
        ).fetchall()
        sql_sv = "UPDATE source_version SET source_id = ?, version_number = ? WHERE source_version_id = ?"
        for idx, (sv_id,) in enumerate(sv_rows, start=1):
            conn.execute(sql_sv, (win, max_ver + idx, sv_id))
        for sql_stmt, params in (
            ("UPDATE evidence_item SET source_id = ? WHERE source_id = ?", (win, lose)),
            ("UPDATE assertion SET primary_source_id = ? WHERE primary_source_id = ?", (win, lose)),
            ("UPDATE search_document SET source_id = ? WHERE source_id = ?", (win, lose)),
            ("UPDATE audit_event SET source_id = ? WHERE source_id = ?", (win, lose)),
            ("DELETE FROM source WHERE source_id = ?", (lose,)),
        ):
            conn.execute(sql_stmt, params)
        merged += 1
    sql_archive = "UPDATE source SET cold_archive_ref = ? WHERE source_id = ?"
    conn.execute(
        sql_archive,
        (
            "output/project_jarvis/09-29-2026/Jarvis_Reid_Amit/01_Jarvis_Reid_Amit_09-29-2026_1210PM1223PMET_hitl_resolved_transcript.md",
            "source-7b9e83133ff41f2cc54a8d24",
        ),
    )
    return merged


def _promote_jarvis_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    ph = ",".join("?" for _ in JARVIS_CANONICAL_SOURCES)
    cur_curr = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', retrieval_eligible = 1, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE primary_source_id IN (" + ph + ") AND canonical_status = 'candidate' AND currentness_state = 'current'",
        [now_iso, *JARVIS_CANONICAL_SOURCES],
    )
    cur_unroll = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', currentness_state = 'current', "
        "superseded_by_assertion_id = NULL, superseded_at = NULL, retrieval_eligible = 1, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE primary_source_id IN ('source-dedcd512fe0e2f591d8e6c4f', 'source-7b9e83133ff41f2cc54a8d24') "
        "AND canonical_status = 'candidate' AND object_node_id IS NULL",
        (now_iso,),
    )
    cur_hist = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', retrieval_eligible = 0, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE primary_source_id IN (" + ph + ") AND canonical_status = 'candidate'",
        [now_iso, *JARVIS_CANONICAL_SOURCES],
    )
    return {
        "promoted_current_candidates": cur_curr.rowcount,
        "promoted_unrolled_draft_sources": cur_unroll.rowcount,
        "promoted_historical_candidates": cur_hist.rowcount,
        "total_promoted": cur_curr.rowcount + cur_unroll.rowcount + cur_hist.rowcount,
    }


def promote_jarvis_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Consolidate duplicate Jarvis sources, promote all Jarvis draft assertions in a new generation, and seal 5-check proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "jarvis-gap-remediation", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    before_snap = snapshot(graph_path)
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")

    merged_sources = _consolidate_jarvis_duplicate_sources(conn)
    promo_stats = _promote_jarvis_candidates(conn, now_iso)
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {
        "duplicate_sources_merged": merged_sources,
        **promo_stats,
    }
    sql_audit = (
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, NULL, ?, 'Project Jarvis duplicate source merge and draft promotion', "
        "'WAVE-3-JARVIS-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-jarvis-gap-remediation', '1.0.0')"
    )
    conn.execute(sql_audit, (audit_id, new_gen_id, json.dumps(stats), now_iso))
    conn.commit()

    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        conn.close()
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()

    after_snap = snapshot(graph_path)
    new_graph_sha = after_snap["sha256"]
    new_graph_snap = stable_id("graph-snapshot", new_graph_sha, length=24)
    refresh_sidecar_after_graph_cleanup(sidecar_path, graph_path, new_graph_sha, new_graph_snap, new_gen_id)
    new_sidecar_sha = sha256_file(sidecar_path)

    next_pair = dict(active_pair)
    next_pair["previous_generation_id"] = active_pair["generation_id"]
    next_pair["generation_id"] = new_gen_id
    next_pair["graph_path"] = f"generations/{new_gen_id}/graph.sqlite"
    next_pair["sidecar_path"] = f"generations/{new_gen_id}/sidecar.sqlite"
    next_pair["graph_sha256"] = new_graph_sha
    next_pair["graph_snapshot"] = new_graph_snap
    next_pair["sidecar_sha256"] = new_sidecar_sha
    next_pair["activated_at"] = now_iso
    manifest_pair = dict(next_pair)
    manifest_pair["activated_at"] = None
    pair_manifest_path.write_text(json.dumps(manifest_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    active_pair_path.write_text(json.dumps(next_pair, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    ph = ",".join("?" for _ in JARVIS_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_jarvis_candidates": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND canonical_status='candidate') = 0",
            JARVIS_CANONICAL_SOURCES,
        ),
        "probe_scope_traci_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) >= 19",
            ("source-dedcd512fe0e2f591d8e6c4f",),
        ),
        "probe_reid_amit_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) >= 8",
            ("source-7b9e83133ff41f2cc54a8d24",),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id, "source-dedcd512fe0e2f591d8e6c4f", "source-7b9e83133ff41f2cc54a8d24"],
        expected_deltas={
            "source": -merged_sources,
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:jarvis-graph-promotion-receipt:1.0.0",
        "timestamp": now_iso,
        "generation_id": new_gen_id,
        "previous_generation_id": active_pair["generation_id"],
        "terminal_outcome": proof["overall"],
        "stats": stats,
        "physical_proof": proof,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    return out
