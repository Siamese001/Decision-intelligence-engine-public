"""E2E test suite for assumption retrieval quarantine, counterfactual routing, and intake sealing.

Verifies:
1. Expired assumptions (past valid_to or qualifiers.stale_after) are excluded from:
   - render_retrieval_records()
   - render_evidence_passage_records()
   - render_proposition_records()
2. Active exploratory assumptions are quarantined to lane='candidate' with answer_use='citation_only'
   and extensions['com.partner_advisory.nee.epistemic-posture'] = 'unconfirmed_assumption'.
3. Rejected options (DECIDED_AGAINST / REJECTED) retain counterfactual queryability as
   lane='candidate' with answer_use='strategic_context', satisfying Gate 30 approved_lane_purity_pi >= 0.95.
4. Governed 2026-09-04 Project Beacon intake packages validate and contain sealed temporal/decision closures.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import ROOT, sha256_bytes, utc_now
from dual_pipeline.index_core.records import render_retrieval_records
from dual_pipeline.index_core.unit_renderers import (
    render_evidence_passage_records,
    render_proposition_records,
)


class TestAssumptionRetrievalQuarantineE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        active_pair_path = ROOT / ".local" / "dual_pipeline" / "active_pair.json"
        if active_pair_path.exists():
            with open(active_pair_path, "r", encoding="utf-8") as f:
                pair = json.load(f)
            self.orig_graph = ROOT / ".local" / "dual_pipeline" / pair["graph_path"]
        else:
            candidates = list((ROOT / ".local" / "dual_pipeline" / "generations").glob("generation-*/graph.sqlite"))
            if not candidates:
                self.skipTest("No baseline generation graph found")
            self.orig_graph = candidates[0]
        self.db_path = Path(self.tmp_dir.name) / "test_retrieval_quarantine.sqlite"
        shutil.copy2(self.orig_graph, self.db_path)

        # Ensure decision predicates are registered in assertion_predicate_registry
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        for pred, obj_kind in [("DECIDED_AGAINST", "entity"), ("REJECTED", "entity"), ("STANDARDIZES_ON", "entity")]:
            cur.execute(
                """INSERT OR IGNORE INTO assertion_predicate_registry (
                    predicate, object_kind, cardinality, evidence_required,
                    temporal_requirement, symmetric_mode, acyclic_flag,
                    conflict_policy, active, definition, created_at
                ) VALUES (?, ?, 'many_to_many', 1, 'optional', 'never', 0, 'explicit_only', 1, 'Decision governance predicate', ?)""",
                (pred, obj_kind, "2026-09-04T14:00:00Z"),
            )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _seed_supporting_evidence(self, conn: sqlite3.Connection, assertion_id: str, evidence_id: str, quote: str) -> None:
        """Seed evidence item and link to assertion."""
        now = utc_now()
        cur = conn.cursor()
        row = cur.execute("SELECT source_id, source_version_id FROM evidence_item LIMIT 1").fetchone()
        source_id = row[0] if row else "source-005c48248556e860fd8fbebc"
        source_version_id = row[1] if row else "source-version-54978435f5bc92117ab99a89"
        fp = sha256_bytes(quote.encode("utf-8"))
        cur.execute(
            """INSERT OR IGNORE INTO evidence_item (
                evidence_id, evidence_fingerprint, source_id, source_version_id, locator_kind, locator_value,
                section_name, direct_quote, evidence_directness, source_authority_rank, extraction_confidence,
                location_status, created_at
            ) VALUES (?, ?, ?, ?, 'section', 'Executive Summary', 'Executive Summary', ?, 'direct_quote', 5, 1.0, 'exact', ?)""",
            (evidence_id, fp, source_id, source_version_id, quote, now),
        )
        cur.execute(
            """INSERT OR IGNORE INTO assertion_evidence (
                assertion_id, evidence_id, evidence_relationship, independent_corroboration, created_at
            ) VALUES (?, ?, 'supports', 0, ?)""",
            (assertion_id, evidence_id, now),
        )

    def test_expired_assumptions_excluded_from_retrieval_records_and_passages(self) -> None:
        """Verify expired unconfirmed assumptions cannot leak into sidecar passages or propositions."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        now = utc_now()
        rec_at = "2026-09-04T14:00:00Z"

        # 1. Expired assumption: valid_to in the past (2026-09-09)
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, object_literal_type, assertion_type, atomicity_state,
                scope_text, speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                valid_from, valid_to, date_precision, date_status, recorded_at, superseded_at,
                superseded_by_assertion_id, resolution_state, canonical_status, canonical_eligible,
                retrieval_eligible, materiality, classification, allowed_audiences_json, hitl_required,
                validation_priority, validation_owner, qualifiers_json, legacy_record_type,
                legacy_record_ids_json, created_at, updated_at, approval_reference
            ) VALUES (
                'asrt_expired_gcp_assumption', ?, 'entity-1fbe770a467b00e9e7df9c2c7349',
                'RELATED_TO', NULL, 'entity-9a178bfe208e265f5c4b7087ac92', NULL, NULL,
                'assumption', 'atomic', 'GCP parallel experiment', 'Drew Oakley',
                'src_test_intake', 'run_test_01', 'structured', 'human_source', 'medium', 0.65,
                'needs_review', 'current', '2026-09-04', '2026-09-09', 'day', 'exact',
                ?, NULL, NULL, 'resolved', 'candidate', 0, 1,
                'medium', 'internal', '["internal_consulting"]', 0, NULL, NULL,
                '{"stale_after": "2026-09-09"}', 'edge', '[]', ?, ?, NULL
            )""",
            ("1" * 64, rec_at, now, now),
        )
        self._seed_supporting_evidence(
            conn,
            "asrt_expired_gcp_assumption",
            "ev_expired_gcp",
            "Dan proposed running a 45-day greenfield experiment on Google Cloud.",
        )

        # 2. Active unconfirmed assumption: valid_to in the future (2099-01-01)
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, object_literal_type, assertion_type, atomicity_state,
                scope_text, speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                valid_from, valid_to, date_precision, date_status, recorded_at, superseded_at,
                superseded_by_assertion_id, resolution_state, canonical_status, canonical_eligible,
                retrieval_eligible, materiality, classification, allowed_audiences_json, hitl_required,
                validation_priority, validation_owner, qualifiers_json, legacy_record_type,
                legacy_record_ids_json, created_at, updated_at, approval_reference
            ) VALUES (
                'asrt_active_candidate_assumption', ?, 'entity-1fbe770a467b00e9e7df9c2c7349',
                'RELATED_TO', NULL, 'entity-9a178bfe208e265f5c4b7087ac92', NULL, NULL,
                'assumption', 'atomic', 'Future unconfirmed hypothesis', 'Drew Oakley',
                'src_test_intake', 'run_test_01', 'structured', 'human_source', 'medium', 0.65,
                'needs_review', 'current', '2026-09-04', '2099-01-01', 'day', 'exact',
                ?, NULL, NULL, 'resolved', 'candidate', 0, 1,
                'medium', 'internal', '["internal_consulting"]', 0, NULL, NULL,
                '{"stale_after": "2099-01-01"}', 'edge', '[]', ?, ?, NULL
            )""",
            ("2" * 64, rec_at, now, now),
        )
        self._seed_supporting_evidence(
            conn,
            "asrt_active_candidate_assumption",
            "ev_active_assumption",
            "Dan proposed exploring future options under active evaluation.",
        )
        conn.commit()
        conn.close()

        # Render retrieval records and passages
        records = render_retrieval_records(self.db_path, "snap_test_01")
        passages = render_evidence_passage_records(self.db_path, "snap_test_01")
        propositions = render_proposition_records(self.db_path, "snap_test_01")

        # 1. Expired assumption must NOT appear in retrieval records
        retrieved_ids = {r.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id") for r in records}
        self.assertNotIn("asrt_expired_gcp_assumption", retrieved_ids)

        # 2. Expired assumption must NOT be cited by passages or propositions
        passage_supported_aids = {
            aid
            for p in passages
            for aid in p.get("extensions", {}).get("com.partner_advisory.nee.graph-assertion-ids", [])
        }
        self.assertNotIn("asrt_expired_gcp_assumption", passage_supported_aids)

        prop_aids = {p.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id") for p in propositions}
        self.assertNotIn("asrt_expired_gcp_assumption", prop_aids)

        # 3. Active candidate assumption must be stamped with candidate_hypothesis and unconfirmed_assumption posture
        self.assertIn("asrt_active_candidate_assumption", retrieved_ids)
        active_rec = next(r for r in records if r.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id") == "asrt_active_candidate_assumption")
        self.assertEqual(active_rec.get("lane"), "candidate")
        self.assertEqual(active_rec.get("answer_use"), "candidate_hypothesis")
        self.assertEqual(active_rec.get("extensions", {}).get("com.partner_advisory.nee.epistemic-posture"), "unconfirmed_assumption")

    def test_counterfactual_rejection_preserves_strategic_context_and_gate30_purity(self) -> None:
        """Verify DECIDED_AGAINST yields answer_use='strategic_context' in candidate lane without violating Gate 30."""
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        now = utc_now()
        rec_at = "2026-09-09T17:00:00Z"

        # Seed Marcus Balk DECIDED_AGAINST GCP parallel rebuild
        cur.execute(
            """INSERT INTO assertion (
                assertion_id, assertion_fingerprint, subject_node_id, predicate, legacy_predicate,
                object_node_id, object_literal, object_literal_type, assertion_type, atomicity_state,
                scope_text, speaker_or_author, primary_source_id, extraction_run_id, extraction_method,
                origin_type, confidence_label, confidence_score, validation_state, currentness_state,
                valid_from, valid_to, date_precision, date_status, recorded_at, superseded_at,
                superseded_by_assertion_id, resolution_state, canonical_status, canonical_eligible,
                retrieval_eligible, materiality, classification, allowed_audiences_json, hitl_required,
                validation_priority, validation_owner, qualifiers_json, legacy_record_type,
                legacy_record_ids_json, created_at, updated_at, approval_reference
            ) VALUES (
                'asrt_marcus_rejects_gcp', ?, 'stakeholder_marcus_candidate',
                'DECIDED_AGAINST', NULL, 'entity-1fbe770a467b00e9e7df9c2c7349', NULL, NULL,
                'decision', 'atomic', 'Client rejected GCP FDX parallel rebuild', 'Marcus Balk',
                'src_test_intake', 'run_test_01', 'structured', 'human_source', 'high', 0.95,
                'needs_review', 'current', '2026-09-09', NULL, 'day', 'exact',
                ?, NULL, NULL, 'resolved', 'candidate', 0, 1,
                'high', 'internal', '["internal_consulting"]', 0, NULL, NULL,
                '{"controlling_stakeholder": "Marcus Balk"}', 'edge', '[]', ?, ?, NULL
            )""",
            ("3" * 64, rec_at, now, now),
        )
        self._seed_supporting_evidence(
            conn,
            "asrt_marcus_rejects_gcp",
            "ev_marcus_decision",
            "Marcus Balk confirmed client direction to continue on AWS and decline GCP greenfield.",
        )
        conn.commit()
        conn.close()

        records = render_retrieval_records(self.db_path, "snap_test_02")
        decision_recs = [
            r for r in records
            if r.get("extensions", {}).get("com.partner_advisory.nee.graph-object-id") == "asrt_marcus_rejects_gcp"
        ]
        self.assertEqual(len(decision_recs), 1)
        d_rec = decision_recs[0]
        self.assertEqual(d_rec.get("lane"), "candidate")
        self.assertEqual(d_rec.get("answer_use"), "strategic_context")

        # Gate 30: Approved Lane Purity check (approved_lane_purity_pi >= 0.95)
        # Evaluates that lane='approved' records are not contaminated by historical/superseded assertions
        approved_recs = [r for r in records if r.get("lane") == "approved"]
        if approved_recs:
            impure_count = sum(
                1 for r in approved_recs
                if r.get("lifecycle") == "historical"
                or r.get("canonical_status") == "superseded"
                or r.get("epistemic_status") != "approved"
            )
            purity = (len(approved_recs) - impure_count) / max(1, len(approved_recs))
            self.assertGreaterEqual(purity, 0.95)

    def test_project_beacon_intake_packages_sealed_and_validated(self) -> None:
        """Verify Project Beacon intake packages validate against schemas and contain sealed decision/temporal closures."""
        pkg_paths = [
            ROOT / "intake" / "MEETING_2026_09_04_project_beacon_FDX.intake.json",
            ROOT / "intake" / "MEETING_2026_09_04_SHERPA_FDX_REBUILD.intake.json",
        ]
        for path in pkg_paths:
            self.assertTrue(path.exists(), f"Missing intake package: {path}")
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Contract schema & digest validation
            validate_artifact(data)

            # Extensions checks
            ext = data.get("extensions", {})
            self.assertIn("com.partner_advisory.nee.temporal-validity", ext)
            self.assertEqual(ext["com.partner_advisory.nee.temporal-validity"]["valid_to"], "2026-09-09")
            self.assertEqual(ext["com.partner_advisory.nee.temporal-validity"]["currentness_state"], "historical")

            self.assertIn("com.partner_advisory.nee.decision-closure", ext)
            closure = ext["com.partner_advisory.nee.decision-closure"]
            self.assertEqual(closure["controlling_stakeholder"], "Marcus Balk")
            self.assertEqual(closure["decided_against_predicate"], "DECIDED_AGAINST")
            self.assertEqual(closure["confirmed_predicate"], "STANDARDIZES_ON")


if __name__ == "__main__":
    unittest.main()
