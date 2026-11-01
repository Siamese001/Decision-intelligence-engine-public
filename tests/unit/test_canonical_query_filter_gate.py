"""Unit tests for canonical assertion eligibility, hygiene archival parity, and MCP SQL guardrails."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.candidate_cleanup_core.executor import _apply_reclassify_candidate_assertion
from dual_pipeline.candidate_cleanup_core.helpers import _assertion_state
from dual_pipeline.core import digest
from dual_pipeline.graph_writer_core.assertions import AssertionOpsMixin
from dual_pipeline.query_core.firewall import GraphDBRequiredError
from scripts.hitl_pre_tool_guard import handle_tool_call_guard
from tools.apply_graph_hygiene_remediation import _remediate_stale_candidates
from tools.archive_stale_candidates import _apply_disposition_mutation
from tools.mcp_graphdb_server import graphdb_read_query


def _init_assertion_and_search_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            canonical_status TEXT,
            currentness_state TEXT,
            canonical_eligible INTEGER,
            retrieval_eligible INTEGER,
            validation_priority TEXT,
            validation_state TEXT,
            primary_source_id TEXT,
            qualifiers_json TEXT,
            updated_at TEXT
        );
        CREATE TABLE search_document (
            search_document_id TEXT PRIMARY KEY,
            assertion_id TEXT,
            canonical_status TEXT,
            currentness_state TEXT,
            retrieval_eligible INTEGER,
            updated_at TEXT
        );
        """
    )


