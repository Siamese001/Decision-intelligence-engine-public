"""
tests/test_heuristic_pre_judge_unit.py

Unit tests for Wave 2: Deterministic Heuristic Pre-Judging & Tier-Arbitrage (Agent 4).
Validates syntax, schema, AST topology, and vector grounding floor fail-fast checks.
"""
from __future__ import annotations

import json
import unittest

from dual_pipeline.llm_judge_core.heuristic_pre_judge import (
    DEFAULT_JUDGE_TOKENS_AVERTED,
    check_ast_topology,
    check_grounding_floor,
    check_json_syntax,
    check_schema_entities,
    evaluate_heuristic_pre_judge,
)
from dual_pipeline.llm_judge_core.orchestrator import DecomposedMultiJudge


class HeuristicPreJudgeUnitTests(unittest.TestCase):
    def test_01_syntax_check_valid_and_fenced_json(self) -> None:
        """Verify JSON syntax validation for raw JSON and markdown code blocks."""
        # Raw JSON dict
        v1, parsed1, err1 = check_json_syntax('{"nodes": [{"id": "n1"}]}')
        self.assertTrue(v1)
        self.assertIsNotNone(parsed1)
        self.assertEqual(len(err1), 0)

        # Markdown fenced JSON
        v2, parsed2, err2 = check_json_syntax('```json\n{"nodes": [{"id": "n2"}]}\n```')
        self.assertTrue(v2)
        self.assertIsNotNone(parsed2)
        self.assertEqual(len(err2), 0)

        # Malformed invalid JSON
        v3, parsed3, err3 = check_json_syntax("INVALID NON JSON TEXT")
        self.assertFalse(v3)
        self.assertIsNone(parsed3)
        self.assertTrue(len(err3) > 0)

    def test_02_schema_entities_validation(self) -> None:
        """Verify schema checking identifies missing or empty graph containers."""
        # Valid schema with nodes
        v1, errs1 = check_schema_entities({"nodes": [{"id": "1", "name": "Item"}]})
        self.assertTrue(v1)
        self.assertEqual(len(errs1), 0)

        # Missing graph topology keys
        v2, errs2 = check_schema_entities({"irrelevant_key": "some_value"})
        self.assertFalse(v2)
        self.assertTrue(any("lacks essential graph keys" in e for e in errs2))

        # Empty graph container
        v3, errs3 = check_schema_entities({"nodes": [], "edges": []})
        self.assertFalse(v3)
        self.assertTrue(any("completely empty" in e for e in errs3))

    def test_03_ast_topology_validation(self) -> None:
        """Verify AST topology identifies missing node IDs and excessive dangling edges."""
        # Valid topology
        valid_payload = {
            "nodes": [{"id": "node_a", "type": "SYSTEM"}, {"id": "node_b", "type": "PERSON"}],
            "edges": [{"source": "node_a", "target": "node_b", "relation": "USES"}],
        }
        v1, errs1 = check_ast_topology(valid_payload)
        self.assertTrue(v1)
        self.assertEqual(len(errs1), 0)

        # Node missing ID
        missing_id_payload = {
            "nodes": [{"type": "SYSTEM"}],
            "edges": [],
        }
        v2, errs2 = check_ast_topology(missing_id_payload)
        self.assertFalse(v2)
        self.assertTrue(any("missing required 'id' or 'name'" in e for e in errs2))

        # Excessive dangling edges (>50% unlinked)
        dangling_payload = {
            "nodes": [{"id": "node_a"}],
            "edges": [
                {"source": "unknown_1", "target": "unknown_2"},
                {"source": "unknown_3", "target": "unknown_4"},
            ],
        }
        v3, errs3 = check_ast_topology(dangling_payload)
        self.assertFalse(v3)
        self.assertTrue(any("dangling edges" in e for e in errs3))

    def test_04_grounding_floor_check(self) -> None:
        """Verify grounding floor detects ungrounded candidates."""
        f1_source = "harper Hoffman and Amit Ayer discussed LENS pipeline architecture and turbine sizing."

        # Grounded candidate with high overlap
        grounded_candidate = {"claims": ["harper Hoffman reviewed turbine sizing for LENS pipeline."]}
        v1, score1, errs1 = check_grounding_floor(grounded_candidate, f1_source, floor_threshold=0.10)
        self.assertTrue(v1)
        self.assertGreaterEqual(score1, 0.10)

        # Completely ungrounded candidate
        hallucinated_candidate = {"claims": ["Unicorn spacecraft visited Jupiter radiation belts with laser cannons."]}
        v2, score2, errs2 = check_grounding_floor(hallucinated_candidate, f1_source, floor_threshold=0.10)
        self.assertFalse(v2)
        self.assertLess(score2, 0.10)
        self.assertTrue(any("Grounding floor failure" in e for e in errs2))

    def test_05_evaluate_heuristic_pre_judge_tiered_rejection(self) -> None:
        """Verify tiered fail-fast rejection latency < 10ms and estimated tokens saved."""
        # Tier 1 syntax fail
        res1 = evaluate_heuristic_pre_judge("MALFORMED")
        self.assertEqual(res1["verdict"], "FAIL")
        self.assertEqual(res1["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")
        self.assertFalse(res1["passed_pre_check"])
        self.assertLess(res1["elapsed_ms"], 10.0)
        self.assertEqual(res1["tokens_saved_estimate"], DEFAULT_JUDGE_TOKENS_AVERTED)

        # Tier 1 schema fail
        res2 = evaluate_heuristic_pre_judge('{"unrelated_dict": 123}')
        self.assertEqual(res2["verdict"], "FAIL")
        self.assertEqual(res2["pre_judge_tier"], "TIER_1_SCHEMA_FAIL")
        self.assertFalse(res2["passed_pre_check"])

        # Tier 2 AST fail
        res3 = evaluate_heuristic_pre_judge('{"nodes": [{"id": "n1"}], "edges": [{"source": "bad1", "target": "bad2"}]}')
        self.assertEqual(res3["verdict"], "FAIL")
        self.assertEqual(res3["pre_judge_tier"], "TIER_2_AST_FAIL")

        # Tier 3 grounding fail
        f1 = "Session regarding LENS contract and telemetry verification."
        res4 = evaluate_heuristic_pre_judge('{"claims": ["Galactic empire space fleets deployed to Saturn."]}', f1_text=f1)
        self.assertEqual(res4["verdict"], "FAIL")
        self.assertEqual(res4["pre_judge_tier"], "TIER_3_GROUNDING_FLOOR_FAIL")

        # Full pass
        valid_f3 = json.dumps({
            "nodes": [{"id": "lens", "name": "Project LENS"}, {"id": "mandy", "name": "harper Hoffman"}],
            "edges": [{"source": "mandy", "target": "lens", "relation": "LEADS"}],
        })
        f1_valid = "harper Hoffman leads the Project LENS initiative."
        res5 = evaluate_heuristic_pre_judge(valid_f3, f1_text=f1_valid)
        self.assertEqual(res5["verdict"], "PASS")
        self.assertEqual(res5["pre_judge_tier"], "PASS_HEURISTIC_PRE_CHECK")
        self.assertTrue(res5["passed_pre_check"])
        self.assertEqual(res5["composite_score"], 100.0)

    def test_06_decomposed_multi_judge_dispatches_pre_judge(self) -> None:
        """Verify DecomposedMultiJudge uses heuristic pre-judging to short-circuit non-compliant candidates."""
        judge = DecomposedMultiJudge()
        # Malformed candidate should be caught by heuristic pre-judge before calling micro-judge
        res = judge.evaluate_graph("MALFORMED_CANDIDATE", "Source transcript text")
        self.assertEqual(res["verdict"], "FAIL")
        self.assertFalse(res["passed_pre_check"])
        self.assertEqual(res["pre_judge_tier"], "TIER_1_SYNTAX_FAIL")


if __name__ == "__main__":
    unittest.main()
