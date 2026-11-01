"""Wave 1 tests: Candidate lane clearance fix, lineage-backed hypothesis/assumption clearance, and unpromoted recent source discovery."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.graph_retrieval_core.clearance_cache import _evaluate_record_clearance
from dual_pipeline.query_core.hydration import (
    _classify_assertion_lane,
    discover_unpromoted_recent_workstream_sources,
    exact_retrieval,
)
from dual_pipeline.retrieval_clearance import retrieval_clearance


class TestQueryCandidateRecencyClearance(unittest.TestCase):
    """Test Wave 1 fixes for candidate lane classification, derived operational clearance, and unpromoted recent sources."""

    def test_classify_assertion_lane_routes_unpromoted_candidates_to_candidate_lane(self) -> None:
        """Non-superseded candidate and historical_candidate assertions with retrieval_eligible=0 classify into 'candidate' lane."""
        row_candidate = {
            "assertion_id": "ass-eduardo-1",
            "canonical_status": "candidate",
            "validation_state": "needs_review",
            "currentness_state": "current",
            "canonical_eligible": 0,
            "retrieval_eligible": 0,
            "confidence_score": 0.80,
            "qualifiers_json": "{}",
        }
        self.assertEqual("candidate", _classify_assertion_lane(row_candidate))

        row_hist_candidate = {
            "assertion_id": "ass-eduardo-hist",
            "canonical_status": "historical_candidate",
            "validation_state": "needs_review",
            "currentness_state": "historical",
            "canonical_eligible": 0,
            "retrieval_eligible": 0,
            "confidence_score": 0.80,
            "qualifiers_json": "{}",
        }
        self.assertEqual("candidate", _classify_assertion_lane(row_hist_candidate))

        row_superseded = {
            "assertion_id": "ass-superseded",
            "canonical_status": "candidate",
            "validation_state": "superseded",
            "currentness_state": "historical",
            "canonical_eligible": 0,
            "retrieval_eligible": 0,
            "confidence_score": 0.90,
            "qualifiers_json": "{}",
        }
        self.assertIsNone(_classify_assertion_lane(row_superseded))

        row_low_conf = {
            "assertion_id": "ass-low-conf",
            "canonical_status": "candidate",
            "validation_state": "needs_review",
            "currentness_state": "current",
            "canonical_eligible": 0,
            "retrieval_eligible": 0,
            "confidence_score": 0.50,
            "qualifiers_json": "{}",
        }
        self.assertIsNone(_classify_assertion_lane(row_low_conf))

    def test_retrieval_clearance_allows_lineage_backed_hypothesis_and_assumption(self) -> None:
        """Evidence-backed hypothesis and assumption assertions with valid extraction lineage pass clearance, while unbacked derived types fail closed."""
        ok_hyp, reason_hyp = retrieval_clearance(
            assertion_type="hypothesis",
            classification="internal",
            audiences=["internal_consulting", "nee-engineering"],
            origin_type="human_source",
            extraction_run_id="extraction-run-db739e8ce3f4d124e6770b0ae01e",
        )
        self.assertTrue(ok_hyp)
        self.assertIsNone(reason_hyp)

        ok_assump, reason_assump = retrieval_clearance(
            assertion_type="assumption",
            classification="internal",
            audiences=["internal_consulting"],
            origin_type="human_source",
            extraction_run_id="extraction-run-b819ec9a59e8f8d4b60389d77bf9",
        )
        self.assertTrue(ok_assump)
        self.assertIsNone(reason_assump)

        # Unbacked hypothesis without extraction_run_id remains denied
        ok_unbacked, reason_unbacked = retrieval_clearance(
            assertion_type="hypothesis",
            classification="internal",
            audiences=["internal_consulting"],
            origin_type=None,
            extraction_run_id=None,
        )
        self.assertFalse(ok_unbacked)
        self.assertEqual("derived_assertion_type_not_retrieval_eligible", reason_unbacked)

        # recommendation and pov remain denied even with lineage
        ok_rec, reason_rec = retrieval_clearance(
            assertion_type="recommendation",
            classification="internal",
            audiences=["internal_consulting"],
            origin_type="human_source",
            extraction_run_id="extraction-run-123",
        )
        self.assertFalse(ok_rec)
        self.assertEqual("derived_assertion_type_not_retrieval_eligible", reason_rec)

    def test_evaluate_record_clearance_permits_non_superseded_candidate_lane_records(self) -> None:
        """_evaluate_record_clearance allows candidate lane records with retrieval_eligible=0 and blocks superseded ones."""
        record = {
            "record_id": "rec-cand-1",
            "lane": "candidate",
            "classification": "internal",
            "audiences": ["internal_consulting"],
            "extensions": {
                "com.partner_advisory.nee.assertion-type": "assumption",
                "com.partner_advisory.nee.origin-type": "human_source",
                "com.partner_advisory.nee.extraction-run-id": "extraction-run-b819ec9a59e8f8d4b60389d77bf9",
            },
        }
        graph_row_valid = {
            "assertion_id": "ass-1",
            "classification": "internal",
            "allowed_audiences_json": '["internal_consulting"]',
            "assertion_type": "assumption",
            "origin_type": "human_source",
            "extraction_run_id": "extraction-run-b819ec9a59e8f8d4b60389d77bf9",
            "currentness_state": "historical",
            "retrieval_eligible": 0,
            "canonical_status": "historical_candidate",
            "canonical_eligible": 0,
            "confidence_score": 0.80,
            "validation_state": "needs_review",
        }
        self.assertTrue(_evaluate_record_clearance(record, graph_row_valid))

        graph_row_superseded = {
            **graph_row_valid,
            "validation_state": "superseded",
        }
        self.assertFalse(_evaluate_record_clearance(record, graph_row_superseded))

    def test_exact_retrieval_and_unpromoted_recent_sources_on_lens_fixture(self) -> None:
        """Hermetic SQLite fixture mirroring Oct 27-28 LENS sources surfaces Eduardo's 5 requests, the 200-record hypothesis, and unpromoted recent sources."""
        with tempfile.TemporaryDirectory(prefix="wave1-lens-") as tmpdir:
            graph_path = Path(tmpdir) / "graph.sqlite"
            conn = sqlite3.connect(graph_path)
            conn.row_factory = sqlite3.Row
            try:
                conn.executescript(
                    """
                    CREATE TABLE source (
                        source_id TEXT PRIMARY KEY,
                        source_name TEXT,
                        source_date TEXT,
                        source_posture TEXT
                    );
                    CREATE TABLE node (
                        node_id TEXT PRIMARY KEY,
                        canonical_name TEXT,
                        display_name TEXT,
                        node_type TEXT,
                        active INTEGER
                    );
                    CREATE TABLE assertion (
                        assertion_id TEXT PRIMARY KEY,
                        subject_node_id TEXT,
                        predicate TEXT,
                        object_node_id TEXT,
                        object_literal TEXT,
                        scope_text TEXT,
                        classification TEXT,
                        allowed_audiences_json TEXT,
                        canonical_status TEXT,
                        canonical_eligible INTEGER,
                        validation_state TEXT,
                        currentness_state TEXT,
                        retrieval_eligible INTEGER,
                        confidence_score REAL,
                        qualifiers_json TEXT,
                        assertion_type TEXT,
                        origin_type TEXT,
                        extraction_run_id TEXT,
                        primary_source_id TEXT
                    );
                    CREATE TABLE assertion_evidence (
                        assertion_id TEXT,
                        evidence_id TEXT,
                        evidence_relationship TEXT
                    );
                    CREATE TABLE search_document (
                        search_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
                        search_document_id TEXT,
                        object_type TEXT,
                        object_id TEXT,
                        title TEXT,
                        body TEXT,
                        canonical_status TEXT,
                        currentness_state TEXT,
                        retrieval_eligible INTEGER,
                        source_id TEXT,
                        assertion_id TEXT
                    );
                    CREATE VIRTUAL TABLE search_fts USING fts5(title, body);
                    """
                )
                conn.executemany(
                    "INSERT INTO source VALUES (?, ?, ?, ?)",
                    [
                        ("source-ed01b486836314055f69c725", "20261028 Project Lens Sync", "2026-10-28", "transcript-governed"),
                        ("source-d4d6a747fa66307138d4f96a", "20261027 Lens Debrief", "2026-10-27", "transcript-governed"),
                        ("source-e17a00483f549a6495c38d44", "20261027 Discuss Prd", "2026-10-27", "transcript-governed"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO node VALUES (?, ?, ?, ?, 1)",
                    [
                        ("workstream_lens", "Project LENS", "Project LENS", "workstream"),
                        ("person_eduardo", "Eduardo Juan More", "Eduardo Juan More", "stakeholder"),
                        ("entity_dashboard_cards", "Dashboard Cards", "Dashboard Cards", "technical_anchor"),
                    ],
                )
                assertions_data = [
                    (
                        "ass-lens-200-skew",
                        "entity_dashboard_cards",
                        "HAS_CLAIM_TEXT",
                        None,
                        "Dashboard cards based on the last 200 filtered records may skew performance interpretation",
                        "Dashboard cards based on the last 200 filtered records may skew performance interpretation",
                        "internal",
                        '["internal_consulting"]',
                        "canonical",
                        1,
                        "accepted_legacy",
                        "current",
                        1,
                        0.85,
                        "{}",
                        "hypothesis",
                        "human_source",
                        "extraction-run-db739e8ce3f4d124e6770b0ae01e",
                        "source-e17a00483f549a6495c38d44",
                    ),
                    (
                        "ass-eduardo-turbine",
                        "workstream_lens",
                        "HAS_CLAIM_TEXT",
                        None,
                        "Display turbine number on the LENS review screen for physical asset identification",
                        "Eduardo requested turbine number on review screen",
                        "internal",
                        '["internal_consulting"]',
                        "candidate",
                        0,
                        "needs_review",
                        "current",
                        0,
                        0.80,
                        "{}",
                        "assumption",
                        "human_source",
                        "extraction-run-b819ec9a59e8f8d4b60389d77bf9",
                        "source-ed01b486836314055f69c725",
                    ),
                    (
                        "ass-eduardo-non-maximo",
                        "workstream_lens",
                        "HAS_CLAIM_TEXT",
                        None,
                        "Retain all extracted nameplate fields in a database including non-Maximo and rejected GE tags prior to Odyssey write-back",
                        "Eduardo data retention requirement",
                        "internal",
                        '["internal_consulting"]',
                        "historical_candidate",
                        0,
                        "needs_review",
                        "historical",
                        0,
                        0.80,
                        "{}",
                        "assumption",
                        "human_source",
                        "extraction-run-b819ec9a59e8f8d4b60389d77bf9",
                        "source-ed01b486836314055f69c725",
                    ),
                    (
                        "ass-eduardo-superseded",
                        "workstream_lens",
                        "HAS_CLAIM_TEXT",
                        None,
                        "Superseded draft LENS claim",
                        "Superseded",
                        "internal",
                        '["internal_consulting"]',
                        "historical_candidate",
                        0,
                        "superseded",
                        "historical",
                        0,
                        0.90,
                        "{}",
                        "extracted_claim",
                        "human_source",
                        "extraction-run-b819ec9a59e8f8d4b60389d77bf9",
                        "source-ed01b486836314055f69c725",
                    ),
                ]
                conn.executemany(
                    "INSERT INTO assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    assertions_data,
                )
                for idx, a in enumerate(assertions_data, start=1):
                    aid = a[0]
                    conn.execute("INSERT INTO assertion_evidence VALUES (?, ?, 'supports')", (aid, f"ev-{aid}"))
                    conn.execute(
                        "INSERT INTO search_document VALUES (?, ?, 'assertion', ?, ?, ?, ?, ?, ?, ?, ?)",
                        (idx, f"sd-{aid}", aid, f"LENS {aid}", a[4], a[8], a[11], a[12], a[18], aid),
                    )
                    conn.execute("INSERT INTO search_fts(rowid, title, body) VALUES (?, ?, ?)", (idx, f"LENS {aid}", a[4]))
                conn.commit()

                unpromoted = discover_unpromoted_recent_workstream_sources(conn, ["workstream_lens"])
                self.assertEqual(1, len(unpromoted))
                self.assertEqual("source-ed01b486836314055f69c725", unpromoted[0]["source_id"])
                self.assertEqual(2, unpromoted[0]["unpromoted_count"])
            finally:
                conn.close()

            exact = exact_retrieval(
                "What are the Project LENS turbine number non-Maximo database retention and 200 filtered records requirements?",
                graph_path,
                classification="internal",
                audiences=["nee-engineering"],
                retrieval_mode="candidate_review",
            )
            matched_ids = {row["assertion_id"] for row in exact["exact_anchors"]}
            self.assertIn("ass-lens-200-skew", matched_ids)
            self.assertIn("ass-eduardo-turbine", matched_ids)
            self.assertIn("ass-eduardo-non-maximo", matched_ids)
            self.assertNotIn("ass-eduardo-superseded", matched_ids)
            self.assertEqual(1, len(exact["unpromoted_recent_sources"]))
            self.assertEqual("source-ed01b486836314055f69c725", exact["unpromoted_recent_sources"][0]["source_id"])


if __name__ == "__main__":
    unittest.main()
