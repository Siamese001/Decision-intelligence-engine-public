from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.core import sha256_file
from dual_pipeline.source_retirement import (
    apply_source_retirement_plan,
    build_source_retirement_plan,
)


class SourceRetirementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="nee-source-retirement-")
        self.graph = Path(self.temp.name) / "graph.sqlite"
        connection = sqlite3.connect(self.graph)
        try:
            connection.executescript(
                """
                CREATE TABLE source (
                  source_id TEXT PRIMARY KEY,
                  source_name TEXT NOT NULL,
                  source_posture TEXT NOT NULL,
                  notes TEXT
                );
                CREATE TABLE source_version (
                  source_version_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  scope_text TEXT NOT NULL,
                  posture_text TEXT NOT NULL
                );
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY,
                  primary_source_id TEXT NOT NULL,
                  object_literal TEXT,
                  scope_text TEXT,
                  qualifiers_json TEXT,
                  superseded_by_assertion_id TEXT
                );
                CREATE TABLE claim (
                  claim_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  claim_text TEXT NOT NULL,
                  superseded_by TEXT
                );
                CREATE TABLE edge (
                  edge_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  description TEXT NOT NULL,
                  superseded_by TEXT
                );
                CREATE TABLE alias (
                  alias_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL
                );
                CREATE TABLE canonical_alias (
                  canonical_alias_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL
                );
                CREATE TABLE evidence_item (
                  evidence_id TEXT PRIMARY KEY,
                  source_id TEXT NOT NULL,
                  direct_quote TEXT NOT NULL
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
                """
            )
            connection.executemany(
                "INSERT INTO source VALUES (?,?,?,?)",
                [
                    ("source-target", "Old source", "old posture", "old notes"),
                    ("source-other", "Other source", "other posture", "other notes"),
                ],
            )
            connection.execute(
                "INSERT INTO source_version VALUES (?,?,?,?)",
                ("source-version-target", "source-target", "old scope", "old version posture"),
            )
            connection.executemany(
                "INSERT INTO assertion VALUES (?,?,?,?,?,?)",
                [
                    ("assertion-remove", "source-target", "remove this", "scope", "{}", None),
                    ("assertion-retain", "source-target", "preserved history", "scope", "{}", None),
                    (
                        "assertion-related",
                        "source-other",
                        None,
                        "scope",
                        '{"description":"obsolete portfolio framing"}',
                        None,
                    ),
                ],
            )
            connection.execute(
                "INSERT INTO claim VALUES (?,?,?,?)",
                ("claim-remove", "source-target", "remove claim", None),
            )
            connection.executemany(
                "INSERT INTO edge VALUES (?,?,?,?)",
                [
                    ("edge-remove", "source-target", "remove edge", None),
                    ("edge-retain", "source-target", "retained edge", None),
                ],
            )
            connection.execute("INSERT INTO alias VALUES (?,?)", ("alias-remove", "source-target"))
            connection.execute(
                "INSERT INTO canonical_alias VALUES (?,?)", ("canonical-alias-remove", "source-target")
            )
            connection.executemany(
                "INSERT INTO evidence_item VALUES (?,?,?)",
                [
                    ("evidence-remove", "source-target", "remove evidence"),
                    ("evidence-retain", "source-target", "preserved evidence"),
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _control() -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "target_source_id": "source-target",
            "remove": {
                "assertion_ids": ["assertion-remove", "assertion-related"],
                "claim_ids": ["claim-remove"],
                "edge_ids": ["edge-remove"],
                "alias_ids": ["alias-remove"],
                "canonical_alias_ids": ["canonical-alias-remove"],
                "evidence_ids": ["evidence-remove"],
            },
            "retain": {
                "assertion_ids": ["assertion-retain"],
                "claim_ids": [],
                "edge_ids": ["edge-retain"],
                "alias_ids": [],
                "canonical_alias_ids": [],
                "evidence_ids": ["evidence-retain"],
            },
            "related_removals": [
                {
                    "object_type": "assertion_ids",
                    "object_id": "assertion-related",
                    "expected_text_fragment": "obsolete portfolio framing",
                }
            ],
            "supersession_detachments": [],
            "source_updates": [
                {
                    "source_id": "source-target",
                    "expected_source_name": "Old source",
                    "expected_source_posture": "old posture",
                    "expected_notes": "old notes",
                    "source_name": "Retained historical source",
                    "source_posture": "revised posture",
                    "notes": "revised notes",
                }
            ],
            "source_version_updates": [
                {
                    "source_version_id": "source-version-target",
                    "expected_scope_text": "old scope",
                    "expected_posture_text": "old version posture",
                    "scope_text": "revised scope",
                    "posture_text": "revised version posture",
                }
            ],
            "reason": "Remove the obsolete bounded source representation.",
        }

    def _plan(self, control: dict[str, object] | None = None) -> dict[str, object]:
        return build_source_retirement_plan(
            self.graph,
            active_pair={
                "generation_id": "generation-source-retirement-test",
                "graph_sha256": sha256_file(self.graph),
            },
            control=control or self._control(),
        )

    def test_applies_exact_source_scope_and_related_qualifier_removal(self) -> None:
        before_hash = sha256_file(self.graph)
        plan = self._plan()
        receipt = apply_source_retirement_plan(
            self.graph,
            plan=plan,
            approval_id="approval-source-retirement-test",
            run_id="run-source-retirement-test",
        )

        self.assertEqual("COMMITTED", receipt["status"])
        self.assertNotEqual(before_hash, sha256_file(self.graph))
        connection = sqlite3.connect(self.graph)
        try:
            for table, field, removed_id in (
                ("assertion", "assertion_id", "assertion-remove"),
                ("assertion", "assertion_id", "assertion-related"),
                ("claim", "claim_id", "claim-remove"),
                ("edge", "edge_id", "edge-remove"),
                ("alias", "alias_id", "alias-remove"),
                ("canonical_alias", "canonical_alias_id", "canonical-alias-remove"),
                ("evidence_item", "evidence_id", "evidence-remove"),
            ):
                self.assertIsNone(
                    connection.execute(
                        f"SELECT 1 FROM {table} WHERE {field}=?", (removed_id,)
                    ).fetchone()
                )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM assertion WHERE assertion_id='assertion-retain'"
                ).fetchone()
            )
            self.assertIsNotNone(
                connection.execute("SELECT 1 FROM edge WHERE edge_id='edge-retain'").fetchone()
            )
            self.assertIsNotNone(
                connection.execute(
                    "SELECT 1 FROM evidence_item WHERE evidence_id='evidence-retain'"
                ).fetchone()
            )
            self.assertEqual(
                ("Retained historical source", "revised posture", "revised notes"),
                connection.execute(
                    "SELECT source_name,source_posture,notes FROM source WHERE source_id='source-target'"
                ).fetchone(),
            )
            self.assertEqual(
                ("revised scope", "revised version posture"),
                connection.execute(
                    "SELECT scope_text,posture_text FROM source_version "
                    "WHERE source_version_id='source-version-target'"
                ).fetchone(),
            )
            self.assertEqual(1, connection.execute("SELECT count(*) FROM audit_event").fetchone()[0])
        finally:
            connection.close()

    def test_rejects_missing_direct_source_artifact(self) -> None:
        control = self._control()
        control["retain"]["assertion_ids"] = []  # type: ignore[index]
        with self.assertRaisesRegex(RuntimeError, "does not exactly cover"):
            self._plan(control)

    def test_accepts_null_prior_source_notes(self) -> None:
        connection = sqlite3.connect(self.graph)
        try:
            connection.execute(
                "UPDATE source SET notes=NULL WHERE source_id='source-target'"
            )
            connection.commit()
        finally:
            connection.close()
        control = self._control()
        control["source_updates"][0]["expected_notes"] = None  # type: ignore[index]
        receipt = apply_source_retirement_plan(
            self.graph,
            plan=self._plan(control),
            approval_id="approval-source-retirement-test",
            run_id="run-source-retirement-test",
        )
        self.assertEqual("COMMITTED", receipt["status"])

    def test_detaches_explicit_historical_supersession_before_removal(self) -> None:
        connection = sqlite3.connect(self.graph)
        try:
            connection.execute(
                "INSERT INTO assertion VALUES (?,?,?,?,?,?)",
                (
                    "assertion-historical-upstream",
                    "source-other",
                    "Historical source remains audit-only after successor removal.",
                    "historical scope",
                    "{}",
                    "assertion-remove",
                ),
            )
            connection.commit()
        finally:
            connection.close()
        control = self._control()
        control["supersession_detachments"] = [  # type: ignore[index]
            {
                "object_type": "assertion_ids",
                "object_id": "assertion-historical-upstream",
                "expected_superseded_by_object_id": "assertion-remove",
                "expected_text_fragment": "Historical source remains audit-only",
            }
        ]
        apply_source_retirement_plan(
            self.graph,
            plan=self._plan(control),
            approval_id="approval-source-retirement-test",
            run_id="run-source-retirement-test",
        )
        connection = sqlite3.connect(self.graph)
        try:
            self.assertIsNone(
                connection.execute(
                    "SELECT superseded_by_assertion_id FROM assertion "
                    "WHERE assertion_id='assertion-historical-upstream'"
                ).fetchone()[0]
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
