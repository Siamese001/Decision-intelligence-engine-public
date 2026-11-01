"""End-to-End test suite for Wave 9: On-demand, GraphDB-grounded copilot."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Tuple

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.live_meeting_recorder_core.collateral_watcher import CollateralFsWatcher
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)


def _setup_test_graph_runtime(runtime_root: Path) -> Tuple[Path, str]:
    """Sets up an isolated, valid SQLite graph and active_pair.json."""
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "test_graph.sqlite"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE node (
            node_id TEXT PRIMARY KEY,
            canonical_name TEXT,
            display_name TEXT,
            node_type TEXT,
            description TEXT,
            status TEXT,
            active INTEGER
        );
        CREATE TABLE assertion (
            assertion_id TEXT PRIMARY KEY,
            subject_node_id TEXT,
            predicate TEXT,
            object_node_id TEXT,
            object_literal TEXT,
            canonical_status TEXT,
            retrieval_eligible INTEGER,
            materiality TEXT,
            classification TEXT,
            allowed_audiences_json TEXT,
            observed_at TEXT,
            source_date_normalized TEXT,
            primary_source_id TEXT
        );
        CREATE TABLE assertion_evidence (
            assertion_id TEXT,
            evidence_id TEXT,
            evidence_relationship TEXT
        );
        CREATE TABLE evidence_item (
            evidence_id TEXT PRIMARY KEY,
            source_id TEXT,
            locator TEXT,
            content TEXT
        );
        CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            title TEXT,
            source_date_normalized TEXT
        );
        CREATE VIEW v_canonical_edge AS
        SELECT assertion_id, subject_node_id AS from_node_id, predicate AS relationship_family,
               predicate AS edge_type, object_node_id AS to_node_id, materiality, primary_source_id AS source_id,
               0 AS hitl_required
        FROM assertion WHERE object_node_id IS NOT NULL;
    """)

    con.execute("INSERT INTO node VALUES ('person:jen_davis', 'Jen Davis', 'Jen Davis', 'person', 'Delivery Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:amit_ayer', 'Amit Ayer', 'Amit Ayer', 'person', 'Enterprise Architect', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('project:nexus', 'Project Nexus', 'Project Nexus', 'project', 'DocIQ Intelligent Extraction', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('anchor_scada', 'SCADA Gateway', 'SCADA Gateway', 'system', 'Real-time telemetry tracking', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('workstream:substation', 'Substation Onboarding', 'Substation Onboarding', 'workstream', 'Substation rollout', 'active', 1)")

    con.execute("INSERT INTO source VALUES ('s1', 'Sprint Sync 10/01/2026', '10/01/2026')")
    con.execute("INSERT INTO source VALUES ('s2', 'Architecture Review 10/02/2026', '10/02/2026')")
    con.execute("INSERT INTO source VALUES ('s3', 'Legacy Baseline 09/15/2026', '09/15/2026')")

    con.execute("INSERT INTO assertion VALUES ('ast-001', 'person:jen_davis', 'OWNS', 'project:nexus', NULL, 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's1')")
    con.execute("INSERT INTO assertion VALUES ('ast-002', 'person:amit_ayer', 'DELIVERS', 'workstream:substation', NULL, 'affirmed', 1, 'high', 'internal', '[]', '10/02/2026', '10/02/2026', 's2')")
    con.execute("INSERT INTO assertion VALUES ('ast-003', 'anchor_scada', 'HAS_RISK', NULL, 'SCADA latency exceeds 500ms under load', 'affirmed', 1, 'high', 'internal', '[]', '09/15/2026', '09/15/2026', 's3')")
    con.execute("INSERT INTO assertion VALUES ('ast-004', 'anchor_scada', 'CONSTRAINS', NULL, 'Legacy PI historian throughput bottleneck', 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's1')")
    con.execute("INSERT INTO assertion VALUES ('ast-005', 'person:jen_davis', 'HAS_RISK', NULL, 'Scope creep in DocIQ migration deliverables', 'affirmed', 1, 'high', 'internal', '[]', '10/02/2026', '10/02/2026', 's2')")

    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "test_graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": "generation-w9-test-01",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return db_path, sha256


class TestCopilotOnDemandGraphGroundedE2E(unittest.TestCase):
    """End-to-End verification of Wave 9 on-demand GraphDB-grounded copilot."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_w9_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.db_path, self.graph_sha = _setup_test_graph_runtime(self.runtime_root)
        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        # Write sample live transcript
        seg_file = self.meeting_dir / "segments.jsonl"
        turns = [
            {"speaker": "Jen Davis", "text": "We are reviewing SCADA latency and Project Nexus timelines."},
            {"speaker": "Amit Ayer", "text": "The substation workstream depends on PI historian integration."},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_load_copilot_graph_loaded_active_pair(self) -> None:
        """Verifies load_copilot_graph successfully loads active pair and builds indexes."""
        status, in_mem, retriever = load_copilot_graph(runtime_root=self.runtime_root)
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)
        self.assertIsNotNone(in_mem)
        self.assertIsNotNone(retriever)
        self.assertIn("person:jen_davis", in_mem.nodes)
        self.assertEqual(retriever.graph_sha256, self.graph_sha)

    def test_all_five_intents_e2e(self) -> None:
        """Verifies all 5 canonical intents cite >= 1 existing assertion and append to copilot_answers.jsonl."""
        intents_to_test = [
            ("next-questions", None),
            ("next-actions", None),
            ("respond", "Jen Davis"),
            ("graph-context", None),
            ("gaps", None),
        ]

        answers_file = self.meeting_dir / "copilot_answers.jsonl"

        for intent, target in intents_to_test:
            args = argparse.Namespace(
                intent=intent,
                target=target,
                meeting_dir=str(self.meeting_dir),
                runtime_root=self.runtime_root,
            )
            result = handle_copilot(args)
            self.assertEqual(result.get("status"), "PASS", f"Intent {intent} failed: {result}")
            self.assertEqual(result.get("intent"), intent)
            cited_ids = result.get("cited_assertion_ids", [])
            self.assertGreaterEqual(len(cited_ids), 1, f"Intent {intent} did not cite any assertions")
            # Verify cited IDs exist in graph
            for aid in cited_ids:
                self.assertIn(aid, ["ast-001", "ast-002", "ast-003", "ast-004", "ast-005"])

        # Verify copilot_answers.jsonl has 5 records
        self.assertTrue(answers_file.is_file())
        with open(answers_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 5)
        for entry in lines:
            self.assertIn("timestamp", entry)
            self.assertIn("intent", entry)
            self.assertIn("response", entry)
            self.assertIn("cited_assertion_ids", entry)
            self.assertEqual(entry.get("graph_sha256"), self.graph_sha)

    def test_aliases_nbq_and_review(self) -> None:
        """Verifies aliases nbq -> next-questions and review -> gaps."""
        args_nbq = argparse.Namespace(
            intent="nbq",
            target=None,
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res_nbq = handle_copilot(args_nbq)
        self.assertEqual(res_nbq.get("status"), "PASS")
        self.assertEqual(res_nbq.get("intent"), "next-questions")

        args_rev = argparse.Namespace(
            intent="review",
            target=None,
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res_rev = handle_copilot(args_rev)
        self.assertEqual(res_rev.get("status"), "PASS")
        self.assertEqual(res_rev.get("intent"), "gaps")

    def test_gaps_intent_cross_meeting_and_no_prior_record(self) -> None:
        """Verifies gaps intent requires >= 2 dated assertions from different meetings."""
        # Query matching SCADA has ast-003 (09/15/2026) and ast-004 (10/01/2026) -> >= 2 distinct sources
        status, _, retriever = load_copilot_graph(runtime_root=self.runtime_root)
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)
        assertions, has_prior = retriever.get_gaps("SCADA latency historian")
        self.assertTrue(has_prior)
        self.assertGreaterEqual(len(assertions), 2)

        # Query matching only one assertion with unique date
        assertions_single, has_prior_single = retriever.get_gaps("substation")
        self.assertFalse(has_prior_single)

    def test_collateral_fs_watcher_detection(self) -> None:
        """Verifies CollateralFsWatcher detects dropped files and writes collateral_drop_log.json."""
        ref_dir = self.meeting_dir / "referenced_artifacts"
        ref_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.meeting_dir / "collateral_drop_log.json"

        watcher = CollateralFsWatcher(
            ref_dir=ref_dir,
            get_elapsed_fn=lambda: 42.0,
            log_path=log_path,
        )

        # Create a test file
        test_file = ref_dir / "architecture_diagram.pdf"
        test_file.write_bytes(b"Simulated PDF content for architecture diagram")

        # Run scan
        watcher._scan()
        watcher.stop(log_path=log_path)

        self.assertTrue(log_path.is_file())
        with open(log_path, "r", encoding="utf-8") as f:
            drops = json.load(f)
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["filename"], "architecture_diagram.pdf")
        self.assertEqual(drops[0]["dropped_at_elapsed"], "00:00:42")
        self.assertEqual(drops[0]["attribution_mode"], "COLLATERAL_REVIEWED")


if __name__ == "__main__":
    unittest.main()
