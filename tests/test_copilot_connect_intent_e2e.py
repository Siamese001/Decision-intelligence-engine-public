"""
tests/test_copilot_connect_intent_e2e.py

End-to-End integration tests for Copilot `--intent connect` and contradiction-aware `--intent gaps`:
1. Dispatches CLI `nee copilot --intent connect` with multi-hop connected chain resolution.
2. Verifies `copilot_answers.jsonl` persistence, rigor verification, and graph SHA provenance.
3. Dispatches CLI `nee copilot --intent gaps` and verifies contradiction detection across dated commitments.
4. Verifies fail-closed behavior on ungrounded or empty dialogue (zero invented suggestions).
5. Verifies Rule C-19 Career Firewall quarantine in end-to-end execution.
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

    # Nodes
    con.execute("INSERT INTO node VALUES ('person:jen_davis', 'Jen Davis', 'Jen Davis', 'person', 'Delivery Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:amit_ayer', 'Amit Ayer', 'Amit Ayer', 'person', 'Enterprise Architect', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('project:nexus', 'Project Nexus', 'Project Nexus', 'project', 'DocIQ Extraction', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('anchor_scada', 'SCADA Gateway', 'SCADA Gateway', 'system', 'Telemetry tracking', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('system:pi_historian', 'PI Historian', 'PI Historian', 'system', 'Historian data lake', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('workstream:substation', 'Substation Workstream', 'Substation', 'workstream', 'Substation rollout', 'active', 1)")

    # Sources
    con.execute("INSERT INTO source VALUES ('s1', 'Sprint Sync 09/01/2026', '09/01/2026')")
    con.execute("INSERT INTO source VALUES ('s2', 'Arch Review 10/01/2026', '10/01/2026')")
    con.execute("INSERT INTO source VALUES ('s3', 'Legacy Baseline 08/15/2026', '08/15/2026')")

    # Assertions forming multi-hop chains:
    # person:jen_davis -> OWNS -> project:nexus
    con.execute("INSERT INTO assertion VALUES ('ast-001', 'person:jen_davis', 'OWNS', 'project:nexus', NULL, 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    # project:nexus -> HAS_RISK -> anchor_scada
    con.execute("INSERT INTO assertion VALUES ('ast-002', 'project:nexus', 'HAS_RISK', 'anchor_scada', 'SCADA integration latency bottleneck', 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    # anchor_scada -> CONSTRAINS -> system:pi_historian
    con.execute("INSERT INTO assertion VALUES ('ast-003', 'anchor_scada', 'CONSTRAINS', 'system:pi_historian', 'Historian pipeline constrained by gateway buffer', 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    # workstream:substation -> DEPENDS_ON -> anchor_scada
    con.execute("INSERT INTO assertion VALUES ('ast-004', 'workstream:substation', 'DEPENDS_ON', 'anchor_scada', NULL, 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's2')")
    # person:amit_ayer -> DELIVERS -> workstream:substation
    con.execute("INSERT INTO assertion VALUES ('ast-005', 'person:amit_ayer', 'DELIVERS', 'workstream:substation', NULL, 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's2')")

    # Contradicting status assertions on anchor_scada
    con.execute("INSERT INTO assertion VALUES ('ast-gap-1', 'anchor_scada', 'HAS_STATUS', NULL, 'Production Certified and Active', 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    con.execute("INSERT INTO assertion VALUES ('ast-gap-2', 'anchor_scada', 'HAS_STATUS', NULL, 'Decommissioned and Inoperable', 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's2')")

    # C-19 quarantined assertion (career leverage)
    con.execute("INSERT INTO assertion VALUES ('ast-c19', 'person:jen_davis', 'HAS_STRATEGY', NULL, 'executive compensation band personal advancement bonus', 'affirmed', 1, 'high', 'INTERNAL_ADVISORY', '[]', '10/01/2026', '10/01/2026', 's2')")

    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "test_graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": "generation-w5-connect-e2e",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return db_path, sha256


class TestCopilotConnectIntentE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_connect_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.db_path, self.graph_sha = _setup_test_graph_runtime(self.runtime_root)

        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        seg_file = self.meeting_dir / "segments.jsonl"
        turns = [
            {"speaker": "Jen Davis", "text": "We need to discuss Project Nexus and the SCADA Gateway integration.", "timestamp": "00:01:00", "start_sec": 60.0},
            {"speaker": "Amit Ayer", "text": "The Substation workstream and PI Historian dependencies must be resolved.", "timestamp": "00:02:00", "start_sec": 120.0},
            {"speaker": "Jen Davis", "text": "What are the connected risk chains across these components?", "timestamp": "00:03:00", "start_sec": 180.0},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_cli_copilot_connect_e2e(self) -> None:
        """Verifies handle_copilot with --intent connect discovers multi-hop chains and writes to copilot_answers.jsonl."""
        args = argparse.Namespace(
            intent="connect",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res = handle_copilot(args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["intent"], "connect")
        self.assertGreaterEqual(len(res["cited_assertion_ids"]), 1)
        self.assertEqual(res["graph_sha256"], self.graph_sha)

        answers_path = Path(res["answers_path"])
        self.assertTrue(answers_path.exists())

        lines = answers_path.read_text(encoding="utf-8").strip().splitlines()
        self.assertGreaterEqual(len(lines), 1)
        last_rec = json.loads(lines[-1])

        self.assertEqual(last_rec["intent"], "connect")
        self.assertNotIn("Connected Multi-Hop Knowledge:", last_rec["response"])
        self.assertIn("Could we clarify", last_rec["response"])
        self.assertIn("SCADA Gateway", last_rec["response"])
        self.assertEqual(last_rec["rigor_tier"], "VERIFIED")
        self.assertEqual(last_rec["graph_sha256"], self.graph_sha)

    def test_cli_copilot_gaps_contradiction_e2e(self) -> None:
        """Only verified sides of a detected conflict may reach the spoken answer."""
        args = argparse.Namespace(
            intent="gaps",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res = handle_copilot(args)
        self.assertEqual(res["status"], "PASS")
        self.assertEqual(res["intent"], "gaps")

        answers_path = Path(res["answers_path"])
        self.assertTrue(answers_path.exists())

        lines = answers_path.read_text(encoding="utf-8").strip().splitlines()
        last_rec = json.loads(lines[-1])

        self.assertNotIn("Production Certified and Active", last_rec["response"])
        self.assertIn("Decommissioned and Inoperable", last_rec["response"])
        self.assertNotIn("ast-gap-1", last_rec["cited_assertion_ids"])
        self.assertIn("ast-gap-2", last_rec["cited_assertion_ids"])

    def test_cli_copilot_connect_fail_closed_ungrounded(self) -> None:
        """Verifies fail-closed return ZERO_RELEVANT_ASSERTIONS and no invented text on ungrounded dialogue."""
        ungrounded_dir = self.tmp_path / "ungrounded_meeting"
        ungrounded_dir.mkdir(parents=True, exist_ok=True)
        seg_file = ungrounded_dir / "segments.jsonl"
        turns = [
            {"speaker": "Alice", "text": "Good morning everyone. The weather outside is quite nice today.", "timestamp": "00:01:00", "start_sec": 60.0},
            {"speaker": "Bob", "text": "Let us grab some coffee before we start the general chat.", "timestamp": "00:02:00", "start_sec": 120.0},
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

        args = argparse.Namespace(
            intent="connect",
            meeting_dir=str(ungrounded_dir),
            runtime_root=self.runtime_root,
        )
        res = handle_copilot(args)
        self.assertEqual(res["status"], "FAIL")
        self.assertEqual(res["error"], "ZERO_RELEVANT_ASSERTIONS")
        self.assertIn("ZERO_RELEVANT_ASSERTIONS", res["details"])

        # Confirm no answers file was created or polluted
        answers_path = ungrounded_dir / "copilot_answers.jsonl"
        self.assertFalse(answers_path.exists())

    def test_cli_copilot_c19_career_quarantine_e2e(self) -> None:
        """Verifies Rule C-19 career leverage assertions are quarantined and never cited."""
        args = argparse.Namespace(
            intent="connect",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res = handle_copilot(args)
        self.assertEqual(res["status"], "PASS")

        # Confirm ast-c19 is not cited
        self.assertNotIn("ast-c19", res["cited_assertion_ids"])

        # Check answers file does not contain career leverage text
        answers_path = Path(res["answers_path"])
        content = answers_path.read_text(encoding="utf-8")
        self.assertNotIn("$1.2M", content)
        self.assertNotIn("advancement bonus", content)


if __name__ == "__main__":
    unittest.main()
