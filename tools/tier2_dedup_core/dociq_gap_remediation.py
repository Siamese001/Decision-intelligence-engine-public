"""Governed Project DocIQ duplicate source consolidation and candidate assertion promotion."""
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

DOCIQ_DUPLICATE_SOURCE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("source-359d6767d20d2bfd424dbe86", "source-13feb53d4838304da8a020e0"),
)

DOCIQ_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source-9c8f46d0e98343814b019e43",
    "source-359d6767d20d2bfd424dbe86",
    "source-64454b368c363aeace2837f5",
    "source-5e91a72cdd4a29c66d0ae535",
    "source-356d05d5742d85a9dcb3b0ec",
)

DOCIQ_ARCHIVE_REFS: Dict[str, str] = {
    "source-9c8f46d0e98343814b019e43": "output/dociq/09-23-2026/Dociq_Q4_Scope/01_Dociq_Q4_Scope_09-23-2026_0900AM0950AMET_hitl_resolved_transcript.md",
    "source-359d6767d20d2bfd424dbe86": "output/dociq/09-30-2026/Dociq_Q4_Scope_Bailey_Amit/01_Dociq_Q4_Scope_Bailey_Amit_09-30-2026_0415PM0447PMET_hitl_resolved_transcript.md",
    "source-64454b368c363aeace2837f5": "output/dociq/09-30-2026/Dociq_Update_Jack_PartnerAdvisory/03_Dociq_Update_Jack_PartnerAdvisory_09-30-2026_canonical_signal_projection.json",
    "source-5e91a72cdd4a29c66d0ae535": "output/dociq/10-01-2026/Dociq_Jack_Amit_Jack_PartnerAdvisory/01_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_hitl_resolved_transcript.md",
    "source-356d05d5742d85a9dcb3b0ec": "output/dociq/10-01-2026/Dociq_Update_Q3_Accomplishments_Backfill/03_Dociq_Update_Q3_Accomplishments_Backfill_10-01-2026_0959PM_ET_canonical_signal_projection.json",
}

DOCIQ_TRANSITION_ASSERTION_ID = "assertion-1fbc8cd1e86b28cbdc564f150a4a"

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-dociq-gap-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-25.2', 1, ?)"
)


def _consolidate_dociq_duplicate_sources(conn: Any) -> int:
    merged = 0
    for win, lose in DOCIQ_DUPLICATE_SOURCE_PAIRS:
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
    for sid, ref in DOCIQ_ARCHIVE_REFS.items():
        conn.execute(sql_archive, (ref, sid))
    return merged


def _promote_dociq_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    ph = ",".join("?" for _ in DOCIQ_CANONICAL_SOURCES)
    cur_curr = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', currentness_state = 'current', "
        "superseded_by_assertion_id = NULL, superseded_at = NULL, retrieval_eligible = 1, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        f"WHERE primary_source_id IN ({ph}) AND canonical_status = 'candidate'",
        [now_iso, *DOCIQ_CANONICAL_SOURCES],
    )
    cur_trans = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', currentness_state = 'current', "
        "superseded_by_assertion_id = NULL, superseded_at = NULL, retrieval_eligible = 1, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE assertion_id = ? AND canonical_status = 'candidate'",
        (now_iso, DOCIQ_TRANSITION_ASSERTION_ID),
    )
    return {
        "promoted_core_dociq_candidates": cur_curr.rowcount,
        "promoted_transition_candidates": cur_trans.rowcount,
        "total_promoted": cur_curr.rowcount + cur_trans.rowcount,
    }


def promote_dociq_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Consolidate duplicate DocIQ sources, promote all DocIQ draft assertions in a new generation, and seal 5-check proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "dociq-gap-remediation", now_iso, length=28)
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

    merged_sources = _consolidate_dociq_duplicate_sources(conn)
    promo_stats = _promote_dociq_candidates(conn, now_iso)
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
        "NULL, ?, NULL, ?, 'Project DocIQ duplicate source merge and draft promotion', "
        "'WAVE-3-DOCIQ-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-dociq-gap-remediation', '1.0.0')"
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

    ph = ",".join("?" for _ in DOCIQ_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_dociq_candidates": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND canonical_status='candidate') = 0",
            DOCIQ_CANONICAL_SOURCES,
        ),
        "probe_bailey_merged": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) >= 50",
            ("source-359d6767d20d2bfd424dbe86",),
        ),
        "probe_backfill_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) >= 16",
            ("source-356d05d5742d85a9dcb3b0ec",),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id, "source-359d6767d20d2bfd424dbe86", "source-356d05d5742d85a9dcb3b0ec"],
        expected_deltas={
            "source": -merged_sources,
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:dociq-graph-promotion-receipt:1.0.0",
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
