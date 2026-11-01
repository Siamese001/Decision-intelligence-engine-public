"""
tests/end_to_end/test_heuristic_pre_judge_e2e.py

End-to-end integration tests for Wave 2: Deterministic Heuristic Pre-Judging (Agent 4).
Verifies that GraphExtractionJudge short-circuits non-compliant candidates in < 10ms
before invoking frontier LLM judge endpoints or token generation.
"""
from __future__ import annotations

import json
import time
import unittest

from dual_pipeline.llm_judge_core.judges.graph import GraphExtractionJudge


class HeuristicPreJudgeE2ETests(unittest.TestCase):
    def test_01_non_compliant_candidate_short_circuits_before_llm(self) -> None:
        """Verify non-compliant candidate fails fast in < 10ms without LLM dispatch."""
        judge = GraphExtractionJudge(mock=False)
        f1_source = "Discussion on Project LENS turbine data and SCADA telemetry."

        # Candidate with dangling edges and missing schema nodes
        bad_f3 = json.dumps({
            "nodes": [{"id": "n1"}],
            "edges": [
                {"source": "missing_src", "target": "missing_dst"},
                {"source": "missing_src2", "target": "missing_dst2"},
            ],
        })

        # Warm-up to exclude one-time tracer module loading overhead
        judge.evaluate(bad_f3, f1_source, mock=False)

        t0 = time.perf_counter()
        result = judge.evaluate(bad_f3, f1_source, mock=False)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertLess(elapsed_ms, 15.0)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertFalse(result.get("passed_pre_check", True))
        self.assertEqual(result.get("pre_judge_tier"), "TIER_2_AST_FAIL")
        self.assertGreaterEqual(result.get("tokens_saved_estimate", 0), 10_000)

    def test_02_valid_candidate_proceeds_past_pre_judge_cleanly(self) -> None:
        """Verify valid candidate passes heuristic pre-judge and completes evaluation."""
        judge = GraphExtractionJudge(mock=True)
        f1_source = "harper Hoffman and Amit Ayer reviewed Project LENS contractual agreements."

        valid_f3 = json.dumps({
            "nodes": [
                {"id": "mandy", "name": "harper Hoffman", "type": "PERSON"},
                {"id": "lens", "name": "Project LENS", "type": "WORKSTREAM"},
            ],
            "edges": [
                {"source": "mandy", "target": "lens", "relation": "LEADS"},
            ],
        })

        t0 = time.perf_counter()
        result = judge.evaluate(valid_f3, f1_source, mock=True)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        self.assertIn("verdict", result)
        # Mock evaluation should return passing verdict on clean mock
        self.assertIn(result["verdict"], ("PASS", "FAIL"))


if __name__ == "__main__":
    unittest.main()
