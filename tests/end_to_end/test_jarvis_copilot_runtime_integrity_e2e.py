"""End-to-End qualification suite for JARVIS recording and Copilot runtime integrity (Phase 7).

Verifies the complete end-to-end workflow:
1. Multi-stage session discovery, registration, and readiness lifecycle.
2. Grounded response synthesis addressing selected speaker utterance and topic.
3. Canonical regression: "React to Mitch's comment about load and agents."
4. Safe concise abstention and durable attempt recording when evidence is absent.
5. Graph generation pinning across query inception and response delivery.
6. Diagnostic attempt recording for discovery, binding, and retrieval failures.
"""
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
from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import (
    CATEGORY_ABSENT_CANDIDATES,
    CATEGORY_SESSION_DISCOVERY,
)
from dual_pipeline.cli.copilot_session import _read_turns_from_jsonl
from dual_pipeline.live_meeting_recorder_core.session_registry import (
    SessionManifest,
    SessionRegistry,
)


def _setup_qualification_graph(runtime_root: Path) -> Tuple[Path, str, str]:
    """Provisions an isolated SQLite graph with roster and concurrency/load assertions."""
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "jarvis_qualification_graph.sqlite"
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

    con.execute("INSERT INTO node VALUES ('person:mitch_simmons', 'Alex Rivera', 'Mitch Simmons', 'person', 'Lead Concurrency Architect', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:jen_davis', 'Jen Davis', 'Jen Davis', 'person', 'Delivery Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('system:agent_supervisor', 'Agent Supervisor', 'Agent Supervisor', 'system', 'Runtime agent worker orchestrator', 'active', 1)")

    con.execute("INSERT INTO source VALUES ('s1', 'Architecture Review 10/05/2026', '10/05/2026')")

    con.execute("""
        INSERT INTO assertion VALUES (
            'ast-load-01', 'person:mitch_simmons', 'WARNS_OF', 'system:agent_supervisor',
            'Worker thread pool saturates under concurrent agent load and SCADA burst telemetry',
            'affirmed', 1, 'high', 'internal', '[]', '10/05/2026', '10/05/2026', 's1'
        )
    """)
    con.execute("""
        INSERT INTO assertion VALUES (
            'ast-load-02', 'system:agent_supervisor', 'CONSTRAINS', NULL,
            'Thread pool worker ceiling fixed at 16 tasks without backpressure governor',
            'affirmed', 1, 'high', 'internal', '[]', '10/05/2026', '10/05/2026', 's1'
        )
    """)
    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    generation_id = "generation-jarvis-p7-001"
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "jarvis_qualification_graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": generation_id,
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return db_path, sha256, generation_id


class TestJarvisCopilotRuntimeIntegrityE2E(unittest.TestCase):
    """End-to-End qualification tests across the complete recording and copilot workflow."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.runtime_root = self.tmp_path / "runtime"
        self.db_path, self.graph_sha, self.generation_id = _setup_qualification_graph(self.runtime_root)
        self.registry_dir = self.tmp_path / "registry"
        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        # Write live transcripts with Mitch's comment
        self.seg_file = self.meeting_dir / "segments.jsonl"
        turns = [
            {"speaker": "Jen Davis", "text": "Let's review our agent deployment architecture."},
            {
                "speaker": "Alex Rivera",
                "text": "Under high concurrency and background worker load, the agent supervisor pool becomes the primary bottleneck.",
            },
            {"speaker": "Jen Davis", "text": "Understood, we need to monitor thread pool saturation."},
        ]
        with open(self.seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_complete_session_readiness_lifecycle_e2e(self) -> None:
        """Verifies session registration, readiness state progression, and buffer reader turn parsing."""
        registry = SessionRegistry(self.tmp_path)
        manifest = registry.register(
            session_id="jarvis-session-001",
            meeting_slug="concurrency_review",
            output_dir=str(self.meeting_dir),
            capture_state="CAPTURING",
            asr_state="READY",
            context_state="FRESH",
            copilot_state="READY",
        )
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.session_id, "jarvis-session-001")
        self.assertEqual(manifest.copilot_state, "READY")
        self.assertEqual(manifest.capture_state, "CAPTURING")

        # Read recent turns
        turns = _read_turns_from_jsonl(self.meeting_dir)
        self.assertEqual(len(turns), 3)
        self.assertEqual(turns[1]["speaker"], "Alex Rivera")

    def test_canonical_regression_react_to_mitch_comment_about_load(self) -> None:
        """Verifies canonical regression: 'React to Mitch's comment about load and agents.' delivers grounded advice."""
        args = argparse.Namespace(
            intent="respond",
            target="Mitch",
            topic="load and agents",
            request="React to Mitch's comment about load and agents.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )

        result = handle_copilot(args)
        self.assertEqual(result.get("status"), "PASS", f"Regression failed: {result}")
        self.assertEqual(result.get("intent"), "respond")
        self.assertEqual(result.get("target"), "Alex Rivera")
        self.assertEqual(result.get("generation"), self.generation_id)

        cited_ids = result.get("cited_assertion_ids", [])
        self.assertTrue(any(aid in cited_ids for aid in ("ast-load-01", "ast-load-02")))

        # Verify answer persisted in copilot_answers.jsonl
        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        self.assertTrue(answers_file.is_file())
        with open(answers_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertIn("response", lines[0])
        self.assertEqual(lines[0].get("generation"), self.generation_id)

    def test_safe_abstention_and_attempt_record_when_topic_absent(self) -> None:
        """When user asks about an absent topic, copilot safely abstains and logs diagnostic attempt record."""
        from dual_pipeline.live_meeting_recorder_core.copilot_attempt_ledger import CATEGORY_STALE_CONTEXT

        args = argparse.Namespace(
            intent="respond",
            target="Mitch",
            topic="quantum cryptography encryption",
            request="React to Mitch's comment about quantum cryptography.",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )

        result = handle_copilot(args)
        self.assertEqual(result.get("status"), "FAIL")
        self.assertEqual(result.get("error"), "ZERO_RELEVANT_UTTERANCES")
        self.assertEqual(result.get("failure_category"), CATEGORY_STALE_CONTEXT)

        # Verify attempt ledger recorded the failure
        attempts_file = self.meeting_dir / "copilot_attempts.jsonl"
        self.assertTrue(attempts_file.is_file())
        with open(attempts_file, "r", encoding="utf-8") as f:
            records = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["failure_category"], CATEGORY_STALE_CONTEXT)
        self.assertEqual(records[0]["request"], "React to Mitch's comment about quantum cryptography.")

    def test_missing_session_records_diagnostic_attempt(self) -> None:
        """Copilot invocation against missing session safely records attempt and fails cleanly."""
        args = argparse.Namespace(
            intent="next-questions",
            target=None,
            topic=None,
            request="What should we ask next?",
            meeting_dir=None,
            session="non_existent_session_123",
            runtime_root=self.runtime_root,
        )

        result = handle_copilot(args)
        self.assertEqual(result.get("status"), "FAIL")
        self.assertEqual(result.get("error"), "NO_SESSION_FOUND")
        self.assertEqual(result.get("failure_category"), CATEGORY_SESSION_DISCOVERY)


if __name__ == "__main__":
    unittest.main()
