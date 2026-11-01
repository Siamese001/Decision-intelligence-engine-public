"""End-to-End E2E test for maintenance tool mutation approval gating."""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from dual_pipeline.cli.ingest_resolve_cmd import execute_ingest_resolve
from dual_pipeline.core import ROOT
from tools.archive_stale_candidates import archive_stale_candidates
from tools.graph_gc import run_gc
from tools.mcp_test_orchestrator_server import provision_isolated_test_db
from tools.prune_superseded_search_docs import prune_search_and_align_sidecar


class TestMaintenanceApprovalE2E(unittest.TestCase):
    """E2E suite verifying maintenance mutation approval gating and resolution lifecycle."""

    def setUp(self) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="nee_maint_approval_e2e_"))
        self.addCleanup(lambda: shutil.rmtree(self.temp_dir, ignore_errors=True))

        prov_res = json.loads(provision_isolated_test_db())
        self.isolated_db_path = Path(prov_res["isolated_db_path"])
        self.addCleanup(lambda: self.isolated_db_path.unlink(missing_ok=True))

        self.ledger_path = self.temp_dir / "hitl_decision_ledger.sqlite"

    def _setup_stale_data(self) -> None:
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS risks (
                risk_id TEXT PRIMARY KEY,
                title TEXT,
                stale_after TEXT,
                created_at TEXT
            )"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO risks (risk_id, title, stale_after, created_at)
               VALUES ('risk-e2e-001', 'Stale Cloud Outage Risk', '2020-01-01', '2019-01-01')"""
        )
        conn.commit()
        conn.close()

    def test_e2e_graph_gc_unapproved_halts_without_mutation(self) -> None:
        """Verify graph_gc creates an approval request and halts with AWAITING_APPROVAL, leaving DB untouched."""
        self._setup_stale_data()

        result = run_gc(
            self.isolated_db_path,
            as_of=date(2026, 1, 1),
            prune_orphans=False,
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )

        self.assertEqual(result["status"], "AWAITING_APPROVAL")
        self.assertFalse(result["mutated"])
        self.assertEqual(result["stale_count"], 1)
        self.assertIn("approval_request_id", result)
        self.assertTrue(result["approval_request_id"].startswith("req-"))

        # Verify DB is untouched
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM risks WHERE risk_id = 'risk-e2e-001'")
        self.assertEqual(cur.fetchone()[0], 1)
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='archived_risks'")
        if cur.fetchone()[0] > 0:
            cur.execute("SELECT count(*) FROM archived_risks WHERE risk_id = 'risk-e2e-001'")
            self.assertEqual(cur.fetchone()[0], 0)
        conn.close()

    def test_e2e_graph_gc_approved_via_ingest_resolve_mutates_cleanly(self) -> None:
        """Verify after approval via ingest-resolve, re-running GC applies real mutation."""
        self._setup_stale_data()

        # Step 1: Trigger GC to register request
        result1 = run_gc(
            self.isolated_db_path,
            as_of=date(2026, 1, 1),
            prune_orphans=False,
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        req_id = result1["approval_request_id"]
        self.assertFalse(result1["mutated"])

        # Step 2: Approve the request via nee ingest-resolve
        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator_maintenance_test",
            rationale="Approved stale risk archival for test",
            ledger_path=self.ledger_path,
            graph=str(self.isolated_db_path),
        )
        resolve_res = execute_ingest_resolve(args)
        self.assertEqual(resolve_res["status"], "APPROVED")

        # Step 3: Run GC again with the approved request
        result2 = run_gc(
            self.isolated_db_path,
            as_of=date(2026, 1, 1),
            prune_orphans=False,
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )

        self.assertEqual(result2["status"], "archived")
        self.assertTrue(result2["mutated"])
        self.assertEqual(result2["total_archived"], 1)

        # Step 4: Verify in database that risk was moved to archive
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM risks WHERE risk_id = 'risk-e2e-001'")
        self.assertEqual(cur.fetchone()[0], 0)
        cur.execute("SELECT count(*) FROM archived_risks WHERE risk_id = 'risk-e2e-001'")
        self.assertEqual(cur.fetchone()[0], 1)
        conn.close()

    def test_e2e_prune_search_docs_approval_lifecycle(self) -> None:
        """Verify search document pruning halts at AWAITING_APPROVAL and deletes rows only after approval."""
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                currentness_state TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS search_document (
                search_document_id TEXT PRIMARY KEY,
                assertion_id TEXT,
                retrieval_eligible INTEGER,
                currentness_state TEXT
            )"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO search_document (
                search_document_id, assertion_id, retrieval_eligible, currentness_state
            ) VALUES ('doc-test-1', 'asrt-1', 0, 'superseded')"""
        )
        conn.commit()
        conn.close()

        # Step 1: Run pruning without approval -> AWAITING_APPROVAL
        res1 = prune_search_and_align_sidecar(
            self.isolated_db_path,
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res1["status"], "AWAITING_APPROVAL")
        self.assertFalse(res1["mutated"])
        req_id = res1["approval_request_id"]

        # Verify row still exists
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM search_document WHERE search_document_id = 'doc-test-1'")
        self.assertEqual(cur.fetchone()[0], 1)
        conn.close()

        # Step 2: Approve via ingest-resolve
        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator_maint",
            rationale="Approved search doc pruning",
            ledger_path=self.ledger_path,
            graph=str(self.isolated_db_path),
        )
        resolve_res = execute_ingest_resolve(args)
        self.assertEqual(resolve_res["status"], "APPROVED")

        # Step 3: Run pruning again -> mutates
        res2 = prune_search_and_align_sidecar(
            self.isolated_db_path,
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res2["status"], "PASS")
        self.assertTrue(res2["mutated"])

        # Verify pruned count
        self.assertEqual(res2["search_documents_pruned"], 1)

    def test_e2e_archive_stale_candidates_targeted_approval_lifecycle(self) -> None:
        """Verify targeted stale candidate archival halts at AWAITING_APPROVAL and archives only target IDs."""
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                created_at TEXT,
                updated_at TEXT,
                scope_text TEXT,
                object_literal TEXT,
                object_node_id TEXT
            )"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO assertion (
                assertion_id, canonical_status, canonical_eligible, validation_state, created_at
            ) VALUES
                ('cand-target-1', 'candidate', 1, 'needs_review', '2020-01-01T00:00:00Z'),
                ('cand-untargeted-2', 'candidate', 1, 'needs_review', '2020-01-01T00:00:00Z')"""
        )
        conn.commit()
        conn.close()

        # Step 1: Run targeted archival -> AWAITING_APPROVAL
        res1 = archive_stale_candidates(
            self.isolated_db_path,
            target_assertion_ids=["cand-target-1"],
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res1["status"], "AWAITING_APPROVAL")
        self.assertFalse(res1["mutated"])
        self.assertEqual(res1["candidates_found"], 1)
        req_id = res1["approval_request_id"]

        # Step 2: Approve via ingest-resolve
        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator_maint",
            rationale="Approved targeted candidate archival",
            ledger_path=self.ledger_path,
            graph=str(self.isolated_db_path),
        )
        resolve_res = execute_ingest_resolve(args)
        self.assertEqual(resolve_res["status"], "APPROVED")

        # Step 3: Run targeted archival again -> mutates only target ID
        res2 = archive_stale_candidates(
            self.isolated_db_path,
            target_assertion_ids=["cand-target-1"],
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res2["status"], "PASS")
        self.assertTrue(res2["mutated"])
        self.assertEqual(res2["candidates_archived"], 1)

        # Verify only target was archived
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute("SELECT canonical_status FROM assertion WHERE assertion_id = 'cand-target-1'")
        self.assertEqual(cur.fetchone()[0], "historical_candidate")
        cur.execute("SELECT canonical_status FROM assertion WHERE assertion_id = 'cand-untargeted-2'")
        self.assertEqual(cur.fetchone()[0], "candidate")
        conn.close()

    def test_e2e_archive_stale_candidates_with_rich_provenance_and_promotion(self) -> None:
        """Verify candidate processing joins full node/evidence context and executes promotion disposition."""
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS node (
                node_id TEXT PRIMARY KEY,
                canonical_name TEXT,
                node_type TEXT,
                active INTEGER DEFAULT 1
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS source (
                source_id TEXT PRIMARY KEY,
                source_name TEXT,
                source_type TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS evidence_item (
                evidence_id TEXT PRIMARY KEY,
                source_id TEXT,
                direct_quote TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT,
                PRIMARY KEY (assertion_id, evidence_id)
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                canonical_status TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                created_at TEXT,
                updated_at TEXT,
                scope_text TEXT,
                object_literal TEXT,
                object_node_id TEXT
            )"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO node (node_id, canonical_name, node_type, active)
               VALUES ('node-matt', 'Matt Barrows', 'person', 1),
                      ('node-alex', 'Alex Brennan', 'person', 1)"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO source (source_id, source_name, source_type)
               VALUES ('src-org-chart', 'Q3 Org Chart Review', 'document')"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO evidence_item (evidence_id, source_id, direct_quote)
               VALUES ('ev-quote-1', 'src-org-chart', 'Matt Barrows reports directly to Alex Brennan.')"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO assertion_evidence (assertion_id, evidence_id)
               VALUES ('cand-rich-1', 'ev-quote-1')"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO assertion (
                assertion_id, subject_node_id, predicate, object_node_id,
                canonical_status, canonical_eligible, validation_state,
                created_at, scope_text
            ) VALUES (
                'cand-rich-1', 'node-matt', 'REPORTS_TO', 'node-alex',
                'candidate', 1, 'needs_review',
                '2020-01-01T00:00:00Z', 'Matt Barrows reports to Alex Brennan'
            )"""
        )
        conn.commit()
        conn.close()

        # Step 1: Run with disposition="promote" -> AWAITING_APPROVAL
        res1 = archive_stale_candidates(
            self.isolated_db_path,
            target_assertion_ids=["cand-rich-1"],
            disposition="promote",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res1["status"], "AWAITING_APPROVAL")
        self.assertFalse(res1["mutated"])
        self.assertEqual(res1["candidates_found"], 1)
        self.assertEqual(res1["disposition"], "promote")
        req_id = res1["approval_request_id"]

        # Verify candidate_details context is rich and complete
        details = res1["candidate_details"][0]
        self.assertEqual(details["assertion_id"], "cand-rich-1")
        self.assertEqual(details["subject_name"], "Matt Barrows")
        self.assertEqual(details["object_name"], "Alex Brennan")
        self.assertEqual(details["predicate"], "REPORTS_TO")
        self.assertEqual(details["claim_text"], "Matt Barrows reports to Alex Brennan")
        self.assertEqual(details["evidence_quote"], "Matt Barrows reports directly to Alex Brennan.")
        self.assertEqual(details["source_title"], "Q3 Org Chart Review")
        self.assertEqual(details["recommended_option"], "PROMOTE")

        # Step 2: Approve via ingest-resolve
        args = argparse.Namespace(
            request_id=req_id,
            decision="approve",
            runtime_root=self.temp_dir,
            reviewer="operator_maint",
            rationale="Approved candidate promotion to canonical",
            ledger_path=self.ledger_path,
            graph=str(self.isolated_db_path),
        )
        resolve_res = execute_ingest_resolve(args)
        self.assertEqual(resolve_res["status"], "APPROVED")

        # Step 3: Run again -> mutates to canonical status
        res2 = archive_stale_candidates(
            self.isolated_db_path,
            target_assertion_ids=["cand-rich-1"],
            disposition="promote",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res2["status"], "PASS")
        self.assertTrue(res2["mutated"])
        self.assertEqual(res2["candidates_promoted"], 1)

        # Step 4: Verify DB row is now canonical and confirmed
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute("SELECT canonical_status, canonical_eligible, validation_state FROM assertion WHERE assertion_id = 'cand-rich-1'")
        row = cur.fetchone()
        self.assertEqual(row[0], "canonical")
        self.assertEqual(row[1], 1)
        self.assertEqual(row[2], "confirmed")
        conn.close()

    def test_e2e_archive_stale_candidates_protects_grounded_unsuperseded_candidates(self) -> None:
        """Verify archive disposition skips grounded unsuperseded candidates while archiving superseded ones."""
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS evidence_item (
                evidence_id TEXT PRIMARY KEY,
                direct_quote TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion_evidence (
                assertion_id TEXT,
                evidence_id TEXT
            )"""
        )
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                confidence_score REAL,
                superseded_by_assertion_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                scope_text TEXT
            )"""
        )
        cur.execute("INSERT OR REPLACE INTO evidence_item VALUES ('ev-e2e-1', 'Grounded evidence quote')")
        cur.execute("INSERT OR REPLACE INTO assertion_evidence VALUES ('cand-grounded-unsup', 'ev-e2e-1')")
        cur.execute("INSERT OR REPLACE INTO assertion_evidence VALUES ('cand-grounded-sup', 'ev-e2e-1')")
        cur.execute(
            """INSERT OR REPLACE INTO assertion (
                assertion_id, subject_node_id, predicate, object_literal,
                canonical_status, currentness_state, canonical_eligible, validation_state,
                confidence_score, created_at
            ) VALUES
                ('cand-grounded-unsup', 'subj-1', 'HAS_CONSTRAINT', 'Valid unreplaced constraint',
                 'candidate', 'current', 1, 'needs_review', 0.91, '2020-01-01T00:00:00Z'),
                ('cand-grounded-sup', 'subj-1', 'HAS_STATUS', 'Draft Phase',
                 'candidate', 'current', 1, 'needs_review', 0.89, '2020-01-01T00:00:00Z'),
                ('canon-active-sup', 'subj-1', 'HAS_STATUS', 'Production Active',
                 'canonical', 'current', 1, 'confirmed', 0.95, '2026-01-01T00:00:00Z')"""
        )
        conn.commit()
        conn.close()

        res = archive_stale_candidates(
            self.isolated_db_path,
            disposition="archive",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res["status"], "AWAITING_APPROVAL")
        self.assertEqual(res["candidates_found"], 1)
        self.assertEqual(res["candidate_details"][0]["assertion_id"], "cand-grounded-sup")

    def test_e2e_90_day_revalidation_confirm_and_remove_dispositions(self) -> None:
        """Verify 90-day escalated candidates can be resolved via confirm (retain) or remove dispositions."""
        conn = sqlite3.connect(self.isolated_db_path)
        cur = conn.cursor()
        cur.execute(
            """CREATE TABLE IF NOT EXISTS assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                canonical_eligible INTEGER,
                validation_state TEXT,
                confidence_score REAL,
                superseded_by_assertion_id TEXT,
                created_at TEXT,
                updated_at TEXT,
                scope_text TEXT
            )"""
        )
        cur.execute(
            """INSERT OR REPLACE INTO assertion (
                assertion_id, subject_node_id, predicate, object_literal,
                canonical_status, currentness_state, canonical_eligible, validation_state,
                confidence_score, created_at, updated_at
            ) VALUES
                ('cand-90d-confirm', 'subj-1', 'OWNS_SCOPE', 'Retained 90d Scope',
                 'candidate', 'current', 1, 'needs_review', 0.88, '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z'),
                ('cand-90d-remove', 'subj-1', 'OWNS_SCOPE', 'Obsolete 90d Scope',
                 'candidate', 'current', 1, 'needs_review', 0.85, '2020-01-01T00:00:00Z', '2020-01-01T00:00:00Z')"""
        )
        conn.commit()
        conn.close()

        # 1. Confirmcand-90d-confirm
        res_c1 = archive_stale_candidates(
            self.isolated_db_path,
            max_age_days=90,
            target_assertion_ids=["cand-90d-confirm"],
            disposition="confirm",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res_c1["status"], "AWAITING_APPROVAL")
        execute_ingest_resolve(
            argparse.Namespace(
                request_id=res_c1["approval_request_id"],
                decision="approve",
                runtime_root=self.temp_dir,
                reviewer="operator_90d",
                rationale="Confirming 90d candidate",
                ledger_path=self.ledger_path,
                graph=str(self.isolated_db_path),
            )
        )
        res_c2 = archive_stale_candidates(
            self.isolated_db_path,
            max_age_days=90,
            target_assertion_ids=["cand-90d-confirm"],
            disposition="confirm",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertTrue(res_c2["mutated"])
        self.assertEqual(res_c2["candidates_kept"], 1)

        # 2. Remove cand-90d-remove
        res_r1 = archive_stale_candidates(
            self.isolated_db_path,
            max_age_days=90,
            target_assertion_ids=["cand-90d-remove"],
            disposition="remove",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertEqual(res_r1["status"], "AWAITING_APPROVAL")
        execute_ingest_resolve(
            argparse.Namespace(
                request_id=res_r1["approval_request_id"],
                decision="approve",
                runtime_root=self.temp_dir,
                reviewer="operator_90d",
                rationale="Removing obsolete 90d candidate",
                ledger_path=self.ledger_path,
                graph=str(self.isolated_db_path),
            )
        )
        res_r2 = archive_stale_candidates(
            self.isolated_db_path,
            max_age_days=90,
            target_assertion_ids=["cand-90d-remove"],
            disposition="remove",
            ledger_path=self.ledger_path,
            runtime_root=self.temp_dir,
        )
        self.assertTrue(res_r2["mutated"])
        self.assertEqual(res_r2["candidates_archived"], 1)


if __name__ == "__main__":
    unittest.main()




