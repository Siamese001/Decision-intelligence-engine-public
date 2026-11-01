"""End-to-end tests for NEA Q4 Planning Obsolete Partial Source Retirement & Placeholder Identity Reconciliation."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.apply_graph_hygiene_remediation import (
    CANONICAL_JACOB_WELLINGTON_ID,
    FULL_NEA_SOURCE_ID,
    PARTIAL_NEA_SOURCE_ID,
    UNRESOLVED_JAKE_WELLINGTON_ID,
    retire_nea_planning_partial_duplicates,
)


def _seed_sandbox_with_partial_and_full_nea_sources(db_path: Path) -> None:
    """Seed an isolated SQLite sandbox from the active generation or canonical graph and ensure s1, s2, and Jake Wellington exist."""
    repo_root = Path(__file__).resolve().parent.parent
    main_root = repo_root.parent.parent if repo_root.parent.name == ".worktrees" else repo_root
    src_db = repo_root / "graph" / "nee_graph.sqlite"
    for base in (repo_root, main_root):
        ap_file = base / ".local" / "dual_pipeline" / "active_pair.json"
        if ap_file.is_file():
            ap = json.loads(ap_file.read_text(encoding="utf-8"))
            cand_db = base / ".local" / "dual_pipeline" / str(ap.get("graph_path") or "")
            if cand_db.is_file():
                src_db = cand_db
                break

    conn_src = sqlite3.connect(str(src_db))
    conn_dst = sqlite3.connect(str(db_path))
    conn_src.backup(conn_dst)
    conn_src.close()

    # Ensure both sources and the 4 overlapping contradiction assertions exist even if running against base graph/nee_graph.sqlite
    conn_dst.execute("PRAGMA foreign_keys = OFF;")
    conn_dst.execute(
        """INSERT OR REPLACE INTO source (
            source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at
        ) VALUES (?, 'dual_pipeline_intake', 'Document 20261020 Nea Team Assignments', '2026-10-20', 5, 'derivative_internal', '.staging/source.txt', 'Partial ingestion', '2026-10-21T20:44:58Z')""",
        (PARTIAL_NEA_SOURCE_ID,),
    )
    conn_dst.execute(
        """INSERT OR IGNORE INTO source (
            source_id, source_type, source_name, source_date, authority_rank, source_posture, cold_archive_ref, notes, created_at
        ) VALUES (?, 'dual_pipeline_intake', 'Document 20261020 Nea Q4 Planning Assignments', '2026-10-20', 5, 'derivative_internal', '.staging/source.txt', 'Full ingestion', '2026-10-22T05:03:18Z')""",
        (FULL_NEA_SOURCE_ID,),
    )
    conn_dst.execute(
        """INSERT OR IGNORE INTO node (
            node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
        ) VALUES (?, 'stakeholder', 'Jacob Wellington', 'Jacob Wellington', 'AI Software Engineering Leader', 'confirmed canonical stakeholder', 1, '2026-07-09T12:30:00Z', '2026-07-09T12:30:00Z')""",
        (CANONICAL_JACOB_WELLINGTON_ID,),
    )
    conn_dst.execute(
        """INSERT OR REPLACE INTO node (
            node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
        ) VALUES (?, 'stakeholder', 'Jake Wellington', 'Jake Wellington', 'Placeholder roster mention', 'unresolved identity; candidate mapping only', 1, '2026-07-11T00:34:03Z', '2026-07-11T00:34:03Z')""",
        (UNRESOLVED_JAKE_WELLINGTON_ID,),
    )
    conn_dst.execute(
        """INSERT OR IGNORE INTO node (
            node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
        ) VALUES ('entity-cb65b74bc905e5558acdf805e85d', 'workstream', 'Conduit Q4: Execution (PF4)', 'Conduit Q4: Execution (PF4)', 'Conduit Q4 workstream', 'candidate', 1, '2026-10-21T20:44:58Z', '2026-10-21T20:44:58Z')"""
    )

    s1_count = conn_dst.execute(
        "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
        (PARTIAL_NEA_SOURCE_ID,),
    ).fetchone()[0]
    if s1_count == 0:
        for i, aid in enumerate(
            [
                "assertion-bc7e2cf3c88f559869606b0f0cf9",
                "assertion-978b530ce4ec71b0d2020cd7444b",
                "assertion-d4913d2ba85d57cf12372983b8ad",
                "assertion-63beb4575be28dbcba95d9f45593",
            ]
        ):
            ev_id = f"ev-s1-seed-{i}"
            ev_fp = f"{i+100:064x}"
            afp = f"{i+200:064x}"
            er_row = conn_dst.execute("SELECT extraction_run_id FROM extraction_run LIMIT 1").fetchone()
            er_id = er_row[0] if er_row else "extraction-run-9fde51b4dea20a11912d1849be8e"
            sv_row = conn_dst.execute("SELECT source_version_id FROM source_version LIMIT 1").fetchone()
            if conn_dst.execute("SELECT 1 FROM pragma_table_info('evidence_item') WHERE name='source_version_id'").fetchone() and sv_row:
                conn_dst.execute(
                    """INSERT OR REPLACE INTO evidence_item (
                        evidence_id, evidence_fingerprint, source_id, source_version_id, locator_kind, locator_value, direct_quote, evidence_directness, source_authority_rank, extraction_confidence, location_status, created_at
                    ) VALUES (?, ?, ?, ?, 'section', '## Page 1', 'Seeded partial row', 'direct', 5, 0.85, 'exact', '2026-10-21T20:44:58Z')""",
                    (ev_id, ev_fp, PARTIAL_NEA_SOURCE_ID, sv_row[0]),
                )
            else:
                conn_dst.execute(
                    """INSERT OR REPLACE INTO evidence_item (
                        evidence_id, evidence_fingerprint, source_id, locator_kind, locator_value, direct_quote, evidence_directness, source_authority_rank, extraction_confidence, location_status, created_at
                    ) VALUES (?, ?, ?, 'section', '## Page 1', 'Seeded partial row', 'direct', 5, 0.85, 'verified', '2026-10-21T20:44:58Z')""",
                    (ev_id, ev_fp, PARTIAL_NEA_SOURCE_ID),
                )
            if conn_dst.execute("SELECT 1 FROM pragma_table_info('assertion') WHERE name='valid_from'").fetchone():
                conn_dst.execute(
                    """INSERT OR REPLACE INTO assertion (
                        assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id,
                        object_literal, object_literal_type, assertion_type, atomicity_state, scope_text,
                        speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                        origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                        valid_from, date_precision, date_status, recorded_at, resolution_state,
                        canonical_status, canonical_eligible, retrieval_eligible, materiality,
                        classification, allowed_audiences_json, hitl_required, validation_priority,
                        qualifiers_json, legacy_record_type, legacy_record_ids_json, created_at, updated_at
                    ) VALUES (
                        ?, ?, ?, 'AFFILIATED_WITH', 'entity-cb65b74bc905e5558acdf805e85d',
                        NULL, NULL, 'extracted_claim', 'atomic',
                        'Jake Wellington MEMBER_OF Conduit Q4: Execution (PF4).',
                        'NEA Q4 Planning Dashboard', ?, ?, 'dual_pipeline_structured_adapter', 'human_source',
                        'high', 0.85, 'needs_review', 'current', '2026-10-20', 'day', 'exact',
                        '2026-10-21T20:44:58Z', 'resolved', 'candidate', 0, 0, 'medium', 'internal',
                        '["internal_consulting"]', 0, 'P1', '{}', 'edge', '[]',
                        '2026-10-21T20:44:58Z', '2026-10-21T20:44:58Z'
                    )""",
                    (aid, afp, UNRESOLVED_JAKE_WELLINGTON_ID, PARTIAL_NEA_SOURCE_ID, er_id),
                )
            else:
                conn_dst.execute(
                    """INSERT OR REPLACE INTO assertion (
                        assertion_id, assertion_fingerprint, assertion_type, subject_node_id, predicate, object_node_id, scope_text,
                        atomicity_state, extraction_method, origin_type, confidence_label, confidence_score,
                        validation_state, currentness_state, date_precision, date_status,
                        resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                        materiality, primary_source_id, classification, allowed_audiences_json,
                        hitl_required, qualifiers_json, legacy_record_type, legacy_record_ids_json,
                        recorded_at, created_at, updated_at
                    ) VALUES (
                        ?, ?, 'extracted_claim', ?, 'AFFILIATED_WITH', 'entity-cb65b74bc905e5558acdf805e85d',
                        'Jake Wellington MEMBER_OF Conduit Q4: Execution (PF4).',
                        'atomic', 'dual_pipeline_intake', 'human_source', 'high', 0.85,
                        'needs_review', 'current', 'day', 'exact',
                        'resolved', 'candidate', 0, 0,
                        'medium', ?, 'restricted', '["internal_consulting"]',
                        0, '{}', 'claim', '[]',
                        '2026-10-21T20:44:58Z', '2026-10-21T20:44:58Z', '2026-10-21T20:44:58Z'
                    )""",
                    (aid, f"fp-{aid}", UNRESOLVED_JAKE_WELLINGTON_ID, PARTIAL_NEA_SOURCE_ID),
                )
            if conn_dst.execute("SELECT 1 FROM pragma_table_info('assertion_evidence') WHERE name='independent_corroboration'").fetchone():
                conn_dst.execute(
                    """INSERT OR REPLACE INTO assertion_evidence (
                        assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at
                    ) VALUES (?, ?, 'supports', 0, '2026-10-21T20:44:58Z')""",
                    (aid, ev_id),
                )
            else:
                conn_dst.execute(
                    """INSERT OR REPLACE INTO assertion_evidence (
                        assertion_id, evidence_id, evidence_relationship, created_at
                    ) VALUES (?, ?, 'supports', '2026-10-21T20:44:58Z')""",
                    (aid, ev_id),
                )
            conn_dst.execute(
                """INSERT OR REPLACE INTO search_document (
                    search_document_id, object_type, object_id, title, body,
                    canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at
                ) VALUES (?, 'assertion', ?, 'Seeded title', 'Seeded body', 'candidate', 'current', 0, ?, ?, '2026-10-21T20:44:58Z')""",
                (aid, aid, PARTIAL_NEA_SOURCE_ID, aid),
            )
        # Ensure s2 also has a Jake Wellington assertion that gets repointed to canonical Jacob Wellington
        if conn_dst.execute("SELECT 1 FROM pragma_table_info('assertion') WHERE name='valid_from'").fetchone():
            conn_dst.execute(
                """INSERT OR REPLACE INTO assertion (
                    assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id,
                    object_literal, object_literal_type, assertion_type, atomicity_state, scope_text,
                    speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                    origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                    valid_from, date_precision, date_status, recorded_at, resolution_state,
                    canonical_status, canonical_eligible, retrieval_eligible, materiality,
                    classification, allowed_audiences_json, hitl_required, validation_priority,
                    qualifiers_json, legacy_record_type, legacy_record_ids_json, created_at, updated_at
                ) VALUES (
                    'assertion-s2-jw-conduit', 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                    ?, 'AFFILIATED_WITH', 'entity-cb65b74bc905e5558acdf805e85d',
                    NULL, NULL, 'extracted_claim', 'atomic',
                    'Jake Wellington MEMBER_OF Conduit Q4: Execution (PF4).',
                    'NEA Q4 Planning Dashboard', ?, ?, 'dual_pipeline_structured_adapter', 'human_source',
                    'high', 0.85, 'needs_review', 'current', '2026-10-20', 'day', 'exact',
                    '2026-10-22T05:03:18Z', 'resolved', 'candidate', 0, 0, 'medium', 'internal',
                    '["internal_consulting"]', 0, 'P1', '{}', 'edge', '[]',
                    '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z'
                )""",
                (UNRESOLVED_JAKE_WELLINGTON_ID, FULL_NEA_SOURCE_ID, er_id),
            )
            conn_dst.execute(
                """INSERT OR IGNORE INTO assertion_evidence (
                    assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at
                ) VALUES ('assertion-s2-jw-conduit', 'ev-s1-seed-0', 'supports', 0, '2026-10-22T05:03:18Z')"""
            )
            conn_dst.execute(
                """INSERT OR IGNORE INTO search_document (
                    search_document_id, object_type, object_id, title, body,
                    canonical_status, currentness_state, retrieval_eligible, source_id, assertion_id, updated_at
                ) VALUES ('assertion-s2-jw-conduit', 'assertion', 'assertion-s2-jw-conduit', 'Seeded s2 title', 'Seeded s2 body', 'candidate', 'current', 0, ?, 'assertion-s2-jw-conduit', '2026-10-22T05:03:18Z')""",
                (FULL_NEA_SOURCE_ID,),
            )
        else:
            conn_dst.execute(
                """INSERT OR REPLACE INTO assertion (
                    assertion_id, assertion_fingerprint, assertion_type, subject_node_id, predicate, object_node_id, scope_text,
                    atomicity_state, extraction_method, origin_type, confidence_label, confidence_score,
                    validation_state, currentness_state, date_precision, date_status,
                    resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                    materiality, primary_source_id, classification, allowed_audiences_json,
                    hitl_required, qualifiers_json, legacy_record_type, legacy_record_ids_json,
                    recorded_at, created_at, updated_at
                ) VALUES (
                    'assertion-s2-jw-conduit', 'fp-assertion-s2-jw-conduit', 'extracted_claim', ?, 'AFFILIATED_WITH', 'entity-cb65b74bc905e5558acdf805e85d',
                    'Jake Wellington MEMBER_OF Conduit Q4: Execution (PF4).',
                    'atomic', 'dual_pipeline_intake', 'human_source', 'high', 0.85,
                    'needs_review', 'current', 'day', 'exact',
                    'resolved', 'candidate', 0, 0,
                    'medium', ?, 'restricted', '["internal_consulting"]',
                    0, '{}', 'claim', '[]',
                    '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z'
                )""",
                (UNRESOLVED_JAKE_WELLINGTON_ID, FULL_NEA_SOURCE_ID),
            )
            conn_dst.execute(
                """INSERT OR IGNORE INTO assertion_evidence (
                    assertion_id, evidence_id, evidence_relationship, created_at
                ) VALUES ('assertion-s2-jw-conduit', 'ev-s1-seed-0', 'supports', '2026-10-22T05:03:18Z')"""
            )
    conn_dst.commit()
    conn_dst.close()


class TestNeaPlanningDedupHygieneE2E(unittest.TestCase):
    """End-to-end verification of partial NEA planning source retirement and placeholder identity merge."""

    def test_retire_nea_planning_partial_duplicates_and_5check_proof(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_p = Path(tmp)
            sandbox_db = tmp_p / "sandbox_graph.sqlite"
            receipt_file = tmp_p / "wave2_dedup_receipt.json"
            _seed_sandbox_with_partial_and_full_nea_sources(sandbox_db)

            conn_pre = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            pre_s1 = conn_pre.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
                (PARTIAL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertGreater(pre_s1, 0)
            conn_pre.close()

            receipt = retire_nea_planning_partial_duplicates(sandbox_db, receipt_file)
            self.assertEqual(receipt["terminal_outcome"], "PASS")
            proof = receipt["physical_proof"]
            checks_by_name = {c["check"]: c for c in proof["checks"]}
            self.assertEqual(checks_by_name["file_changed"]["status"], "PASS")
            self.assertEqual(checks_by_name["raw_bytes"]["status"], "PASS")
            self.assertEqual(checks_by_name["integrity"]["status"], "PASS")
            self.assertEqual(checks_by_name["readback"]["status"], "PASS")
            self.assertEqual(checks_by_name["row_delta"]["status"], "PASS")

            # Independent read-only verification
            conn_ro = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            post_s1_asrt = conn_ro.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
                (PARTIAL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            post_s1_sd = conn_ro.execute(
                "SELECT COUNT(*) FROM search_document WHERE source_id=?",
                (PARTIAL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(post_s1_asrt, 0)
            self.assertEqual(post_s1_sd, 0)

            # Placeholder node must be merged and redirected to canonical stakeholder
            unres_active = conn_ro.execute(
                "SELECT active, status FROM node WHERE node_id=?",
                (UNRESOLVED_JAKE_WELLINGTON_ID,),
            ).fetchone()
            self.assertEqual(tuple(unres_active), (0, "merged"))

            redirect = conn_ro.execute(
                "SELECT to_node_id, active FROM entity_redirect WHERE from_node_id=?",
                (UNRESOLVED_JAKE_WELLINGTON_ID,),
            ).fetchone()
            self.assertIsNotNone(redirect)
            self.assertEqual(redirect[1], 1)

            # Zero remaining assertions referencing unresolved_jake_wellington_roster_2026_07_11
            dangling_jw = conn_ro.execute(
                "SELECT COUNT(*) FROM assertion WHERE (subject_node_id=? OR object_node_id=?) AND currentness_state='current'",
                (UNRESOLVED_JAKE_WELLINGTON_ID, UNRESOLVED_JAKE_WELLINGTON_ID),
            ).fetchone()[0]
            self.assertEqual(dangling_jw, 0)

            ic = conn_ro.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(ic, "ok")
            conn_ro.close()


if __name__ == "__main__":
    unittest.main()
