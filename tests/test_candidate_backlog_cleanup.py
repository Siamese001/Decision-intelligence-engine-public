from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.candidate_cleanup import (
    PRESERVED_FUTURE_STATE_SOURCE_IDS,
    apply_candidate_cleanup_plan,
    build_candidate_cleanup_plan,
)
from dual_pipeline.core import sha256_file


class CandidateBacklogCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.graph = Path(self.temp.name) / "graph.sqlite"
        connection = sqlite3.connect(self.graph)
        try:
            connection.executescript(
                """
                CREATE TABLE assertion (
                  assertion_id TEXT PRIMARY KEY,
                  canonical_status TEXT NOT NULL,
                  currentness_state TEXT NOT NULL,
                  canonical_eligible INTEGER NOT NULL,
                  retrieval_eligible INTEGER NOT NULL,
                  validation_priority TEXT,
                  validation_state TEXT NOT NULL,
                  primary_source_id TEXT,
                  qualifiers_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                CREATE TABLE controlled_value_registry (
                  vocabulary TEXT NOT NULL,
                  value TEXT NOT NULL,
                  definition TEXT NOT NULL,
                  active INTEGER NOT NULL,
                  sort_order INTEGER NOT NULL,
                  PRIMARY KEY (vocabulary,value)
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
                """INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        "future-state",
                        "candidate",
                        "current",
                        0,
                        1,
                        None,
                        "needs_review",
                        next(iter(PRESERVED_FUTURE_STATE_SOURCE_IDS)),
                        "{}",
                        "2026-07-27T00:00:00Z",
                    ),
                    (
                        "priority-validation",
                        "candidate",
                        "current",
                        0,
                        1,
                        "P1",
                        "unreviewed",
                        "other-source",
                        "{}",
                        "2026-07-27T00:00:00Z",
                    ),
                    (
                        "generic-evidence",
                        "candidate",
                        "current",
                        0,
                        1,
                        None,
                        "needs_review",
                        "other-source",
                        "{}",
                        "2026-07-27T00:00:00Z",
                    ),
                    (
                        "unknown-evidence",
                        "candidate",
                        "unknown",
                        0,
                        1,
                        None,
                        "needs_review",
                        "other-source",
                        "{}",
                        "2026-07-27T00:00:00Z",
                    ),
                ],
            )
            connection.execute(
                """INSERT INTO controlled_value_registry VALUES
                   ('canonical_status','candidate','candidate',1,1)"""
            )
            connection.commit()
        finally:
            connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reclassifies_without_deleting_evidence_or_future_state(self) -> None:
        active_pair = {
            "generation_id": "generation-test",
            "graph_sha256": sha256_file(self.graph),
        }
        plan = build_candidate_cleanup_plan(self.graph, active_pair=active_pair)

        self.assertEqual(
            plan["summary"],
            {
                "candidate_retrieval_assertions_before": 4,
                "future_state_candidates_retained": 1,
                "validation_pending": 1,
                "evidence_only": 2,
                "graph_operation_count": 5,
            },
        )
        receipt = apply_candidate_cleanup_plan(
            self.graph,
            plan=plan,
            approval_id="human-decision-test",
            run_id="run-candidate-cleanup-test",
        )

        self.assertEqual(receipt["operation_count"], 5)
        connection = sqlite3.connect(self.graph)
        try:
            rows = {
                row[0]: row[1:]
                for row in connection.execute(
                    """SELECT assertion_id,canonical_status,validation_state,retrieval_eligible
                         FROM assertion ORDER BY assertion_id"""
                )
            }
            self.assertEqual(rows["future-state"], ("candidate", "needs_review", 1))
            self.assertEqual(
                rows["priority-validation"],
                ("validation_pending", "needs_review", 0),
            )
            self.assertEqual(
                rows["generic-evidence"],
                ("evidence_only", "unreviewed", 0),
            )
            self.assertEqual(
                rows["unknown-evidence"],
                ("evidence_only", "unreviewed", 0),
            )
            self.assertEqual(
                connection.execute("SELECT count(*) FROM audit_event").fetchone()[0],
                5,
            )
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
