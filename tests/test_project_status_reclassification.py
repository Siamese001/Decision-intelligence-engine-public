"""Regression coverage for bounded project-status semantic demotion."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.index import retrieval_metadata_for_record, retrieval_record_type_for_assertion
from dual_pipeline.project_status_reclassification import (
    apply_project_status_reclassification_plan,
    build_project_status_reclassification_plan,
    validate_project_status_reclassification_control,
)


class ProjectStatusReclassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-project-status-demotion-")
        self.graph = Path(self.temp.name) / "graph.sqlite"
        connection = sqlite3.connect(self.graph)
        try:
            connection.executescript(
                """
                CREATE TABLE source (
                  source_id TEXT PRIMARY KEY,
                  source_type TEXT NOT NULL,
                  source_name TEXT NOT NULL,
                  source_posture TEXT NOT NULL,
                  notes TEXT
                );
                CREATE TABLE source_version (
                  source_version_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  content_hash TEXT NOT NULL,
                  scope_text TEXT NOT NULL,
                  posture_text TEXT NOT NULL
                );
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY,
                  primary_source_id TEXT NOT NULL,
                  object_literal TEXT,
                  canonical_status TEXT NOT NULL,
                  canonical_eligible INTEGER NOT NULL,
                  retrieval_eligible INTEGER NOT NULL,
                  currentness_state TEXT NOT NULL,
                  qualifiers_json TEXT NOT NULL,
                  updated_at TEXT
                );
                CREATE TABLE assertion_evidence (
                  assertion_id TEXT NOT NULL,
                  evidence_id TEXT NOT NULL
                );
                CREATE TABLE evidence_item (
                  evidence_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL
                );
                CREATE TABLE search_document (
                  search_document_id TEXT PRIMARY KEY,
                  assertion_id TEXT NOT NULL,
                  canonical_status TEXT NOT NULL,
                  currentness_state TEXT NOT NULL,
                  retrieval_eligible INTEGER NOT NULL,
                  updated_at TEXT
                );
                CREATE TABLE claim (
                  claim_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  evidence_posture TEXT NOT NULL,
                  hitl_required INTEGER NOT NULL,
                  updated_at TEXT
                );
                CREATE TABLE edge (
                  edge_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  evidence_posture TEXT NOT NULL,
                  hitl_required INTEGER NOT NULL,
                  updated_at TEXT
                );
                CREATE TABLE audit_event (
                  audit_event_id TEXT PRIMARY KEY,
                  actor_or_process TEXT NOT NULL,
                  action TEXT NOT NULL,
                  record_type TEXT NOT NULL,
                  record_id TEXT NOT NULL,
                  previous_value_json TEXT,
                  new_value_json TEXT,
                  source_id TEXT,
                  event_timestamp TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  validation_reference TEXT,
                  approval_reference TEXT,
                  migration_or_ingestion_run_id TEXT,
                  release_version TEXT NOT NULL
                );
                CREATE TRIGGER assertion_search_sync_au
                AFTER UPDATE OF canonical_status, canonical_eligible, retrieval_eligible,
                                currentness_state, qualifiers_json, updated_at ON assertion
                BEGIN
                  UPDATE search_document
                  SET canonical_status=NEW.canonical_status,
                      currentness_state=NEW.currentness_state,
                      retrieval_eligible=CASE
                        WHEN NEW.canonical_status='canonical'
                         AND NEW.retrieval_eligible=1 THEN 1 ELSE 0 END,
                      updated_at=NEW.updated_at
                  WHERE assertion_id=NEW.assertion_id;
                END;
                """
            )
            connection.executemany(
                "INSERT INTO source VALUES (?,?,?,?,?)",
                [
                    (
                        "edo-status",
                        "status",
                        "EDO weekly status",
                        "controlling current project status",
                        "legacy status note",
                    ),
                    (
                        "other-source",
                        "meeting",
                        "Other source",
                        "other posture",
                        "other note",
                    ),
                ],
            )
            connection.execute(
                "INSERT INTO source_version VALUES (?,?,?,?,?)",
                (
                    "edo-status-v1",
                    "edo-status",
                    "a" * 64,
                    "legacy status scope",
                    "legacy status posture",
                ),
            )
            connection.executemany(
                "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        "status-canonical",
                        "edo-status",
                        "EDO is green",
                        "canonical",
                        1,
                        1,
                        "current",
                        json.dumps({"legacy": "preserved"}),
                        "2026-06-15T00:00:00Z",
                    ),
                    (
                        "status-historical",
                        "edo-status",
                        "EDO had an earlier risk",
                        "historical_candidate",
                        0,
                        0,
                        "historical",
                        "{}",
                        "2026-06-15T00:00:00Z",
                    ),
                ],
            )
            connection.executemany(
                "INSERT INTO evidence_item VALUES (?,?)",
                [("evidence-canonical", "edo-status"), ("evidence-historical", "edo-status")],
            )
            connection.executemany(
                "INSERT INTO assertion_evidence VALUES (?,?)",
                [
                    ("status-canonical", "evidence-canonical"),
                    ("status-historical", "evidence-historical"),
                ],
            )
            connection.executemany(
                "INSERT INTO search_document VALUES (?,?,?,?,?,?)",
                [
                    ("search-canonical", "status-canonical", "canonical", "current", 1, None),
                    ("search-historical", "status-historical", "historical_candidate", "historical", 0, None),
                ],
            )
            connection.execute(
                "INSERT INTO claim VALUES (?,?,?,?,?)",
                ("claim-status", "edo-status", "formal status", 0, None),
            )
            connection.execute(
                "INSERT INTO edge VALUES (?,?,?,?,?)",
                ("edge-status", "edo-status", "formal status", 0, None),
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _control(assertion_ids: list[str] | None = None) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "targets": [
                {
                    "source_id": "edo-status",
                    "source_version_id": "edo-status-v1",
                    "expected_content_sha256": "a" * 64,
                    "expected_source_type": "status",
                    "expected_source_name": "EDO weekly status",
                    "expected_source_posture": "controlling current project status",
                    "expected_notes": "legacy status note",
                    "expected_scope_text": "legacy status scope",
                    "expected_version_posture_text": "legacy status posture",
                    "scope_id": "edo-program",
                    "as_of_date": "2026-06-15",
                    "assertion_ids": assertion_ids
                    or ["status-canonical", "status-historical"],
                }
            ],
            "reason": "Project status is ephemeral, cited evidence rather than a timeless fact.",
        }

    def _plan(self, control: dict[str, object] | None = None) -> dict[str, object]:
        return build_project_status_reclassification_plan(
            self.graph,
            active_pair={
                "generation_id": "generation-project-status-test",
                "graph_sha256": sha256_file(self.graph),
            },
            control=control or self._control(),
        )

    def test_demotes_complete_source_scope_to_dated_citation_evidence(self) -> None:
        before_hash = sha256_file(self.graph)
        plan = self._plan()
        self.assertEqual(2, plan["summary"]["assertion_count"])
        self.assertEqual({"canonical": 1, "historical_candidate": 1}, plan["summary"]["before_status_counts"])
        receipt = apply_project_status_reclassification_plan(
            self.graph,
            plan=plan,
            approval_id="approval-project-status-test",
            run_id="run-project-status-test",
        )
        self.assertEqual("COMMITTED", receipt["status"])
        self.assertNotEqual(before_hash, sha256_file(self.graph))
        connection = sqlite3.connect(self.graph)
        try:
            rows = connection.execute(
                """SELECT assertion_id,object_literal,canonical_status,canonical_eligible,
                          retrieval_eligible,currentness_state,qualifiers_json
                   FROM assertion ORDER BY assertion_id"""
            ).fetchall()
            self.assertEqual(2, len(rows))
            for assertion_id, text, status, eligible, retrievable, currentness, qualifiers_json in rows:
                self.assertIn(assertion_id, {"status-canonical", "status-historical"})
                self.assertTrue(text.startswith("EDO"))
                self.assertEqual("candidate", status)
                self.assertEqual(0, eligible)
                self.assertEqual(1, retrievable)
                self.assertEqual("current", currentness)
                qualifiers = json.loads(qualifiers_json)
                self.assertEqual("source_chunk", qualifiers["dual_pipeline_candidate_type"])
                self.assertEqual("citation_only", qualifiers["answer_use"])
                self.assertFalse(qualifiers["fact_promotion_eligible"])
                self.assertEqual("edo-program", qualifiers["project_status_snapshot"]["scope_id"])
                self.assertEqual("2026-06-15", qualifiers["project_status_snapshot"]["as_of_date"])
                self.assertEqual(
                    "citation_only",
                    retrieval_metadata_for_record(
                        retrieval_record_type_for_assertion(
                            qualifiers_json=qualifiers_json, object_node_id=None
                        ),
                        "candidate",
                    )["answer_use"],
                )
            self.assertEqual(
                [("candidate", "current", 0), ("candidate", "current", 0)],
                connection.execute(
                    "SELECT canonical_status,currentness_state,retrieval_eligible "
                    "FROM search_document ORDER BY search_document_id"
                ).fetchall(),
            )
            self.assertEqual(
                [
                    (
                        "citation-only project-status snapshot as of 2026-06-15",
                        1,
                    )
                ],
                connection.execute("SELECT evidence_posture,hitl_required FROM claim").fetchall(),
            )
            self.assertEqual(
                [
                    (
                        "citation-only project-status snapshot as of 2026-06-15",
                        1,
                    )
                ],
                connection.execute("SELECT evidence_posture,hitl_required FROM edge").fetchall(),
            )
            posture, notes = connection.execute(
                "SELECT source_posture,notes FROM source WHERE source_id='edo-status'"
            ).fetchone()
            self.assertIn("citation-only candidate evidence", posture)
            self.assertIn("ephemeral edo-program", notes)
            self.assertEqual(1, connection.execute("SELECT count(*) FROM audit_event").fetchone()[0])
        finally:
            connection.close()

    def test_rejects_partial_source_selection(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "does not exactly cover"):
            self._plan(self._control(["status-canonical"]))

    def test_rejects_source_version_drift(self) -> None:
        control = self._control()
        control["targets"][0]["expected_content_sha256"] = "b" * 64  # type: ignore[index]
        with self.assertRaisesRegex(RuntimeError, "source-version drifted"):
            self._plan(control)

    def test_rejects_replay(self) -> None:
        plan = self._plan()
        apply_project_status_reclassification_plan(
            self.graph,
            plan=plan,
            approval_id="approval-project-status-test",
            run_id="run-project-status-test",
        )
        with self.assertRaisesRegex(RuntimeError, "stale relative to its graph"):
            apply_project_status_reclassification_plan(
                self.graph,
                plan=plan,
                approval_id="approval-project-status-test",
                run_id="run-project-status-test",
            )

    def test_rejects_existing_snapshot(self) -> None:
        connection = sqlite3.connect(self.graph)
        try:
            connection.execute(
                "UPDATE assertion SET qualifiers_json=? WHERE assertion_id='status-canonical'",
                (json.dumps({"project_status_snapshot": {}}),),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(RuntimeError, "already a snapshot"):
            self._plan()

    def test_control_rejects_non_status_attestation_shape(self) -> None:
        control = self._control()
        control["targets"][0]["scope_id"] = ""  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "scope_id"):
            validate_project_status_reclassification_control(control)


if __name__ == "__main__":
    unittest.main()
