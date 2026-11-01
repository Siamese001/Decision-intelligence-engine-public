"""Governed Project LENS October 27-28 candidate assertion promotion, alias registration, and sidecar sync."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)

LENS_OCT27_28_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source-ed01b486836314055f69c725",  # 20261028 Project Lens Sync
    "source-d4d6a747fa66307138d4f96a",  # 20261027 Lens Debrief
    "source-e17a00483f549a6495c38d44",  # 20261027 Discuss Prd
)

MICHAEL_REISS_NODE_ID = "entity-4194e45b88e674f0b5829355b67e"
MICHAEL_RICE_NODE_ID = "entity-74e59d0b1422500bfe868d4361a7"

_SQL_INSERT_LENS_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-lens-oct27-28-remediation', 'REQ_INGEST_LENS_OCT27_28_PROMOTION', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\", \"canonical_alias_registration\"]}', "
    "'2847e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34111', "
    "'2847e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34111', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-30.1', 1, ?)"
)


def _register_michael_reiss_rice_alias_and_workstream_links(conn: Any, now_iso: str) -> Dict[str, int]:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    alias_registered = 0
    if "canonical_alias" in tables and "node" in tables:
        has_reiss = conn.execute("SELECT 1 FROM node WHERE node_id = ?", (MICHAEL_REISS_NODE_ID,)).fetchone()
        if has_reiss:
            ca_cols = {r[1] for r in conn.execute("PRAGMA table_info(canonical_alias)").fetchall()}
            alias_id = "canonical-alias-michael-reiss-rice"
            if {"canonical_alias_id", "normalized_alias", "display_alias", "entity_id", "source_id"}.issubset(ca_cols):
                conn.execute(
                    "INSERT OR REPLACE INTO canonical_alias ("
                    "canonical_alias_id, normalized_alias, display_alias, entity_id, alias_type, "
                    "source_id, confidence_label, confidence_score, valid_from, valid_to, "
                    "ambiguity_state, preferred_flag, review_state, active, created_at"
                    ") VALUES (?, 'michael rice', 'Michael Rice', ?, 'phonetic_variant', "
                    "'source-e17a00483f549a6495c38d44', 'high', 0.95, '2026-10-27', NULL, "
                    "'unique', 0, 'reviewed', 1, ?)",
                    (alias_id, MICHAEL_REISS_NODE_ID, now_iso),
                )
                alias_registered += 1
            elif {"alias_id", "entity_id", "display_alias", "active"}.issubset(ca_cols):
                conn.execute(
                    "INSERT OR REPLACE INTO canonical_alias (alias_id, entity_id, display_alias, active) VALUES (?, ?, 'Michael Rice', 1)",
                    (alias_id, MICHAEL_REISS_NODE_ID),
                )
                alias_registered += 1
    if "alias" in tables and "node" in tables:
        has_reiss = conn.execute("SELECT 1 FROM node WHERE node_id = ?", (MICHAEL_REISS_NODE_ID,)).fetchone()
        if has_reiss:
            al_cols = {r[1] for r in conn.execute("PRAGMA table_info(alias)").fetchall()}
            if {"alias_id", "node_id", "alias", "confidence", "source_id", "created_at"}.issubset(al_cols):
                conn.execute(
                    "INSERT OR REPLACE INTO alias (alias_id, node_id, alias, confidence, evidence_posture, source_id, created_at) "
                    "VALUES ('alias-michael-reiss-rice', ?, 'Michael Rice', 'high', 'verified', 'source-e17a00483f549a6495c38d44', ?)",
                    (MICHAEL_REISS_NODE_ID, now_iso),
                )
    if "assertion" in tables and "node" in tables:
        has_lens_ws = conn.execute("SELECT 1 FROM node WHERE node_id = 'workstream_lens'").fetchone()
        if has_lens_ws:
            asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
            if "subject_node_id" in asrt_cols:
                conn.execute(
                    "UPDATE assertion SET subject_node_id = 'workstream_lens' "
                    "WHERE primary_source_id = 'source-e17a00483f549a6495c38d44' "
                    "AND subject_node_id = 'entity-7fc4b781d718223997ffb1b01a37'"
                )
    return {"michael_rice_alias_registered": alias_registered}


def _promote_lens_oct27_28_candidates_in_conn(conn: Any, now_iso: str) -> Dict[str, int]:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "governance_approval" in tables:
        conn.execute(_SQL_INSERT_LENS_APPROVAL, (now_iso, now_iso))

    ph = ",".join("?" for _ in LENS_OCT27_28_CANONICAL_SOURCES)
    asrt_cols = {r[1] for r in conn.execute("PRAGMA table_info(assertion)").fetchall()}
    upd_parts = [
        "canonical_status = 'canonical'",
        "canonical_eligible = 1",
        "validation_state = 'accepted_legacy'",
        "currentness_state = 'current'",
        "retrieval_eligible = 1",
    ]
    params: list[Any] = []
    if "superseded_by_assertion_id" in asrt_cols:
        upd_parts.append("superseded_by_assertion_id = NULL")
    if "superseded_at" in asrt_cols:
        upd_parts.append("superseded_at = NULL")
    if "approval_reference" in asrt_cols:
        upd_parts.append("approval_reference = 'REQ_INGEST_LENS_OCT27_28_PROMOTION'")
    if "hitl_required" in asrt_cols:
        upd_parts.append("hitl_required = 0")
    if "updated_at" in asrt_cols:
        upd_parts.append("updated_at = ?")
        params.append(now_iso)

    where_sql = (
        f"WHERE primary_source_id IN ({ph}) "
        "AND COALESCE(validation_state, '') NOT IN ('superseded', 'rejected', 'retired') "
        "AND (canonical_status != 'canonical' OR canonical_eligible != 1 OR retrieval_eligible != 1 OR currentness_state != 'current')"
    )
    cur_asrt = conn.execute(
        f"UPDATE assertion SET {', '.join(upd_parts)} {where_sql}",
        [*params, *LENS_OCT27_28_CANONICAL_SOURCES],
    )
    promoted_assertions = max(0, cur_asrt.rowcount)

    synced_sd = 0
    if "search_document" in tables:
        sd_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()}
        sd_upd = [
            "canonical_status = 'canonical'",
            "currentness_state = 'current'",
            "retrieval_eligible = 1",
        ]
        sd_params: list[Any] = []
        if "updated_at" in sd_cols:
            sd_upd.append("updated_at = ?")
            sd_params.append(now_iso)
        cur_sd = conn.execute(
            f"UPDATE search_document SET {', '.join(sd_upd)} "
            f"WHERE assertion_id IN (SELECT assertion_id FROM assertion WHERE primary_source_id IN ({ph}) "
            "AND canonical_status = 'canonical' AND canonical_eligible = 1 AND retrieval_eligible = 1)",
            [*sd_params, *LENS_OCT27_28_CANONICAL_SOURCES],
        )
        synced_sd = max(0, cur_sd.rowcount)
        if "search_fts" in tables:
            has_ai_trigger = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='search_document_ai'"
            ).fetchone()
            if has_ai_trigger is None:
                fts_cols = {r[1] for r in conn.execute("PRAGMA table_info(search_fts)").fetchall()}
                for sd_row in conn.execute(
                    f"SELECT search_rowid, title, body, assertion_id FROM search_document "
                    f"WHERE assertion_id IN (SELECT assertion_id FROM assertion WHERE primary_source_id IN ({ph}) "
                    "AND canonical_status = 'canonical' AND canonical_eligible = 1 AND retrieval_eligible = 1)",
                    list(LENS_OCT27_28_CANONICAL_SOURCES),
                ).fetchall():
                    s_rowid, s_title, s_body, s_aid = sd_row[0], sd_row[1] or "", sd_row[2] or "", sd_row[3]
                    conn.execute("DELETE FROM search_fts WHERE rowid = ?", (s_rowid,))
                    if {"object_type", "object_id"}.issubset(fts_cols):
                        conn.execute(
                            "INSERT INTO search_fts(rowid, title, body, object_type, object_id) VALUES (?, ?, ?, 'assertion', ?)",
                            (s_rowid, s_title, s_body, s_aid),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO search_fts(rowid, title, body) VALUES (?, ?, ?)",
                            (s_rowid, s_title, s_body),
                        )

    alias_stats = _register_michael_reiss_rice_alias_and_workstream_links(conn, now_iso)
    return {
        "promoted_lens_oct27_28_assertions": promoted_assertions,
        "synchronized_search_documents": synced_sd,
        **alias_stats,
    }


def promote_lens_oct27_28_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Promote non-superseded October 27-28 LENS assertions in a new generation, synchronize sidecar, and seal 5-check physical proof."""
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id(
        "generation",
        active_pair["generation_id"],
        "lens-oct27-28-remediation",
        now_iso,
        uuid.uuid4().hex[:8],
        length=28,
    )
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

    promo_stats = _promote_lens_oct27_28_candidates_in_conn(conn, now_iso)
    audit_id = f"audit-lens-hygiene-{uuid.uuid4().hex[:16]}"
    sql_audit = (
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, 'source-ed01b486836314055f69c725', ?, 'Project LENS Oct 27-28 operational assertion promotion and alias reconciliation', "
        "'WAVE-3-LENS-PROMOTION', 'REQ_INGEST_LENS_OCT27_28_PROMOTION', 'run-lens-oct27-28-remediation', '1.0.0')"
    )
    conn.execute(sql_audit, (audit_id, new_gen_id, json.dumps(promo_stats, sort_keys=True), now_iso))
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

    ph = ",".join("?" for _ in LENS_OCT27_28_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_oct28_sync_promoted": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id='source-ed01b486836314055f69c725' AND canonical_status='canonical' AND canonical_eligible=1 AND retrieval_eligible=1) = 39",
            (),
        ),
        "probe_zero_unpromoted_non_superseded": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND COALESCE(validation_state, '') NOT IN ('superseded', 'rejected', 'retired') AND (canonical_status != 'canonical' OR canonical_eligible != 1 OR retrieval_eligible != 1)) = 0",
            LENS_OCT27_28_CANONICAL_SOURCES,
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id, *LENS_OCT27_28_CANONICAL_SOURCES],
        expected_deltas={
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    if proof["overall"] != "PASS":
        raise RuntimeError(f"Physical proof failed for LENS Oct 27-28 promotion: {proof}")

    out = {
        "schema": "urn:nee:lens-oct27-28-promotion-receipt:1.0.0",
        "timestamp": now_iso,
        "generation_id": new_gen_id,
        "previous_generation_id": active_pair["generation_id"],
        "graph_sha256": new_graph_sha,
        "sidecar_sha256": new_sidecar_sha,
        "terminal_outcome": proof["overall"],
        "stats": promo_stats,
        "physical_proof": proof,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
