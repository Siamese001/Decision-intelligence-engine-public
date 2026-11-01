"""Governed Project NEXUS duplicate source consolidation, 10-04 ingestion, and candidate promotion."""
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

NEXUS_DUPLICATE_SOURCE_PAIRS: Tuple[Tuple[str, str], ...] = (
    ("source-f6f74a5571077d5a18d4e8ad", "source-2ac1c0f995e28e84e6625b66"),
    ("source-167fcdac0b5307456d82c08f", "source-cb1421d5370d5f05d9de139a"),
    ("source-a27123ba04ef0f65f2105214", "source-619a0273fe4357416470e53d"),
    ("source-4505907394a4b8a617f5d21b", "source-1a12bf59bdf4e859fb2310a4"),
    ("source-22cae43d86bde3027606e0eb", "source-13b4df9b967732f8b9ef0822"),
    ("source-22cae43d86bde3027606e0eb", "source-eca6e991fb179b9cb9a1d026"),
    ("source-8cd4edca099059228e9d437c", "source-6e077bed59d2c12d6e5250cb"),
    ("source-93aa4ccd3eaa54fdf849225f", "source-5f535ff95b3df4010fb944bb"),
)

NEXUS_CANONICAL_SOURCES: Tuple[str, ...] = (
    "source_gordon_meeting_2026_07_07",
    "source-f6f74a5571077d5a18d4e8ad",
    "source-167fcdac0b5307456d82c08f",
    "source-c8b210b288699dd973fb41f1",
    "source-81397f6793aca14151f70076",
    "source-a27123ba04ef0f65f2105214",
    "source-03d4360fcfbe9c64c0b8cd15",
    "source-1acfc7fa9be51d46c7aff034",
    "source-93d9f915e93ac66500756b05",
    "source-aad33814e62cdad44e4401fb",
    "source-0e68c71b692eca5c5ff9a61e",
    "source-4505907394a4b8a617f5d21b",
    "source-5c0d92c8fc4cb8966f19523d",
    "source-22cae43d86bde3027606e0eb",
    "source-1db4a06f16fd507ae807edd1",
    "source-8dd2f4a54f7e533889412ed2",
    "source-8cd4edca099059228e9d437c",
    "source-64454b368c363aeace2837f5",
    "source-5e91a72cdd4a29c66d0ae535",
    "source-356d05d5742d85a9dcb3b0ec",
    "source-62cd32261ed5c778fdc62262",
    "source-6a8ae6ba2bc6638d84cdb395",
    "source-93aa4ccd3eaa54fdf849225f",
    "source-5ec96e22d7164bb4e1a66670",
    "source-22e522e1934de27ddae4bb4d",
)

