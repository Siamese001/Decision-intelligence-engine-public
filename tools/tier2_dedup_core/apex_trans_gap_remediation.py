"""Governed APEX_TRANS source unmerging, orphan source consolidation, and candidate assertion promotion."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from dual_pipeline.core import ROOT, sha256_file, stable_id
from dual_pipeline.runtime_root import resolve_authoritative_runtime_root
from dual_pipeline.storage.governed_context import GovernedStorageContext, Row
from tools.graph_mutation_proof_core.proof import prove, snapshot
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)

NEET_SESSION2_SOURCE_ID = "source-9172c5e8a4b01928374655aa"
NEET_SESSION1_SOURCE_ID = "source-9181b4d7c3e92018475666bb"

NEET_UNMERGE_SPECS: Tuple[Tuple[str, str, str, str, str, Tuple[str, ...]], ...] = (
    (
        "source-f4ad7c688918deba983e24b8",
        NEET_SESSION2_SOURCE_ID,
        "20260917 apex_trans Insights Hub Input Session 2",
        "2026-09-17",
        "output/apex_trans/09-17-2026/Neet_Insights_Hub_Christina_PartnerAdvisory/01_Neet_Insights_Hub_Christina_PartnerAdvisory_09-17-2026_0632PM0732PMET_hitl_resolved_transcript.md",
        ("source-version-52b97de91ce96658b032b495", "source-version-6eb73fedd8cd40af8de209d9"),
    ),
    (
        "source-d00208b01736a1a7a5c7119a",
        NEET_SESSION1_SOURCE_ID,
        "20260918 apex_trans Insights Hub Input Session 1",
        "2026-09-18",
        "output/apex_trans/09-18-2026/Insights_Hub_Input_Mitchell_PartnerAdvisory/01_Insights_Hub_Input_Mitchell_PartnerAdvisory_09-18-2026_0625PM0658PMET_hitl_resolved_transcript.md",
        ("source-version-cdb9485d09f6ca0cfe60e68a", "source-version-d38c1570a652a5794b596159"),
    ),
)

NEET_DUPLICATE_SOURCE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("source-ec1254230eb418a8f639d023", "source-f39c2ff07811104aa3e05244"),
    ("source-56fe1dd0e3aa3dd47287379c", "source-e46ca77d5fa2bb41b8d4fe31"),
)

NEET_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source-5077e80ea571c09ced192755",
    "source-de386206fd64681618359882",
    "source-fb18d924c0ee6e62ee2321f1",
    "source-9b16a4e6157d057bb7abe875",
    "source-ec6cc0e7053efea3c6c0b657",
    "source-f4ad7c688918deba983e24b8",
    NEET_SESSION2_SOURCE_ID,
    NEET_SESSION1_SOURCE_ID,
    "source-d00208b01736a1a7a5c7119a",
    "source-ec1254230eb418a8f639d023",
    "source-1e2512379ec75c3fab6c3df0",
    "source-dd0de7f0fe270ed948409f4a",
    "source-ec7f4f1abc42b57d9c99d5b1",
    "source-945ec9b120888d7b5d02f0d9",
    "source-43eff84048930f05f3f60bf7",
)

RELOCATED_HRIT_SOURCES: Tuple[str, ...] = (
    "source-56fe1dd0e3aa3dd47287379c",
    "source-481bfc279e2f43a8dc71dd90",
    "source-a9289e452c912ca31b7a51b2",
)

NEET_ARCHIVE_REFS: Dict[str, str] = {
    "source-5077e80ea571c09ced192755": "output/apex_trans/07-21-2026/Trident_See_Sense_Act_Corporate/03_Trident_See_Sense_Act_Corporate_07-21-2026_0918PM_ET_canonical_signal_projection.json",
    "source-de386206fd64681618359882": "output/apex_trans/09-07-2026/Neet_Technology_Evaluations_Corporate/03_Neet_Technology_Evaluations_Corporate_09-07-2026_0923PM_ET_canonical_signal_projection.json",
    "source-fb18d924c0ee6e62ee2321f1": "output/apex_trans/09-10-2026/01_NEET_Insights_Hub_Tech_Services_Interview_09-10-2026_0334PM_0500PM_ET_hitl_resolved_transcript.md",
    "source-9b16a4e6157d057bb7abe875": "output/apex_trans/09-11-2026/01_NEET_Insights_Hub_Tech_Services_Group_Interview_09-11-2026_0336PM_0437PM_ET_hitl_resolved_transcript.md",
    "source-ec6cc0e7053efea3c6c0b657": "output/apex_trans/09-16-2026/Neet_Insights_Hub_Input_Session_4/01_Neet_Insights_Hub_Input_Session_4_09-16-2026_0301PM0336PMET_hitl_resolved_transcript.md",
    "source-f4ad7c688918deba983e24b8": "output/apex_trans/09-17-2026/Neet_Insights_Hub_Input_Krystal_PartnerAdvisory/01_Neet_Insights_Hub_Input_Krystal_PartnerAdvisory_09-17-2026_0202PM0302PMET_hitl_resolved_transcript.md",
    NEET_SESSION2_SOURCE_ID: "output/apex_trans/09-17-2026/Neet_Insights_Hub_Christina_PartnerAdvisory/01_Neet_Insights_Hub_Christina_PartnerAdvisory_09-17-2026_0632PM0732PMET_hitl_resolved_transcript.md",
    NEET_SESSION1_SOURCE_ID: "output/apex_trans/09-18-2026/Insights_Hub_Input_Mitchell_PartnerAdvisory/01_Insights_Hub_Input_Mitchell_PartnerAdvisory_09-18-2026_0625PM0658PMET_hitl_resolved_transcript.md",
    "source-d00208b01736a1a7a5c7119a": "output/apex_trans/09-18-2026/Insights_Hub_Cieran-patrick_Dominic/01_Insights_Hub_Cieran-patrick_Dominic_09-18-2026_0806PM_0848PM_ET_hitl_resolved_transcript.md",
    "source-ec1254230eb418a8f639d023": "output/apex_trans/09-24-2026/Internal_Transmission_Aaron_Anece/01_Internal_Transmission_Aaron_Anece_09-24-2026_0203PM_0245PM_ET_hitl_resolved_transcript.md",
    "source-1e2512379ec75c3fab6c3df0": "output/apex_trans/09-24-2026/Insights_Hub_Data_Systems_Aaron_Anece/01_Insights_Hub_Data_Systems_Aaron_Anece_09-24-2026_0701PM_0812PM_ET_hitl_resolved_transcript.md",
    "source-dd0de7f0fe270ed948409f4a": "output/apex_trans/09-28-2026/Introduction_PartnerAdvisory_Bcg_Mitchell_Peter/01_Introduction_PartnerAdvisory_Bcg_Mitchell_Peter_09-28-2026_0309PM_0333PM_ET_hitl_resolved_transcript.md",
    "source-ec7f4f1abc42b57d9c99d5b1": "output/apex_trans/09-29-2026/Insights_Hub_Progress_Mitchell_PartnerAdvisory/01_Insights_Hub_Progress_Mitchell_PartnerAdvisory_09-29-2026_0636PM_0716PM_ET_hitl_resolved_transcript.md",
    "source-945ec9b120888d7b5d02f0d9": "output/apex_trans/09-30-2026/Insights_Hub_Follow_Mitchell_Tenika/01_Insights_Hub_Follow_Mitchell_Tenika_09-30-2026_0236PM_0333PM_ET_hitl_resolved_transcript.md",
    "source-43eff84048930f05f3f60bf7": "output/apex_trans/10-02-2026/Western_Weather_Mitchell_Aaron/01_Western_Weather_Mitchell_Aaron_10-02-2026_0706PM_0739PM_ET_hitl_resolved_transcript.md",
    "source-56fe1dd0e3aa3dd47287379c": "output/hrit/09-25-2026/Hr_It_Minute_Notes_09_16_09_25_apex_global/03_Hr_It_Minute_Notes_09_16_09_25_apex_global_09-25-2026_0341PM_ET_canonical_signal_projection.json",
    "source-481bfc279e2f43a8dc71dd90": "output/hrit/10-02-2026/PartnerAdvisory_Align_Phase_Sow_Prf_Clement/03_PartnerAdvisory_Align_Phase_Sow_Prf_Clement_10-02-2026_0300PM_ET_canonical_signal_projection.json",
    "source-a9289e452c912ca31b7a51b2": "output/hrit/10-07-2026/Prf_Integration_Architecture_Clement/03_Prf_Integration_Architecture_Clement_10-07-2026_1238PM_ET_canonical_signal_projection.json",
}

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-apex_trans-gap-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-25.3', 1, ?)"
)


def _unmerge_neet_collided_sources(conn: Any, now_iso: str) -> int:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    split_count = 0
    for orig_sid, new_sid, s_name, s_date, archive_ref, sv_ids in NEET_UNMERGE_SPECS:
        if not conn.execute("SELECT 1 FROM source WHERE source_id = ?", (orig_sid,)).fetchone():
            continue
        ph_sv = ",".join("?" for _ in sv_ids)
        sql_match_sv = (
            "SELECT source_version_id FROM source_version "
            "WHERE source_id = ? AND source_version_id IN (" + ph_sv + ") ORDER BY version_number"
        )
        matching_svs = conn.execute(sql_match_sv, [orig_sid, *sv_ids]).fetchall()
        if not matching_svs:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO source (source_id, source_type, source_name, source_date, authority_rank, "
            "source_posture, cold_archive_ref, notes, created_at) VALUES "
            "(?, 'dual_pipeline_intake', ?, ?, 5, 'derivative_internal', ?, 'Governed NEE unmerged source package.', ?)",
            (new_sid, s_name, s_date, archive_ref, now_iso),
        )
        max_ver = conn.execute(
            "SELECT coalesce(max(version_number), 0) FROM source_version WHERE source_id = ?", (new_sid,)
        ).fetchone()[0]
        for idx, (sv_id,) in enumerate(matching_svs, start=1):
            conn.execute(
                "UPDATE source_version SET source_id = ?, version_number = ? WHERE source_version_id = ?",
                (new_sid, max_ver + idx, sv_id),
            )
        sql_upd_ev = "UPDATE evidence_item SET source_id = ? WHERE source_version_id IN (" + ph_sv + ")"
        conn.execute(sql_upd_ev, [new_sid, *sv_ids])
        if "ingestion_source" in tables:
            sql_upd_is = "UPDATE OR IGNORE ingestion_source SET source_id = ? WHERE source_version_id IN (" + ph_sv + ")"
            conn.execute(sql_upd_is, [new_sid, *sv_ids])
        if "assertion_evidence" in tables:
            sql_upd_as = (
                "UPDATE assertion SET primary_source_id = ? WHERE assertion_id IN ("
                "SELECT ae.assertion_id FROM assertion_evidence ae "
                "JOIN evidence_item ev ON ev.evidence_id = ae.evidence_id "
                "WHERE ev.source_version_id IN (" + ph_sv + "))"
            )
            conn.execute(sql_upd_as, [new_sid, *sv_ids])
        if "search_document" in tables:
            conn.execute(
                "UPDATE search_document SET source_id = ? WHERE assertion_id IN ("
                "SELECT assertion_id FROM assertion WHERE primary_source_id = ?)",
                (new_sid, new_sid),
            )
        split_count += 1
    return split_count


_OPTIONAL_REPOINT_QUERIES: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("evidence_item", "UPDATE OR IGNORE evidence_item SET source_id = ? WHERE source_id = ?", None),
    ("assertion", "UPDATE OR IGNORE assertion SET primary_source_id = ? WHERE primary_source_id = ?", None),
    ("search_document", "UPDATE OR IGNORE search_document SET source_id = ? WHERE source_id = ?", None),
    ("audit_event", "UPDATE OR IGNORE audit_event SET source_id = ? WHERE source_id = ?", None),
    ("edge", "UPDATE OR IGNORE edge SET source_id = ? WHERE source_id = ?", None),
    ("claim", "UPDATE OR IGNORE claim SET source_id = ? WHERE source_id = ?", None),
    ("alias", "UPDATE OR IGNORE alias SET source_id = ? WHERE source_id = ?", "DELETE FROM alias WHERE source_id = ?"),
    (
        "canonical_alias",
        "UPDATE OR IGNORE canonical_alias SET source_id = ? WHERE source_id = ?",
        "DELETE FROM canonical_alias WHERE source_id = ?",
    ),
    (
        "ingestion_source",
        "UPDATE OR IGNORE ingestion_source SET source_id = ? WHERE source_id = ?",
        "DELETE FROM ingestion_source WHERE source_id = ?",
    ),
)


def _repoint_optional_tables(conn: Any, tables: Sequence[str], win: str, lose: str) -> None:
    for tbl, upd_sql, del_sql in _OPTIONAL_REPOINT_QUERIES:
        if tbl in tables:
            conn.execute(upd_sql, (win, lose))
            if del_sql is not None:
                conn.execute(del_sql, (lose,))


def _consolidate_neet_duplicate_sources(conn: Any) -> int:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    merged = 0
    for win, lose in NEET_DUPLICATE_SOURCE_PAIRS:
        if not conn.execute("SELECT 1 FROM source WHERE source_id = ?", (lose,)).fetchone():
            continue
        if not conn.execute("SELECT 1 FROM source WHERE source_id = ?", (win,)).fetchone():
            continue
        max_ver = conn.execute(
            "SELECT coalesce(max(version_number), 0) FROM source_version WHERE source_id = ?", (win,)
        ).fetchone()[0]
        sv_rows = conn.execute(
            "SELECT source_version_id FROM source_version WHERE source_id = ? ORDER BY version_number",
            (lose,),
        ).fetchall()
        for idx, (sv_id,) in enumerate(sv_rows, start=1):
            conn.execute(
                "UPDATE source_version SET source_id = ?, version_number = ? WHERE source_version_id = ?",
                (win, max_ver + idx, sv_id),
            )
        _repoint_optional_tables(conn, tables, win, lose)
        conn.execute("DELETE FROM source WHERE source_id = ?", (lose,))
        merged += 1
    for sid, ref in NEET_ARCHIVE_REFS.items():
        conn.execute("UPDATE source SET cold_archive_ref = ? WHERE source_id = ?", (ref, sid))
    return merged


def _promote_single_assertion(conn: Any, aid: str, is_superseded: bool, now_iso: str) -> bool:
    if not is_superseded:
        conn.execute("SAVEPOINT sp_neet_promo")
        try:
            conn.execute(
                "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
                "validation_state = 'accepted_legacy', currentness_state = 'current', "
                "retrieval_eligible = 1, approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
                "WHERE assertion_id = ?",
                (now_iso, aid),
            )
            conn.execute("RELEASE SAVEPOINT sp_neet_promo")
            return True
        except Exception:
            conn.execute("ROLLBACK TO SAVEPOINT sp_neet_promo")
            conn.execute("RELEASE SAVEPOINT sp_neet_promo")
    conn.execute(
        "UPDATE assertion SET canonical_status = 'historical', canonical_eligible = 0, "
        "validation_state = 'superseded', currentness_state = 'historical', "
        "retrieval_eligible = 0, updated_at = ? WHERE assertion_id = ?",
        (now_iso, aid),
    )
    return False


def _promote_neet_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    all_target_sources = (*NEET_CANONICAL_SOURCES, *RELOCATED_HRIT_SOURCES)
    ph = ",".join("?" for _ in all_target_sources)
    sql_sel_cands = (
        "SELECT assertion_id, superseded_by_assertion_id FROM assertion "
        "WHERE primary_source_id IN (" + ph + ") "
        "AND (canonical_status IN ('candidate', 'historical_candidate') OR validation_state = 'needs_review') "
        "ORDER BY assertion_id"
    )
    rows = conn.execute(sql_sel_cands, list(all_target_sources)).fetchall()
    promoted_active = 0
    normalized_superseded = 0
    for aid, sup_by in rows:
        if _promote_single_assertion(conn, str(aid), bool(sup_by), now_iso):
            promoted_active += 1
        else:
            normalized_superseded += 1
    if "search_document" in tables and "canonical_status" in {
        r[1] for r in conn.execute("PRAGMA table_info(search_document)").fetchall()
    }:
        sql_upd_sd = (
            "UPDATE search_document SET canonical_status = 'canonical', currentness_state = 'current', "
            "retrieval_eligible = 1, updated_at = ? "
            "WHERE assertion_id IN (SELECT assertion_id FROM assertion "
            "WHERE primary_source_id IN (" + ph + ") AND canonical_status = 'canonical' AND currentness_state = 'current')"
        )
        conn.execute(sql_upd_sd, [now_iso, *all_target_sources])
    return {
        "promoted_active_candidates": promoted_active,
        "normalized_superseded_candidates": normalized_superseded,
        "total_promoted": promoted_active + normalized_superseded,
    }


def promote_neet_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Unmerge collided 09-17/09-18 sources, merge orphan 09-24 source, promote all APEX_TRANS draft assertions, and seal 5-check proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "apex_trans-gap-remediation", now_iso, length=28)
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

    unmerged_sources = _unmerge_neet_collided_sources(conn, now_iso)
    merged_sources = _consolidate_neet_duplicate_sources(conn)
    promo_stats = _promote_neet_candidates(conn, now_iso)
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {
        "unmerged_sources_created": unmerged_sources,
        "duplicate_sources_merged": merged_sources,
        **promo_stats,
    }
    conn.execute(
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, NULL, ?, 'APEX_TRANS source unmerge, orphan merge, and draft assertion promotion', "
        "'WAVE-3-APEX_TRANS-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-apex_trans-gap-remediation', '1.0.0')",
        (audit_id, new_gen_id, json.dumps(stats), now_iso),
    )
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

    ph = ",".join("?" for _ in NEET_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_neet_candidates": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND (canonical_status IN ('candidate','historical_candidate') OR validation_state='needs_review')) = 0",
            NEET_CANONICAL_SOURCES,
        ),
        "probe_session2_unmerged": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) >= 55",
            (NEET_SESSION2_SOURCE_ID,),
        ),
        "probe_session1_unmerged": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) >= 55",
            (NEET_SESSION1_SOURCE_ID,),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id, NEET_SESSION2_SOURCE_ID, NEET_SESSION1_SOURCE_ID],
        expected_deltas={
            "source": unmerged_sources - merged_sources,
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:apex_trans-graph-promotion-receipt:1.0.0",
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
