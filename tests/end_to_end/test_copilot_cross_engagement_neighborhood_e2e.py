"""
tests/end_to_end/test_copilot_cross_engagement_neighborhood_e2e.py

End-to-End integration test suite for Wave 5:
1. Real SQLite storage load via load_copilot_graph reports status = LOADED_ACTIVE_PAIR.
2. Cross-project query retrieves Daniel Brankin and SAP ERP context via topological inheritance.
3. Precomputed neighborhood store is active and queried.
4. Foreign tenant assertions (project:solar) fail closed (0 leaked claims).
5. Output writes to copilot_answers.jsonl with status PASS.
6. Rule C-19 Career Leverage & Privacy claims are strictly blocked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict

from dual_pipeline.cli.copilot_handlers import handle_copilot
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)
from tests.fixtures.copilot_engagement.loader import (
    provision_multi_tenant_sqlite_graph,
)


def _setup_active_pair(runtime_root: Path) -> Tuple[Path, str]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    db_path = runtime_root / "test_graph.sqlite"
    conn = provision_multi_tenant_sqlite_graph(db_path)
    conn.close()
    sha256 = hashlib.sha256(db_path.read_bytes()).hexdigest()
    active_pair = {
        "schema_version": "1.0.0",
        "graph_path": "test_graph.sqlite",
        "graph_sha256": sha256,
        "generation_id": "gen_multi_tenant_w5_test",
    }
    (runtime_root / "active_pair.json").write_text(
        json.dumps(active_pair), encoding="utf-8"
    )
    return db_path, sha256



class TestCopilotCrossEngagementNeighborhoodE2E(unittest.TestCase):
    """End-to-End verification of cross-engagement copilot retrieval and neighborhood cache."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_cross_eng_e2e_")
        self.tmp_path = Path(self.temp_dir)
        self.runtime_root = self.tmp_path / "runtime"
        self.db_path, self.graph_sha = _setup_active_pair(self.runtime_root)
        self.meeting_dir = self.tmp_path / "meeting_session"
        self.meeting_dir.mkdir(parents=True, exist_ok=True)

        # Write transcript with target Jason asking about SAP ERP
        seg_file = self.meeting_dir / "segments.jsonl"
        turns = [
            {
                "speaker": "Jason",
                "text": "Who is the technical owner for SAP ERP integration?",
                "timestamp": "00:01:10",
                "turn_sec": 70.0,
            },
            {
                "speaker": "Sharon",
                "text": "Daniel Brankin leads SAP architecture in our predecessor project.",
                "timestamp": "00:01:25",
                "turn_sec": 85.0,
            },
        ]
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_sqlite_storage_load_reports_loaded_active_pair(self) -> None:
        """Verifies load_copilot_graph successfully loads active pair from SQLite."""
        status, in_mem, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:jarvis",
            precompute_timeout_seconds=0.150,
        )
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)
        self.assertIsNotNone(in_mem)
        self.assertIsNotNone(retriever)
        self.assertEqual(retriever.graph_sha256, self.graph_sha)

        # Check topological inheritance: project:nexus inherited from project:jarvis
        self.assertIn("project:nexus", retriever.topological_engagements)
        self.assertIn("project:jarvis", retriever.permitted_engagements)
        self.assertIn("project:nexus", retriever.permitted_engagements)

        # Check precomputed neighborhood store
        self.assertIsNotNone(retriever.neighborhood_store)
        self.assertGreaterEqual(retriever.neighborhood_store.chain_count, 1)

    def test_02_execute_copilot_request_cross_engagement_success(self) -> None:
        """Full-path test: execute_copilot_request retrieves predecessor SAP owner and outputs PASS."""
        args = argparse.Namespace(
            intent="respond",
            target="Jason",
            topic="SAP ERP",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
            project="jarvis",
        )
        result = execute_copilot_request(args)
        self.assertEqual(result.get("status"), "PASS", f"Copilot execution failed: {result}")
        self.assertEqual(result.get("intent"), "respond")
        self.assertEqual(result.get("target"), "Jason Doe")

        cited_ids = result.get("cited_assertion_ids", [])
        self.assertIn("asrt-nexus-daniel-sap", cited_ids)

        # Verify copilot_answers.jsonl was written and valid
        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        self.assertTrue(answers_file.is_file())
        with open(answers_file, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(lines), 1)
        record = lines[0]
        self.assertEqual(record.get("intent"), "respond")
        self.assertEqual(record.get("target"), "Jason Doe")
        self.assertIn("asrt-nexus-daniel-sap", record.get("cited_assertion_ids", []))
        self.assertIn("Daniel Brankin", record.get("response", ""))

    def test_03_handle_copilot_cli_entrypoint_parity(self) -> None:
        """Verifies handle_copilot CLI entrypoint parity with execute_copilot_request."""
        args = argparse.Namespace(
            intent="respond",
            target="Jason",
            topic="SAP ERP",
            meeting_dir=str(self.meeting_dir),
            runtime_root=self.runtime_root,
            project="jarvis",
        )
        result = handle_copilot(args)
        self.assertEqual(result.get("status"), "PASS")
        self.assertIn("asrt-nexus-daniel-sap", result.get("cited_assertion_ids", []))

    def test_04_multihop_neighborhood_cross_engagement_query(self) -> None:
        """Verifies neighborhood store chains and latency (< 5.0ms) during multi-hop fallback."""
        status, in_mem, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:jarvis",
        )
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)
        store = retriever.neighborhood_store
        self.assertIsNotNone(store)

        t0 = time.time()
        chains = store.get_chains_for_node("project:jarvis")
        elapsed_ms = (time.time() - t0) * 1000.0
        self.assertLess(elapsed_ms, 5.0, f"Neighborhood search too slow: {elapsed_ms:.2f}ms")
        self.assertGreaterEqual(len(chains), 1)


    def test_05_foreign_tenant_isolation_fails_closed(self) -> None:
        """Verifies unlinked foreign tenant (project:solar) assertions fail closed with 0 leaked claims."""
        status, in_mem, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:jarvis",
        )
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)

        # Unlinked tenant assertions must NOT be returned
        solar_results = retriever.retrieve_structured_assertions("Solar Inverter SCADA")
        self.assertEqual(len(solar_results), 0)

        # Inverted index must not contain foreign assertion
        for word, asrt_list in retriever._salient_assertions.items():
            for a in asrt_list:
                self.assertNotEqual(
                    a.get("assertion_id"),
                    "asrt-solar-maintain",
                    f"Foreign assertion leaked into word '{word}': {a}",
                )

    def test_06_rule_c19_quarantined_claims_blocked(self) -> None:
        """Verifies Rule C-19 quarantined career leverage claims are strictly blocked."""
        status, in_mem, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:jarvis",
        )
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)

        # Rule C-19 assertions must NOT be returned
        c19_results = retriever.retrieve_structured_assertions("compensation trajectory")
        self.assertEqual(len(c19_results), 0)

        # C-19 node must NOT be allowed
        self.assertNotIn("node:c19_career_leverage", retriever._allowed_nodes)