class TestWave1IngestionAndHygieneEligibility(unittest.TestCase):
    """Wave 1: Verify ingestion and hygiene parity for retrieval_eligible."""

    def test_low_confidence_directional_candidate_sets_retrieval_eligible_zero(self) -> None:
        """Exact regression: assertion-cd256f2fbf979bd7c87a5fae8fa8 (0.45 directional candidate) gets retrieval_eligible=0."""
        payload = {
            "candidate": {
                "candidate_id": "candidate-118ee8abd8f46016b1f9fca5b8",
                "candidate_type": "assertion",
                "normalized_text": (
                    "Apex Global applies a screening heuristic favoring at least 7 years of experience, "
                    "requiring candidates with less tenure to demonstrate proven senior aptitude."
                ),
                "materiality": "medium",
                "classification": "confidential",
                "audiences": ["nea-leadership", "nee-engineering", "partner_advisory-leadership"],
                "extraction_confidence": 0.75,
            },
            "subject_node_id": "person_stephane",
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "CANDIDATE",
            "edge_traits": {
                "confidence": {"score": 0.45, "tier": "directional"},
            },
        }
        source = {
            "source_id": "source-392902d1636a221571f00264",
            "source_version_id": "sv-2026-09-14",
            "date": "2026-09-14",
        }
        vals = AssertionOpsMixin._assertion_values(
            payload=payload,
            target_id="assertion-cd256f2fbf979bd7c87a5fae8fa8",
            source=source,
            extraction_run_id="extraction-run-126e5f28cdc4ca00742f760446d4",
            now="2026-09-14T22:08:46Z",
            validation_state="needs_review",
        )
        # Index 28: canonical_status, Index 29: canonical_eligible, Index 30: retrieval_eligible
        self.assertEqual(vals[28], "candidate")
        self.assertEqual(vals[29], 0)
        self.assertEqual(vals[30], 0)

    def test_approved_disposition_sets_retrieval_eligible_one(self) -> None:
        payload = {
            "candidate": {
                "candidate_id": "cand-approved-01",
                "candidate_type": "assertion",
                "normalized_text": "Confirmed canonical fact.",
                "materiality": "high",
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "extraction_confidence": 0.92,
            },
            "subject_node_id": "person_dan",
            "predicate": "HAS_CLAIM_TEXT",
            "disposition": "APPROVED",
        }
        source = {
            "source_id": "src-01",
            "source_version_id": "sv-01",
            "date": "2026-10-21",
        }
        vals = AssertionOpsMixin._assertion_values(
            payload=payload,
            target_id="assertion-approved-01",
            source=source,
            extraction_run_id="run-01",
            now="2026-10-21T12:00:00Z",
        )
        self.assertEqual(vals[30], 1)

    def test_hygiene_remediate_stale_candidates_demotes_assertion_and_search_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            _init_assertion_and_search_schema(conn)
            aid = "assertion-cd256f2fbf979bd7c87a5fae8fa8"
            conn.execute(
                "INSERT INTO assertion VALUES (?, 'candidate', 'current', 0, 1, 'P2', 'needs_review', 'src-1', '{}', '2026-09-14')",
                (aid,),
            )
            conn.execute(
                "INSERT INTO search_document VALUES ('sd-1', ?, 'candidate', 'current', 1, '2026-09-14')",
                (aid,),
            )
            pkg = {
                "findings": [
                    {
                        "proposed_fix": "archive_candidate_assertion",
                        "raw_data": {"assertion_id": aid},
                    }
                ]
            }
            sample_ids: list[str] = []
            count, archived = _remediate_stale_candidates(conn, pkg, "2026-10-21T15:00:00Z", sample_ids)
            conn.commit()
            self.assertEqual(count, 1)
            self.assertEqual(archived, [aid])

            asrt_row = conn.execute(
                "SELECT canonical_status, currentness_state, canonical_eligible, retrieval_eligible FROM assertion WHERE assertion_id=?",
                (aid,),
            ).fetchone()
            sd_row = conn.execute(
                "SELECT canonical_status, currentness_state, retrieval_eligible FROM search_document WHERE assertion_id=?",
                (aid,),
            ).fetchone()
            self.assertEqual(tuple(asrt_row), ("historical_candidate", "historical", 0, 0))
            self.assertEqual(tuple(sd_row), ("historical_candidate", "historical", 0))
            conn.close()

    def test_archive_stale_candidates_and_cleanup_executor_sync_search_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            _init_assertion_and_search_schema(conn)
            aid1 = "assertion-stale-1"
            aid2 = "assertion-stale-2"
            for aid in (aid1, aid2):
                conn.execute(
                    "INSERT INTO assertion VALUES (?, 'candidate', 'current', 0, 1, 'P2', 'needs_review', 'src-1', '{}', '2026-09-14')",
                    (aid,),
                )
                conn.execute(
                    "INSERT INTO search_document VALUES (?, ?, 'candidate', 'current', 1, '2026-09-14')",
                    (f"sd-{aid}", aid),
                )
            conn.commit()

            # 1. Test archive_stale_candidates._apply_disposition_mutation
            receipt_data: dict[str, object] = {}
            _apply_disposition_mutation(db_path, "archive", [aid1], "2026-10-21T15:00:00Z", receipt_data)

            row1 = conn.execute(
                "SELECT canonical_status, canonical_eligible, retrieval_eligible FROM assertion WHERE assertion_id=?",
                (aid1,),
            ).fetchone()
            sd1 = conn.execute(
                "SELECT canonical_status, retrieval_eligible FROM search_document WHERE assertion_id=?",
                (aid1,),
            ).fetchone()
            self.assertEqual(tuple(row1), ("historical_candidate", 0, 0))
            self.assertEqual(tuple(sd1), ("historical_candidate", 0))

            # 2. Test candidate_cleanup_core.executor._apply_reclassify_candidate_assertion
            before_row = conn.execute("SELECT * FROM assertion WHERE assertion_id=?", (aid2,)).fetchone()
            op = {
                "target_id": aid2,
                "before_state_hash": digest(_assertion_state(before_row)),
                "target_canonical_status": "validation_pending",
                "target_validation_state": "needs_review",
                "reason": "stale_candidate_demotion",
            }
            _apply_reclassify_candidate_assertion(conn, op, "2026-10-21T15:00:00Z")
            conn.commit()

            row2 = conn.execute(
                "SELECT canonical_status, canonical_eligible, retrieval_eligible FROM assertion WHERE assertion_id=?",
                (aid2,),
            ).fetchone()
            sd2 = conn.execute(
                "SELECT canonical_status, retrieval_eligible FROM search_document WHERE assertion_id=?",
                (aid2,),
            ).fetchone()
            self.assertEqual(tuple(row2), ("validation_pending", 0, 0))
            self.assertEqual(tuple(sd2), ("validation_pending", 0))
            conn.close()


