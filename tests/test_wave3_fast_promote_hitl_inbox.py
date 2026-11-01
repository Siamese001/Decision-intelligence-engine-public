"""Tests for Wave 3: Back-to-Back Meeting Fast-Path (--fast-promote & nee hitl-inbox)."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.hitl_governance_core.inbox_runner import (
    execute_hitl_inbox,
    list_inbox_items,
    park_shift_left_exceptions,
    resolve_inbox_item,
)


class Wave3FastPromoteHitlInboxTests(unittest.TestCase):
    def _create_test_graph(self, db_path: Path) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE node (
                    node_id TEXT PRIMARY KEY,
                    canonical_name TEXT NOT NULL,
                    node_type TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'canonical',
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE node_type_registry (
                    node_type_id TEXT PRIMARY KEY,
                    registry_state TEXT NOT NULL DEFAULT 'active',
                    identity_rule TEXT NOT NULL DEFAULT 'never auto-merge on name similarity'
                );
                INSERT INTO node_type_registry VALUES ('person', 'active', 'none'), ('stakeholder', 'active', 'none');
                CREATE TABLE canonical_alias (
                    canonical_alias_id TEXT PRIMARY KEY,
                    normalized_alias TEXT NOT NULL,
                    display_alias TEXT,
                    entity_id TEXT NOT NULL,
                    alias_type TEXT,
                    source_id TEXT,
                    confidence_label TEXT,
                    confidence_score REAL,
                    ambiguity_state TEXT,
                    preferred_flag INTEGER,
                    review_state TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT
                );
                CREATE TABLE assertion (
                    assertion_id TEXT PRIMARY KEY,
                    subject_node_id TEXT,
                    predicate TEXT,
                    object_node_id TEXT,
                    object_literal TEXT,
                    canonical_status TEXT DEFAULT 'canonical',
                    currentness_state TEXT DEFAULT 'current',
                    classification TEXT DEFAULT 'confidential',
                    audiences TEXT DEFAULT '["internal_consulting"]',
                    allowed_audiences_json TEXT DEFAULT '["internal_consulting"]',
                    extraction_confidence REAL,
                    superseded_at TEXT,
                    superseded_by_assertion_id TEXT
                );
                CREATE TABLE search_document (
                    search_document_id TEXT PRIMARY KEY,
                    object_type TEXT,
                    object_id TEXT,
                    title TEXT,
                    body TEXT,
                    canonical_status TEXT DEFAULT 'canonical',
                    currentness_state TEXT DEFAULT 'current',
                    retrieval_eligible INTEGER DEFAULT 1,
                    source_id TEXT,
                    assertion_id TEXT
                );
                """
            )
            conn.commit()

    def test_park_shift_left_exceptions_creates_inbox_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            pkg_path = temp_path / "package.json"
            pkg_path.write_text("{}", encoding="utf-8")

            digest = {
                "run_id": "run-fast-001",
                "total_candidates": 2,
                "tier_0_auto_promoted_count": 1,
                "tier_0_candidate_ids": ["cand-clean-01"],
                "has_exceptions": True,
                "exception_count": 1,
                "exceptions": [
                    {
                        "exception_id": "exc-contra-01",
                        "candidate_id": "cand-contra-01",
                        "kind": "CONTRADICTION",
                        "route": "CONTRADICTION",
                        "candidate_type": "assertion",
                        "text": "Platform team confirms readiness for preview dry-run.",
                        "summary": "Contradiction against assertion-prior-001",
                        "evidence_ids": ["ev-101"],
                        "existing_target_id": "assertion-prior-001",
                        "options": [
                            {"decision": "SUPERSEDE", "label": "Retire prior assertion and promote new claim"},
                            {"decision": "KEEP_CANDIDATE", "label": "Keep new claim as candidate"},
                        ],
                    }
                ],
            }

            parked = park_shift_left_exceptions(
                runtime_root=temp_path,
                output_dir=temp_path,
                digest=digest,
                package_path=pkg_path,
                source_title="Sprint Review Meeting",
            )
            self.assertEqual(len(parked), 1)
            self.assertEqual(parked[0]["exception_id"], "exc-contra-01")
            self.assertEqual(parked[0]["status"], "PENDING")

            inbox_file = temp_path / "hitl_inbox.json"
            self.assertTrue(inbox_file.is_file())
            data = json.loads(inbox_file.read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["exception_id"], "exc-contra-01")
            self.assertEqual(data[0]["source_title"], "Sprint Review Meeting")

            # Listing functions
            pending = list_inbox_items(temp_path, status="PENDING")
            self.assertEqual(len(pending), 1)
            resolved = list_inbox_items(temp_path, status="RESOLVED")
            self.assertEqual(len(resolved), 0)

    def test_resolve_contradiction_supersede_micro_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "graph.sqlite"
            self._create_test_graph(db_path)

            # Insert baseline prior assertion and candidate assertion
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state)
                       VALUES ('assertion-prior-001', 'canonical', 'current')"""
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, canonical_status, currentness_state, retrieval_eligible)
                       VALUES ('sd-prior-001', 'assertion-prior-001', 'canonical', 'current', 1)"""
                )
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state)
                       VALUES ('cand-contra-01', 'candidate', 'current')"""
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, canonical_status, currentness_state, retrieval_eligible)
                       VALUES ('sd-cand-001', 'cand-contra-01', 'candidate', 'current', 0)"""
                )
                conn.commit()

            # Seed inbox
            inbox_item = {
                "inbox_id": "inbox-01",
                "exception_id": "exc-contra-01",
                "candidate_id": "cand-contra-01",
                "run_id": "run-fast-001",
                "package_path": str(temp_path / "package.json"),
                "graph_path": str(db_path),
                "source_title": "Sprint Review",
                "created_at": "2026-10-30T10:00:00Z",
                "status": "PENDING",
                "kind": "CONTRADICTION",
                "route": "CONTRADICTION",
                "candidate_type": "assertion",
                "text": "Sprint goal was superseded by director decision.",
                "summary": "Contradiction against assertion-prior-001",
                "evidence_ids": ["ev-1"],
                "existing_target_id": "assertion-prior-001",
                "options": [{"decision": "SUPERSEDE"}, {"decision": "KEEP_CANDIDATE"}],
            }
            inbox_file = temp_path / "hitl_inbox.json"
            inbox_file.write_text(json.dumps([inbox_item], indent=2), encoding="utf-8")

            t0 = time.perf_counter()
            res = resolve_inbox_item(
                runtime_root=temp_path,
                target_id="exc-contra-01",
                decision="SUPERSEDE",
                reviewer="operator",
                rationale="Approved state supersession",
                graph_path=db_path,
            )
            elapsed = time.perf_counter() - t0

            # Must resolve in under 5.0 seconds
            self.assertLess(elapsed, 5.0)
            self.assertEqual(res["status"], "SUCCESS")
            self.assertEqual(res["decision"], "SUPERSEDE")

            # Verify SQLite mutations
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                prior_row = conn.execute(
                    "SELECT canonical_status, currentness_state, superseded_by_assertion_id FROM assertion WHERE assertion_id='assertion-prior-001'"
                ).fetchone()
                self.assertEqual(prior_row["canonical_status"], "superseded")
                self.assertEqual(prior_row["currentness_state"], "historical")
                self.assertEqual(prior_row["superseded_by_assertion_id"], "cand-contra-01")

                prior_sd = conn.execute(
                    "SELECT canonical_status, currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='assertion-prior-001'"
                ).fetchone()
                self.assertEqual(prior_sd["canonical_status"], "superseded")
                self.assertEqual(prior_sd["retrieval_eligible"], 0)

                cand_row = conn.execute(
                    "SELECT canonical_status, currentness_state FROM assertion WHERE assertion_id='cand-contra-01'"
                ).fetchone()
                self.assertEqual(cand_row["canonical_status"], "canonical")
                self.assertEqual(cand_row["currentness_state"], "current")

                cand_sd = conn.execute(
                    "SELECT canonical_status, currentness_state, retrieval_eligible FROM search_document WHERE assertion_id='cand-contra-01'"
                ).fetchone()
                self.assertEqual(cand_sd["canonical_status"], "canonical")
                self.assertEqual(cand_sd["retrieval_eligible"], 1)

            # Verify ledger and inbox
            ledger_file = temp_path / "hitl_decision_ledger.json"
            self.assertTrue(ledger_file.is_file())
            ledger_entries = json.loads(ledger_file.read_text(encoding="utf-8"))
            self.assertEqual(len(ledger_entries), 1)
            self.assertEqual(ledger_entries[0]["decision"], "SUPERSEDE")

            updated_inbox = json.loads(inbox_file.read_text(encoding="utf-8"))
            self.assertEqual(updated_inbox[0]["status"], "RESOLVED")
            self.assertEqual(updated_inbox[0]["resolution"]["decision"], "SUPERSEDE")

    def test_resolve_entity_alias_merge_alias_micro_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "graph.sqlite"
            self._create_test_graph(db_path)

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """INSERT INTO assertion (assertion_id, canonical_status, currentness_state)
                       VALUES ('cand-alias-01', 'candidate', 'current')"""
                )
                conn.execute(
                    """INSERT INTO search_document (search_document_id, assertion_id, canonical_status, currentness_state, retrieval_eligible)
                       VALUES ('sd-alias-01', 'cand-alias-01', 'candidate', 'current', 0)"""
                )
                conn.commit()

            inbox_item = {
                "inbox_id": "inbox-alias-01",
                "exception_id": "exc-alias-01",
                "candidate_id": "cand-alias-01",
                "run_id": "run-fast-002",
                "package_path": str(temp_path / "package.json"),
                "graph_path": str(db_path),
                "source_title": "Architecture Deep Dive",
                "created_at": "2026-10-30T10:00:00Z",
                "status": "PENDING",
                "kind": "ENTITY_ALIAS_REVIEW",
                "route": "ENTITY_ALIAS_REVIEW",
                "candidate_type": "entity_mention",
                "text": "Michael Reiss",
                "summary": "Entity alias review for 'Michael Reiss'",
                "evidence_ids": ["ev-2"],
                "existing_target_id": "person:michael-rice",
                "options": [{"decision": "MERGE_ALIAS"}, {"decision": "CREATE_NEW"}],
            }
            inbox_file = temp_path / "hitl_inbox.json"
            inbox_file.write_text(json.dumps([inbox_item], indent=2), encoding="utf-8")

            res = resolve_inbox_item(
                runtime_root=temp_path,
                target_id="exc-alias-01",
                decision="MERGE_ALIAS",
                reviewer="operator",
                rationale="Phonetic name variant confirmed",
                graph_path=db_path,
            )
            self.assertEqual(res["status"], "SUCCESS")

            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                ca_row = conn.execute(
                    "SELECT entity_id, normalized_alias, display_alias FROM canonical_alias WHERE entity_id='person:michael-rice'"
                ).fetchone()
                self.assertIsNotNone(ca_row)
                self.assertEqual(ca_row["entity_id"], "person:michael-rice")
                self.assertEqual(ca_row["normalized_alias"], "michael reiss")

                cand_sd = conn.execute(
                    "SELECT retrieval_eligible FROM search_document WHERE assertion_id='cand-alias-01'"
                ).fetchone()
                self.assertEqual(cand_sd["retrieval_eligible"], 1)

    def test_execute_hitl_inbox_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            db_path = temp_path / "graph.sqlite"
            self._create_test_graph(db_path)

            inbox_item = {
                "inbox_id": "inbox-cli-01",
                "exception_id": "exc-cli-01",
                "candidate_id": "cand-cli-01",
                "run_id": "run-fast-003",
                "package_path": str(temp_path / "package.json"),
                "graph_path": str(db_path),
                "source_title": "Executive Review",
                "created_at": "2026-10-30T10:00:00Z",
                "status": "PENDING",
                "kind": "CONTRADICTION",
                "route": "CONTRADICTION",
                "candidate_type": "assertion",
                "text": "Migration schedule moved forward by 2 weeks.",
                "summary": "Schedule conflict",
                "evidence_ids": ["ev-cli-1"],
                "existing_target_id": "assertion-prior-cli",
                "options": [{"decision": "SUPERSEDE"}, {"decision": "KEEP_CANDIDATE"}],
            }
            inbox_file = temp_path / "hitl_inbox.json"
            inbox_file.write_text(json.dumps([inbox_item], indent=2), encoding="utf-8")

            # 1. Test CLI listing (--list)
            args_list = MagicMock()
            args_list.runtime_root = temp_path
            args_list.inbox_file = inbox_file
            args_list.resolve = None
            args_list.batch = None
            args_list.json_output = True

            exit_code = execute_hitl_inbox(args_list)
            self.assertEqual(exit_code, 0)

            # 2. Test CLI resolution (--resolve)
            args_resolve = MagicMock()
            args_resolve.runtime_root = temp_path
            args_resolve.inbox_file = inbox_file
            args_resolve.resolve = "exc-cli-01"
            args_resolve.decision = "SUPERSEDE"
            args_resolve.batch = None
            args_resolve.reviewer = "operator"
            args_resolve.rationale = "CLI test resolution"
            args_resolve.json_output = True

            exit_code_res = execute_hitl_inbox(args_resolve)
            self.assertEqual(exit_code_res, 0)

            # Verify resolved
            pending = list_inbox_items(temp_path, inbox_file=inbox_file, status="PENDING")
            self.assertEqual(len(pending), 0)


if __name__ == "__main__":
    unittest.main()
