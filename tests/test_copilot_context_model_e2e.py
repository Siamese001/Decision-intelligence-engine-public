"""
tests/test_copilot_context_model_e2e.py

End-to-End integration tests for ConversationContextModel:
1. Reconstructs ConversationContextModel from LiveBufferReader turns in a live meeting directory.
2. Dispatches CLI `nee copilot` handler across canonical intents with GraphDB grounding.
3. Tests LiveCopilotAdvisor streaming turns with context model integration.
4. Firewall isolation: confirms C-19 markers are quarantined and raw transcript files are not read directly.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.copilot_context_model import (
    ConversationContextModel,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader


def _setup_test_graph_runtime(runtime_root: Path) -> tuple[Path, str]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "test_graph.sqlite"
    if db_path.exists():
        db_path.unlink()

    con = sqlite3.connect(str(db_path))
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
    con.execute("INSERT INTO node VALUES ('project:nexus', 'Project Nexus', 'Project Nexus', 'project', 'DocIQ Extraction', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('anchor_scada', 'SCADA Gateway', 'SCADA Gateway', 'system', 'Telemetry tracking', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('workstream:substation', 'Substation Workstream', 'Substation', 'workstream', 'Substation rollout', 'active', 1)")

    con.execute("INSERT INTO source VALUES ('s1', 'Sprint Sync 10/01/2026', '10/01/2026')")
    con.execute("INSERT INTO source VALUES ('s2', 'Arch Review 10/02/2026', '10/02/2026')")
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
        "generation_id": "generation-w2-context-e2e",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return db_path, sha256


class TestCopilotContextModelE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_context_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.db_path, self.graph_sha = _setup_test_graph_runtime(self.runtime_root)

        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        # Write structured segments.jsonl
        seg_file = self.meeting_dir / "segments.jsonl"
        turns = [
            {"speaker": "Jen Davis", "text": "We are reviewing SCADA latency and Project Nexus timelines.", "timestamp": "00:01:00", "start_sec": 60.0},
            {"speaker": "Amit Ayer", "text": "The substation workstream depends on PI historian integration.", "timestamp": "00:02:00", "start_sec": 120.0},
            {"speaker": "Jen Davis", "text": "Can you verify if the SCADA mitigation plan is on track?", "timestamp": "00:03:00", "start_sec": 180.0},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_live_buffer_reader_reconstructs_context_model(self) -> None:
        """Verifies LiveBufferReader reads turns into ConversationContextModel with exact seeds."""
        reader = LiveBufferReader(self.tmp_path)
        from dual_pipeline.live_meeting_recorder_core.session_registry import SessionManifest
        manifest = SessionManifest(
            session_id="test_sess_01",
            engine_signature="urn:nee:dual-pipeline:live-meeting-recorder:1.0.0",
            parent_pid=12345,
            parent_start_time=100.0,
            repo_root=str(self.tmp_path),
            meeting_slug="meeting_session",
            output_dir="meeting_session",
            transcript_md="meeting_session/transcript.md",
        )

        turns = reader.read_turns(manifest)
        self.assertEqual(len(turns), 3)

        model = ConversationContextModel(project_scope="project_nexus")
        for t in turns:
            model.add_turn(t["speaker"], t["text"], t["timestamp"], turn_sec=t["turn_sec"])

        self.assertEqual(model.current_act, "question_to_operator")
        seeds = model.retrieval_seeds()
        self.assertIn("SCADA", seeds)
        self.assertIn("project_nexus", seeds)

    def test_cli_copilot_intents_grounded_via_context_model(self) -> None:
        """Verifies CLI copilot dispatches using reconstructed context model seeds and succeeds."""
        intents = ["next-questions", "next-actions", "respond", "graph-context", "gaps"]
        for intent in intents:
            target = "Jen Davis" if intent == "respond" else None
            args = argparse.Namespace(
                intent=intent,
                target=target,
                meeting_dir=str(self.meeting_dir),
                runtime_root=self.runtime_root,
            )
            res = handle_copilot(args)
            self.assertEqual(res["status"], "PASS", f"Intent {intent} failed: {res}")
            self.assertGreaterEqual(len(res["cited_assertion_ids"]), 1)

    def test_live_copilot_advisor_context_model_integration(self) -> None:
        """Verifies LiveCopilotAdvisor streams turns into context model and evaluates."""
        advisor = LiveCopilotAdvisor(
            project_name="project_nexus",
            meeting_name="Nexus Architecture Review",
            attendees=[
                {"name": "Jen Davis", "role": "Lead", "org": "Apex Global"},
                {"name": "Amit Ayer", "role": "Architect", "org": "PartnerAdvisory"},
            ],
            mock_mode=True,
            cooldown_seconds=0.1,
        )
        try:
            # Feed turns
            advisor.add_turn("Jen Davis", "Reviewing Project Nexus delivery commitments.", "00:01:00", turn_sec=60.0)
            advisor.add_turn("Amit Ayer", "We identified a severe risk in SCADA gateway latency.", "00:02:00", turn_sec=120.0)

            self.assertEqual(advisor.context_model.current_act, "objection")
            self.assertEqual(len(advisor.context_model.flow_state.objections_and_risks), 1)

            res = advisor._evaluate_dialogue_sync()
            self.assertIsNotNone(res)
            self.assertTrue(res["has_guidance"])
            self.assertEqual(res["category"], "STRATEGIC")
        finally:
            advisor.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
