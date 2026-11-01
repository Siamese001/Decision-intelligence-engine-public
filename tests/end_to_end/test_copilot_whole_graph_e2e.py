"""
tests/end_to_end/test_copilot_whole_graph_e2e.py

End-to-End integration test suite for Wave 4:
1. Whole-graph active pair load verifies unpartitioned topology (>= 750 nodes, >= 2,500 vectors).
2. Live session cross-workstream request retrieves Conduit platform under project_nexus session.
3. Live session entity alias resolution retrieves harper Hoffman under project_nexus session.
4. Exact Oct 7 production regression query executes and passes grounding with [PD Conduit] attribution.
5. End-to-end whole-graph retrieval latency p95 is strictly under 15ms.
6. Rule C-19 career leverage firewall verifies 0 leaks in live session context.
"""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from dual_pipeline.live_meeting_recorder_core.c19_firewall import (
    has_c19_marker,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)


class TestCopilotWholeGraphE2E(unittest.TestCase):
    """End-to-End lifecycle tests for whole-graph copilot retrieval."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.runtime_root = self.repo_root / ".local" / "dual_pipeline"
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_wg_e2e_")
        self.meeting_dir = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_meeting_turns(self, turns: List[Dict[str, Any]]) -> None:
        seg_file = self.meeting_dir / "segments.jsonl"
        with open(seg_file, "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

    def test_01_whole_graph_load_active_pair(self) -> None:
        """Verifies load_copilot_graph loads unpartitioned topology with >= 750 nodes."""
        status, in_mem, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:project_nexus",
        )
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)
        self.assertIsNotNone(in_mem)
        self.assertIsNotNone(retriever)

        # Whole-graph unpartitioned invariants
        self.assertGreaterEqual(len(retriever._allowed_nodes), 750)
        self.assertIn("stakeholder_mandy_hopmann", retriever._allowed_nodes)
        self.assertIn("workstream_pd_conduit", retriever._allowed_nodes)

        # Whole-graph assertion topology count
        self.assertGreaterEqual(len(in_mem.assertions), 2500)

        # Pre-normalized dense BLAS vector matrix from sidecar
        self.assertIsNotNone(retriever._dense_matrix)
        self.assertGreaterEqual(len(retriever._dense_record_ids), 1000)
        self.assertEqual(retriever._dense_matrix.shape[0], len(retriever._dense_record_ids))
        self.assertEqual(retriever._dense_matrix.shape[1], 1024)

    def test_02_e2e_live_session_cross_workstream_conduit_request(self) -> None:
        """Verifies cross-workstream Conduit retrieval under project_nexus session."""
        self._write_meeting_turns([
            {
                "speaker": "Jason",
                "text": "Who is leading the Conduit platform and what does the team own?",
                "timestamp": "00:01:00",
                "turn_sec": 60.0,
            }
        ])
        args = argparse.Namespace(
            intent="respond",
            target="Jason",
            topic="Conduit platform",
            meeting_dir=str(self.meeting_dir),
            runtime_root=str(self.runtime_root),
            project="project_nexus",
        )
        result = execute_copilot_request(args)
        self.assertEqual(result.get("status"), "PASS")
        self.assertEqual(result.get("target"), "Jason")

        cited_ids = result.get("cited_assertion_ids", [])
        self.assertGreaterEqual(len(cited_ids), 1)

        # Answers file verification
        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        self.assertTrue(answers_file.is_file())
        with open(answers_file, "r", encoding="utf-8") as f:
            answer_records = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(answer_records), 1)
        resp_text = answer_records[0].get("response", "")
        self.assertIn("[PD Conduit]", resp_text)

        # Attempts file verification
        attempts_file = self.meeting_dir / "copilot_attempts.jsonl"
        self.assertTrue(attempts_file.is_file())
        with open(attempts_file, "r", encoding="utf-8") as f:
            attempt_records = [json.loads(line) for line in f if line.strip()]
        self.assertEqual(len(attempt_records), 1)
        self.assertEqual(attempt_records[0].get("terminal_outcome"), "SUCCESS")

    def test_03_e2e_live_session_mandy_hopmann_alias_request(self) -> None:
        """Verifies harper Hoffman alias resolution under project_nexus session."""
        self._write_meeting_turns([
            {
                "speaker": "Jason",
                "text": "Can someone tell me who harper Hoffman is and what she manages?",
                "timestamp": "00:01:00",
                "turn_sec": 60.0,
            }
        ])
        args = argparse.Namespace(
            intent="respond",
            target="Jason",
            topic="harper Hoffman",
            meeting_dir=str(self.meeting_dir),
            runtime_root=str(self.runtime_root),
            project="project_nexus",
        )
        result = execute_copilot_request(args)
        self.assertEqual(result.get("status"), "PASS")

        cited_ids = result.get("cited_assertion_ids", [])
        self.assertGreaterEqual(len(cited_ids), 1)

        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        self.assertTrue(answers_file.is_file())
        with open(answers_file, "r", encoding="utf-8") as f:
            answer_records = [json.loads(line) for line in f if line.strip()]
        resp_text = answer_records[0].get("response", "")
        self.assertIn("Mandy", resp_text)

    def test_04_e2e_exact_oct7_production_utterance_regression(self) -> None:
        """Pins the exact Oct 7 regression utterance, verifying grounded response delivery."""
        utterance = (
            "Retiring standalone GRID_OS work to fold capabilities into the broader Conduit "
            "platform directly consolidates generation workflows under harper Hoffman's "
            "PGD Technician Conduit umbrella, shifting engineering focus to unified Conduit tooling."
        )
        self._write_meeting_turns([
            {
                "speaker": "Jason",
                "text": utterance,
                "timestamp": "00:01:00",
                "turn_sec": 60.0,
            }
        ])
        args = argparse.Namespace(
            intent=None,
            target=None,
            topic=None,
            request=utterance,
            meeting_dir=str(self.meeting_dir),
            runtime_root=str(self.runtime_root),
            project="project_nexus",
        )
        result = execute_copilot_request(args)
        self.assertEqual(result.get("status"), "PASS", f"Regression query failed: {result}")
        cited_ids = result.get("cited_assertion_ids", [])
        self.assertGreaterEqual(len(cited_ids), 3)

        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        with open(answers_file, "r", encoding="utf-8") as f:
            answer_records = [json.loads(line) for line in f if line.strip()]
        resp_text = answer_records[0].get("response", "")
        self.assertIn("[PD Conduit]", resp_text)

    def test_05_e2e_retrieval_latency_p95_under_15ms(self) -> None:
        """Verifies whole-graph vectorized retrieval p95 latency remains strictly under 15ms."""
        _, _, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:project_nexus",
        )
        self.assertIsNotNone(retriever)

        queries = [
            "Conduit platform architecture",
            "harper Hoffman product manager",
            "PGD technician tooling",
            "SAP ERP integration owner",
            "LENS catch up status",
            "Chakra governance cadence",
            "Arc Angel closeout assess",
            "Elements AMP timing",
            "Apollo schema migration",
            "ROCC Conduit fault code",
        ]
        latencies: List[float] = []

        # Warm-up pass
        retriever.retrieve_structured_assertions("warmup Conduit", max_results=10)

        # 30 timed queries
        for i in range(30):
            q = queries[i % len(queries)]
            t0 = time.perf_counter()
            candidates = retriever.retrieve_structured_assertions(q, max_results=15)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(elapsed_ms)
            self.assertGreaterEqual(len(candidates), 1)

        p95 = float(np.percentile(latencies, 95))
        self.assertLess(
            p95,
            15.0,
            f"Whole-graph p95 latency breached 15ms ceiling: {p95:.2f}ms (samples: {latencies})",
        )

    def test_06_e2e_c19_career_firewall_zero_leakage(self) -> None:
        """Verifies Rule C-19 career leverage assertions are strictly absent and fail closed."""
        self._write_meeting_turns([
            {
                "speaker": "Jason",
                "text": "What is the compensation trajectory and personal career leverage strategy?",
                "timestamp": "00:01:00",
                "turn_sec": 60.0,
            }
        ])
        args = argparse.Namespace(
            intent="respond",
            target="Jason",
            topic="compensation trajectory personal career leverage",
            meeting_dir=str(self.meeting_dir),
            runtime_root=str(self.runtime_root),
            project="project_nexus",
        )
        result = execute_copilot_request(args)
        # Result must either be FAIL or have 0 C-19 assertions
        cited_ids = result.get("cited_assertion_ids", [])
        for cid in cited_ids:
            self.assertFalse(has_c19_marker(cid))
            self.assertNotIn("career_leverage", cid.lower())

        answers_file = self.meeting_dir / "copilot_answers.jsonl"
        if answers_file.is_file():
            with open(answers_file, "r", encoding="utf-8") as f:
                lines = [json.loads(line) for line in f if line.strip()]
            for record in lines:
                text = record.get("response", "").lower()
                self.assertNotIn("1.2m", text)
                self.assertNotIn("compensation trajectory", text)
                self.assertNotIn("internal_advisory", text)
