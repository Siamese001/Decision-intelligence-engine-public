"""Governed retirement of duplicate/draft ENTERPRISE4U SOW sources and sidecar sync."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.core import ROOT, digest, sha256_file, stable_id
from dual_pipeline.index_core.records import (
    render_retrieval_record_object_facets,
    render_retrieval_records,
)
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.sidecar_vector_recovery import (
    _ensure_missing_dense_vectors,
    repair_falsely_historical_merged_assertions,
)

ENTERPRISE4U_SOW_DUPLICATE_SOURCES: Tuple[str, ...] = (
    "source-b30ffba35403e3c572331788",
    "source-db3854c94736a40c6c5370f0",
    "source-48924d5f3d6942ae8abc56d8",
    "source-58153ab17945a980ec134c3b",
    "source-80a1214d074015b01c0454f7",
    "source-f1d1b78a52e0217ca4c35950",
)

__all__ = [
    "ENTERPRISE4U_SOW_DUPLICATE_SOURCES",
    "refresh_sidecar_after_graph_cleanup",
    "repair_falsely_historical_merged_assertions",
    "retire_enterprise4u_sow_duplicates",
]


def _compute_sidecar_metadata_updates(
    kept_records: List[Dict[str, Any]],
    kept_facets: List[Dict[str, Any]],
    graph_sha: str,
    graph_snap: str,
    generation_id: Optional[str],
) -> Dict[str, str]:
    lane_counts = Counter(r["lane"] for r in kept_records)
    manifest_rows = [
        {"record_id": r["record_id"], "lane": r["lane"], "retrieval_text_hash": r["retrieval_text_hash"]}
        for r in kept_records
    ]
    meta_rows = sorted(
        [
            {
                "record_id": r["record_id"],
                "retrieval_kind": r["retrieval_kind"],
                "epistemic_status": r["epistemic_status"],
                "provenance_kind": r["provenance_kind"],
                "answer_use": r["answer_use"],
            }
            for r in kept_records
        ],
        key=lambda x: x["record_id"],
    )
    updates = {
        "graph_sha256": graph_sha,
        "bound_graph_sha256": graph_sha,
        "graph_snapshot": graph_snap,
        "record_count": str(len(kept_records)),
        "approved_record_count": str(lane_counts.get("approved", 0)),
        "candidate_record_count": str(lane_counts.get("candidate", 0)),
        "external_public_record_count": str(lane_counts.get("external_public", 0)),
        "retrieval_text_manifest_digest": digest(manifest_rows),
        "retrieval_metadata_manifest_digest": digest(meta_rows),
        "object_facet_assignment_count": str(len(kept_facets)),
        "object_facet_manifest_digest": digest(kept_facets),
    }
    if generation_id:
        updates["generation_id"] = generation_id
    return updates


def refresh_sidecar_after_graph_cleanup(
    sidecar_path: Path, graph_path: Path, graph_sha: str, graph_snap: str, generation_id: Optional[str] = None
) -> None:
    """Prune stale sidecar records, insert missing records, and recompute sidecar metadata digests."""
    records = render_retrieval_records(graph_path, graph_snap)
    facet_rows = render_retrieval_record_object_facets(graph_path, records)
    valid_rids = {r["record_id"] for r in records}

    sconn = GovernedStorageContext.connect(sidecar_path, readonly=False, timeout=30.0)
    sconn.row_factory = Row
    sconn.execute("PRAGMA foreign_keys = ON;")
    sconn.execute("BEGIN IMMEDIATE;")
    existing_rows = sconn.execute("SELECT record_id, retrieval_text_hash FROM retrieval_record").fetchall()
    existing_rids = {r[0] for r in existing_rows}
    existing_hashes = {r[0]: r[1] for r in existing_rows}
    stale_rids = sorted(existing_rids - valid_rids)
    if stale_rids:
        ph = ",".join("?" for _ in stale_rids)
        for tbl in ("dense_vector", "sparse_weight", "colbert_token", "retrieval_record_object_facet", "retrieval_record"):
            sconn.execute("DELETE FROM " + tbl + " WHERE record_id IN (" + ph + ")", stale_rids)

    sql_upsert_rec = (
        "INSERT INTO retrieval_record "
        "(record_id, lane, record_type, namespace, retrieval_text_hash, "
        "retrieval_kind, epistemic_status, provenance_kind, answer_use, artifact_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(record_id) DO UPDATE SET "
        "lane=excluded.lane, record_type=excluded.record_type, namespace=excluded.namespace, "
        "retrieval_text_hash=excluded.retrieval_text_hash, retrieval_kind=excluded.retrieval_kind, "
        "epistemic_status=excluded.epistemic_status, provenance_kind=excluded.provenance_kind, "
        "answer_use=excluded.answer_use, artifact_json=excluded.artifact_json"
    )
    modified_rids: List[str] = []
    for r in records:
        if r["record_id"] in existing_hashes and existing_hashes[r["record_id"]] != r["retrieval_text_hash"]:
            modified_rids.append(r["record_id"])
        sconn.execute(
            sql_upsert_rec,
            (
                r["record_id"], r["lane"], r["record_type"], r["namespace"], r["retrieval_text_hash"],
                r["retrieval_kind"], r["epistemic_status"], r["provenance_kind"],
                r["answer_use"], json.dumps(r, ensure_ascii=False, sort_keys=True),
            ),
        )
    _ensure_missing_dense_vectors(sconn, sorted(valid_rids - existing_rids), modified_rids=modified_rids)

    sconn.execute("DELETE FROM retrieval_record_object_facet")
    sql_ins_facets = (
        "INSERT INTO retrieval_record_object_facet "
        "(record_id,assignment_id,object_type,object_id,vocabulary,value,assignment_method,review_state) "
        "VALUES (?,?,?,?,?,?,?,?)"
    )
    sconn.executemany(
        sql_ins_facets,
        [
            (
                row["record_id"], row["assignment_id"], row["object_type"], row["object_id"],
                row["vocabulary"], row["value"], row["assignment_method"], row["review_state"],
            )
            for row in facet_rows
            if row["record_id"] in valid_rids
        ],
    )
    kept_facets = [
        dict(r)
        for r in sconn.execute(
            """SELECT record_id,assignment_id,object_type,object_id,
                      vocabulary,value,assignment_method,review_state
                 FROM retrieval_record_object_facet
                ORDER BY record_id,assignment_id"""
        ).fetchall()
    ]
    updates = _compute_sidecar_metadata_updates(list(records), kept_facets, graph_sha, graph_snap, generation_id)
    for k, v in updates.items():
        sconn.execute("INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)", (k, v))
    sconn.commit()
    sconn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    sconn.close()


def _delete_sow_assertions_and_nodes(
    conn: Any, src_ph: str, asrt_ids: List[str], excl_nodes: List[str]
) -> None:
    if asrt_ids:
        aph = ",".join("?" for _ in asrt_ids)
        for sql, params in (
            ("UPDATE assertion SET superseded_by_assertion_id = NULL WHERE superseded_by_assertion_id IN (" + aph + ")", asrt_ids),
            ("UPDATE assertion SET canonical_status = 'candidate', validation_state = 'unreviewed', retrieval_eligible = 0 WHERE assertion_id IN (" + aph + ")", asrt_ids),
            ("DELETE FROM assertion_conflict WHERE assertion_id_1 IN (" + aph + ") OR assertion_id_2 IN (" + aph + ")", [*asrt_ids, *asrt_ids]),
            ("DELETE FROM legacy_claim_assertion_map WHERE assertion_id IN (" + aph + ")", asrt_ids),
            ("DELETE FROM legacy_edge_assertion_map WHERE assertion_id IN (" + aph + ")", asrt_ids),
            ("DELETE FROM assertion_evidence WHERE assertion_id IN (" + aph + ")", asrt_ids),
            ("DELETE FROM assertion WHERE assertion_id IN (" + aph + ")", asrt_ids),
            ("DELETE FROM search_document WHERE assertion_id IN (" + aph + ") OR object_id IN (" + aph + ")", [*asrt_ids, *asrt_ids]),
        ):
            conn.execute(sql, params)

    if excl_nodes:
        nph = ",".join("?" for _ in excl_nodes)
        for sql, params in (
            ("UPDATE assertion SET canonical_status = 'candidate', validation_state = 'unreviewed', retrieval_eligible = 0 WHERE subject_node_id IN (" + nph + ") OR object_node_id IN (" + nph + ")", [*excl_nodes, *excl_nodes]),
            ("DELETE FROM assertion WHERE subject_node_id IN (" + nph + ") OR object_node_id IN (" + nph + ")", [*excl_nodes, *excl_nodes]),
            ("DELETE FROM edge WHERE from_node_id IN (" + nph + ") OR to_node_id IN (" + nph + ")", [*excl_nodes, *excl_nodes]),
            ("DELETE FROM claim WHERE subject_node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM alias WHERE node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM canonical_alias WHERE entity_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM canonical_route WHERE object_type='node' AND object_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM node_card_map WHERE node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM validation_queue WHERE node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM entity_resolution_candidate_member WHERE node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM entity_redirect WHERE from_node_id IN (" + nph + ") OR to_node_id IN (" + nph + ")", [*excl_nodes, *excl_nodes]),
            ("DELETE FROM entity_merge_event WHERE canonical_node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM candidate_entity WHERE matched_node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM node_state WHERE node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM node WHERE node_id IN (" + nph + ")", excl_nodes),
            ("DELETE FROM search_document WHERE object_type='node' AND object_id IN (" + nph + ")", excl_nodes),
        ):
            conn.execute(sql, params)

    srcs = list(ENTERPRISE4U_SOW_DUPLICATE_SOURCES)
    for sql in (
        "DELETE FROM search_document WHERE source_id IN (" + src_ph + ")",
        "DELETE FROM assertion_evidence WHERE evidence_id IN (SELECT evidence_id FROM evidence_item WHERE source_id IN (" + src_ph + "))",
        "DELETE FROM evidence_item WHERE source_id IN (" + src_ph + ")",
        "DELETE FROM canonical_alias WHERE source_id IN (" + src_ph + ")",
        "DELETE FROM alias WHERE source_id IN (" + src_ph + ")",
        "DELETE FROM ingestion_source WHERE source_id IN (" + src_ph + ")",
        "UPDATE audit_event SET source_id = NULL WHERE source_id IN (" + src_ph + ")",
        "DELETE FROM source_version WHERE source_id IN (" + src_ph + ")",
        "DELETE FROM source WHERE source_id IN (" + src_ph + ")",
    ):
        conn.execute(sql, srcs)


def _execute_sow_retirement_tx(graph_path: Path, now_iso: str) -> Tuple[str, Dict[str, int]]:
    conn = GovernedStorageContext.connect(graph_path, readonly=False, timeout=30.0)
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("BEGIN IMMEDIATE;")

    src_ph = ",".join("?" for _ in ENTERPRISE4U_SOW_DUPLICATE_SOURCES)
    asrt_ids = [
        r[0]
        for r in conn.execute(
            f"""SELECT DISTINCT a.assertion_id FROM assertion a
                LEFT JOIN assertion_evidence ae ON a.assertion_id = ae.assertion_id
                LEFT JOIN evidence_item e ON ae.evidence_id = e.evidence_id
                WHERE e.source_id IN ({src_ph}) OR a.primary_source_id IN ({src_ph})""",
            [*ENTERPRISE4U_SOW_DUPLICATE_SOURCES, *ENTERPRISE4U_SOW_DUPLICATE_SOURCES],
        ).fetchall()
    ]
    excl_nodes = [
        r[0]
        for r in conn.execute(
            f"""SELECT n.node_id FROM node n
                WHERE n.node_id IN (
                    SELECT a.subject_node_id FROM assertion a
                    JOIN assertion_evidence ae ON a.assertion_id=ae.assertion_id
                    JOIN evidence_item e ON ae.evidence_id=e.evidence_id
                    WHERE e.source_id IN ({src_ph})
                    UNION
                    SELECT a.object_node_id FROM assertion a
                    JOIN assertion_evidence ae ON a.assertion_id=ae.assertion_id
                    JOIN evidence_item e ON ae.evidence_id=e.evidence_id
                    WHERE e.source_id IN ({src_ph}) AND a.object_node_id IS NOT NULL
                )
                AND NOT EXISTS (
                    SELECT 1 FROM assertion a2
                    JOIN assertion_evidence ae2 ON a2.assertion_id=ae2.assertion_id
                    JOIN evidence_item e2 ON ae2.evidence_id=e2.evidence_id
                    WHERE e2.source_id NOT IN ({src_ph})
                    AND (a2.subject_node_id = n.node_id OR a2.object_node_id = n.node_id)
                )""",
            [*ENTERPRISE4U_SOW_DUPLICATE_SOURCES, *ENTERPRISE4U_SOW_DUPLICATE_SOURCES, *ENTERPRISE4U_SOW_DUPLICATE_SOURCES],
        ).fetchall()
    ]
    _delete_sow_assertions_and_nodes(conn, src_ph, asrt_ids, excl_nodes)

    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {
        "retired_sources": len(ENTERPRISE4U_SOW_DUPLICATE_SOURCES),
        "deleted_assertions": len(asrt_ids),
        "deleted_exclusive_nodes": len(excl_nodes),
    }
    sql_audit = (
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, "
        "release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', 'generation-retire-enterprise4u-sow', "
        "NULL, ?, NULL, ?, 'Retire duplicate ENTERPRISE4U SOW sources and bloated nodes', "
        "'WAVE-3-SOW-CLEANUP', 'HITL-10-24-2026', 'run-retire-enterprise4u-sow', '1.0.0')"
    )
    conn.execute(sql_audit, (audit_id, json.dumps(stats), now_iso))
    conn.commit()
    ic = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    fk = conn.execute("PRAGMA foreign_key_check").fetchall()
    if ic != ["ok"] or len(fk) > 0:
        raise RuntimeError(f"Database validation failed: integrity={ic}, fk_violations={fk}")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    conn.close()
    return audit_id, stats


def retire_enterprise4u_sow_duplicates(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Retire the 5 duplicate/draft ENTERPRISE4U SOW sources and their 64 exclusive bloated/split-identity nodes in a new generation."""
    import shutil

    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "retire-enterprise4u-sow-dups", now_iso, length=28)
    new_gen_dir = rt_root / "generations" / new_gen_id
    new_gen_dir.mkdir(parents=True, exist_ok=True)
    graph_path = new_gen_dir / "graph.sqlite"
    sidecar_path = new_gen_dir / "sidecar.sqlite"
    pair_manifest_path = new_gen_dir / "pair_manifest.json"
    shutil.copy2(old_graph_path, graph_path)
    shutil.copy2(old_sidecar_path, sidecar_path)

    before_snap = snapshot(graph_path)
    audit_id, stats = _execute_sow_retirement_tx(graph_path, now_iso)

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

    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id],
        expected_deltas={
            "node": after_snap["row_counts"]["node"] - before_snap["row_counts"]["node"],
            "assertion": after_snap["row_counts"]["assertion"] - before_snap["row_counts"]["assertion"],
        },
        readback_probes={"probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,))},
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:sow-duplicate-retirement-receipt:1.0.0",
        "timestamp": now_iso,
        "terminal_outcome": proof["overall"],
        "stats": stats,
        "physical_proof": proof,
    }
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