NEXUS_ARCHIVE_REFS: Tuple[Tuple[str, str], ...] = (
    ("source-f6f74a5571077d5a18d4e8ad", "output/project_nexus/08-18-2026/01_APEX_Nexus_Rewire_08-18-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-167fcdac0b5307456d82c08f", "output/nea/08-27-2026/Nea_Chakra_Operating_Model/01_Nea_chakra_operating_model_Stefan_PartnerAdvisory_08-27-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-c8b210b288699dd973fb41f1", "output/project_nexus/08-28-2026/01_Nexus_Reid_NEA_08-28-2026_1000AM_1045AM_ET_hitl_resolved_transcript.md"),
    ("source-81397f6793aca14151f70076", "output/nea/09-01-2026/NEA_Screener_Sync/01_NEA_Screener_Sync_09-01-2026_153232_hitl_resolved_transcript.md"),
    ("source-a27123ba04ef0f65f2105214", "output/nea/09-07-2026/NEA_Onboarding_Charter_Teams/01_NEA_Onboarding_Charter_Teams_09-07-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-03d4360fcfbe9c64c0b8cd15", "output/nea/09-08-2026/Rianne_PartnerAdvisory/01_Rianne_09-08-2026_1200_hitl_resolved_transcript.md"),
    ("source-1acfc7fa9be51d46c7aff034", "output/project_nexus/09-10-2026/01_Project_Nexus_09-10-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-aad33814e62cdad44e4401fb", "output/project_nexus/09-14-2026/Draft_Charters/01_Project_Nexus_Draft_Charters_09-14-2026_0137PM_0210PM_ET_hitl_resolved_transcript.md"),
    ("source-4505907394a4b8a617f5d21b", "output/project_lens/09-14-2026/SOW1_Observability_Catchup/01_Project_Lens_SOW1_Observability_Catchup_09-14-2026_0502PM_0539PM_ET_hitl_resolved_transcript.md"),
    ("source-5c0d92c8fc4cb8966f19523d", "output/partner_alliances/09-22-2026/Google_Internal_Anya_Shreyak/01_Google_Internal_Anya_Shreyak_09-22-2026_1000AM_1030AM_ET_hitl_resolved_transcript.md"),
    ("source-22cae43d86bde3027606e0eb", "output/partner_alliances/09-25-2026/Google_Alex_Shreyak_Amit_Anya/01_Google_Alex_Shreyak_Amit_Anya_09-25-2026_0900AM_1000AM_ET_hitl_resolved_transcript.md"),
    ("source-8cd4edca099059228e9d437c", "output/partner_alliances/09-29-2026/PartnerAdvisory_Gcp_Alex_Amit/01_PartnerAdvisory_Gcp_Alex_Amit_09-29-2026_0307PM0350PMET_hitl_resolved_transcript.md"),
    ("source-64454b368c363aeace2837f5", "output/dociq/09-30-2026/Dociq_Update_Jack_PartnerAdvisory/03_Dociq_Update_Jack_PartnerAdvisory_09-30-2026_canonical_signal_projection.json"),
    ("source-5e91a72cdd4a29c66d0ae535", "output/dociq/10-01-2026/Dociq_Jack_Amit_Jack_PartnerAdvisory/01_Dociq_Jack_Amit_Jack_PartnerAdvisory_10-01-2026_1107AM1128AMET_hitl_resolved_transcript.md"),
    ("source-62cd32261ed5c778fdc62262", "output/market_intelligence/10-04-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-04-2026_0357PM_ET_canonical_signal_projection.json"),
    ("source-6a8ae6ba2bc6638d84cdb395", "output/market_intelligence/10-06-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-06-2026_0722AM_ET_canonical_signal_projection.json"),
    ("source-93aa4ccd3eaa54fdf849225f", "output/nea/10-09-2026/Jeffrey_Amit_Cweos_Jeffrey_PartnerAdvisory/01_Jeffrey_Amit_Cweos_Jeffrey_PartnerAdvisory_10-09-2026_0102PM_0134PM_ET_hitl_resolved_transcript.md"),
    ("source-22e522e1934de27ddae4bb4d", "output/internal_consulting/10-12-2026/Agile_Delivery_Roles_Product_PartnerAdvisory/03_Agile_Delivery_Roles_Product_PartnerAdvisory_10-12-2026_0237AM_ET_canonical_signal_projection.json"),
)

_SQL_INSERT_APPROVAL = (
    "INSERT OR IGNORE INTO governance_approval ("
    "approval_id, approval_reference, approval_type, scope_json, "
    "proposal_sha256, approval_statement_sha256, approved_at, expires_at, "
    "status, approved_by, release_version, active, created_at"
    ") VALUES ("
    "'approval-nexus-gap-remediation', 'REQ_INGEST_CEC57955C84BCE3A', 'ingestion_promotion', "
    "'{\"allowed_actions\": [\"canonical_assertion_promotion\"]}', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "'1947e9b79b4a312769d22f7b01765e76406542599de17c0f2d42270cc8f34110', "
    "?, '2099-12-31T23:59:59Z', 'approved', 'Operator', '2026-10-25.2', 1, ?)"
)

