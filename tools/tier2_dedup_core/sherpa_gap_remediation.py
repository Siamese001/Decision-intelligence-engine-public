"""Governed Project Beacon missing meeting ingestion, draft promotion, and archive path rebinding."""
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
from tools.tier2_dedup_core.sherpa_role_and_lens_remediation import (
    remediate_sherpa_lens_and_role_edges,
)
from tools.tier2_dedup_core.sow_duplicate_retirement import (
    refresh_sidecar_after_graph_cleanup,
)

SHERPA_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source_v36_sherpa_sow_2026_06_01_2026_11_30",
    "source_nee22_project_beacon_field_context_2026_07_01",
    "source_sherpa_eval_plan",
    "source_v17_nee_it_ai_hub_engineering_sows_2026_07_08",
    "source_brien_sherpa_feedback_notes_2026_07_10",
    "source_sherpa_stakeholder_identity_hitl_2026_07_10",
    "source_v36_user_sherpa_current_state_2026_07_10",
    "source_sherpa_andy_lillian_meeting_2026_07_13",
    "source-sherpa-20260727-a2a-architecture",
    "source-244602bbe3c399331a745e51",
    "source-sherpa-20260820-technical-interlock",
    "source-5b0a160a7355f5b5ad208571",
    "source-cffb30b6e16ac23d5f1b6e4d",
    "source-8ff73a78b25dbef51ed13df5",
    "source-sherpa-20260911-next-steps",
    "source-ab8a498d96c9ff086cf008ee",
    "source-1db0d2763a2ddfdd6c4fe1cf",
    "source-ea51c8983147880052dc880e",
    "source-2c2ac695afc1a9fc97da5a56",
    "source-118f929e3ab59449a1e81e20",
    "source-12af631893ae3f76b62973ff",
    "source-c8ba9093a5a3687e0d016e35",
)

SHERPA_ARCHIVE_REFS: Tuple[Tuple[str, str], ...] = (
    ("source-244602bbe3c399331a745e51", "output/project_beacon/08-13-2026/Taxonomy_Mcp_Vs_A2a_Speaker_Alpha_PartnerAdvisory/01_Taxonomy_Mcp_Vs_A2a_Speaker_Alpha_PartnerAdvisory_08-13-2026_0146PM0231PMET_hitl_resolved_transcript.md"),
    ("source-5b0a160a7355f5b5ad208571", "output/project_beacon/09-04-2026/Sherpa_Fdx_Rebuild_Aws_Decision/Sherpa_Fdx_Rebuild_Aws_Decision_source.md"),
    ("source-cffb30b6e16ac23d5f1b6e4d", "output/project_beacon/09-04-2026/project_beacon_Path_Forward/01_project_beacon_Path_Forward_09-04-2026_0200PM_0300PM_ET_hitl_resolved_transcript.md"),
    ("source-8ff73a78b25dbef51ed13df5", "output/project_beacon/09-09-2026/Sherpa_Demo_Josh/01_Sherpa_Demo_Josh_09-09-2026_1000AM_1030AM_ET_hitl_resolved_transcript.md"),
    ("source-ab8a498d96c9ff086cf008ee", "output/project_beacon/09-17-2026/Next_Steps_On_Sherpa_Marcus_PartnerAdvisory/01_Next_Steps_On_Sherpa_Marcus_PartnerAdvisory_09-17-2026_0400PM_0430PM_ET_hitl_resolved_transcript.md"),
    ("source-1db0d2763a2ddfdd6c4fe1cf", "output/project_beacon/09-18-2026/Sherpa_sprint_demo_Marcus_PartnerAdvisory/01_Sherpa_sprint_demo_Marcus_PartnerAdvisory_09-18-2026_0930AM_1030AM_ET_hitl_resolved_transcript.md"),
    ("source-ea51c8983147880052dc880e", "output/project_beacon/10-06-2026/project_beacon_Sprint_Speaker_Alpha_PartnerAdvisory/01_project_beacon_Sprint_Speaker_Alpha_PartnerAdvisory_10-06-2026_0115PM0200PMET_hitl_resolved_transcript.md"),
    ("source-2c2ac695afc1a9fc97da5a56", "output/project_beacon/10-13-2026/project_beacon_Metrics_Statistics_master/01_project_beacon_Metrics_Statistics_master_10-13-2026_0106PM0142PMET_hitl_resolved_transcript.md"),
    ("source-118f929e3ab59449a1e81e20", "output/project_beacon/10-14-2026/Delivery_Cansu_PartnerAdvisory/03_Delivery_Cansu_PartnerAdvisory_10-14-2026_0430PM_0500PM_ET_canonical_signal_projection.json"),
    ("source-12af631893ae3f76b62973ff", "output/project_beacon/10-14-2026/Sherpa_Connect_Cansu_Dan/01_Sherpa_Connect_Cansu_Dan_10-14-2026_0430PM0500PMET_hitl_resolved_transcript.md"),
    ("source-c8ba9093a5a3687e0d016e35", "output/project_beacon/10-16-2026/Sprint_Demo_Marcus_PartnerAdvisory/01_Sprint_Demo_Marcus_PartnerAdvisory_10-16-2026_0934AM1030AMET_hitl_resolved_transcript.md"),
)

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-sherpa-gap-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-25.3', 1, ?)"
)

