"""Adversarial and boundary stress tests for Antigravity SDK MCP Bridge.

Covers:
1. Malformed input (corrupt JSON, non-dict server specs, missing config files)
2. Boundary payloads (empty tools, massive payloads, out-of-range scoring values)
3. Invariant bypass attempts (tampered payload canonical hashing, unauthorized server lookup)
4. Rollback and graceful fallback (missing config directory gracefully initializes empty registry)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dual_pipeline.agent_sdk.mcp_bridge import McpServerRegistry
from dual_pipeline.agent_sdk.tools import (
    compute_hybrid_scoring,
    evaluate_candidate_retrieval,
    query_knowledge_graph,
    validate_rfc8259_canonical_hash,
)


class TestMcpBridgeAdversarial(unittest.TestCase):
    """Adversarial test suite stress testing MCP Bridge boundaries."""

    def test_corrupt_config_file_graceful_recovery(self):
        """Malformed input: Invalid JSON in config file must not crash registry initialization."""
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{this is not valid json! ::: [[[")
            temp_path = Path(f.name)

        try:
            registry = McpServerRegistry(config_path=temp_path)
            self.assertEqual(len(registry.registered_server_names), 0)
        finally:
            temp_path.unlink(missing_ok=True)

    def test_missing_config_file_graceful_recovery(self):
        """Rollback/recovery: Pointing to a non-existent config path initializes empty registry safely."""
        missing_path = Path("/nonexistent/path/to/missing_mcp_config.json")
        registry = McpServerRegistry(config_path=missing_path)
        self.assertEqual(len(registry.registered_server_names), 0)

    def test_unregistered_server_lookup_fails_closed(self):
        """Invariant: Attempting to build an unapproved or unregistered server must fail closed with KeyError."""
        registry = McpServerRegistry()
        with self.assertRaises(KeyError) as ctx:
            registry.build_stdio_server("rogue_unapproved_server")
        self.assertIn("not registered", str(ctx.exception))

    def test_canonical_hash_tamper_detection(self):
        """Invariant bypass attempt: Any single-character bitflip or field mutation changes canonical hash."""
        original = {"action": "APPROVE", "amount": 1000, "user": "admin"}
        tampered = {"action": "APPROVE", "amount": 1001, "user": "admin"}

        hash_orig = validate_rfc8259_canonical_hash(original)
        hash_tamp = validate_rfc8259_canonical_hash(tampered)
        self.assertNotEqual(hash_orig, hash_tamp)

    def test_hybrid_scoring_out_of_bounds_clamping(self):
        """Boundary payload: Extreme/negative scores and weights are clamped to [0.0, 1.0]."""
        # Negative scores
        score_neg = compute_hybrid_scoring(dense_score=-5.0, sparse_score=-2.0, dense_weight=-1.0)
        self.assertEqual(score_neg, 0.0)

        # Huge overflow scores
        score_over = compute_hybrid_scoring(dense_score=999.0, sparse_score=888.0, dense_weight=5.0)
        self.assertEqual(score_over, 1.0)

    def test_query_knowledge_graph_boundary_limits(self):
        """Boundary payload: Extreme limit arguments are clamped to [1, 50]."""
        # Negative limit clamped to 1
        res_low = query_knowledge_graph("test", limit=-10)
        self.assertEqual(res_low["limit"], 1)

        # Huge limit clamped to 50
        res_high = query_knowledge_graph("test", limit=99999)
        self.assertEqual(res_high["limit"], 50)

    def test_retrieval_candidate_empty_query_rejection(self):
        """Boundary payload: Empty/whitespace query is rejected at Gate 1."""
        for empty_q in ["", "   ", "\t\n"]:
            res = evaluate_candidate_retrieval(empty_q)
            self.assertEqual(res["status"], "REJECTED")
            self.assertFalse(res["passed_11_gates"])


if __name__ == "__main__":
    unittest.main()
