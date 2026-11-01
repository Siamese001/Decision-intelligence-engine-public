"""
tests/adversarial/test_copilot_whole_graph_adversarial.py

Adversarial stress and security test suite for Wave 4:
Covers 5 mandatory edge-case categories under e2e-and-edge-case-coverage.md:
1. Invariant bypass attempt: Rule C-19 career leverage injection across 1,000 permutations (0 leaks).
2. Boundary payload: Empty strings, giant 50k tokens, whitespace, unicode fuzzing.
3. Malformed input: Corrupted active_pair, missing database, malformed arguments.
4. Concurrency & race: 100 concurrent workers executing whole-graph retrieval simultaneously.
5. Exact production regression: Pinning Oct 7 whole-graph retrieval blindness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List

from dual_pipeline.live_meeting_recorder_core.c19_firewall import (
    has_c19_marker,
    is_c19_quarantined_assertion,
    is_c19_quarantined_node,
)
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    GraphLoadStatus,
    load_copilot_graph,
)


class TestCopilotWholeGraphAdversarial(unittest.TestCase):
    """Adversarial security, concurrency, boundary, and regression tests."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parent.parent.parent
        self.runtime_root = self.repo_root / ".local" / "dual_pipeline"
        self.temp_dir = tempfile.mkdtemp(prefix="test_copilot_wg_adv_")
        self.meeting_dir = Path(self.temp_dir)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_01_invariant_bypass_c19_injection_1000_permutations(self) -> None:
        """Category: Invariant bypass attempt. 1,000 randomized C-19 leak probes verify 0 leaks."""
        _, _, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:project_nexus",
        )
        self.assertIsNotNone(retriever)

        c19_tokens = [
            "compensation",
            "trajectory",
            "$1.2M",
            "career leverage",
            "personal advancement",
            "private advisory notes",
            "shadow graph",
            "equity refresh",
            "base salary",
            "stealth score",
            "INTERNAL_ADVISORY",
        ]
        innocent_tokens = [
            "Conduit",
            "Nexus",
            "harper Hoffman",
            "PGD technician",
            "status",
            "architecture",
            "deliverables",
            "review",
        ]

        leaked_count = 0
        for _ in range(1000):
            # Mix 1-3 C-19 tokens with 1-2 innocent tokens
            chosen_c19 = random.sample(c19_tokens, k=random.randint(1, 3))
            chosen_innocent = random.sample(innocent_tokens, k=random.randint(1, 2))
            query = " ".join(chosen_c19 + chosen_innocent)

            results = retriever.retrieve_structured_assertions(query, max_results=10)
            for r in results:
                claim = str(r.get("claim_text") or r.get("object_literal") or "")
                aid = str(r.get("assertion_id") or "")
                if has_c19_marker(claim) or is_c19_quarantined_assertion(r, aid):
                    leaked_count += 1

        self.assertEqual(
            leaked_count,
            0,
            f"Rule C-19 invariant violated: {leaked_count} leaks across 1,000 permutations",
        )

    def test_02_boundary_payload_edge_cases(self) -> None:
        """Category: Boundary payload. Verifies empty, null, whitespace, giant, and unicode strings."""
        _, _, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:project_nexus",
        )
        self.assertIsNotNone(retriever)

        boundary_queries = [
            "",
            " ",
            "   \t\n   ",
            "a",
            "??? !!! @@@ ### $$$ %%% ^^^ &&& *** ((( )))",
            "Conduit " * 5000,  # 40,000-character payload
            "\U0001F600 \U0001F680 \u4e2d\u6587 \u0440\u0443\u0441\u0441\u043a\u0438\u0439",
            "None",
            "null",
            "undefined",
        ]

        for bq in boundary_queries:
            results = retriever.retrieve_structured_assertions(bq, max_results=10)
            self.assertIsInstance(results, list)
            # Must not raise or return corrupt non-dict items
            for r in results:
                self.assertIsInstance(r, dict)

    def test_03_malformed_input_fail_closed(self) -> None:
        """Category: Malformed input. Verifies corrupted active_pair and bad arguments fail closed."""
        # 1. Corrupted hash in active_pair.json
        bad_runtime = self.meeting_dir / "bad_runtime"
        bad_runtime.mkdir(parents=True, exist_ok=True)
        bad_pair = {
            "schema_version": "1.0.0",
            "graph_path": "nonexistent.sqlite",
            "graph_sha256": "0" * 64,
        }
        (bad_runtime / "active_pair.json").write_text(json.dumps(bad_pair), encoding="utf-8")

        status, in_mem, ret = load_copilot_graph(runtime_root=bad_runtime)
        self.assertEqual(status, GraphLoadStatus.ABSENT)
        self.assertIsNone(in_mem)
        self.assertIsNone(ret)

        # 2. Corrupted arguments to execute_copilot_request
        bad_args = argparse.Namespace(
            intent="unknown_intent_xyz",
            target="NonexistentTarget999",
            topic="NonexistentTopic999",
            meeting_dir=str(self.meeting_dir),
            runtime_root=str(self.runtime_root),
        )
        res = execute_copilot_request(bad_args)
        self.assertEqual(res.get("status"), "FAIL")
        self.assertIn("error", res)

    def test_04_concurrency_race_100_workers(self) -> None:
        """Category: Concurrency & race. 100 concurrent workers query whole graph simultaneously."""
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

        def worker_query(worker_id: int) -> int:
            q = queries[worker_id % len(queries)]
            res = retriever.retrieve_structured_assertions(q, max_results=10)
            return len(res)

        successes = 0
        with ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(worker_query, i) for i in range(100)]
            for future in as_completed(futures):
                count = future.result()
                if count >= 1:
                    successes += 1

        self.assertEqual(
            successes,
            100,
            f"100-worker concurrency experienced failures: {successes}/100 passed",
        )

    def test_05_exact_oct7_production_regression(self) -> None:
        """Category: Exact production regression. Verifies Oct 7 blindness is completely resolved."""
        status, _, retriever = load_copilot_graph(
            runtime_root=self.runtime_root,
            engagement_id="project:project_nexus",
        )
        self.assertEqual(status, GraphLoadStatus.LOADED_ACTIVE_PAIR)

        # Oct 7 exact scenario: Querying harper Hoffman and Conduit during project_nexus session
        query = (
            "Retiring standalone GRID_OS work to fold capabilities into the broader Conduit "
            "platform directly consolidates generation workflows under harper Hoffman's "
            "PGD Technician Conduit umbrella, shifting engineering focus to unified Conduit tooling."
        )
        candidates = retriever.retrieve_structured_assertions(query, max_results=15)

        # Prior regression returned 0 valid candidates due to engagement partition pre-filtering
        self.assertGreaterEqual(
            len(candidates),
            3,
            "Exact production regression detected: whole-graph retrieval returned < 3 candidates",
        )

        c_texts = " ".join(
            str(c.get("claim_text") or c.get("object_literal") or "") for c in candidates
        ).lower()
        self.assertTrue(
            "conduit" in c_texts or "hopmann" in c_texts,
            "Retrieved candidates do not ground Conduit or harper Hoffman",
        )
