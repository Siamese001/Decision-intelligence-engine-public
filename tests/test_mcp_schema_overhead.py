"""Unit tests for MCP schema token overhead auditor and token budget enforcement."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from tools.audit_mcp_schema_overhead import (
    DEFAULT_MAX_TOKENS_PER_SERVER,
    REPO_MCP_SERVERS,
    ServerTokenAuditResult,
    audit_all_mcp_servers,
    audit_single_server,
    build_audit_receipt,
    count_schema_tokens,
    extract_server_tools_schema,
)


class MCPSchemaTokenOverheadTests(unittest.TestCase):
    """Test suite verifying MCP tool schema token budgets and auditing logic."""

    def test_count_schema_tokens_positive(self) -> None:
        """Token counter should return positive integer for nonempty schema strings."""
        sample_schema = '{"name": "test_tool", "description": "A test tool", "parameters": {}}'
        token_count = count_schema_tokens(sample_schema)
        self.assertGreater(token_count, 0)
        self.assertLess(token_count, 50)

    def test_count_schema_tokens_fallback(self) -> None:
        """Token counter should handle long text proportionally."""
        text = "word " * 100
        count = count_schema_tokens(text)
        self.assertGreaterEqual(count, 50)

    def test_extract_server_tools_schema_mock(self) -> None:
        """extract_server_tools_schema should serialize tools to compact JSON."""
        mock_server = MagicMock()
        mock_tool = MagicMock()
        mock_tool.model_dump.return_value = {
            "name": "sample_tool",
            "description": "Inspects graphs",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }

        async def _mock_list_tools():
            return [mock_tool]

        mock_server.list_tools.side_effect = _mock_list_tools

        tools_dict, compact_json = extract_server_tools_schema(mock_server)
        self.assertEqual(len(tools_dict), 1)
        self.assertEqual(tools_dict[0]["name"], "sample_tool")
        self.assertIn('"name":"sample_tool"', compact_json)
        self.assertNotIn(" ", compact_json.split(":")[0])  # verifies compact separators

    def test_audit_single_server_compliant(self) -> None:
        """audit_single_server should pass when token count is under max_tokens."""
        res = audit_single_server(
            server_name="test-server",
            module_name="tools.mcp_contract_server",
            max_tokens=2000,
        )
        self.assertEqual(res.server_name, "test-server")
        self.assertTrue(res.is_compliant)
        self.assertGreater(res.tool_count, 0)
        self.assertLess(res.token_count, 2000)

    def test_audit_single_server_exceeded(self) -> None:
        """audit_single_server should flag when token count exceeds budget."""
        res = audit_single_server(
            server_name="test-server",
            module_name="tools.mcp_contract_server",
            max_tokens=10,  # artificially low limit
        )
        self.assertFalse(res.is_compliant)
        self.assertGreater(res.token_count, 10)

    def test_audit_single_server_missing_module(self) -> None:
        """audit_single_server should handle nonexistent modules gracefully."""
        res = audit_single_server(
            server_name="nonexistent",
            module_name="tools.nonexistent_module_xyz",
            max_tokens=1000,
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.tool_count, 0)
        self.assertIn("ERROR:", res.tool_names[0])

    def test_build_audit_receipt(self) -> None:
        """build_audit_receipt should produce an immutable receipt with valid digest."""
        results = [
            ServerTokenAuditResult(
                server_name="server-a",
                module_name="mod.a",
                tool_count=3,
                raw_char_length=1200,
                token_count=300,
                max_tokens_limit=1000,
                is_compliant=True,
                tool_names=["tool1", "tool2", "tool3"],
            )
        ]
        receipt = build_audit_receipt(results, max_tokens=1000)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["wave_id"], "Wave-5")
        self.assertIn("canonical_digest", receipt)
        self.assertEqual(len(receipt["canonical_digest"]), 64)

    def test_repo_mcp_servers_comply_with_ceiling(self) -> None:
        """All repository MCP servers must strictly conform to the 1,000-token ceiling."""
        results = audit_all_mcp_servers(REPO_MCP_SERVERS, max_tokens=DEFAULT_MAX_TOKENS_PER_SERVER)
        self.assertEqual(len(results), 6)
        for r in results:
            self.assertTrue(
                r.is_compliant,
                f"MCP server '{r.server_name}' exceeds token ceiling: {r.token_count} > 1000 tokens",
            )
            self.assertLessEqual(r.token_count, DEFAULT_MAX_TOKENS_PER_SERVER)


if __name__ == "__main__":
    unittest.main()
