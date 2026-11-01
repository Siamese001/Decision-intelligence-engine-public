"""Unit tests for the Governed Graph Health & Remediation Scout."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import sha256_file
from dual_pipeline.graph_scout import (
    GraphScout,
)


def _init_test_graph(db_path: Path) -> None:
    """Initialize a mock SQLite knowledge graph for testing."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute(
        """CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            display_name TEXT,
            description TEXT,
            status TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    cursor.execute(
        """CREATE TABLE node_state (
            node_id TEXT PRIMARY KEY,
            lifecycle_state TEXT,
            validation_state TEXT,
            currentness_state TEXT,
            approval_state TEXT,
            rag_state TEXT,
            resolution_state TEXT,
            retrieval_eligibility TEXT,
            publication_state TEXT,
            review_state TEXT,
            legacy_status_text TEXT,
            intentional_orphan INTEGER DEFAULT 0,
            intentional_orphan_reason TEXT,
            updated_at TEXT
        )"""
    )

    cursor.execute(
        """CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            assertion_fingerprint TEXT,
            subject_node_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT NOT NULL,
            currentness_state TEXT NOT NULL,
            validation_state TEXT,
            confidence_score REAL,
            valid_from TEXT,
            recorded_at TEXT,
            created_at TEXT,
            updated_at TEXT
        )"""
    )

    # Insert sample nodes:
    # 1. Connected node A
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus_core', 'project', 'Project Nexus Core Architecture', 'Nexus Core',
            'Core telemetry and decision intelligence platform', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute("INSERT INTO node_state (node_id, intentional_orphan) VALUES ('project:nexus_core', 0)")

    # 2. Near-duplicate of node A
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:nexus_core_platform', 'project', 'Project Nexus Core Platform Architecture', 'Nexus Core Platform',
            'Core telemetry and decision intelligence platform initiative', 'active', 1, '2026-06-15T00:00:00Z', '2026-06-15T00:00:00Z'
        )"""
    )
    cursor.execute("INSERT INTO node_state (node_id, intentional_orphan) VALUES ('project:nexus_core_platform', 0)")

    # 3. Unrelated node B
    cursor.execute(
        """INSERT INTO node VALUES (
            'system:fpl_scada', 'system', 'APEX Grid SCADA Core', 'SCADA Core',
            'Grid operations and telemetry control network', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute("INSERT INTO node_state (node_id, intentional_orphan) VALUES ('system:fpl_scada', 0)")

    # 4. Intentional orphan node (should NOT be flagged)
    cursor.execute(
        """INSERT INTO node VALUES (
            'external:vendor_concept', 'external_concept', 'Vendor Isolated Concept', 'Vendor Concept',
            'Isolated concept', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute("INSERT INTO node_state (node_id, intentional_orphan) VALUES ('external:vendor_concept', 1)")

    # 5. Non-intentional orphan node (SHOULD be flagged)
    cursor.execute(
        """INSERT INTO node VALUES (
            'project:abandoned_initiative', 'project', 'Abandoned Initiative Prototype', 'Abandoned',
            'Unreferenced project', 'active', 1, '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z'
        )"""
    )
    cursor.execute("INSERT INTO node_state (node_id, intentional_orphan) VALUES ('project:abandoned_initiative', 0)")

    # Insert sample assertions:
    # Contradiction: older status vs newer status on project:nexus_core
    cursor.execute(
        """INSERT INTO assertion VALUES (
            'assertion-nexus-status-v1', 'fp1', 'project:nexus_core', 'HAS_DEPLOYMENT_STATUS',
            NULL, 'AWS Cloud Staging', 'canonical', 'current', 'approved', 0.90,
            '2026-06-01', '2026-06-01T10:00:00Z', '2026-06-01T10:00:00Z', '2026-06-01T10:00:00Z'
        )"""
    )
    cursor.execute(
        """INSERT INTO assertion VALUES (
            'assertion-nexus-status-v2', 'fp2', 'project:nexus_core', 'HAS_DEPLOYMENT_STATUS',
            NULL, 'GCP Cloud Production', 'canonical', 'current', 'approved', 0.95,
            '2026-07-15', '2026-07-15T10:00:00Z', '2026-07-15T10:00:00Z', '2026-07-15T10:00:00Z'
        )"""
    )

    # Stale candidate assertion (>30 days old)
    cursor.execute(
        """INSERT INTO assertion VALUES (
            'assertion-stale-candidate', 'fp3', 'project:nexus_core_platform', 'HAS_RISK',
            NULL, 'Dependency bottleneck on database drivers', 'candidate', 'current', 'needs_review', 0.60,
            '2026-05-01', '2026-05-01T10:00:00Z', '2026-05-01T10:00:00Z', '2026-05-01T10:00:00Z'
        )"""
    )

    conn.commit()
    conn.close()


class TestGraphScout(unittest.TestCase):
    """Test suite for the Graph Health & Remediation Scout."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.graph_path = self.temp_path / "test_graph.sqlite"
        _init_test_graph(self.graph_path)
        self.scout = GraphScout(
            graph_path=self.graph_path,
            runtime_root=self.temp_path,
            similarity_threshold=0.70,
            max_candidate_age_days=30,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_zero_mutation_enforcement(self) -> None:
        """Verify that the scout connection strictly prohibits mutations."""
        with self.scout._connect_ro() as conn:
            # SELECT is permitted
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM node")
            self.assertGreater(cursor.fetchone()[0], 0)

            # INSERT / UPDATE must fail with OperationalError
            with self.assertRaises(sqlite3.OperationalError):
                cursor.execute("INSERT INTO node (node_id, node_type, canonical_name) VALUES ('x', 'y', 'z')")

    def test_near_duplicate_entity_scan_and_proposal(self) -> None:
        """Verify near-duplicate entity detection and learning-change-proposal generation."""
        with self.scout._connect_ro() as conn:
            findings = self.scout.scan_duplicate_entities(conn)

        self.assertGreaterEqual(len(findings), 1)
        finding = findings[0]
        self.assertIn("Project Nexus Core", finding.canonical_name_a)
        self.assertIn("Project Nexus Core", finding.canonical_name_b)
        self.assertGreater(finding.similarity_score, 0.70)
        self.assertIsInstance(finding.margin_delta, float)

        # Emit proposal and validate schema
        proposals = self.scout.emit_proposals(
            duplicate_findings=findings,
            contradiction_findings=[],
            output_dir=self.temp_path,
            run_id="test-run-1",
        )
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]

        # Invariant checks
        self.assertEqual(proposal["contract_id"], "urn:nee:dual-pipeline:learning-change-proposal:1.0.0")
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])
        self.assertEqual(proposal["proposal_type"], "alias_addition")

        # Contract schema validation
        validate_artifact(proposal)

    def test_temporal_contradiction_scan_and_proposal(self) -> None:
        """Verify temporal contradiction detection and supersession proposal generation."""
        with self.scout._connect_ro() as conn:
            findings = self.scout.scan_temporal_contradictions(conn)

        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f.predicate, "HAS_DEPLOYMENT_STATUS")
        self.assertEqual(f.claim_older, "AWS Cloud Staging")
        self.assertEqual(f.claim_newer, "GCP Cloud Production")
        self.assertEqual(f.assertion_id_older, "assertion-nexus-status-v1")
        self.assertEqual(f.assertion_id_newer, "assertion-nexus-status-v2")

        # Emit proposal and validate schema
        proposals = self.scout.emit_proposals(
            duplicate_findings=[],
            contradiction_findings=findings,
            output_dir=self.temp_path,
            run_id="test-run-2",
        )
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]

        self.assertEqual(proposal["proposal_type"], "workflow")
        self.assertEqual(proposal["status"], "proposed")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["auto_apply"])
        validate_artifact(proposal)

    def test_orphan_and_clutter_scan(self) -> None:
        """Verify that intentional orphans are skipped and real orphans/ungrounded clutter are found."""
        with self.scout._connect_ro() as conn:
            clutter = self.scout.scan_orphan_and_clutter(conn)

        types = [c.finding_type for c in clutter]
        self.assertIn("orphan_node", types)
        self.assertIn("stale_candidate", types)

        target_ids = [c.target_id for c in clutter]
        # Abandoned initiative must be in clutter
        self.assertIn("project:abandoned_initiative", target_ids)
        # Intentional orphan must NOT be in clutter
        self.assertNotIn("external:vendor_concept", target_ids)
        # Ungrounded low-confidence stale candidate must be in clutter
        self.assertIn("assertion-stale-candidate", target_ids)

    def test_grounded_candidate_protected_unless_superseded(self) -> None:
        """Verify aged high-confidence/grounded candidates are NOT flagged for ARCHIVE unless superseded, and >=90d uncorroborated ones escalate for CONFIRM_OR_REMOVE."""
        conn = sqlite3.connect(self.graph_path)
        cur = conn.cursor()
        cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        cur.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, direct_quote TEXT, created_at TEXT)")
        cur.execute("INSERT INTO evidence_item VALUES ('ev-1', 'Verified strategic quote', '2026-04-01T10:00:00Z')")
        cur.execute("INSERT INTO evidence_item VALUES ('ev-recent', 'Recent corroborating quote', datetime('now', '-5 days'))")
        cur.execute("INSERT INTO assertion_evidence VALUES ('assertion-grounded-valid', 'ev-1')")
        cur.execute("INSERT INTO assertion_evidence VALUES ('assertion-grounded-corroborated', 'ev-recent')")
        cur.execute("INSERT INTO assertion_evidence VALUES ('assertion-grounded-superseded', 'ev-1')")
        # 1a. Grounded, unsuperseded >=90d candidate with NO new corroboration -> escalates as revalidation_review_90d (NEVER stale_candidate ARCHIVE)
        cur.execute(
            """INSERT INTO assertion VALUES (
                'assertion-grounded-valid', 'fp-g1', 'project:nexus_core', 'HAS_STRATEGIC_NOTE',
                NULL, 'Long-standing valid architectural constraint', 'candidate', 'current', 'needs_review', 0.90,
                '2026-04-01', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z'
            )"""
        )
        # 1b. Grounded, unsuperseded >=90d candidate WITH recent corroboration -> not flagged at all
        cur.execute(
            """INSERT INTO assertion VALUES (
                'assertion-grounded-corroborated', 'fp-g1b', 'project:nexus_core', 'HAS_STRATEGIC_NOTE',
                NULL, 'Recently corroborated architectural constraint', 'candidate', 'current', 'needs_review', 0.90,
                '2026-04-01', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z'
            )"""
        )
        # 2. Grounded aged candidate superseded by canonical assertion on HAS_DEPLOYMENT_STATUS (MUST be flagged as stale_candidate)
        cur.execute(
            """INSERT INTO assertion VALUES (
                'assertion-grounded-superseded', 'fp-g2', 'project:nexus_core', 'HAS_DEPLOYMENT_STATUS',
                NULL, 'Legacy On-Prem Lab', 'candidate', 'current', 'needs_review', 0.88,
                '2026-04-01', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z'
            )"""
        )
        # 3. Already-historical candidate (must NOT be re-flagged)
        cur.execute(
            """INSERT INTO assertion VALUES (
                'assertion-already-historical', 'fp-g3', 'project:nexus_core_platform', 'HAS_RISK',
                NULL, 'Old risk', 'candidate', 'historical', 'needs_review', 0.40,
                '2026-04-01', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z', '2026-04-01T10:00:00Z'
            )"""
        )
        conn.commit()
        conn.close()

        with self.scout._connect_ro() as ro_conn:
            clutter = self.scout.scan_orphan_and_clutter(ro_conn)

        by_id = {c.target_id: c for c in clutter}
        self.assertNotIn("assertion-grounded-corroborated", by_id)
        self.assertNotIn("assertion-already-historical", by_id)
        self.assertIn("assertion-grounded-valid", by_id)
        self.assertEqual(by_id["assertion-grounded-valid"].finding_type, "revalidation_review_90d")
        self.assertEqual(by_id["assertion-grounded-valid"].details["recommended_option"], "CONFIRM_OR_REMOVE")
        self.assertEqual(by_id["assertion-grounded-valid"].details["hitl_options"], ["CONFIRM", "REMOVE"])
        self.assertIn("assertion-grounded-superseded", by_id)
        self.assertEqual(by_id["assertion-grounded-superseded"].finding_type, "stale_candidate")
        self.assertEqual(
            by_id["assertion-grounded-superseded"].details["eviction_reason"],
            "superseded_by_newer_or_canonical_assertion",
        )
        self.assertIsNotNone(by_id["assertion-grounded-superseded"].details["superseded_by_assertion_id"])

    def test_wave1_retirement_guardrails_edges_candidates_and_locators(self) -> None:
        """Verify historical/superseded edges, fresh candidate nodes, and locator-backed assertions are never proposed for retirement."""
        conn = sqlite3.connect(self.graph_path)
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE edge (edge_id TEXT PRIMARY KEY, from_node_id TEXT, to_node_id TEXT, edge_type TEXT, superseded_by TEXT)"
        )
        cur.execute("CREATE TABLE legacy_edge_assertion_map (edge_id TEXT, assertion_id TEXT)")
        cur.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        cur.execute(
            "CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, direct_quote TEXT, locator_value TEXT, extraction_confidence REAL, created_at TEXT)"
        )

        # 1. Consolidated historical endpoint node & superseded edge
        cur.execute(
            """INSERT INTO node VALUES (
                'person:merged_alias', 'person', 'Merged Alias', 'Merged',
                'Consolidated duplicate', 'merged', 0, '2026-06-01T00:00:00Z', '2026-10-03T00:00:00Z'
            )"""
        )
        cur.execute(
            "INSERT INTO edge VALUES ('edge-superseded-1', 'person:merged_alias', 'project:nexus_core', 'OWNS_PRIMARY', 'assertion-nexus-status-v2')"
        )
        cur.execute(
            "INSERT INTO edge VALUES ('edge-historical-2', 'person:merged_alias', 'project:nexus_core', 'AFFILIATED_WITH', NULL)"
        )

        # 2. Fresh candidate node created today (must NOT be flagged as orphan)
        cur.execute(
            """INSERT INTO node VALUES (
                'entity:fresh_candidate_ws', 'entity', 'Workstream B – PRF Integration', 'WS B',
                'Newly extracted candidate node', 'candidate', 1, datetime('now', '-1 day'), datetime('now', '-1 day')
            )"""
        )
        cur.execute("INSERT INTO node_state (node_id, intentional_orphan) VALUES ('entity:fresh_candidate_ws', 0)")

        # 3. Aged candidate assertion grounded via locator_value / extraction_confidence (direct_quote is NULL)
        cur.execute(
            "INSERT INTO evidence_item VALUES ('ev-locator-only', NULL, '11:12', 1.0, datetime('now', '-5 days'))"
        )
        cur.execute("INSERT INTO assertion_evidence VALUES ('assertion-locator-grounded', 'ev-locator-only')")
        cur.execute(
            """INSERT INTO assertion VALUES (
                'assertion-locator-grounded', 'fp-loc', 'project:nexus_core', 'HAS_STRATEGIC_NOTE',
                NULL, 'Verbatim quote in object literal with timestamp locator', 'candidate', 'current', 'source_statement', 0.55,
                '2026-05-01', '2026-05-01T10:00:00Z', '2026-05-01T10:00:00Z', '2026-05-01T10:00:00Z'
            )"""
        )
        conn.commit()
        conn.close()

        with self.scout._connect_ro() as ro_conn:
            parity = self.scout.scan_edge_assertion_parity(ro_conn)
            clutter = self.scout.scan_orphan_and_clutter(ro_conn)

        dangling_ids = [p.edge_id for p in parity if p.issue_type == "dangling_endpoint"]
        self.assertNotIn("edge-superseded-1", dangling_ids)
        self.assertNotIn("edge-historical-2", dangling_ids)

        clutter_ids = {c.target_id for c in clutter}
        self.assertNotIn("entity:fresh_candidate_ws", clutter_ids)
        self.assertNotIn("assertion-locator-grounded", clutter_ids)

    def test_full_run_zero_mutation_guarantee(self) -> None:
        """Verify end-to-end scout run preserves exact before and after graph SHA-256."""
        before_hash = sha256_file(self.graph_path)

        out_dir = self.temp_path / "output_scout"
        report = self.scout.run(output_dir=out_dir)

        after_hash = sha256_file(self.graph_path)

        # Graph MUST remain strictly bit-for-bit identical
        self.assertEqual(before_hash, after_hash)
        self.assertTrue(report["summary"]["zero_mutation_verified"])
        self.assertTrue(Path(report["report_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
