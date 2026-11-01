"""End-to-end tests for Wave 4: NEA Q4 Planning High-Signal Canonical Promotion & 5-Check Physical Proof Activation."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_nea_planning_dedup_hygiene_e2e import _seed_sandbox_with_partial_and_full_nea_sources
from tools.apply_graph_hygiene_remediation import (
    FULL_NEA_SOURCE_ID,
    PARTIAL_NEA_SOURCE_ID,
    UNRESOLVED_JAKE_WELLINGTON_ID,
    activate_nea_q4_planning_assignments,
)


def _seed_nea_q4_full_activation_sandbox(db_path: Path) -> None:
    """Seed sandbox with partial & full NEA sources plus representative candidate nodes, stubs, and relationship assertions."""
    _seed_sandbox_with_partial_and_full_nea_sources(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = OFF;")
    s2_rel_count = conn.execute(
        "SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND object_node_id IS NOT NULL",
        (FULL_NEA_SOURCE_ID,),
    ).fetchone()[0]
    if s2_rel_count < 10:
        for idx in range(5):
            ws_id = f"entity-nea-ws-seed-{idx}"
            st_id = f"entity-nea-st-seed-{idx}"
            conn.execute(
                """INSERT OR IGNORE INTO node (
                    node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
                ) VALUES (?, 'workstream', ?, ?, 'Seeded NEA workstream', 'candidate', 1, '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z')""",
                (ws_id, f"NEA Seeded Workstream {idx}", f"NEA Seeded Workstream {idx}"),
            )
            conn.execute(
                """INSERT OR IGNORE INTO node (
                    node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
                ) VALUES (?, 'stakeholder', ?, ?, 'Seeded NEA stakeholder', 'candidate', 1, '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z')""",
                (st_id, f"NEA Stakeholder {idx}", f"NEA Stakeholder {idx}"),
            )
            ev_id = f"ev-s2-seed-{idx}"
            conn.execute(
                """INSERT OR IGNORE INTO evidence_item (
                    evidence_id, evidence_fingerprint, source_id, locator_kind, locator_value, direct_quote, evidence_directness, source_authority_rank, extraction_confidence, location_status, created_at
                ) VALUES (?, ?, ?, 'section', '## Page 1', 'Seeded full row', 'direct', 5, 0.90, 'verified', '2026-10-22T05:03:18Z')""",
                (ev_id, f"fp-{ev_id}", FULL_NEA_SOURCE_ID),
            )
            rel_aid = f"assertion-s2-rel-seed-{idx}"
            conn.execute(
                """INSERT OR IGNORE INTO assertion (
                    assertion_id, assertion_fingerprint, assertion_type, subject_node_id, predicate, object_node_id, scope_text,
                    atomicity_state, extraction_method, origin_type, confidence_label, confidence_score,
                    validation_state, currentness_state, date_precision, date_status,
                    resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                    materiality, primary_source_id, classification, allowed_audiences_json,
                    hitl_required, qualifiers_json, legacy_record_type, legacy_record_ids_json,
                    recorded_at, created_at, updated_at
                ) VALUES (
                    ?, ?, 'extracted_claim', ?, 'AFFILIATED_WITH', ?,
                    ?, 'atomic', 'dual_pipeline_intake', 'human_source', 'high', 0.90,
                    'needs_review', 'current', 'day', 'exact',
                    'resolved', 'candidate', 0, 0,
                    'medium', ?, 'restricted', '["internal_consulting"]',
                    0, '{}', 'claim', '[]',
                    '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z'
                )""",
                (rel_aid, f"fp-{rel_aid}", st_id, ws_id, f"NEA Stakeholder {idx} AFFILIATED_WITH NEA Seeded Workstream {idx}.", FULL_NEA_SOURCE_ID),
            )
            conn.execute(
                """INSERT OR IGNORE INTO assertion_evidence (
                    assertion_id, evidence_id, evidence_relationship, created_at
                ) VALUES (?, ?, 'supports', '2026-10-22T05:03:18Z')""",
                (rel_aid, ev_id),
            )
            stub_aid = f"assertion-s2-stub-seed-{idx}"
            conn.execute(
                """INSERT OR IGNORE INTO assertion (
                    assertion_id, assertion_fingerprint, assertion_type, subject_node_id, predicate, object_node_id, object_literal, object_literal_type, scope_text,
                    atomicity_state, extraction_method, origin_type, confidence_label, confidence_score,
                    validation_state, currentness_state, date_precision, date_status,
                    resolution_state, canonical_status, canonical_eligible, retrieval_eligible,
                    materiality, primary_source_id, classification, allowed_audiences_json,
                    hitl_required, qualifiers_json, legacy_record_type, legacy_record_ids_json,
                    recorded_at, created_at, updated_at
                ) VALUES (
                    ?, ?, 'extracted_claim', ?, 'HAS_CLAIM_TEXT', NULL, 'identified workstream', 'text',
                    ?, 'atomic', 'dual_pipeline_intake', 'human_source', 'high', 0.85,
                    'needs_review', 'current', 'day', 'exact',
                    'resolved', 'candidate', 0, 0,
                    'medium', ?, 'restricted', '["internal_consulting"]',
                    0, '{}', 'claim', '[]',
                    '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z'
                )""",
                (stub_aid, f"fp-{stub_aid}", ws_id, f"NEA Seeded Workstream {idx} is an identified workstream in Document 20261020 Nea Q4 Planning Assignments.", FULL_NEA_SOURCE_ID),
            )
            conn.execute(
                """INSERT OR IGNORE INTO assertion_evidence (
                    assertion_id, evidence_id, evidence_relationship, created_at
                ) VALUES (?, ?, 'supports', '2026-10-22T05:03:18Z')""",
                (stub_aid, ev_id),
            )
    conn.commit()
    conn.close()


class TestNeaQ4PlanningActivationE2E(unittest.TestCase):
    """End-to-end verification of NEA Q4 Planning canonical activation and 5-check physical proof."""

    def test_activate_nea_q4_planning_assignments_and_5check_proof(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            tmp_p = Path(tmp)
            sandbox_db = tmp_p / "sandbox_graph.sqlite"
            receipt_file = tmp_p / "wave4_activation_receipt.json"
            _seed_nea_q4_full_activation_sandbox(sandbox_db)

            receipt = activate_nea_q4_planning_assignments(sandbox_db, receipt_file)
            self.assertEqual(receipt["terminal_outcome"], "PASS")
            proof = receipt["physical_proof"]
            checks_by_name = {c["check"]: c for c in proof["checks"]}
            self.assertEqual(checks_by_name["file_changed"]["status"], "PASS")
            self.assertEqual(checks_by_name["raw_bytes"]["status"], "PASS")
            self.assertEqual(checks_by_name["integrity"]["status"], "PASS")
            self.assertEqual(checks_by_name["readback"]["status"], "PASS")
            self.assertEqual(checks_by_name["row_delta"]["status"], "PASS")

            conn_ro = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            # 1. Partial source retired with 0 remaining assertions
            s1_rem = conn_ro.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=?",
                (PARTIAL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(s1_rem, 0)

            # 2. Zero 'Not specified' assertions or redundant connected stub assertions
            not_spec = conn_ro.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND lower(coalesce(scope_text,'')) LIKE '%not specified%'",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(not_spec, 0)

            # 3. Zero remaining candidate nodes linked to FULL_NEA_SOURCE_ID
            rem_cand_nodes = conn_ro.execute(
                """SELECT COUNT(*) FROM node
                    WHERE status='candidate'
                      AND node_id IN (
                          SELECT subject_node_id FROM assertion WHERE primary_source_id=?
                          UNION
                          SELECT object_node_id FROM assertion WHERE primary_source_id=? AND object_node_id IS NOT NULL
                      )""",
                (FULL_NEA_SOURCE_ID, FULL_NEA_SOURCE_ID),
            ).fetchone()[0]
            self.assertEqual(rem_cand_nodes, 0)

            # 4. All remaining current assertions for FULL_NEA_SOURCE_ID are canonical and retrieval_eligible=1
            non_canon_asrt = conn_ro.execute(
                """SELECT COUNT(*) FROM assertion
                    WHERE primary_source_id=?
                      AND currentness_state='current'
                      AND (canonical_status != 'canonical' OR retrieval_eligible != 1)""",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(non_canon_asrt, 0)

            canon_asrt_count = conn_ro.execute(
                """SELECT COUNT(*) FROM assertion
                    WHERE primary_source_id=?
                      AND currentness_state='current'
                      AND canonical_status='canonical'
                      AND retrieval_eligible=1""",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertGreater(canon_asrt_count, 0)

            # 5. Sidecar parity on search_document
            sd_count = conn_ro.execute(
                """SELECT COUNT(*) FROM search_document sd
                     JOIN assertion a ON a.assertion_id = sd.assertion_id
                    WHERE a.primary_source_id=?
                      AND a.currentness_state='current'
                      AND sd.canonical_status='canonical'
                      AND sd.retrieval_eligible=1""",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(sd_count, canon_asrt_count)

            # 6. Placeholder Jake Wellington merged
            unres_row = conn_ro.execute(
                "SELECT active, status FROM node WHERE node_id=?",
                (UNRESOLVED_JAKE_WELLINGTON_ID,),
            ).fetchone()
            self.assertEqual(tuple(unres_row), (0, "merged"))

            # 7. All 44 open/future-hire role slots ingested and promoted to canonical CANDIDATE_FOR assertions
            open_role_edges = conn_ro.execute(
                """SELECT COUNT(*) FROM assertion
                    WHERE primary_source_id=?
                      AND predicate='CANDIDATE_FOR'
                      AND canonical_status='canonical'
                      AND retrieval_eligible=1""",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(open_role_edges, 44)

            # 8. NEA JARVIS workstream node present and canonical with charter mission & $284K NAV
            jarvis_row = conn_ro.execute(
                "SELECT status, active, description FROM node WHERE node_id='workstream_nea_jarvis'"
            ).fetchone()
            self.assertIsNotNone(jarvis_row)
            self.assertEqual(jarvis_row[0], "canonical")
            self.assertEqual(jarvis_row[1], 1)
            self.assertIn("$284K NAV", jarvis_row[2])

            ic = conn_ro.execute("PRAGMA integrity_check").fetchone()[0]
            self.assertEqual(ic, "ok")
            conn_ro.close()


if __name__ == "__main__":
    unittest.main()