_MARKET_1004_CLAIMS: Tuple[Tuple[Any, ...], ...] = (
    (
        "assertion-779f1a936da7ed10ac2a2d461797",
        "adcb52830d401fd55c83e81446d6a9be28f8574cbb289c6d970442de72de549d",
        "org_fpl",
        "HAS_CLAIM_TEXT",
        None,
        "Florida Power is an identified org in Document 20261004 Energy Utilities Market Trends.",
        "text",
        "Florida Power is an identified org in Document 20261004 Energy Utilities Market Trends.",
        0.85,
        "medium",
        "P2",
        "claim",
        "search-0a153c99e71ba562a0124a8c9620",
    ),
    (
        "assertion-450f19765adcafb0298daaa3615b",
        "7920b986b6434b80f2f12a03139c10c0a46f8b091dd7130ca394a5f9ff94afa6",
        "entity-b150b12b1a9528f6b17bbad318fc",
        "HAS_CLAIM_TEXT",
        None,
        "FPSC is an identified org in Document 20261004 Energy Utilities Market Trends.",
        "text",
        "FPSC is an identified org in Document 20261004 Energy Utilities Market Trends.",
        0.85,
        "medium",
        "P2",
        "claim",
        "search-938a955942a65901e6530222a4b4",
    ),
    (
        "assertion-4f753ea541ec5ae12656460f2dc3",
        "6cbf461e977d4867b9308e7e5208400b36ef97a75075350150f1ccc91711ae00",
        "entity-b150b12b1a9528f6b17bbad318fc",
        "AFFILIATED_WITH",
        "org_fpl",
        None,
        None,
        "Fpsc MEMBER_OF Florida-Power-And-Light.",
        0.9,
        "high",
        "P1",
        "edge",
        "search-b99de883116b962fed4563e4a909",
    ),
)


def _ingest_market_trends_1004(conn: Any, now_iso: str) -> int:
    sid = "source-62cd32261ed5c778fdc62262"
    if conn.execute("SELECT 1 FROM source WHERE source_id = ?", (sid,)).fetchone():
        return 0
    ref = "output/market_intelligence/10-04-2026/Energy_Utilities_Market_Antigravity/03_Energy_Utilities_Market_Antigravity_10-04-2026_0357PM_ET_canonical_signal_projection.json"
    conn.execute(
        "INSERT INTO source (source_id, source_type, source_name, source_date, authority_rank, "
        "source_posture, cold_archive_ref, notes, created_at) VALUES "
        "(?, 'dual_pipeline_intake', 'Document 20261004 Energy Utilities Market Trends', '2026-10-04', "
        "5, 'derivative_internal', ?, 'Governed NEE dual-pipeline source package.', '2026-10-04T19:58:26Z')",
        (sid, ref),
    )
    sv_id = "source-version-ead990b1fa33c7a32c71b6ed"
    conn.execute(
        "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, "
        "source_fingerprint, content_hash, source_date_normalized, source_date_precision, "
        "source_date_status, authority_rank, scope_text, posture_text, created_at) VALUES "
        "(?, ?, 1, '882b4c96b20d86d1705003b5906615f846cb7465813a5e067f1d0d4d371d9413', "
        "'882b4c96b20d86d1705003b5906615f846cb7465813a5e067f1d0d4d371d9413', '2026-10-04', "
        "'day', 'exact', 5, 'Governed source-bound candidate intake.', 'derivative_internal', '2026-10-04T19:58:26Z')",
        (sv_id, sid),
    )
    er_id = "extraction-run-05da3f381e9067a22a88e79c84cf"
    conn.execute(
        "INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', "
        "'156065a6a57872cc241a2e478b1403753ffe19fb8349112a60d4c3fe4ede10b5', "
        "'882b4c96b20d86d1705003b5906615f846cb7465813a5e067f1d0d4d371d9413', "
        "'2026-10-04T19:58:26Z', '2026-10-04T19:58:26Z', 'completed', 1)",
        (er_id,),
    )
    ev_id = "evidence-a6aeaed41635afac80b2bcfc9c"
    conn.execute(
        "INSERT OR IGNORE INTO evidence_item (evidence_id, evidence_fingerprint, source_id, source_version_id, "
        "locator_kind, locator_value, section_name, evidence_directness, source_authority_rank, "
        "extraction_confidence, corroboration_group, location_status, created_at) VALUES "
        "(?, '0e228f179d713795a7f2a8b9b871989ba2cbc1a6b65088c4b376942379dacef9', ?, ?, "
        "'section', '## 1. Executive Summary', '## 1. Executive Summary', 'derivative', 5, 1.0, "
        "'corroboration-6da7961bc9549cc0ad93c55c', 'exact', '2026-10-04T19:58:26Z')",
        (ev_id, sid, sv_id),
    )
    for aid, fp, subj, pred, obj_node, obj_lit, obj_type, scope, conf, mat, prio, ltype, sdoc_id in _MARKET_1004_CLAIMS:
        conn.execute(
            "INSERT OR IGNORE INTO assertion (assertion_id, assertion_fingerprint, subject_node_id, "
            "predicate, object_node_id, object_literal, object_literal_type, assertion_type, "
            "atomicity_state, scope_text, speaker_or_author, primary_source_id, extraction_run_id, "
            "extraction_method, origin_type, confidence_label, confidence_score, validation_state, "
            "currentness_state, valid_from, date_precision, date_status, recorded_at, resolution_state, "
            "canonical_status, canonical_eligible, retrieval_eligible, materiality, classification, "
            "allowed_audiences_json, hitl_required, validation_priority, qualifiers_json, "
            "legacy_record_type, legacy_record_ids_json, created_at, updated_at, approval_reference) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, 'extracted_claim', 'atomic', ?, 'Antigravity Market Intelligence Agent', "
            "?, ?, 'dual_pipeline_structured_adapter', 'human_source', 'high', ?, 'needs_review', "
            "'current', '2026-10-04', 'day', 'exact', '2026-10-04T19:58:26Z', 'resolved', "
            "'candidate', 0, 1, ?, 'internal', '[\"internal_consulting\",\"nee-engineering\"]', 0, ?, '{}', "
            "?, '[]', '2026-10-04T19:58:26Z', ?, NULL)",
            (aid, fp, subj, pred, obj_node, obj_lit, obj_type, scope, sid, er_id, conf, mat, prio, ltype, now_iso),
        )
        conn.execute(
            "INSERT OR IGNORE INTO assertion_evidence (assertion_id, evidence_id, evidence_relationship, "
            "independent_corroboration, created_at) VALUES (?, ?, 'supports', 0, '2026-10-04T19:58:26Z')",
            (aid, ev_id),
        )
        conn.execute(
            "INSERT OR IGNORE INTO search_document (search_document_id, object_type, object_id, title, body, "
            "canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at) "
            "VALUES (?, 'assertion', ?, ?, ?, 'candidate', 'current', 0, ?, ?, ?)",
            (sdoc_id, aid, scope, scope, sid, aid, now_iso),
        )
    return 3


