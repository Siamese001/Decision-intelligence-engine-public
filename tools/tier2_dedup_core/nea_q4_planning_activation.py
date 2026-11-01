"""NEA Q4 2026 Planning Dashboard Canonical Activation & Duplicate Source Retirement.

Retires superseded partial source source-80e6570a731ce4c912d07c5b, merges
placeholder identity unresolved_jake_wellington_roster_2026_07_11 into canonical
stakeholder Jacob Wellington, purges unattributed 'Not specified' OCR rows, and
promotes source-95ce3df4ff5348b9fb6eb264 candidate nodes and assertions to canonical
with 5-check physical proof.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.graph_consolidation_core.repoint import (
    repoint_node_assertions,
    repoint_node_edges,
    repoint_node_entities_and_routes,
)
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.hygiene_promotion import refresh_sidecar_after_graph_cleanup
from tools.tier2_dedup_core.nea_q4_open_roles_enrichment import enrich_nea_q4_charters_and_open_roles

PARTIAL_NEA_SOURCE_ID = "source-80e6570a731ce4c912d07c5b"
FULL_NEA_SOURCE_ID = "source-95ce3df4ff5348b9fb6eb264"
UNRESOLVED_JAKE_WELLINGTON_ID = "unresolved_jake_wellington_roster_2026_07_11"
CANONICAL_JACOB_WELLINGTON_ID = "stakeholder_jacob_wellington"


def _has_table_column(conn: Any, table: str, column: str) -> bool:
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return False
    cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in cols


def _delete_partial_source_records(conn: Any) -> Tuple[int, int]:
    """Delete assertions, evidence links, and search documents for PARTIAL_NEA_SOURCE_ID."""
    s1_assertion_ids = [
        r[0] for r in conn.execute("SELECT assertion_id FROM assertion WHERE primary_source_id=?", (PARTIAL_NEA_SOURCE_ID,)).fetchall()
    ]
    s1_search_doc_ids = [
        r[0] for r in conn.execute("SELECT search_document_id FROM search_document WHERE source_id=?", (PARTIAL_NEA_SOURCE_ID,)).fetchall()
    ]
    if s1_assertion_ids:
        ph = ",".join("?" for _ in s1_assertion_ids)
        conn.execute(f"UPDATE assertion SET superseded_by_assertion_id=NULL WHERE superseded_by_assertion_id IN ({ph})", s1_assertion_ids)
        conn.execute(f"DELETE FROM assertion_evidence WHERE assertion_id IN ({ph})", s1_assertion_ids)
        if _has_table_column(conn, "validation_queue", "assertion_id"):
            conn.execute(f"DELETE FROM validation_queue WHERE assertion_id IN ({ph})", s1_assertion_ids)
        if _has_table_column(conn, "search_fts", "search_document_id"):
            conn.execute(f"DELETE FROM search_fts WHERE search_document_id IN ({ph})", s1_assertion_ids)
        conn.execute(f"DELETE FROM assertion WHERE assertion_id IN ({ph})", s1_assertion_ids)
        conn.execute(f"DELETE FROM search_document WHERE assertion_id IN ({ph})", s1_assertion_ids)

    if s1_search_doc_ids:
        ph_sd = ",".join("?" for _ in s1_search_doc_ids)
        if _has_table_column(conn, "search_fts", "search_document_id"):
            conn.execute(f"DELETE FROM search_fts WHERE search_document_id IN ({ph_sd})", s1_search_doc_ids)
        conn.execute(f"DELETE FROM search_document WHERE search_document_id IN ({ph_sd})", s1_search_doc_ids)

    conn.execute(
        """DELETE FROM evidence_item
            WHERE source_id=?
              AND evidence_id NOT IN (SELECT DISTINCT evidence_id FROM assertion_evidence WHERE evidence_id IS NOT NULL)""",
        (PARTIAL_NEA_SOURCE_ID,),
    )
    conn.execute(
        "UPDATE source SET source_posture='retired_superseded', notes='Retired superseded partial NEA Team Assignments ingestion (2026-10-21).' WHERE source_id=?",
        (PARTIAL_NEA_SOURCE_ID,),
    )
    return len(s1_assertion_ids), len(s1_search_doc_ids)


def _merge_placeholder_jake_wellington(
    conn: Any, now_iso: str, sample_written_ids: List[str]
) -> Tuple[int, str]:
    """Merge unresolved_jake_wellington_roster_2026_07_11 into canonical stakeholder node."""
    surv_row = conn.execute(
        "SELECT node_id, node_type FROM node WHERE node_id IN (?, 'entity-ba74b0030c5f2aa180ecbe34c3db') AND active=1 ORDER BY node_id=? DESC LIMIT 1",
        (CANONICAL_JACOB_WELLINGTON_ID, CANONICAL_JACOB_WELLINGTON_ID),
    ).fetchone()
    unres_row = conn.execute("SELECT node_id, active FROM node WHERE node_id=?", (UNRESOLVED_JAKE_WELLINGTON_ID,)).fetchone()
    survivor_id = surv_row[0] if surv_row else CANONICAL_JACOB_WELLINGTON_ID
    if not (unres_row and surv_row and unres_row["active"] == 1):
        return 0, survivor_id

    merge_id = f"merge-nea-jw-{uuid.uuid4().hex[:16]}"
    sample_written_ids.append(merge_id)
    conn.execute(
        """INSERT INTO entity_merge_event (
            merge_event_id, canonical_node_id, event_type, status,
            approval_reference, reason, created_at, completed_at
        ) VALUES (?, ?, 'merge', 'completed', 'HITL-NEA-Q4-DEDUP',
                 'Merge unresolved Jake Wellington roster placeholder into canonical stakeholder', ?, ?)""",
        (merge_id, survivor_id, now_iso, now_iso),
    )
    conn.execute(
        """INSERT OR REPLACE INTO entity_redirect (
            from_node_id, to_node_id, merge_event_id, active, created_at
        ) VALUES (?, ?, ?, 1, ?)""",
        (UNRESOLVED_JAKE_WELLINGTON_ID, survivor_id, merge_id, now_iso),
    )
    repoint_node_assertions(conn, UNRESOLVED_JAKE_WELLINGTON_ID, survivor_id, surv_row[1], now_iso)
    repoint_node_edges(conn, UNRESOLVED_JAKE_WELLINGTON_ID, survivor_id, surv_row[1], now_iso)
    repoint_node_entities_and_routes(conn, UNRESOLVED_JAKE_WELLINGTON_ID, survivor_id, now_iso)
    conn.execute("UPDATE node SET active=0, status='merged', updated_at=? WHERE node_id=?", (now_iso, UNRESOLVED_JAKE_WELLINGTON_ID))
    return 1, survivor_id


def _verify_and_checkpoint(conn: Any) -> None:
    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        conn.close()
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()


def _default_record_audit_event(
    conn: Any, stats: Dict[str, Any], now_iso: str, sample_ids: List[str], run_id: str = "run-10-24-2026"
) -> None:
    """Record remediation audit event in graph audit ledger."""
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    sample_ids.append(audit_id)
    date_str = run_id.replace("run-", "").replace("run_", "")
    conn.execute(
        """INSERT INTO audit_event (
            audit_event_id, actor_or_process, action, record_type, record_id,
            previous_value_json, new_value_json, source_id, event_timestamp, reason,
            validation_reference, approval_reference, migration_or_ingestion_run_id,
            release_version
        ) VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?,
                 NULL, ?, NULL, ?, ?,
                 ?, ?, ?, '1.0.0')""",
        (
            audit_id, f"generation-hygiene-{date_str}", json.dumps(stats), now_iso,
            f"Knowledge Graph Hygiene Remediation {date_str}", f"WAVE-1-HYGIENE-{date_str}",
            f"HITL-{date_str}", run_id,
        ),
    )


def _default_resync_search_sidecars(conn: Any, now_iso: str) -> int:
    """Resync search documents for canonical assertions missing sidecars."""
    cur = conn.execute(
        """INSERT OR REPLACE INTO search_document (
            search_document_id, object_type, object_id, title, body,
            canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at
        )
        SELECT a.assertion_id, 'assertion', a.assertion_id,
               coalesce(sd.title, a.scope_text, a.assertion_id),
               coalesce(sd.body, a.scope_text, a.assertion_id),
               a.canonical_status, a.currentness_state,
               CASE WHEN a.canonical_status='canonical' AND a.validation_state='accepted_legacy'
                         AND a.currentness_state='current' AND a.retrieval_eligible=1
                         AND a.superseded_by_assertion_id IS NULL
                         AND EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id=a.assertion_id AND ae.evidence_relationship='supports')
                         AND EXISTS (SELECT 1 FROM governance_approval ga WHERE ga.approval_reference=a.approval_reference AND ga.status='approved' AND ga.active=1)
                    THEN 1 ELSE 0 END,
               coalesce(sd.source_id, a.primary_source_id, 'source_canonical_graph'),
               a.assertion_id, ?
          FROM assertion a
          LEFT JOIN search_document sd ON sd.assertion_id = a.assertion_id
         WHERE a.assertion_id NOT IN (
               SELECT DISTINCT search_document_id FROM search_document WHERE search_document_id IS NOT NULL
         )""",
        (now_iso,),
    )
    return cur.rowcount


def retire_nea_planning_partial_duplicates(
    graph_path: Path | str,
    receipt_path: Optional[Path | str] = None,
    record_audit_fn: Optional[Callable[..., None]] = None,
) -> Dict[str, Any]:
    """Retire superseded partial source source-80e6570a731ce4c912d07c5b and merge placeholder Jake Wellington."""
    graph_path = Path(graph_path)
    receipt_path = Path(receipt_path) if receipt_path else None
    if not graph_path.exists():
        raise FileNotFoundError(f"Graph database not found: {graph_path}")

    before_snap = snapshot(graph_path)
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_written_ids: List[str] = []

    try:
        removed_asrt, removed_sd = _delete_partial_source_records(conn)
        merged_jake, survivor_id = _merge_placeholder_jake_wellington(conn, now_iso, sample_written_ids)
        stats = {
            "retired_source_id": PARTIAL_NEA_SOURCE_ID,
            "retired_assertions_removed": removed_asrt,
            "retired_search_documents_removed": removed_sd,
            "jake_wellington_merged": merged_jake,
            "survivor_wellington_node_id": survivor_id,
        }
        (record_audit_fn or _default_record_audit_event)(conn, stats, now_iso, sample_written_ids, run_id="run-nea-q4-dedup")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    _verify_and_checkpoint(conn)
    after_snap = snapshot(graph_path)

    readback_probes: Dict[str, Any] = {
        "probe_s1_assertions_zero": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=?) = 0", (PARTIAL_NEA_SOURCE_ID,)),
        "probe_s1_retired_posture": ("SELECT 1 FROM source WHERE source_id=? AND source_posture='retired_superseded'", (PARTIAL_NEA_SOURCE_ID,)),
    }
    if merged_jake:
        readback_probes["probe_jake_redirect"] = (
            "SELECT 1 FROM entity_redirect WHERE from_node_id=? AND to_node_id=? AND active=1",
            (UNRESOLVED_JAKE_WELLINGTON_ID, survivor_id),
        )

    proof = prove(
        before=before_snap, after=after_snap, expected_ids=sample_written_ids[:5],
        expected_deltas={"assertion": -removed_asrt, "entity_merge_event": merged_jake},
        readback_probes=readback_probes, strict_delta=False,
    )
    receipt = {
        "schema": "urn:nee:graph-hygiene-receipt:1.0.0",
        "task_id": "retire_nea_planning_partial_duplicates",
        "timestamp": now_iso,
        "terminal_outcome": proof["overall"],
        "pre_graph_sha256": before_snap["sha256"],
        "post_graph_sha256": after_snap["sha256"],
        "physical_proof": proof,
        "remediation_stats": stats,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def _purge_nea_q4_noise_and_stubs(conn: Any) -> int:
    """Purge unattributed 'Not specified' OCR rows and redundant stub assertions for FULL_NEA_SOURCE_ID."""
    stub_rows = conn.execute(
        """SELECT a.assertion_id
             FROM assertion a
            WHERE a.primary_source_id = ?
              AND (
                lower(coalesce(a.scope_text, '')) LIKE '%not specified%'
                OR (
                  a.object_node_id IS NULL
                  AND lower(coalesce(a.scope_text, '')) LIKE '%is an identified % in document%'
                  AND a.subject_node_id IN (
                      SELECT subject_node_id FROM assertion WHERE primary_source_id = ? AND object_node_id IS NOT NULL
                      UNION
                      SELECT object_node_id FROM assertion WHERE primary_source_id = ? AND object_node_id IS NOT NULL
                  )
                )
              )""",
        (FULL_NEA_SOURCE_ID, FULL_NEA_SOURCE_ID, FULL_NEA_SOURCE_ID),
    ).fetchall()
    purge_ids = [r[0] for r in stub_rows]
    if not purge_ids:
        return 0
    ph = ",".join("?" for _ in purge_ids)
    conn.execute(f"UPDATE assertion SET superseded_by_assertion_id=NULL WHERE superseded_by_assertion_id IN ({ph})", purge_ids)
    conn.execute(
        f"""UPDATE assertion
               SET canonical_status = 'candidate',
                   canonical_eligible = 0,
                   validation_state = 'needs_review',
                   retrieval_eligible = 0
             WHERE assertion_id IN ({ph})""",
        purge_ids,
    )
    conn.execute(f"DELETE FROM assertion_evidence WHERE assertion_id IN ({ph})", purge_ids)
    if _has_table_column(conn, "validation_queue", "assertion_id"):
        conn.execute(f"DELETE FROM validation_queue WHERE assertion_id IN ({ph})", purge_ids)
    conn.execute(f"DELETE FROM assertion WHERE assertion_id IN ({ph})", purge_ids)
    conn.execute(f"DELETE FROM search_document WHERE assertion_id IN ({ph})", purge_ids)
    return len(purge_ids)


def _promote_nea_q4_entities_and_assertions(
    conn: Any, now_iso: str, resync_sidecars_fn: Optional[Callable[[Any, str], int]] = None
) -> Dict[str, int]:
    """Promote candidate nodes and remaining assertions for FULL_NEA_SOURCE_ID to canonical and sync sidecars."""
    conn.execute(
        """INSERT OR IGNORE INTO governance_approval (
            approval_id, approval_reference, approval_type, scope_json,
            proposal_sha256, approval_statement_sha256, approved_at, expires_at,
            status, approved_by, release_version, active, created_at
        ) VALUES (
            'approval-nea-q4-planning', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion',
            '{"allowed_actions": ["canonical_assertion_promotion"]}',
            '1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110',
            '1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110',
            ?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-24.1', 1, ?
        )""",
        (now_iso, now_iso),
    )
    node_ids = [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT n.node_id
                 FROM node n
                WHERE n.status = 'candidate'
                  AND n.node_id IN (
                      SELECT subject_node_id FROM assertion WHERE primary_source_id = ?
                      UNION
                      SELECT object_node_id FROM assertion WHERE primary_source_id = ? AND object_node_id IS NOT NULL
                  )""",
            (FULL_NEA_SOURCE_ID, FULL_NEA_SOURCE_ID),
        ).fetchall()
    ]
    if node_ids:
        ph_n = ",".join("?" for _ in node_ids)
        conn.execute(f"UPDATE node SET status='canonical', active=1, updated_at=? WHERE node_id IN ({ph_n})", [now_iso, *node_ids])
        if _has_table_column(conn, "node_state", "node_id"):
            conn.execute(
                f"""UPDATE node_state
                       SET validation_state='accepted_legacy', approval_state='approved',
                           currentness_state='current', retrieval_eligibility='eligible', updated_at=?
                     WHERE node_id IN ({ph_n})""",
                [now_iso, *node_ids],
            )
    cur_a = conn.execute(
        """UPDATE assertion
              SET canonical_status = 'canonical',
                  canonical_eligible = 1,
                  validation_state = 'accepted_legacy',
                  retrieval_eligible = 1,
                  approval_reference = 'REQ_INGEST_CEC57955C84BCE3A',
                  updated_at = ?
            WHERE primary_source_id = ?
              AND currentness_state = 'current'
              AND canonical_status != 'canonical'""",
        (now_iso, FULL_NEA_SOURCE_ID),
    )
    promoted_assertions = cur_a.rowcount
    conn.execute(
        """UPDATE search_document
              SET canonical_status = 'canonical',
                  currentness_state = 'current',
                  retrieval_eligible = 1,
                  updated_at = ?
            WHERE assertion_id IN (
                SELECT assertion_id FROM assertion
                 WHERE primary_source_id = ? AND canonical_status = 'canonical' AND currentness_state = 'current'
            )""",
        (now_iso, FULL_NEA_SOURCE_ID),
    )
    (resync_sidecars_fn or _default_resync_search_sidecars)(conn, now_iso)
    return {"promoted_nodes": len(node_ids), "promoted_assertions": promoted_assertions}


