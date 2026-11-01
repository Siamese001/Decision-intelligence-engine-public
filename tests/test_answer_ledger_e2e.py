"""
tests/test_answer_ledger_e2e.py

End-to-End integration tests for AnswerLedger and Feedback Capture:
1. Dispatches CLI `nee copilot --intent connect` and verifies automatic AnswerRecord creation in SQLite ledger.
2. Dispatches CLI `nee answer-feedback <answer_id> --rating useful` and verifies cryptographic binding to answer digest.
3. Records query pipeline answers independently in the shared ledger.
4. Verifies unbroken hash chain across both copilot and query answers.
5. Verifies canonical graph database remains completely unmutated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger import AnswerLedger, record_query_answer_safe
from dual_pipeline.cli.answer_feedback_handlers import handle_answer_feedback
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
               0 AS hitL_required
        FROM assertion WHERE object_node_id IS NOT NULL;
    """)

    con.execute("INSERT INTO node VALUES ('person:jen_davis', 'Jen Davis', 'Jen Davis', 'person', 'Delivery Lead', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('person:amit_ayer', 'Amit Ayer', 'Amit Ayer', 'person', 'Enterprise Architect', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('project:nexus', 'Project Nexus', 'Project Nexus', 'project', 'DocIQ Extraction', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('anchor_scada', 'SCADA Gateway', 'SCADA Gateway', 'system', 'Telemetry tracking', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('system:pi_historian', 'PI Historian', 'PI Historian', 'system', 'Historian data lake', 'active', 1)")
    con.execute("INSERT INTO node VALUES ('workstream:substation', 'Substation Workstream', 'Substation', 'workstream', 'Substation rollout', 'active', 1)")

    con.execute("INSERT INTO source VALUES ('s1', 'Sprint Sync 09/01/2026', '09/01/2026')")
    con.execute("INSERT INTO source VALUES ('s2', 'Arch Review 10/01/2026', '10/01/2026')")

    con.execute("INSERT INTO assertion VALUES ('ast-001', 'person:jen_davis', 'OWNS', 'project:nexus', NULL, 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    con.execute("INSERT INTO assertion VALUES ('ast-002', 'project:nexus', 'HAS_RISK', 'anchor_scada', 'SCADA integration latency bottleneck', 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    con.execute("INSERT INTO assertion VALUES ('ast-003', 'anchor_scada', 'CONSTRAINS', 'system:pi_historian', 'Historian pipeline buffer constraint', 'affirmed', 1, 'high', 'internal', '[]', '09/01/2026', '09/01/2026', 's1')")
    con.execute("INSERT INTO assertion VALUES ('ast-004', 'workstream:substation', 'DEPENDS_ON', 'anchor_scada', NULL, 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's2')")
    con.execute("INSERT INTO assertion VALUES ('ast-005', 'person:amit_ayer', 'DELIVERS', 'workstream:substation', NULL, 'affirmed', 1, 'high', 'internal', '[]', '10/01/2026', '10/01/2026', 's2')")

    con.commit()
    con.close()

    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "test_graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": "generation-w6-ledger-e2e",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return db_path, sha256


class TestAnswerLedgerE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_answer_ledger_e2e_")
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

    def test_copilot_and_query_answer_ledger_e2e(self) -> None:
        """Verifies end-to-end copilot execution records to AnswerLedger, feedback binds, and query answers link."""
        ledger_db = self.runtime_root / "learning" / "answer_ledger.sqlite"
        ledger = AnswerLedger(ledger_db)

        # 1. Dispatch CLI copilot connect intent
        args_copilot = argparse.Namespace(
            intent="connect",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
        )
        res_copilot = handle_copilot(args_copilot)
        self.assertEqual(res_copilot["status"], "PASS")
        copilot_answer_id = res_copilot.get("answer_id")
        self.assertIsNotNone(copilot_answer_id)

        # Verify record in SQLite store
        ans_rec = ledger.get_answer(copilot_answer_id)
        self.assertIsNotNone(ans_rec)
        self.assertEqual(ans_rec.pipeline, "copilot")
        self.assertEqual(ans_rec.intent_or_type, "connect")
        self.assertIn("SCADA Gateway", ans_rec.response_text)
        self.assertNotIn("Connected Multi-Hop Knowledge:", ans_rec.response_text)
        self.assertEqual(ans_rec.prev_record_digest, "GENESIS")
        self.assertTrue(len(ans_rec.record_digest) == 64)

        # Verify context snapshot contains clean turns (Decision D3)
        self.assertGreaterEqual(len(ans_rec.context_snapshot), 1)
        self.assertTrue(any("risk chains" in t["text"] for t in ans_rec.context_snapshot))

        # 2. Dispatch CLI operator feedback
        args_fb = argparse.Namespace(
            answer_id=copilot_answer_id,
            rating="useful",
            note="Verified multi-hop connection during review.",
            origin="USER_EXPLICIT",
            runtime_root=self.runtime_root,
        )
        res_fb = handle_answer_feedback(args_fb)
        self.assertEqual(res_fb["status"], "PASS")
        self.assertEqual(res_fb["answer_digest"], ans_rec.record_digest)

        feedback_records = ledger.list_feedback(copilot_answer_id)
        self.assertEqual(len(feedback_records), 1)
        self.assertEqual(feedback_records[0].rating, "useful")
        self.assertEqual(feedback_records[0].origin, "USER_EXPLICIT")

        # 3. Record query pipeline answer independently
        query_res = {
            "answer": "SCADA Gateway throughput is constrained by PI Historian buffer latency.",
            "cited_assertion_ids": ["ast-002", "ast-003"],
            "rigor": "VERIFIED",
        }
        query_answer_id = record_query_answer_safe(
            runtime_root=self.runtime_root,
            question="What constrains SCADA Gateway?",
            result=query_res,
            query_type="relationship_explanation",
        )
        self.assertIsNotNone(query_answer_id)

        # Verify query answer record
        q_rec = ledger.get_answer(query_answer_id)
        self.assertIsNotNone(q_rec)
        self.assertEqual(q_rec.pipeline, "query")
        self.assertEqual(q_rec.intent_or_type, "relationship_explanation")
        self.assertEqual(q_rec.prev_record_digest, ans_rec.record_digest)

        # 4. Verify cryptographic hash chain across both pipelines
        is_chain_valid, chain_msg = ledger.verify_chain()
        self.assertTrue(is_chain_valid, chain_msg)
        self.assertIn("2 records intact", chain_msg)

        # 5. Confirm canonical test graph sha is unchanged
        current_graph_sha = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        self.assertEqual(current_graph_sha, self.graph_sha)


if __name__ == "__main__":
    unittest.main()