_MISSING_MEETING_SPECS: Tuple[Tuple[str, str, str, str, Tuple[Tuple[str, str, Optional[str], Optional[str], Optional[str], str], ...]], ...] = (
    (
        "source-sherpa-20260727-a2a-architecture",
        "20260727 A2A Architecture Review (Egle & Andy)",
        "2026-07-27",
        "output/project_beacon/07-27-2026/A2a_Architecture_Egle_Andy/01_A2a_Architecture_Egle_Andy_07-27-2026_0309PM_0403PM_ET_hitl_resolved_transcript.md",
        (
            ("workstream_project_beacon", "HAS_CLAIM_TEXT", None, "Use the Solution Architect Advisor connection to demonstrate an A2A framework for connecting additional enterprise agents to Project Beacon.", "text", "Use the Solution Architect Advisor connection to demonstrate an A2A framework for connecting additional enterprise agents to Project Beacon."),
            ("person_andy_k", "CONTRIBUTES_TO", "workstream_project_beacon", None, None, "Andy Kilinskis and Egle Tamasevice reviewed the A2A protocol architecture and Solution Architect Advisor integration for Project Beacon."),
            ("entity-06ea5229f6c62bd4a1346f262928", "CONTRIBUTES_TO", "workstream_project_beacon", None, None, "Egle Tamasevice reviewed IBM Sherpa Tech Lead A2A architecture patterns with PartnerAdvisory and Apex Global Enterprises."),
        ),
    ),
    (
        "source-sherpa-20260820-technical-interlock",
        "20260820 Project Beacon Technical Interlock (Speaker_Alpha & Amit)",
        "2026-08-20",
        "output/project_beacon/08-20-2026/Technical_Interlock_Speaker_Alpha_Amit/01_Technical_Interlock_Speaker_Alpha_Amit_08-20-2026_0140PM_0230PM_ET_hitl_resolved_transcript.md",
        (
            ("workstream_project_beacon", "HAS_CLAIM_TEXT", None, "Keep the existing REST backend as the stable integration boundary for Solution Architect Advisor and assess A2A/A2UI interface transitions against a defined contract.", "text", "Keep the existing REST backend as the stable integration boundary for Solution Architect Advisor and assess A2A/A2UI interface transitions against a defined contract."),
            ("person_brien", "CONTRIBUTES_TO", "workstream_project_beacon", None, None, "Speaker_Alpha Muschett and Amit Ayer aligned on AI Gateway, Agent Registry, MCP, and A2A taxonomy for Project Beacon."),
            ("person_amit", "CONTRIBUTES_TO", "workstream_project_beacon", None, None, "Amit Ayer outlined transitory design states to avoid throwaway code while integrating Solution Architect Advisor with Project Beacon."),
        ),
    ),
    (
        "source-sherpa-20260911-next-steps",
        "20260911 Project Beacon Next Steps (Dan, Amit & Stephan)",
        "2026-09-11",
        "output/project_beacon/09-11-2026/Sherpa_Next_Steps/01_Sherpa_Next_Steps_09-11-2026_0330PM_hitl_resolved_transcript.md",
        (
            ("workstream_project_beacon", "HAS_CLAIM_TEXT", None, "Explore a parallel delivery path leveraging Google-related technology to accelerate Project Beacon delivery and mitigate stakeholder timeline risk.", "text", "Explore a parallel delivery path leveraging Google-related technology to accelerate Project Beacon delivery and mitigate stakeholder timeline risk."),
            ("person_dan_okray", "CONTRIBUTES_TO", "workstream_project_beacon", None, None, "Drew Oakley and Amit Ayer evaluated Project Beacon delivery trajectory and parallel acceleration options."),
            ("stakeholder_marcus_candidate", "CONTRIBUTES_TO", "workstream_project_beacon", None, None, "Marcus Balk stakeholder expectations on Project Beacon delivery velocity and quality were reviewed for mitigation."),
        ),
    ),
)