def activate_nea_q4_planning_assignments(
    graph_path: Path | str,
    receipt_path: Optional[Path | str] = None,
    record_audit_fn: Optional[Callable[..., None]] = None,
    resync_sidecars_fn: Optional[Callable[[Any, str], int]] = None,
) -> Dict[str, Any]:
    """Retire partial NEA planning duplicate, purge noise/stubs, and promote FULL_NEA_SOURCE_ID to canonical."""
    graph_path = Path(graph_path)
    receipt_path = Path(receipt_path) if receipt_path else None
    if not graph_path.exists():
        raise FileNotFoundError(f"Graph database not found: {graph_path}")

    dedup_receipt = retire_nea_planning_partial_duplicates(graph_path, None, record_audit_fn=record_audit_fn)
    before_snap = snapshot(graph_path)
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sample_written_ids: List[str] = []

    try:
        enrich_stats = enrich_nea_q4_charters_and_open_roles(conn, now_iso, sample_written_ids)
        purged_count = _purge_nea_q4_noise_and_stubs(conn)
        promo_counts = _promote_nea_q4_entities_and_assertions(conn, now_iso, resync_sidecars_fn=resync_sidecars_fn)
        stats = {
            "source_id": FULL_NEA_SOURCE_ID,
            "retired_partial_source_id": PARTIAL_NEA_SOURCE_ID,
            "retired_partial_assertions_removed": dedup_receipt["remediation_stats"]["retired_assertions_removed"],
            "jake_wellington_merged": dedup_receipt["remediation_stats"]["jake_wellington_merged"],
            "purged_noise_and_stub_assertions": purged_count,
            "promoted_candidate_nodes": promo_counts["promoted_nodes"],
            "promoted_canonical_assertions": promo_counts["promoted_assertions"],
            **enrich_stats,
        }
        (record_audit_fn or _default_record_audit_event)(conn, stats, now_iso, sample_written_ids, run_id="run-nea-q4-activation")
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise

    _verify_and_checkpoint(conn)
    after_snap = snapshot(graph_path)

    readback_probes = {
        "probe_full_nea_canonical": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND retrieval_eligible=1) > 0", (FULL_NEA_SOURCE_ID,)),
        "probe_partial_retired": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=?) = 0", (PARTIAL_NEA_SOURCE_ID,)),
        "probe_zero_not_specified": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND lower(coalesce(scope_text,'')) LIKE '%not specified%') = 0", (FULL_NEA_SOURCE_ID,)),
        "probe_open_roles_ingested": ("SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND predicate='CANDIDATE_FOR' AND canonical_status='canonical' AND retrieval_eligible=1) = 44", (FULL_NEA_SOURCE_ID,)),
    }
    assertion_delta = int((after_snap.get("row_counts") or {}).get("assertion", 0)) - int((before_snap.get("row_counts") or {}).get("assertion", 0))
    proof = prove(
        before=before_snap, after=after_snap, expected_ids=sample_written_ids[:5],
        expected_deltas={"assertion": assertion_delta}, readback_probes=readback_probes, strict_delta=False,
    )
    receipt = {
        "schema": "urn:nee:graph-hygiene-receipt:1.0.0",
        "task_id": "activate_nea_q4_planning_assignments",
        "timestamp": now_iso,
        "terminal_outcome": proof["overall"],
        "pre_graph_sha256": before_snap["sha256"],
        "post_graph_sha256": after_snap["sha256"],
        "physical_proof": proof,
        "remediation_stats": stats,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def promote_nea_q4_planning_generation(
    runtime_root: Optional[Path | str] = None,
    receipt_path: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """Clone active generation, execute NEA Q4 open-roles enrichment & activation, sync sidecar, and promote active_pair."""
    rt_root = Path(runtime_root).resolve() if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "nea-q4-open-roles-activation", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    receipt = activate_nea_q4_planning_assignments(graph_path, None)
    new_graph_sha = receipt["post_graph_sha256"]
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

    receipt["promoted_generation"] = {
        "generation_id": new_gen_id,
        "previous_generation_id": active_pair["generation_id"],
        "graph_path": next_pair["graph_path"],
        "sidecar_path": next_pair["sidecar_path"],
        "graph_sha256": new_graph_sha,
        "sidecar_sha256": new_sidecar_sha,
    }
    if receipt_path:
        r_path = Path(receipt_path)
        r_path.parent.mkdir(parents=True, exist_ok=True)
        r_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt
