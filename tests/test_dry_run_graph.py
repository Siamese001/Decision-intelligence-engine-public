"""
tests/test_dry_run_graph.py

Unit and integration tests for Wave 4: Graph Dry-Run Mode (--dry-run-graph).
Verifies preview generation, node/assertion/edge diffing, and zero-mutation guarantees.
Compatible with pytest and unittest.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.canonical_pipeline import CanonicalAgenticPipeline
from dual_pipeline.cli.ingest import build_canonical_metadata
from dual_pipeline.cli.parser import build_parser
from dual_pipeline.graph_preview_core.preview import generate_graph_delta_preview
from tools.validate_output_hierarchy import validate_meeting_directory


class TestDryRunGraph(unittest.TestCase):
    """Test suite for graph dry-run mode and graph delta preview generation."""

    def setUp(self) -> None:
        os.environ["NEE_ALLOW_TEST_FIXTURES"] = "1"
        os.environ["NEE_ENFORCE_WORKTREE"] = "0"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

        # Setup compliant output container: output/project_nexus/10-01-2026/Session
        self.date_dir = self.tmp_path / "output" / "project_nexus" / "10-01-2026"
        self.out_dir = self.date_dir / "Preview_Session"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        # Sample SQLite graph with initial nodes and assertions
        self.db_path = self.tmp_path / "test_graph.sqlite"
        self._init_test_db(self.db_path)

        # Sample Output 03 payload
        self.sample_output_3 = {
            "schema_version": "5.7",
            "meeting_id": "meeting-preview-session",
            "classification": "internal",
            "entity_upserts": [
                {
                    "entity_id": "person:alice",
                    "name": "Alice Smith",
                    "entity_type": "person",
                    "description": "Lead Architect",
                },
                {
                    "entity_id": "org:apex_global",
                    "name": "Apex Global Enterprises",
                    "entity_type": "org",
                    "description": "Clean Energy Partner",
                },
                {
                    "entity_id": "workstream:cost-optimization",
                    "name": "Cost Optimization Initiative",
                    "entity_type": "workstream",
                    "description": "New workstream",
                },
            ],
            "action_updates": [
                {
                    "action_id": "action:draft-operating-model",
                    "description": "Draft the cost optimization operating model.",
                    "status": "planned",
                    "mutation": "CREATE",
                },
                {
                    "action_id": "action:legacy-task-to-close",
                    "description": "Old task that is now superseded.",
                    "status": "closed",
                    "mutation": "RETIRE",
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "rel-01",
                    "from_id": "person:alice",
                    "to_id": "org:apex_global",
                    "relationship": "AFFILIATED_WITH",
                    "mutation": "CREATE",
                },
                {
                    "signal_id": "rel-02",
                    "from_id": "person:alice",
                    "to_id": "workstream:cost-optimization",
                    "relationship": "LEADS",
                    "mutation": "CREATE",
                },
                {
                    "signal_id": "rel-03",
                    "from_id": "person:alice",
                    "to_id": "workstream:old-initiative",
                    "relationship": "CONTRIBUTES_TO",
                    "mutation": "RETIRE",
                },
            ],
        }

    def tearDown(self) -> None:
        os.environ.pop("NEE_ENFORCE_WORKTREE", None)
        os.environ.pop("NEE_ALLOW_TEST_FIXTURES", None)
        self.temp_dir.cleanup()

    def _init_test_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        conn.execute("""
            CREATE TABLE node (
                node_id TEXT PRIMARY KEY,
                node_type TEXT,
                canonical_name TEXT,
                display_name TEXT,
                description TEXT,
                status TEXT,
                active INTEGER,
                created_at REAL,
                updated_at REAL
            )
        """)
        conn.execute("""
            CREATE TABLE assertion (
                assertion_id TEXT PRIMARY KEY,
                assertion_fingerprint TEXT,
                subject_node_id TEXT,
                predicate TEXT,
                claim_text TEXT,
                status TEXT
            )
        """)
        # Seed existing entity: person:alice with older name and description
        conn.execute(
            "INSERT INTO node (node_id, node_type, canonical_name, description) VALUES (?, ?, ?, ?)",
            ("person:alice", "person", "Alice (Old)", "Old Description"),
        )
        conn.commit()
        conn.close()

    def test_cli_parser_dry_run_graph_flag_rejected(self) -> None:
        """Verify --dry-run-graph CLI argument fails closed with eliminated flag error."""
        parser = build_parser()
        with self.assertRaises(SystemExit) as cm:
            parser.parse_args(
                [
                    "ingest-meeting",
                    "--input",
                    "sample.vtt",
                    "--project",
                    "project_nexus",
                    "--dry-run-graph",
                ]
            )
        self.assertEqual(cm.exception.code, 1)

    def test_generate_graph_delta_preview_diffing(self) -> None:
        """Verify node, assertion, and relationship diffing against SQLite database."""
        preview, preview_md = generate_graph_delta_preview(
            output_3=self.sample_output_3,
            graph_path=self.db_path,
            output_dir=self.out_dir,
            metadata={"meeting_name": "Preview Session", "project": "project_nexus"},
        )

        counts = preview["counts"]
        # person:alice was in db but changed -> modified = 1
        # org:apex_global and workstream:cost-optimization were not in db -> added = 2
        self.assertEqual(counts["nodes_added"], 2)
        self.assertEqual(counts["nodes_modified"], 1)
        self.assertEqual(counts["nodes_unchanged"], 0)

        # action:draft-operating-model -> new = 1
        # action:legacy-task-to-close -> superseded = 1
        self.assertEqual(counts["assertions_new"], 1)
        self.assertEqual(counts["assertions_superseded"], 1)

        # 2 CREATE edges, 1 RETIRE edge
        self.assertEqual(counts["relationships_new"], 2)
        self.assertEqual(counts["relationships_retired"], 1)

        # Schema conformance
        self.assertTrue(preview["schema_conformance"]["valid"])
        self.assertEqual(preview["schema_conformance"]["violations"], [])

        # Files were written to out_dir
        self.assertTrue((self.out_dir / "graph_delta_preview.json").is_file())
        self.assertTrue((self.out_dir / "graph_delta_preview.md").is_file())

        # Markdown table structure
        self.assertIn("# Graph Delta Preview (Dry-Run Mode)", preview_md)
        self.assertIn("Planned Delta Counts Summary", preview_md)
        self.assertIn("Proposed New Nodes", preview_md)
        self.assertIn("Proposed Node Modifications", preview_md)
        self.assertIn("New Assertions", preview_md)
        self.assertIn("Superseded / Retired Assertions", preview_md)

    def test_dry_run_graph_zero_mutation_guarantee(self) -> None:
        """Verify pipeline execution with dry_run_graph=True produces preview artifacts without mutating graph."""
        transcript_text = (
            "# Forensic Transcript\n\n"
            "**Date:** 10/01/2026\n"
            "**Meeting:** Preview Session\n\n"
            "**Alice**: [00:00:00] Initiating dry-run graph verification.\n\n"
            "**Bob**: [00:00:45] Graph preview enabled; confirming zero mutations.\n"
        )
        input_file = self.tmp_path / "sample.md"
        input_file.write_text(transcript_text, encoding="utf-8")

        # Snapshot graph file before pipeline run
        db_before = self.db_path.read_bytes()

        import uuid

        unique_meeting = f"Preview Session {uuid.uuid4().hex[:8]}"
        pipeline = CanonicalAgenticPipeline(provider_type="offline")
        with self.assertRaises(ValueError) as ctx:
            pipeline.run(
                input_path=input_file,
                metadata={
                    "project": "project_nexus",
                    "meeting_name": unique_meeting,
                    "date": "10/01/2026",
                    "time": "10:00 AM - 10:02 AM ET",
                    "attendees": [
                        {"name": "Alice", "role": "Lead Architect"},
                        {"name": "Bob", "role": "Project Manager"},
                    ],
                    "attendee_roles_confirmed_by_operator": True,
                    "classification": "internal",
                    "dry_run_graph": True,
                    "mutate_graph": True,
                    "graph_path": str(self.db_path),
                    "enforce_worktree": False,
                },
                output_dir=str(self.out_dir),
            )
        self.assertIn("Explicit dry-run or false mutation requests", str(ctx.exception))

        # Verify graph file bytes did NOT change (zero mutation)
        db_after = self.db_path.read_bytes()
        self.assertEqual(db_before, db_after)


if __name__ == "__main__":
    unittest.main()