def _consolidate_nexus_duplicate_sources(conn: Any) -> int:
    merged = 0
    for win, lose in NEXUS_DUPLICATE_SOURCE_PAIRS:
        if not conn.execute("SELECT 1 FROM source WHERE source_id = ?", (lose,)).fetchone():
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
        for sql_stmt, params in (
            ("UPDATE evidence_item SET source_id = ? WHERE source_id = ?", (win, lose)),
            ("UPDATE assertion SET primary_source_id = ? WHERE primary_source_id = ?", (win, lose)),
            ("UPDATE search_document SET source_id = ? WHERE source_id = ?", (win, lose)),
            ("UPDATE audit_event SET source_id = ? WHERE source_id = ?", (win, lose)),
            ("DELETE FROM source WHERE source_id = ?", (lose,)),
        ):
            conn.execute(sql_stmt, params)
        merged += 1
    for sid, ref in NEXUS_ARCHIVE_REFS:
        conn.execute("UPDATE source SET cold_archive_ref = ? WHERE source_id = ?", (ref, sid))
    return merged


def _promote_nexus_candidates(conn: Any, now_iso: str) -> Dict[str, int]:
    conn.execute(_SQL_INSERT_APPROVAL, (now_iso, now_iso))
    ingested_1004 = _ingest_market_trends_1004(conn, now_iso)
    ph = ",".join("?" for _ in NEXUS_CANONICAL_SOURCES)
    cur_curr = conn.execute(
        "UPDATE assertion SET canonical_status = 'canonical', canonical_eligible = 1, "
        "validation_state = 'accepted_legacy', retrieval_eligible = 1, "
        "approval_reference = 'REQ_INGEST_CEC57955C84BCE3A', updated_at = ? "
        "WHERE primary_source_id IN (" + ph + ") AND canonical_status = 'candidate'",
        [now_iso, *NEXUS_CANONICAL_SOURCES],
    )
    conn.execute(
        "UPDATE search_document SET canonical_status = 'canonical', retrieval_eligible = 1, updated_at = ? "
        "WHERE source_id IN (" + ph + ") AND canonical_status = 'candidate'",
        [now_iso, *NEXUS_CANONICAL_SOURCES],
    )
    return {
        "ingested_1004_assertions": ingested_1004,
        "promoted_candidates": cur_curr.rowcount,
        "total_promoted": cur_curr.rowcount,
    }


