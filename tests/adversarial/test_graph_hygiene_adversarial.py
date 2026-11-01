"""Adversarial and edge-case tests for Knowledge Graph Hygiene time-staleness and supersession safeguards."""
from __future__ import annotations

from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest

from dual_pipeline.graph_freshness_core.agents.detection import FreshnessWarden
from dual_pipeline.graph_freshness_core.policy import GraphFreshnessPolicy
from dual_pipeline.graph_scout import GraphScout
from dual_pipeline.staleness_core.ttl_sweeper import AssertionTTLSweeper
from tools.archive_stale_candidates import archive_stale_candidates
from tools.graph_gc import _find_stale_entities


class TestGraphHygieneAdversarial(unittest.TestCase):
    """Covers boundary payloads, malformed timestamps, invariant bypass attempts, and exact production regression."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "adversarial_graph.sqlite"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _init_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                active INTEGER DEFAULT 1
            )"""
        )
        conn.execute(
            """CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                subject_node_id TEXT,
                predicate TEXT,
                object_node_id TEXT,
                object_literal TEXT,
                canonical_status TEXT,
                currentness_state TEXT,
                validation_state TEXT,
                status TEXT,
                confidence_score REAL,
                superseded_by_assertion_id TEXT,
                qualifiers_json TEXT,
                scope_text TEXT,
                valid_from TEXT,
                recorded_at TEXT,
                stale_after TEXT,
                created_at TEXT,
                updated_at TEXT,
                retrieval_eligible INTEGER DEFAULT 1,
                canonical_eligible INTEGER DEFAULT 1
            )"""
        )
        conn.execute("CREATE TABLE assertion_evidence (assertion_id TEXT, evidence_id TEXT)")
        conn.execute("CREATE TABLE evidence_item (evidence_id TEXT PRIMARY KEY, direct_quote TEXT, source_id TEXT)")
        conn.execute("CREATE TABLE source (source_id TEXT PRIMARY KEY, source_name TEXT)")

    def test_boundary_payload_empty_and_missing_tables(self) -> None:
        """Category 1 (Boundary payload): Empty graph or missing tables must not fail or emit false positives."""
        conn = sqlite3.connect(self.db_path)
        conn.close()
        scout = GraphScout(graph_path=self.db_path, runtime_root=self.temp_path)
        with scout._connect_ro() as ro_conn:
            self.assertEqual(scout.scan_orphan_and_clutter(ro_conn), [])
            self.assertEqual(scout.scan_temporal_contradictions(ro_conn), [])
            self.assertEqual(scout.scan_edge_assertion_parity(ro_conn), [])

    def test_malformed_timestamps_and_null_fields(self) -> None:
        """Category 2 (Malformed input): Invalid ISO timestamps, empty strings, and null confidence scores."""
        with sqlite3.connect(self.db_path) as conn:
            self._init_schema(conn)
            conn.execute("INSERT INTO node VALUES ('n1', 'person', 'Alice', 1)")
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_literal,
                    canonical_status, currentness_state, validation_state, status,
                    confidence_score, stale_after, created_at
                ) VALUES
                    ('bad-ts-1', 'n1', 'HAS_ROLE', 'Lead', 'candidate', 'current', 'needs_review', 'PROPOSED', NULL, 'not-a-date', 'corrupted-date'),
                    ('bad-ts-2', 'n1', 'HAS_ROLE', 'Lead', 'candidate', 'current', 'needs_review', 'CONFIRMED', 0.95, '', '')"""
            )
            conn.commit()

        scout = GraphScout(graph_path=self.db_path, runtime_root=self.temp_path)
        with scout._connect_ro() as ro_conn:
            clutter = scout.scan_orphan_and_clutter(ro_conn)
            self.assertEqual([c for c in clutter if c.finding_type == "stale_candidate"], [])

        with sqlite3.connect(self.db_path) as conn:
            sweeper = AssertionTTLSweeper()
            res = sweeper.sweep_stale_assertions(conn, as_of_date="invalid-as-of")
            self.assertEqual(res["status"], "PASS")
            self.assertEqual(res["swept_count"], 0)

    def test_invariant_bypass_attempt_age_only_eviction_blocked(self) -> None:
        """Category 3 (Invariant bypass attempt): Attempting to evict grounded unsuperseded assertions via age alone."""
        with sqlite3.connect(self.db_path) as conn:
            self._init_schema(conn)
            conn.execute("INSERT INTO node VALUES ('person:stephan', 'person', 'Cameron Mercer', 1)")
            conn.execute("INSERT INTO evidence_item VALUES ('ev-s1', 'Stephan confirmed the architecture', 'src-1')")
            conn.execute("INSERT INTO assertion_evidence VALUES ('asrt-stephan-valid', 'ev-s1')")
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_literal,
                    canonical_status, currentness_state, validation_state, status,
                    confidence_score, qualifiers_json, recorded_at, stale_after, created_at
                ) VALUES (
                    'asrt-stephan-valid', 'person:stephan', 'CHAMPIONS_ARCHITECTURE', 'Zero-Copy Mesh',
                    'candidate', 'current', 'needs_review', 'CONFIRMED',
                    0.92, '{}', '2025-01-01T00:00:00Z', '2025-06-01', '2025-01-01T00:00:00Z'
                )"""
            )
            conn.execute(
                """CREATE TABLE decisions (
                    decision_id TEXT PRIMARY KEY,
                    title TEXT,
                    status TEXT,
                    stale_after TEXT
                )"""
            )
            conn.execute(
                "INSERT INTO decisions VALUES ('dec-open-1', 'Active Core Decision', 'open', '2025-01-01')"
            )
            conn.commit()

        # 1. archive_stale_candidates with disposition='archive' must refuse to flag grounded unsuperseded candidate
        res_arch = archive_stale_candidates(
            self.db_path,
            max_age_days=30,
            target_assertion_ids=["asrt-stephan-valid"],
            disposition="archive",
        )
        self.assertEqual(res_arch["candidates_found"], 0)
        self.assertEqual(res_arch["status"], "clean")

        # 2. AssertionTTLSweeper must retain unsuperseded CONFIRMED assertion even when stale_after < as_of_date
        with sqlite3.connect(self.db_path) as conn:
            sweeper = AssertionTTLSweeper()
            sweep_res = sweeper.sweep_stale_assertions(conn, as_of_date="2026-10-16")
            self.assertEqual(sweep_res["swept_count"], 0)
            self.assertIn("asrt-stephan-valid", sweep_res["retained_unsuperseded_ids"])

            # 3. FreshnessWarden must route grounded unsuperseded old assertion to needs_revalidation_ids
            conn.execute("UPDATE assertion SET stale_after = NULL WHERE assertion_id = 'asrt-stephan-valid'")
            warden_res = FreshnessWarden().scan(conn, GraphFreshnessPolicy(default_ttl_days=30))
            self.assertNotIn("asrt-stephan-valid", warden_res.stale_assertion_ids)
            self.assertIn("asrt-stephan-valid", warden_res.needs_revalidation_ids)

            # 4. graph_gc _find_stale_entities must skip open decisions even when stale_after < as_of
            stale_entities = _find_stale_entities(conn, as_of=date(2026, 10, 16))
            self.assertNotIn("decisions", stale_entities)

        # 5. GraphScout on >90-day grounded unsuperseded candidate must escalate as revalidation_review_90d (CONFIRM vs REMOVE), never stale_candidate ARCHIVE
        scout = GraphScout(graph_path=self.db_path, runtime_root=self.temp_path, max_candidate_age_days=30)
        with scout._connect_ro() as ro_conn:
            clutter = scout.scan_orphan_and_clutter(ro_conn)
        stale_arch = [c for c in clutter if c.finding_type == "stale_candidate"]
        self.assertEqual(stale_arch, [])
        reval_90d = [c for c in clutter if c.finding_type == "revalidation_review_90d"]
        self.assertEqual(len(reval_90d), 1)
        self.assertEqual(reval_90d[0].target_id, "asrt-stephan-valid")
        self.assertEqual(reval_90d[0].details["recommended_option"], "CONFIRM_OR_REMOVE")
        self.assertEqual(reval_90d[0].details["hitl_options"], ["CONFIRM", "REMOVE"])

    def test_exact_production_regression_oct_16_hygiene_false_positives(self) -> None:
        """Category 4 (Exact production regression): Reproduces the Oct 16 hygiene scan false-positive scenario."""
        with sqlite3.connect(self.db_path) as conn:
            self._init_schema(conn)
            conn.execute("INSERT INTO node VALUES ('node:brett', 'person', 'Brett Gavagni', 1)")
            conn.execute("INSERT INTO node VALUES ('node:npl', 'org', 'Apex Global', 1)")
            conn.execute("INSERT INTO evidence_item VALUES ('ev-oct16', 'Direct transcript evidence', 'src-oct16')")
            # Connect evidence to both candidates
            conn.execute("INSERT INTO assertion_evidence VALUES ('assertion-brett-unique', 'ev-oct16')")
            conn.execute("INSERT INTO assertion_evidence VALUES ('assertion-brett-related', 'ev-oct16')")

            # 1. Already historical candidate from Sept 15 (assertion-d0fb114643da19b86194840ea717 equivalent)
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_node_id,
                    canonical_status, currentness_state, validation_state, confidence_score, created_at
                ) VALUES (
                    'assertion-d0fb114643da19b86194840ea717', 'node:brett', 'RELATED_TO', 'node:npl',
                    'candidate', 'historical', 'source_statement', 0.95, '2026-09-15T12:00:00Z'
                )"""
            )
            # 2. Grounded unique claim > 30 days old with NO superseding assertion (must NOT be archived)
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_literal,
                    canonical_status, currentness_state, validation_state, confidence_score, created_at
                ) VALUES (
                    'assertion-brett-unique', 'node:brett', 'OWNS_DELIVERABLE', 'Grid Telemetry Spec',
                    'candidate', 'current', 'needs_review', 0.92, '2026-09-10T12:00:00Z'
                )"""
            )
            # 3. Generic RELATED_TO candidate superseded by canonical AFFILIATED_WITH on same (subject, object)
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_node_id,
                    canonical_status, currentness_state, validation_state, confidence_score, created_at
                ) VALUES
                    ('assertion-brett-related', 'node:brett', 'RELATED_TO', 'node:npl',
                     'candidate', 'current', 'needs_review', 0.85, '2026-09-10T12:00:00Z'),
                    ('assertion-brett-affiliated', 'node:brett', 'AFFILIATED_WITH', 'node:npl',
                     'canonical', 'current', 'confirmed', 0.96, '2026-09-20T12:00:00Z')"""
            )
            conn.commit()

        scout = GraphScout(graph_path=self.db_path, runtime_root=self.temp_path, max_candidate_age_days=30)
        with scout._connect_ro() as ro_conn:
            clutter = scout.scan_orphan_and_clutter(ro_conn)

        flagged_ids = {c.target_id: c for c in clutter if c.finding_type == "stale_candidate"}
        self.assertNotIn("assertion-d0fb114643da19b86194840ea717", flagged_ids)
        self.assertNotIn("assertion-brett-unique", flagged_ids)
        self.assertIn("assertion-brett-related", flagged_ids)
        self.assertEqual(
            flagged_ids["assertion-brett-related"].details["superseded_by_assertion_id"],
            "assertion-brett-affiliated",
        )

    def test_jarvis_on_lens_cross_project_contamination_and_antipattern_guards(self) -> None:
        """Category 5 (Exact production regression & invariant bypass): Jarvis claim on workstream_lens and leaked candidate guards."""
        with sqlite3.connect(self.db_path) as conn:
            self._init_schema(conn)
            conn.execute("INSERT INTO node VALUES ('workstream_lens', 'workstream', 'LENS Workstream', 1)")
            conn.execute("INSERT INTO node VALUES ('person_amit', 'person', 'Amit Ayer', 1)")
            conn.execute("INSERT INTO node VALUES ('stakeholder:amit-ayer', 'stakeholder', 'Amit Ayer', 1)")
            conn.execute("INSERT INTO source VALUES ('src-jarvis', 'Jarvis Architecture Sync')")
            conn.execute("INSERT INTO evidence_item VALUES ('ev-jarvis-1', 'I think I need to create a project in Jira for Jarvis.', 'src-jarvis')")
            conn.execute("INSERT INTO assertion_evidence VALUES ('assertion-ac53023302d9ec7bf85a487c7d6f', 'ev-jarvis-1')")
            conn.execute("INSERT INTO assertion_evidence VALUES ('assertion-lens-clean', 'ev-jarvis-1')")

            # Contaminated Jarvis claim attached to workstream_lens
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_literal, scope_text,
                    canonical_status, currentness_state, validation_state, retrieval_eligible, canonical_eligible
                ) VALUES (
                    'assertion-ac53023302d9ec7bf85a487c7d6f', 'workstream_lens', 'HAS_CLAIM',
                    'Create Jira project for Project Jarvis', 'I think I need to create a project in Jira for Jarvis.',
                    'canonical', 'current', 'verified', 1, 1
                )"""
            )
            # Legitimate LENS assertion mentioning LENS Platform (must NOT be flagged as contaminated)
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_literal, scope_text,
                    canonical_status, currentness_state, validation_state, retrieval_eligible, canonical_eligible
                ) VALUES (
                    'assertion-lens-clean', 'workstream_lens', 'HAS_CLAIM',
                    'LENS Platform integrates geospatial layers', 'LENS Platform integrates geospatial layers',
                    'canonical', 'current', 'verified', 1, 1
                )"""
            )
            # Leaked candidate assertion with retrieval_eligible = 1
            conn.execute(
                """INSERT INTO assertion (
                    assertion_id, subject_node_id, predicate, object_literal, scope_text,
                    canonical_status, currentness_state, validation_state, retrieval_eligible, canonical_eligible
                ) VALUES (
                    'assertion-leaked-cand', 'person_amit', 'HAS_ROLE',
                    'Advisor', 'Advisor', 'candidate', 'current', 'needs_review', 1, 0
                )"""
            )
            conn.commit()

        scout = GraphScout(graph_path=self.db_path, runtime_root=self.temp_path)
        with scout._connect_ro() as ro_conn:
            antipatterns = scout.scan_retrieval_antipatterns(ro_conn)
            dups = scout.scan_duplicate_entities(ro_conn)

        by_id = {f.target_id: f for f in antipatterns}
        self.assertIn("assertion-ac53023302d9ec7bf85a487c7d6f", by_id)
        self.assertEqual(by_id["assertion-ac53023302d9ec7bf85a487c7d6f"].action_recommended, "quarantine_cross_project_assertion")
        self.assertNotIn("assertion-lens-clean", by_id)
        self.assertIn("assertion-leaked-cand", by_id)
        self.assertEqual(by_id["assertion-leaked-cand"].action_recommended, "demote_leaked_candidate_assertion")
        self.assertTrue(any({d.node_id_a, d.node_id_b} == {"person_amit", "stakeholder:amit-ayer"} for d in dups))



if __name__ == "__main__":
    unittest.main()