class TestWave2McpSqlAndPreToolGuardrails(unittest.TestCase):
    """Wave 2: Verify MCP graphdb_read_query and pre-tool SQL guardrails."""

    def test_mcp_graphdb_read_query_strips_candidate_assertions_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "graph.sqlite"
            conn = sqlite3.connect(str(db_path))
            conn.execute(
                """
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    object_literal TEXT,
                    canonical_status TEXT,
                    canonical_eligible INTEGER,
                    retrieval_eligible INTEGER,
                    currentness_state TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO assertion VALUES ('assertion-cd256f2fbf979bd7c87a5fae8fa8', 'person_stephane', 'HAS_CLAIM_TEXT', 'Apex Global applies a screening heuristic favoring at least 7 years of experience.', 'candidate', 0, 1, 'current')"
            )
            conn.execute(
                "INSERT INTO assertion VALUES ('assertion-canonical-01', 'person_stephane', 'HAS_CLAIM_TEXT', 'Cameron Mercer leads NEA Q4 account alignment.', 'canonical', 1, 1, 'current')"
            )
            conn.commit()
            conn.close()

            # 1. Default SELECT * strips candidate row
            res_star = json.loads(
                graphdb_read_query(
                    "SELECT * FROM assertion WHERE subject_node_id = 'person_stephane'",
                    db_path=str(db_path),
                )
            )
            self.assertEqual(res_star["count"], 1)
            self.assertEqual(res_star["rows"][0]["assertion_id"], "assertion-canonical-01")

            # 2. Projected column SELECT without canonical_status in SELECT list still strips candidate row via assertion_id check
            res_proj = json.loads(
                graphdb_read_query(
                    "SELECT assertion_id, object_literal FROM assertion WHERE subject_node_id = 'person_stephane'",
                    db_path=str(db_path),
                )
            )
            self.assertEqual(res_proj["count"], 1)
            self.assertEqual(res_proj["rows"][0]["assertion_id"], "assertion-canonical-01")

            # 3. Diagnostic mode include_candidates=True returns both
            res_diag = json.loads(
                graphdb_read_query(
                    "SELECT * FROM assertion WHERE subject_node_id = 'person_stephane'",
                    db_path=str(db_path),
                    include_candidates=True,
                )
            )
            self.assertEqual(res_diag["count"], 2)

    def test_pre_tool_guard_blocks_unfiltered_assertion_queries(self) -> None:
        # 1. Unfiltered MCP graphdb_read_query is denied
        unfiltered_mcp = json.dumps(
            {
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": "graphdb-inspector",
                        "ToolName": "graphdb_read_query",
                        "Arguments": json.dumps(
                            {
                                "query": "SELECT * FROM assertion WHERE subject_node_id IN ('person_stephane') LIMIT 25;"
                            }
                        ),
                    },
                }
            }
        )
        res_deny = handle_tool_call_guard(unfiltered_mcp)
        self.assertIsNotNone(res_deny)
        self.assertEqual(res_deny["decision"], "deny")
        self.assertIn("CANONICAL_QUERY_FILTER_REQUIRED", res_deny["reason"])

        # 2. Canonical-filtered MCP graphdb_read_query is allowed
        filtered_mcp = json.dumps(
            {
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": "graphdb-inspector",
                        "ToolName": "graphdb_read_query",
                        "Arguments": json.dumps(
                            {
                                "query": "SELECT * FROM assertion WHERE subject_node_id = 'person_stephane' AND canonical_status = 'canonical' AND canonical_eligible = 1 LIMIT 25;"
                            }
                        ),
                    },
                }
            }
        )
        res_allow = handle_tool_call_guard(filtered_mcp)
        self.assertIsNotNone(res_allow)
        self.assertEqual(res_allow["decision"], "allow")

        # 3. Unfiltered ad-hoc sqlite3 run_command against assertion is denied
        unfiltered_cmd = json.dumps(
            {
                "toolCall": {
                    "name": "run_command",
                    "args": {
                        "CommandLine": "sqlite3 graph/nee_graph.sqlite \"SELECT * FROM assertion WHERE subject_node_id='person_stephane'\""
                    },
                }
            }
        )
        res_cmd_deny = handle_tool_call_guard(unfiltered_cmd)
        self.assertIsNotNone(res_cmd_deny)
        self.assertEqual(res_cmd_deny["decision"], "deny")
        self.assertIn("CANONICAL_QUERY_FILTER_REQUIRED", res_cmd_deny["reason"])

        # 4. Firewall error message mandates canonical_status and canonical_eligible
        err = GraphDBRequiredError(command="grep -r foo output/", matched_pattern="grep", target_path="output/")
        self.assertIn("canonical_status = 'canonical' AND canonical_eligible = 1", str(err))

    def test_ast_linter_enforces_canonical_assertion_query_filters(self) -> None:
        from tools.lint_ast_invariants import _check_canonical_query_filter_invariants

        bad_violations = _check_canonical_query_filter_invariants(
            "dual_pipeline/decision_adapter_core/row_reader.py",
            "SELECT * FROM assertion WHERE retrieval_eligible = 1",
        )
        self.assertEqual(len(bad_violations), 1)
        self.assertEqual(bad_violations[0].rule, "CANONICAL_ASSERTION_QUERY_FILTER_REQUIRED")

        good_violations = _check_canonical_query_filter_invariants(
            "dual_pipeline/decision_adapter_core/row_reader.py",
            "SELECT * FROM assertion WHERE retrieval_eligible = 1 AND canonical_status = 'canonical' AND canonical_eligible = 1",
        )
        self.assertEqual(good_violations, [])


if __name__ == "__main__":
    unittest.main()