def promote_nexus_sources_and_assertions(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Consolidate duplicate Nexus sources, ingest 10-04 Market Trends, promote draft assertions, and seal 5-check proof."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "nexus-gap-remediation", now_iso, length=28)
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

    merged_sources = _consolidate_nexus_duplicate_sources(conn)
    promo_stats = _promote_nexus_candidates(conn, now_iso)
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    stats = {"duplicate_sources_merged": merged_sources, **promo_stats}
    sql_audit = (
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'candidate_remediation', 'graph', ?, "
        "NULL, ?, NULL, ?, 'Project Nexus duplicate source merge, 10-04 ingestion, and draft promotion', "
        "'WAVE-3-NEXUS-PROMOTION', 'REQ_INGEST_CEC57955C84BCE3A', 'run-nexus-gap-remediation', '1.0.0')"
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

    ph = ",".join("?" for _ in NEXUS_CANONICAL_SOURCES)
    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_zero_nexus_candidates": (
            f"SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id IN ({ph}) AND canonical_status='candidate') = 0",
            NEXUS_CANONICAL_SOURCES,
        ),
        "probe_market_1004_live": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical' AND canonical_eligible=1) = 3",
            ("source-62cd32261ed5c778fdc62262",),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id, "source-62cd32261ed5c778fdc62262", "source-f6f74a5571077d5a18d4e8ad"],
        expected_deltas={
            "source": 1 - merged_sources,
            "assertion": promo_stats["ingested_1004_assertions"],
            "audit_event": 1,
        },
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:nexus-graph-promotion-receipt:1.0.0",
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


_LENS_0925_SOURCE_ID = "source-c8eb652572b13965218a9fb2"
_LENS_0925_SV_ID = "source-version-8e4c19a7203bd518c44ef012"
_LENS_0925_ER_ID = "extraction-run-9b3012f48e1a6620c14a8891"
_GOOGLE_0925_SOURCE_ID = "source-22cae43d86bde3027606e0eb"
_TRACI_0925_SOURCE_ID = "source-dedcd512fe0e2f591d8e6c4f"
_TRACI_0925_SV_ID = "source-version-51c419f5a2311cab8f67c828"
_GOOGLE_LENS_ENTITY_ID = "entity-f27a0c6e24ea46d4bc90a66a0947"

_EARLY_GOOGLE_MISBOUND_ASSERTIONS: Tuple[str, ...] = (
    "assertion-21b32e04a1145fe6f939f3c65dfb",
    "assertion-55c7d6b486aa20c9cc38b40d80b4",
    "assertion-873b343545d5cb7d0de44d817bb3",
    "assertion-1a8e0c567e70910375e88755247d",
    "assertion-13ec5b607cb4ab50a86d56e772a7",
    "assertion-580485708d0ea6dff8f8bb3a4ad2",
    "assertion-d427caed59d0095c4474023c5eb8",
    "assertion-3b72cf17e79f416040864cc5d059",
    "assertion-0dba0e9ca0e7dc478118de968819",
)


