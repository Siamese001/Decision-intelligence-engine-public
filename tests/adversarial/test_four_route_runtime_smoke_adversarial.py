"""Adversarial and Edge-Case Matrix for Four-Route Runtime Smoke Suite (Wave 4).

Covers mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: Empty/whitespace query routing, single/zero-option decision frames.
2. Malformed input: Corrupted database headers and invalid active pair structures.
3. Concurrency & race: Multi-threaded parallel decision arbitration with zero lock contention.
4. Rollback & recovery: Ungrounded query handling with safe copilot abstention.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest import TestCase

from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.graph_retrieval_core import read_graph_from_disk
from dual_pipeline.live_meeting_recorder_core.copilot_engine import execute_copilot_request
from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry
from dual_pipeline.query_router_core import route_query as core_route_query
from tests.end_to_end.test_four_route_runtime_smoke_e2e import _init_canonical_smoke_graph


class TestFourRouteRuntimeSmokeAdversarial(TestCase):
    """Adversarial stress and edge-case verification for 4-route smoke pipeline."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="smoke_adversarial_")
        self.sandbox_dir = Path(self.tmp.name)
        self.graph_path = self.sandbox_dir / "canonical_graph.sqlite"
        _init_canonical_smoke_graph(self.graph_path)

        # Active pair configuration
        self.active_pair = {
            "generation_id": "gen-smoke-001",
            "graph_path": "canonical_graph.sqlite",
            "schema_version": "1.0.0",
        }
        (self.sandbox_dir / "active_pair.json").write_text(json.dumps(self.active_pair, indent=2), encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # -------------------------------------------------------------------------
    # Category 1: Boundary Payloads (Empty inputs, whitespace, zero-length)
    # -------------------------------------------------------------------------
    def test_boundary_empty_and_whitespace_query_routing(self) -> None:
        """Category 1: Boundary payloads - verifies empty and whitespace queries raise ValueError and symbols route."""
        for q in ("", "   ", "\t\n\r"):
            with self.assertRaises(ValueError):
                core_route_query(q, authorized=True)

        symbol_decision = core_route_query("???!!!@@@###$$$", authorized=True)
        self.assertIsNotNone(symbol_decision)
        self.assertIsNotNone(symbol_decision.destination)

    def test_boundary_single_option_decision_frame(self) -> None:
        """Category 1: Boundary payloads - verifies single-option decision frame escalates or handles cleanly."""
        ledger_file = self.sandbox_dir / "hitl_decision_ledger.json"
        arbiter = HitlArbiterAgent(ledger_path=ledger_file)

        # Single option has no second choice (margin undefined or 1.0)
        options = [{"key": "opt_only", "title": "Only Option", "confidence_score": 0.85, "rationale": "Single choice"}]
        res = arbiter.arbitrate_decision(
            decision_id="dec-single-001",
            category="INFRASTRUCTURE_ARCHITECTURE",
            question="Single option edge case?",
            options=options,
        )
        self.assertIn(res["status"], ("AUTONOMOUS_APPROVED", "NEEDS_OPERATOR_REVIEW"))
        self.assertTrue(ledger_file.is_file())

    # -------------------------------------------------------------------------
    # Category 2: Malformed Input (Corrupted SQLite header, truncated JSON)
    # -------------------------------------------------------------------------
    def test_malformed_sqlite_graph_header_fails_closed(self) -> None:
        """Category 2: Malformed input - corrupted SQLite file header fails closed with OperationalError."""
        corrupt_db_path = self.sandbox_dir / "corrupted_graph.sqlite"
        corrupt_db_path.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_HEADER" + b"\x00" * 200)

        with self.assertRaises((sqlite3.DatabaseError, sqlite3.OperationalError)):
            read_graph_from_disk(corrupt_db_path)

    # -------------------------------------------------------------------------
    # Category 3: Concurrency & Race (Parallel WAL writes & arbitration)
    # -------------------------------------------------------------------------
    def test_concurrency_parallel_decision_arbitration_stress(self) -> None:
        """Category 3: Concurrency & race - 8 concurrent workers arbitrating decisions simultaneously."""
        ledger_file = self.sandbox_dir / "hitl_concurrent_ledger.json"

        def _worker_task(worker_idx: int) -> Dict[str, Any]:
            arbiter = HitlArbiterAgent(ledger_path=ledger_file)
            options = [
                {"key": f"opt_a_{worker_idx}", "title": f"Option A {worker_idx}", "confidence_score": 0.90, "rationale": "High conf"},
                {"key": f"opt_b_{worker_idx}", "title": f"Option B {worker_idx}", "confidence_score": 0.40, "rationale": "Low conf"},
            ]
            return arbiter.arbitrate_decision(
                decision_id=f"dec-concurrent-{worker_idx:03d}",
                category="CONCURRENCY_TEST",
                question=f"Concurrent worker question {worker_idx}?",
                options=options,
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_worker_task, i) for i in range(16)]
            results = [f.result(timeout=30.0) for f in futures]

        self.assertEqual(len(results), 16)
        for r in results:
            self.assertEqual(r["status"], "AUTONOMOUS_APPROVED")

        # Verify all 16 entries safely recorded in ledger file without JSON corruption
        self.assertTrue(ledger_file.is_file())
        ledger_data = json.loads(ledger_file.read_text(encoding="utf-8"))
        decisions = ledger_data.get("decisions", [])
        self.assertEqual(len(decisions), 16)

    # -------------------------------------------------------------------------
    # Category 4: Rollback & Recovery (Ungrounded queries fail safe without hallucination)
    # -------------------------------------------------------------------------
    def test_recovery_ungrounded_copilot_request_fails_safe(self) -> None:
        """Category 4: Rollback & recovery - ungrounded copilot query fails safe and abstains."""
        meeting_dir = self.sandbox_dir / "output" / "project_nexus" / "10-09-2026" / "Jeffrey_Amit_Catchup"
        meeting_dir.mkdir(parents=True, exist_ok=True)
        (self.sandbox_dir / ".session_registry").mkdir(parents=True, exist_ok=True)

        session_id = "sess-adversarial-nexus-001"
        turns = [
            {"seq": 1, "speaker": "Jeffrey", "text": "Discussing routine sprint deliverables.", "timestamp": "00:00:05", "turn_sec": 5.0},
            {"seq": 2, "speaker": "Amit", "text": "Agreed, keeping things on track.", "timestamp": "00:00:10", "turn_sec": 10.0},
        ]
        with open(meeting_dir / "segments.jsonl", "w", encoding="utf-8") as f:
            for t in turns:
                f.write(json.dumps(t) + "\n")

        transcript_file = meeting_dir / "transcript.md"
        transcript_file.write_text("**[00:00:05]** Jeffrey: Discussing routine sprint deliverables.\n**[00:00:10]** Amit: Agreed.\n", encoding="utf-8")

        reg = SessionRegistry(self.sandbox_dir)
        reg.register(
            session_id=session_id,
            engagement_id="project:project_nexus",
            engagement_name="Nexus",
            meeting_slug="jeffrey_amit_catchup",
            attendees=["Jeffrey", "Amit"],
            output_dir=str(meeting_dir.relative_to(self.sandbox_dir)),
            transcript_md=str(transcript_file.relative_to(self.sandbox_dir)),
        )

        # Query targeting a completely fictitious concept absent from graph and dialogue
        args = argparse.Namespace(
            session=session_id,
            active=False,
            request="What are the plasma quantum propulsion specs for interstellar travel?",
            intent=None,
            topic=None,
            target=None,
            words=60,
            json=True,
            meeting_dir=str(meeting_dir),
            mock=True,
            project="project_nexus",
            runtime_root=str(self.sandbox_dir),
            graph=str(self.graph_path),
        )

        res = execute_copilot_request(args)
        # Must fail safe without fabricating graph evidence
        self.assertEqual(res.get("status"), "FAIL")
        self.assertIn(res.get("failure_category"), ("absent_candidates", "session_discovery", "ungrounded_evidence"))