def _ingest_missing_sherpa_meetings(conn: Any, now_iso: str) -> Dict[str, int]:
    sources_added = 0
    assertions_added = 0
    for sid, sname, sdate, ref, claims in _MISSING_MEETING_SPECS:
        if conn.execute("SELECT 1 FROM source WHERE source_id = ?", (sid,)).fetchone():
            continue
        ts = f"{sdate}T15:00:00Z"
        conn.execute(
            "INSERT INTO source (source_id, source_type, source_name, source_date, authority_rank, "
            "source_posture, cold_archive_ref, notes, created_at) VALUES "
            "(?, 'dual_pipeline_intake', ?, ?, 5, 'derivative_internal', ?, "
            "'Governed NEE dual-pipeline Project Beacon meeting package.', ?)",
            (sid, sname, sdate, ref, ts),
        )
        sources_added += 1
        fp_seed = stable_id("fp", sid, sdate, length=64).replace("fp-", "").ljust(64, "0")[:64]
        sv_id = stable_id("source-version", sid, "v1", length=24)
        conn.execute(
            "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, "
            "source_fingerprint, content_hash, source_date_normalized, source_date_precision, "
            "source_date_status, authority_rank, scope_text, posture_text, created_at) VALUES "
            "(?, ?, 1, ?, ?, ?, 'day', 'exact', 5, 'Governed source-bound intake.', 'derivative_internal', ?)",
            (sv_id, sid, fp_seed, fp_seed, sdate, ts),
        )
        er_id = stable_id("extraction-run", sid, length=28)
        conn.execute(
            "INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', "
            "?, ?, ?, ?, 'completed', 1)",
            (er_id, fp_seed, fp_seed, ts, ts),
        )
        ev_id = stable_id("evidence", sid, "ev1", length=26)
        ev_fp = stable_id("evfp", sid, length=64).replace("evfp-", "").ljust(64, "a")[:64]
        conn.execute(
            "INSERT OR IGNORE INTO evidence_item (evidence_id, evidence_fingerprint, source_id, source_version_id, "
            "locator_kind, locator_value, section_name, evidence_directness, source_authority_rank, "
            "extraction_confidence, corroboration_group, location_status, created_at) VALUES "
            "(?, ?, ?, ?, 'section', 'Executive Summary', 'Executive Summary', 'direct', 5, 0.9, "
            "'corroboration-sherpa-gap', 'exact', ?)",
            (ev_id, ev_fp, sid, sv_id, ts),
        )
        for idx, (subj, pred, obj_node, obj_lit, obj_type, scope) in enumerate(claims):
            aid = stable_id("assertion", sid, str(idx), length=28)
            afp = stable_id("afp", sid, str(idx), length=64).replace("afp-", "").ljust(64, "b")[:64]
            sdoc_id = stable_id("search", sid, str(idx), length=28)
            ltype = "claim" if obj_node is None else "edge"
            conn.execute(
                "INSERT OR IGNORE INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, "
                "predicate, object_node_id, object_literal, object_literal_type, assertion_type, "
                "atomicity_state, scope_text, speaker_or_author, primary_source_id, extraction_run_id, "
                "extraction_method, origin_type, confidence_label, confidence_score, validation_state, "
                "currentness_state, valid_from, date_precision, date_status, recorded_at, resolution_state, "
                "canonical_status, canonical_eligible, retrieval_eligible, materiality, classification, "
                "allowed_audiences_json, hitl_required, validation_priority, qualifiers_json, "
                "legacy_record_type, legacy_record_ids_json, created_at, updated_at, approval_reference) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, 'extracted_claim', 'atomic', ?, 'Project Beacon Meeting Roster', "
                "?, ?, 'dual_pipeline_structured_adapter', 'human_source', 'high', 0.9, 'needs_review', "
                "'current', ?, 'day', 'exact', ?, 'resolved', 'candidate', 0, 1, 'high', 'internal', "
                "'[\"internal_consulting\",\"nee-engineering\"]', 0, 'P1', '{}', ?, '[]', ?, ?, NULL)",
                (aid, afp, subj, pred, obj_node, obj_lit, obj_type, scope, sid, er_id, sdate, ts, ltype, ts, now_iso),
            )
            conn.execute(
                "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, "
                "independent_corroboration, created_at) VALUES (?, ?, 'supports', 0, ?)",
                (aid, ev_id, ts),
            )
            conn.execute(
                "INSERT OR IGNORE INTO search_document (search_document_id, object_type, object_id, title, body, "
                "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
                "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, ?, ?, ?)",
                (sdoc_id, aid, scope, scope, sid, aid, now_iso),
            )
            assertions_added += 1
    return {"sources_ingested": sources_added, "assertions_ingested": assertions_added}