def _apply_0925_split_sql(conn: Any, now_iso: str) -> Dict[str, int]:
    lens_ref = "output/project_lens/09-25-2026/Lens_Escalation_Jen_Amit/01_Lens_Escalation_Jen_Amit_09-25-2026_0923AM_0948AM_ET_hitl_resolved_transcript.md"
    conn.execute(
        "INSERT OR IGNORE INTO source (source_id, source_type, source_name, source_date, authority_rank, "
        "source_posture, cold_archive_ref, notes, created_at) VALUES "
        "(?, 'dual_pipeline_intake', '20260925 Lens Escalation Jen Amit', '2026-09-25', "
        "5, 'derivative_internal', ?, 'Governed split of 09-25-2026 LENS 1:1 escalation segment.', ?)",
        (_LENS_0925_SOURCE_ID, lens_ref, now_iso),
    )
    conn.execute(
        "UPDATE source SET cold_archive_ref = ? WHERE source_id = ?",
        (lens_ref, _LENS_0925_SOURCE_ID),
    )
    conn.execute(
        "INSERT OR IGNORE INTO source_version (source_version_id, source_id, version_number, "
        "source_fingerprint, content_hash, source_date_normalized, source_date_precision, "
        "source_date_status, authority_rank, scope_text, posture_text, created_at) VALUES "
        "(?, ?, 1, 'ab6b6f8ad8c6c8807c5ddbb28c2909d4c7febb32ad7aefc43606ca2aac95ed22', "
        "'ab6b6f8ad8c6c8807c5ddbb28c2909d4c7febb32ad7aefc43606ca2aac95ed22', '2026-09-25', "
        "'day', 'exact', 5, 'Governed 09-25-2026 Project LENS 1:1 escalation split.', 'derivative_internal', ?)",
        (_LENS_0925_SV_ID, _LENS_0925_SOURCE_ID, now_iso),
    )
    conn.execute(
        "INSERT OR IGNORE INTO extraction_run VALUES (?, 'dual-pipeline-structured-adapter-1.0.0', "
        "'ab6b6f8ad8c6c8807c5ddbb28c2909d4c7febb32ad7aefc43606ca2aac95ed22', "
        "'ab6b6f8ad8c6c8807c5ddbb28c2909d4c7febb32ad7aefc43606ca2aac95ed22', ?, ?, 'completed', 1)",
        (_LENS_0925_ER_ID, now_iso, now_iso),
    )

    ph_early = ",".join("?" for _ in _EARLY_GOOGLE_MISBOUND_ASSERTIONS)
    repointed_google = conn.execute(
        f"UPDATE assertion SET subject_node_id = ?, updated_at = ? WHERE assertion_id IN ({ph_early})",
        [_GOOGLE_LENS_ENTITY_ID, now_iso, *_EARLY_GOOGLE_MISBOUND_ASSERTIONS],
    ).rowcount

    ev_lens = conn.execute(
        "UPDATE evidence_item SET source_id = ?, source_version_id = ? "
        "WHERE source_id = ? AND ("
        "  (locator_kind = 'timestamp' AND locator_value >= '00:23:27' AND locator_value <= '00:49:45' AND locator_value != '00:24:45') "
        "  OR (locator_kind = 'section' AND (locator_value LIKE '%| Jen | Jen Pan%' OR locator_value LIKE 'Jen Pan -- Delivery Lead%'))"
        ")",
        (_LENS_0925_SOURCE_ID, _LENS_0925_SV_ID, _GOOGLE_0925_SOURCE_ID),
    ).rowcount

    ev_traci = conn.execute(
        "UPDATE evidence_item SET source_id = ?, source_version_id = ? "
        "WHERE source_id = ? AND locator_kind = 'timestamp' AND locator_value >= '00:53:42'",
        (_TRACI_0925_SOURCE_ID, _TRACI_0925_SV_ID, _GOOGLE_0925_SOURCE_ID),
    ).rowcount

    asrt_traci = conn.execute(
        "UPDATE assertion SET primary_source_id = ?, updated_at = ? "
        "WHERE primary_source_id = ? AND ("
        "  subject_node_id IN ('entity-4b51afc96e42d5686cb30ff131b7', 'entity-c7808e04f0add3cf3751911034a3', "
        "    'entity-487c43902705f2a28c2fc9d05687', 'entity-f4f8ebd228077a7782d5382d1fa6', "
        "    'entity-9a178bfe208e265f5c4b7087ac92', 'entity-de69b2f01f8a93ff005dec4b554b', "
        "    'entity-cc21fe7bd501d406b671ed33f001', 'entity-2318f003409f21352ec0b9d09f64') "
        "  OR object_node_id IN ('entity-c7808e04f0add3cf3751911034a3', 'entity-487c43902705f2a28c2fc9d05687')"
        ")",
        (_TRACI_0925_SOURCE_ID, now_iso, _GOOGLE_0925_SOURCE_ID),
    ).rowcount

    asrt_lens = conn.execute(
        "UPDATE assertion SET primary_source_id = ?, extraction_run_id = ?, updated_at = ? "
        "WHERE primary_source_id = ? AND ("
        "  subject_node_id IN ('person_jen_pan', 'entity-fb874a8b3c9086c399e4f600ca7b', "
        "    'entity-c515af8c5a6dac97d7f6e4b9f582', 'entity-16aed7183bc3b9c1ba66b5040058', "
        "    'entity-de7ce8aebb7b4dc5ca71a6b1a5a5') "
        "  OR (subject_node_id = 'workstream_lens' AND assertion_id != 'assertion-f40cb163a2d64980d1b42f818af6') "
        "  OR EXISTS ("
        "    SELECT 1 FROM assertion_evidence ae JOIN evidence_item ei ON ei.evidence_id = ae.evidence_id "
        "    WHERE ae.assertion_id = assertion.assertion_id AND ei.source_id = ?"
        "  )"
        ") AND assertion_id != 'assertion-e9440dd224c576af8623d9ef5d7b'",
        (_LENS_0925_SOURCE_ID, _LENS_0925_ER_ID, now_iso, _GOOGLE_0925_SOURCE_ID, _LENS_0925_SOURCE_ID),
    ).rowcount

    conn.execute(
        "UPDATE assertion SET subject_node_id = 'entity-95d04ee9d1865ad1764f327bfe81', updated_at = ? "
        "WHERE assertion_id = 'assertion-f40cb163a2d64980d1b42f818af6'",
        (now_iso,),
    )
    conn.execute(
        "UPDATE search_document SET source_id = ("
        "  SELECT a.primary_source_id FROM assertion a WHERE a.assertion_id = search_document.assertion_id"
        "), updated_at = ? "
        "WHERE assertion_id IN (SELECT assertion_id FROM assertion WHERE primary_source_id IN (?, ?, ?))",
        (now_iso, _GOOGLE_0925_SOURCE_ID, _LENS_0925_SOURCE_ID, _TRACI_0925_SOURCE_ID),
    )
    return {
        "repointed_google_assertions": repointed_google,
        "rebound_lens_evidence_items": ev_lens,
        "rebound_traci_evidence_items": ev_traci,
        "rebound_lens_assertions": asrt_lens,
        "rebound_traci_assertions": asrt_traci,
    }


