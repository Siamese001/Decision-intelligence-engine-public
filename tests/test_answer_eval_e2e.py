"""End-to-End integration tests for Answer Evaluation Harness (Wave 7).

Verifies:
1. End-to-end execution: copilot answers + query answers recorded in AnswerLedger.
2. Operator feedback captured via `nee answer-feedback` CLI.
3. Offline evaluation via `nee answer-eval` producing sealed AnswerEvalReceipt.
4. Receipt conforms strictly to `urn:nee:eval:answer-eval-receipt:1.0.0`.
5. Replay sanity pin holds (unchanged candidate parameters reproduce citations with 1.0 exact match).
6. Canonical graph database remains completely unmutated (+0 mutations, sha256 unchanged).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from dual_pipeline.answer_ledger import record_query_answer_safe
from dual_pipeline.cli.answer_feedback_handlers import handle_answer_feedback
from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.evals.answer_eval import run_answer_eval
from dual_pipeline.evals.answer_eval_core import validate_eval_receipt


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
        "generation_id": "generation-w7-eval-e2e",
    }
    (runtime_root / "active_pair.json").write_text(json.dumps(active_pair), encoding="utf-8")
    return db_path, sha256


class TestAnswerEvalE2E(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_answer_eval_e2e_")
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

    def test_e2e_answer_eval_lifecycle_and_sealed_receipt(self) -> None:
        # Step 1: Execute copilot command to generate copilot answer in ledger
        copilot_args = argparse.Namespace(
            command="copilot",
            intent="connect",
            target=None,
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
            rigor="FAST",
            output_dir=self.tmp_path / "output",
        )
        copilot_res = handle_copilot(copilot_args)
        self.assertEqual(copilot_res["status"], "PASS")
        copilot_ans_id = copilot_res.get("answer_id")
        self.assertIsNotNone(copilot_ans_id)

        # Step 2: Record query answer in ledger
        query_res = {
            "status": "PASS",
            "text": "Project Nexus is owned by Jen Davis [ast-001].",
            "cited_assertion_ids": ["ast-001"],
            "latency_ms": 42.0,
            "rigor": "VERIFIED",
        }
        query_ans_id = record_query_answer_safe(
            self.runtime_root,
            "Who owns Project Nexus?",
            query_res,
            query_type="factual",
        )
        self.assertIsNotNone(query_ans_id)

        # Step 3: Dispatch CLI answer-feedback to bind operator feedback
        fb_args = argparse.Namespace(
            answer_id=copilot_ans_id,
            rating="useful",
            note="Accurate multi-hop chain",
            origin="USER_EXPLICIT",
            runtime_root=self.runtime_root,
        )
        fb_res = handle_answer_feedback(fb_args)
        self.assertEqual(fb_res["status"], "PASS")

        # Step 4: Run replay candidate parameters (empty/unchanged for sanity pin)
        candidate_params_file = self.tmp_path / "candidate_params.json"
        candidate_params_file.write_text(json.dumps({}), encoding="utf-8")

        eval_args = argparse.Namespace(
            command="answer-eval",
            pipeline="both",
            since=None,
            session=None,
            candidate_params=candidate_params_file,
            llm_judge=True,
            output=None,
            runtime_root=self.runtime_root,
        )
        receipt = run_answer_eval(eval_args)

        # Step 5: Assertions on sealed receipt
        self.assertEqual(receipt["contract_id"], "urn:nee:eval:answer-eval-receipt:1.0.0")
        self.assertEqual(receipt["pipeline"], "both")
        self.assertEqual(receipt["ledger_range"]["record_count"], 2)
        self.assertTrue(receipt["ledger_range"]["chain_valid"])

        metrics = receipt["metrics"]
        self.assertGreater(metrics["groundedness"], 0.0)
        self.assertEqual(metrics["citation_durability"], 1.0)
        self.assertEqual(metrics["staleness_rate"], 0.0)
        self.assertEqual(metrics["conflict_miss_rate"], 0.0)

        op_use = metrics["operator_usefulness"]
        self.assertEqual(op_use["useful_count"], 1)
        self.assertEqual(op_use["total_feedback"], 1)
        self.assertGreater(op_use["beta_posterior_mean"], 0.50)

        # Verify Replay Sanity Pin: 100% exact citation match
        replay = receipt.get("replay_comparison")
        self.assertIsNotNone(replay)
        self.assertEqual(replay["reproduced_citation_exact_match_rate"], 1.0)
        self.assertEqual(replay["replayed_count"], 1)

        # Verify LLM judge evaluation
        judge = receipt.get("llm_judge")
        self.assertIsNotNone(judge)
        self.assertEqual(judge["evaluated_count"], 2)

        # Validate against JSON schema
        validate_eval_receipt(receipt)

        # Verify sealed receipt on disk
        saved_file = Path(receipt["_output_file"])
        self.assertTrue(saved_file.is_file())
        with open(saved_file, "r", encoding="utf-8") as f:
            disk_receipt = json.load(f)
        self.assertEqual(disk_receipt["receipt_digest"], receipt["receipt_digest"])

        # Step 6: Verify canonical graph remains unmodified (+0 mutations)
        current_db_sha = hashlib.sha256(self.db_path.read_bytes()).hexdigest()
        self.assertEqual(current_db_sha, self.graph_sha)


if __name__ == "__main__":
    unittest.main()