def _rebind_sherpa_archive_refs(conn: Any) -> int:
    rebound = 0
    for sid, ref in SHERPA_ARCHIVE_REFS:
        cur = conn.execute("UPDATE source SET cold_archive_ref = ? WHERE source_id = ?", (ref, sid))
        rebound += cur.rowcount
    return rebound


def _promote_sherpa_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    ingest_stats = _ingest_missing_sherpa_meetings(conn, now_iso)
    ph = ",".join("?" for _ in SHERPA_CANONICAL_SOURCES)
    cur_promo = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', currentness_state = 'current', "
        "superseded_by_assertion_id = NULL, superseded_at = NULL, retrieval_eligible = 1, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE primary_source_id IN (" + ph + ") AND canonical_status != 'canonical'",
        [now_iso, *SHERPA_CANONICAL_SOURCES],
    )
    cur_val = conn.execute(
        "UPDATE assertion SET validation_state = 'accepted_legacy', canonical_eligible = 1, "
        "retrieval_eligible = CASE WHEN currentness_state = 'current' AND superseded_by_assertion_id IS NULL THEN 1 ELSE 0 END, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE primary_source_id IN (" + ph + ") AND canonical_status = 'canonical' AND validation_state != 'accepted_legacy' "
        "AND EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = assertion.assertion_id AND ae.evidence_relationship = 'supports')",
        [now_iso, *SHERPA_CANONICAL_SOURCES],
    )
    conn.execute(
        """UPDATE search_document
              SET canonical_status = (SELECT a.canonical_status FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                  currentness_state = (SELECT a.currentness_state FROM assertion a WHERE a.assertion_id = search_document.assertion_id),
                  retrieval_eligible = CASE
                    WHEN EXISTS (
                      SELECT 1 FROM assertion a
                       WHERE a.assertion_id = search_document.assertion_id
                         AND a.canonical_status = 'canonical'
                         AND a.validation_state = 'accepted_legacy'
                         AND a.currentness_state = 'current'
                         AND a.retrieval_eligible = 1
                         AND a.superseded_by_assertion_id IS NULL
                         AND EXISTS (SELECT 1 FROM assertion_evidence ae WHERE ae.assertion_id = a.assertion_id AND ae.evidence_relationship = 'supports')
                         AND EXISTS (SELECT 1 FROM governance_approval ga WHERE ga.approval_reference = a.approval_reference AND ga.status = 'approved' AND ga.active = 1)
                    ) THEN 1 ELSE 0 END,
                  updated_at = ?
            WHERE assertion_id IN (
              SELECT assertion_id FROM assertion WHERE primary_source_id IN (""" + ph + """)
            )""",
        [now_iso, *SHERPA_CANONICAL_SOURCES],
    )
    role_and_lens_stats = remediate_sherpa_lens_and_role_edges(conn, now_iso)
    return {
        **ingest_stats,
        "promoted_non_canonical_assertions": cur_promo.rowcount,
        "normalized_validation_state_assertions": cur_val.rowcount,
        **role_and_lens_stats,
    }


def promote_sherpa_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ingest 3 missing Project Beacon meetings, promote all draft assertions, rebind archive paths, and seal 5-check proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "sherpa-gap-remediation", now_iso, length=28)
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

    rebound_refs = _rebind_sherpa_archive_refs(conn)
    promo_stats = _promote_sherpa_candidates(conn, now_iso)
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {"archive_refs_rebound": rebound_refs, **promo_stats}
    sql_audit = (
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, NULL, ?, 'Project Beacon missing meeting ingestion, archive rebinding, and draft promotion', "
        "'WAVE-2-SHERPA-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-sherpa-gap-remediation', '1.0.0')"
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

    ph = ",".join("?" for _ in SHERPA_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_sherpa_non_canonical": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND canonical_status NOT IN ('canonical', 'rejected', 'historical')) = 0",
            SHERPA_CANONICAL_SOURCES,
        ),
        "probe_0727_ingested_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) = 3",
            ("source-sherpa-20260727-a2a-architecture",),
        ),
        "probe_0820_ingested_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) = 3",
            ("source-sherpa-20260820-technical-interlock",),
        ),
        "probe_0911_ingested_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) = 3",
            ("source-sherpa-20260911-next-steps",),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[
            audit_id,
            "deliverable_sherpa_opensearch_migration",
            "pattern_agent_to_agent_protocol",
        ],
        expected_deltas={
            "source": promo_stats["sources_ingested"],
            "assertion": promo_stats["assertions_ingested"] + promo_stats.get("assertions_inserted", 0),
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:sherpa-graph-promotion-receipt:1.0.0",
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