def split_0925_google_lens_traci_sources(
    runtime_root: Optional[Path] = None,
    receipt_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Split 09-25-2026 multi-call source into Partner Alliances, Project LENS 1:1, and Project Jarvis 1:1."""
    rt_root = Path(runtime_root) if runtime_root else resolve_authoritative_runtime_root(ROOT)
    active_pair_path = rt_root / "active_pair.json"
    active_pair = json.loads(active_pair_path.read_text(encoding="utf-8"))
    old_graph_path = (rt_root / active_pair["graph_path"]).resolve()
    old_sidecar_path = (rt_root / active_pair["sidecar_path"]).resolve()
    now_iso = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_gen_id = stable_id("generation", active_pair["generation_id"], "split-0925-google-lens", now_iso, length=28)
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

    src_before = conn.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    stats = _apply_0925_split_sql(conn, now_iso)
    src_after = conn.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    audit_id = f"audit-hygiene-{uuid.uuid4().hex[:20]}"
    conn.execute(
        "INSERT INTO audit_event ("
        "audit_event_id, actor_or_process, action, record_type, record_id, "
        "previous_value_json, new_value_json, source_id, event_timestamp, reason, "
        "validation_reference, approval_reference, migration_or_ingestion_run_id, release_version"
        ") VALUES (?, 'hygiene_agent', 'source_split_remediation', 'source', ?, "
        "NULL, ?, ?, ?, 'Split 09-25-2026 concatenated recording into Google Partner Alliances, LENS 1:1, and Jarvis 1:1', "
        "'WAVE-4-SPLIT-0925', 'REQ_INGEST_CEC57955C84BCE3A', 'run-split-0925-google-lens', '1.0.0')",
        (audit_id, _LENS_0925_SOURCE_ID, json.dumps(stats), _LENS_0925_SOURCE_ID, now_iso),
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

    probes = {
        "probe_audit_event": ("SELECT 1 FROM audit_event WHERE audit_event_id=?", (audit_id,)),
        "probe_lens_source_created": ("SELECT 1 FROM source WHERE source_id=?", (_LENS_0925_SOURCE_ID,)),
        "probe_zero_lens_in_google_source": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND subject_node_id IN ('workstream_lens', 'person_jen_pan')) = 0",
            (_GOOGLE_0925_SOURCE_ID,),
        ),
        "probe_lens_assertions_bound": (
            "SELECT 1 WHERE (SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical') >= 20",
            (_LENS_0925_SOURCE_ID,),
        ),
    }
    proof = prove(
        before=before_snap,
        after=after_snap,
        expected_ids=[audit_id, _LENS_0925_SOURCE_ID, _GOOGLE_0925_SOURCE_ID],
        expected_deltas={"source": src_after - src_before, "audit_event": 1},
        readback_probes=probes,
        strict_delta=False,
    )
    out = {
        "schema": "urn:nee:split-0925-google-lens-receipt:1.0.0",
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

