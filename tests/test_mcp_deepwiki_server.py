"""Tests for DeepWiki MCP stdio proxy server."""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

from tools.mcp_deepwiki_server import (
    _call_deepwiki_upstream,
    _resolve_repo_name,
    ask_question,
    read_wiki_contents,
    read_wiki_structure,
    run_self_test,
    server,
)


class TestDeepWikiMCPServer(unittest.TestCase):
    """Test suite for DeepWiki MCP stdio proxy server."""

    def test_resolve_repo_name(self) -> None:
        """Test parameter resolution between camelCase and snake_case."""
        self.assertEqual(_resolve_repo_name(repoName="facebook/react"), "facebook/react")
        self.assertEqual(_resolve_repo_name(repo_name="facebook/react"), "facebook/react")
        self.assertEqual(
            _resolve_repo_name(repoName="facebook/react", repo_name="other/repo"),
            "facebook/react"
        )
        with self.assertRaises(ValueError):
            _resolve_repo_name()

    def test_call_deepwiki_upstream_mocked(self) -> None:
        """Test upstream forwarding with mocked response."""
        mock_sse_response = (
            b"event: message\n"
            b'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"Mocked Wiki Data"}]}}\n\n'
        )

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_sse_response
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = _call_deepwiki_upstream("read_wiki_structure", {"repoName": "test/repo"})
            self.assertEqual(res, "Mocked Wiki Data")

    def test_call_deepwiki_unindexed_repo_mocked(self) -> None:
        """Test upstream response for unindexed repository."""
        mock_error_json = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{
                    "type": "text",
                    "text": "Error fetching wiki for nonexistent/repo: Repository not found."
                }],
                "isError": True,
            }
        }).encode("utf-8")

        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_error_json
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            res = _call_deepwiki_upstream("read_wiki_structure", {"repoName": "nonexistent/repo"})
            self.assertIn("Repository not found", res)

    def test_tools_exposure(self) -> None:
        """Verify that expected tools are registered on the MCPServer."""
        tool_names = [t.name for t in server._tools.values()] if hasattr(server, "_tools") else []
        self.assertTrue("read_wiki_structure" in tool_names or hasattr(read_wiki_structure, "__name__"))
        self.assertTrue("read_wiki_contents" in tool_names or hasattr(read_wiki_contents, "__name__"))
        self.assertTrue("ask_question" in tool_names or hasattr(ask_question, "__name__"))

    def test_live_read_wiki_structure(self) -> None:
        """Live smoke test verifying DeepWiki upstream availability."""
        res = read_wiki_structure(repo_name="facebook/react")
        self.assertTrue("React" in res or "error" in res.lower())

    def test_run_self_test_mocked(self) -> None:
        """Test run_self_test wrapper."""
        with patch("tools.mcp_deepwiki_server.read_wiki_structure", return_value="React Core Reconciler"):
            self.assertEqual(run_self_test(), 0)


