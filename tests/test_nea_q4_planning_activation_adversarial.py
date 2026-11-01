"""Adversarial & edge-case tests for Wave 4: NEA Q4 Planning High-Signal Canonical Promotion & Physical Proof."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_nea_q4_planning_activation_e2e import _seed_nea_q4_full_activation_sandbox
from tools.apply_graph_hygiene_remediation import (
    FULL_NEA_SOURCE_ID,
    activate_nea_q4_planning_assignments,
)


class TestNeaQ4PlanningActivationAdversarial(unittest.TestCase):
    """Adversarial tests covering boundary payloads, OCR noise rejection, and idempotent re-execution."""

    def test_boundary_missing_database_raises_file_not_found(self) -> None:
        """Category 1 (Boundary payload): Missing SQLite database path fails closed."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            missing_db = Path(tmp) / "does_not_exist.sqlite"
            with self.assertRaises(FileNotFoundError):
                activate_nea_q4_planning_assignments(missing_db)

    def test_invariant_defense_purges_injected_not_specified_ocr_noise(self) -> None:
        """Category 2 (Invariant bypass attempt): Injected 'Not specified' OCR rows are purged, never promoted."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            sandbox_db = Path(tmp) / "sandbox_graph.sqlite"
            _seed_nea_q4_full_activation_sandbox(sandbox_db)

            conn = sqlite3.connect(str(sandbox_db))
            conn.execute("PRAGMA foreign_keys = OFF;")
            conn.execute(
                """INSERT OR IGNORE INTO node (
                    node_id, node_type, canonical_name, display_name, description, status, active, created_at, updated_at
                ) VALUES ('entity-not-specified-noise', 'workstream', 'Not specified', 'Not specified', 'OCR noise', 'candidate', 1, '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z')"""
            )
            er_row = conn.execute("SELECT extraction_run_id FROM extraction_run LIMIT 1").fetchone()
            er_id = er_row[0] if er_row else "extraction-run-9fde51b4dea20a11912d1849be8e"
            if conn.execute("SELECT 1 FROM pragma_table_info('assertion') WHERE name='valid_from'").fetchone():
                conn.execute(
                    """INSERT OR IGNORE INTO assertion (
                        assertion_id, assertion_fingerprint, subject_node_id, predicate, object_node_id,
                        object_literal, object_literal_type, assertion_type, atomicity_state, scope_text,
                        speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                        origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                        valid_from, date_precision, date_status, recorded_at, resolution_state,
                        canonical_status, canonical_eligible, retrieval_eligible, materiality,
                        classification, allowed_audiences_json, hitl_required, validation_priority,
                        qualifiers_json, legacy_record_type, legacy_record_ids_json, created_at, updated_at
                    ) VALUES (
                        'assertion-injected-not-specified', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                        'entity-not-specified-noise', 'HAS_CLAIM_TEXT', NULL, 'Not specified', 'text',
                        'extracted_claim', 'atomic',
                        'Not specified is an identified workstream in Document 20261020 Nea Q4 Planning Assignments.',
                        'NEA Q4 Planning Dashboard', ?, ?, 'dual_pipeline_structured_adapter', 'human_source',
                        'low', 0.40, 'needs_review', 'current', '2026-10-20', 'day', 'exact',
                        '2026-10-22T05:03:18Z', 'resolved', 'candidate', 0, 0, 'medium', 'internal',
                        '["internal_consulting"]', 0, 'P1', '{}', 'claim', '[]',
                        '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z'
                    )""",
                    (FULL_NEA_SOURCE_ID, er_id),
                )
            else:
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
                        'assertion-injected-not-specified', 'fp-assertion-injected-not-specified', 'extracted_claim', 'entity-not-specified-noise', 'HAS_CLAIM_TEXT', NULL, 'Not specified', 'text',
                        'Not specified is an identified workstream in Document 20261020 Nea Q4 Planning Assignments.',
                        'atomic', 'dual_pipeline_intake', 'human_source', 'low', 0.40,
                        'needs_review', 'current', 'day', 'exact',
                        'resolved', 'candidate', 0, 0,
                        'medium', ?, 'restricted', '["internal_consulting"]',
                        0, '{}', 'claim', '[]',
                        '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z', '2026-10-22T05:03:18Z'
                    )""",
                    (FULL_NEA_SOURCE_ID,),
                )
            conn.commit()
            conn.close()

            receipt = activate_nea_q4_planning_assignments(sandbox_db)
            self.assertEqual(receipt["terminal_outcome"], "PASS")
            self.assertGreaterEqual(receipt["remediation_stats"]["purged_noise_and_stub_assertions"], 1)

            conn_ro = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            rem_noise = conn_ro.execute(
                "SELECT COUNT(*) FROM assertion WHERE assertion_id='assertion-injected-not-specified'"
            ).fetchone()[0]
            self.assertEqual(rem_noise, 0)
            conn_ro.close()

    def test_idempotent_second_invocation_preserves_canonical_counts_and_sidecar_parity(self) -> None:
        """Category 3 (Rollback & recovery / Idempotency): Second invocation produces zero drift and preserves sidecar parity."""
        with tempfile.TemporaryDirectory(prefix="eval_sandbox_") as tmp:
            sandbox_db = Path(tmp) / "sandbox_graph.sqlite"
            _seed_nea_q4_full_activation_sandbox(sandbox_db)

            r1 = activate_nea_q4_planning_assignments(sandbox_db)
            self.assertEqual(r1["terminal_outcome"], "PASS")

            conn_ro1 = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            c1 = conn_ro1.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical'",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            conn_ro1.close()

            r2 = activate_nea_q4_planning_assignments(sandbox_db)
            self.assertEqual(r2["terminal_outcome"], "PASS")
            self.assertEqual(r2["remediation_stats"]["purged_noise_and_stub_assertions"], 0)
            self.assertEqual(r2["remediation_stats"]["promoted_canonical_assertions"], 0)
            self.assertEqual(r2["remediation_stats"]["open_role_assertions_added"], 0)

            conn_ro2 = sqlite3.connect(f"file:{sandbox_db}?mode=ro", uri=True)
            c2 = conn_ro2.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND canonical_status='canonical'",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(c2, c1)
            open_roles_c2 = conn_ro2.execute(
                "SELECT COUNT(*) FROM assertion WHERE primary_source_id=? AND predicate='CANDIDATE_FOR' AND canonical_status='canonical'",
                (FULL_NEA_SOURCE_ID,),
            ).fetchone()[0]
            self.assertEqual(open_roles_c2, 44)
            conn_ro2.close()


if __name__ == "__main__":
    unittest.main()
