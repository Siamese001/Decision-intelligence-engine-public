"""Unit tests for Antigravity SDK MCP Bridge and tool wrappers.

Validates:
- McpServerRegistry discovers and instantiates contract-valid McpStdioServer objects
- Context-safe tool allowlists are applied
- Profile filtering maps to appropriate functional subsets
- Native tool wrappers return structured schema-compliant payloads
"""
from __future__ import annotations

import unittest

from google.antigravity.types import McpStdioServer

from dual_pipeline.agent_sdk.mcp_bridge import (
    McpServerDefinition,
    McpServerRegistry,
)
from dual_pipeline.agent_sdk.tools import (
    compute_hybrid_scoring,
    evaluate_candidate_retrieval,
    inspect_graph_snapshot,
    query_knowledge_graph,
    read_assertion_provenance,
    validate_rfc8259_canonical_hash,
    verify_contract_compliance,
)


class TestMcpBridge(unittest.TestCase):
    """Unit test suite for McpServerRegistry and tool wrappers."""

    def setUp(self):
        self.registry = McpServerRegistry()

    def test_registry_loads_default_servers(self):
        """Registry discovers registered servers from .agents/mcp_config.json."""
        servers = self.registry.registered_server_names
        self.assertGreaterEqual(len(servers), 5)
        self.assertIn("nee-knowledge-graph", servers)
        self.assertIn("graphdb-inspector", servers)
        self.assertIn("contract-governance", servers)
        self.assertIn("judge-retrieval-eval", servers)

    def test_build_stdio_server_with_default_allowlist(self):
        """Building a stdio server applies default context-safe tool allowlist."""
        server = self.registry.build_stdio_server("nee-knowledge-graph")
        self.assertIsInstance(server, McpStdioServer)
        self.assertEqual(server.name, "nee-knowledge-graph")
        self.assertEqual(server.type, "stdio")
        self.assertIsNotNone(server.enabled_tools)
        self.assertIn("nee_query", server.enabled_tools)

    def test_build_profile_servers(self):
        """Building profile servers returns only the scoped servers for that profile."""
        graph_servers = self.registry.build_profile_servers("graph")
        server_names = [s.name for s in graph_servers]
        self.assertEqual(sorted(server_names), ["graphdb-inspector", "nee-knowledge-graph"])

    def test_unknown_profile_raises_value_error(self):
        """Attempting to resolve a non-existent profile raises ValueError."""
        with self.assertRaises(ValueError):
            self.registry.build_profile_servers("non_existent_profile")

    def test_manual_server_registration(self):
        """Custom MCP server can be manually registered and built."""
        custom_def = McpServerDefinition(
            name="custom-spark-analytics",
            command="python3",
            args=("-m", "dual_pipeline.spark.server"),
            enabled_tools=("run_batch_analytics",),
        )
        self.registry.register_server(custom_def)
        server = self.registry.build_stdio_server("custom-spark-analytics")
        self.assertEqual(server.name, "custom-spark-analytics")
        self.assertEqual(server.enabled_tools, ["run_batch_analytics"])

    # Tool wrapper tests
    def test_query_knowledge_graph_tool(self):
        """Graph query tool returns structured matches."""
        result = query_knowledge_graph("Find project dependencies", project="project_nexus")
        self.assertEqual(result["status"], "SUCCESS")
        self.assertGreater(result["match_count"], 0)
        self.assertEqual(result["project"], "project_nexus")

    def test_query_knowledge_graph_empty_query(self):
        """Graph query tool rejects empty input."""
        result = query_knowledge_graph("")
        self.assertEqual(result["status"], "ERROR")
        self.assertEqual(result["match_count"], 0)

    def test_inspect_graph_snapshot_tool(self):
        """Snapshot tool returns node/edge counts and sidecar health."""
        snapshot = inspect_graph_snapshot("project_nexus")
        self.assertEqual(snapshot["status"], "SYNCHRONIZED")
        self.assertTrue(snapshot["sidecars"]["dense_vectors_synced"])

    def test_read_assertion_provenance_tool(self):
        """Assertion provenance tool retrieves evidence links."""
        assertion = read_assertion_provenance("assertion:nexus:001")
        self.assertEqual(assertion["status"], "FOUND")
        self.assertEqual(assertion["subject"], "org:apex_global_energy")
        self.assertIn("01_forensic_transcript.md", assertion["evidence_sources"][0])

    def test_validate_rfc8259_canonical_hash_tool(self):
        """RFC 8259 tool computes deterministic canonical SHA-256."""
        payload1 = {"b": 2, "a": 1}
        payload2 = {"a": 1, "b": 2}
        hash1 = validate_rfc8259_canonical_hash(payload1)
        hash2 = validate_rfc8259_canonical_hash(payload2)
        self.assertEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)

    def test_verify_contract_compliance_tool(self):
        """Contract verification tool checks required intake fields."""
        valid_meta = {
            "project": "project_nexus",
            "meeting_name": "Sync",
            "date": "09-29-2026",
        }
        res_valid = verify_contract_compliance(valid_meta)
        self.assertEqual(res_valid["status"], "COMPLIANT")
        self.assertTrue(res_valid["is_valid"])

        invalid_meta = {"project": "project_nexus"}
        res_invalid = verify_contract_compliance(invalid_meta)
        self.assertEqual(res_invalid["status"], "NON_COMPLIANT")
        self.assertFalse(res_invalid["is_valid"])

    def test_evaluate_candidate_retrieval_tool(self):
        """Retrieval evaluation tool returns 11-gate clearance verdict."""
        res = evaluate_candidate_retrieval("What is the battery storage capacity?")
        self.assertEqual(res["status"], "QUALIFIED")
        self.assertTrue(res["passed_11_gates"])
        self.assertEqual(len(res["gates"]), 11)

    def test_compute_hybrid_scoring_tool(self):
        """Hybrid scoring combines dense and sparse weights correctly."""
        # 50/50 balance
        score1 = compute_hybrid_scoring(dense_score=0.8, sparse_score=0.4, dense_weight=0.5)
        self.assertEqual(score1, 0.6)

        # 100% dense
        score2 = compute_hybrid_scoring(dense_score=0.8, sparse_score=0.4, dense_weight=1.0)
        self.assertEqual(score2, 0.8)

        # 100% sparse
        score3 = compute_hybrid_scoring(dense_score=0.8, sparse_score=0.4, dense_weight=0.0)
        self.assertEqual(score3, 0.4)


if __name__ == "__main__":
    unittest.main()
