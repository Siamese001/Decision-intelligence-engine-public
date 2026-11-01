"""DeepWiki MCP Stdio Proxy Server.

Exposes DeepWiki repository documentation and query capabilities (read_wiki_structure,
read_wiki_contents, ask_question) over standard I/O, proxying calls via robust HTTP POST
to https://mcp.deepwiki.com/mcp.

Eliminates remote SSE transport context cancellations and incorporates corporate SSL
certificate support for transparent developer experience across all branches and worktrees.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from mcp.server.mcpserver import MCPServer

DEEPWIKI_ENDPOINT = "https://mcp.deepwiki.com/mcp"

server = MCPServer(
    name="deepwiki",
    instructions=(
        "DeepWiki MCP Server — provides AI-powered documentation and question answering "
        "for public GitHub repositories."
    ),
)


def _call_deepwiki_upstream(tool_name: str, arguments: Dict[str, Any], timeout_sec: int = 30) -> str:
    """Forward a tool call to the upstream DeepWiki HTTP service."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }
    req_body = json.dumps(payload).encode("utf-8")

    cafile = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE")
    if cafile and os.path.exists(cafile):
        ctx = ssl.create_default_context(cafile=cafile)
    else:
        ctx = ssl.create_default_context()

    req = urllib.request.Request(
        DEEPWIKI_ENDPOINT,
        data=req_body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": "Antigravity-DeepWiki-Proxy/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout_sec) as resp:
            content = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        content = e.read().decode("utf-8")
    except Exception as e:
        return f"[DeepWiki Error] Connection to upstream failed: {e}"

    # Parse response: upstream returns SSE-delimited messages or raw JSON
    parsed: Optional[Dict[str, Any]] = None
    for line in content.splitlines():
        if line.startswith("data: "):
            with contextlib.suppress(Exception):
                parsed = json.loads(line[6:].strip())
                break

    if parsed is None and content.strip().startswith("{"):
        with contextlib.suppress(Exception):
            parsed = json.loads(content.strip())

    if not parsed:
        return f"[DeepWiki Error] Unparseable upstream response: {content[:200]}"

    if "error" in parsed:
        err_msg = parsed["error"].get("message", str(parsed["error"]))
        return f"[DeepWiki Error] {err_msg}"

    res = parsed.get("result", {})
    if isinstance(res, dict):
        content_items = res.get("content", [])
        if content_items and isinstance(content_items, list):
            texts = [item.get("text", "") for item in content_items if isinstance(item, dict) and "text" in item]
            if texts:
                return "\n\n".join(texts)
        if "text" in res:
            return str(res["text"])

    return json.dumps(res, indent=2)


def _resolve_repo_name(repoName: str = "", repo_name: str = "") -> str:
    """Resolve parameter aliasing between camelCase and snake_case."""
    repo = (repoName or repo_name).strip()
    if not repo:
        raise ValueError("Parameter 'repoName' or 'repo_name' is required (e.g. 'owner/repo').")
    return repo


@server.tool(name="read_wiki_structure")
def read_wiki_structure(repoName: str = "", repo_name: str = "") -> str:
    """Get a list of documentation topics for a GitHub repository.

    Args:
        repoName: GitHub repository in owner/repo format (e.g. 'facebook/react').
        repo_name: Alias for repoName.
    """
    repo = _resolve_repo_name(repoName=repoName, repo_name=repo_name)
    return _call_deepwiki_upstream("read_wiki_structure", {"repoName": repo})


@server.tool(name="read_wiki_contents")
def read_wiki_contents(repoName: str = "", repo_name: str = "") -> str:
    """View documentation about a GitHub repository.

    Args:
        repoName: GitHub repository in owner/repo format (e.g. 'facebook/react').
        repo_name: Alias for repoName.
    """
    repo = _resolve_repo_name(repoName=repoName, repo_name=repo_name)
    return _call_deepwiki_upstream("read_wiki_contents", {"repoName": repo})


@server.tool(name="ask_question")
def ask_question(question: str, repoName: str = "", repo_name: str = "") -> str:
    """Ask any question about a GitHub repository and get an AI-powered, context-grounded response.

    Args:
        question: The question to ask about the repository.
        repoName: GitHub repository in owner/repo format (e.g. 'facebook/react').
        repo_name: Alias for repoName.
    """
    repo = _resolve_repo_name(repoName=repoName, repo_name=repo_name)
    return _call_deepwiki_upstream("ask_question", {"repoName": repo, "question": question})


def run_self_test() -> int:
    """Verify upstream connectivity and tool execution."""
    print("Executing DeepWiki MCP self-test against upstream...")
    res = read_wiki_structure(repoName="facebook/react")
    if "React" in res and "Core Reconciler" in res:
        print("✅ DeepWiki MCP self-test passed: read_wiki_structure returned valid contents.")
        print(f"Sample response snippet:\n{res[:200]}...")
        return 0
    print(f"❌ DeepWiki MCP self-test failed with response:\n{res}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepWiki MCP Stdio Proxy Server")
    parser.add_argument("--test", action="store_true", help="Run self-test and exit")
    args = parser.parse_args()

    if args.test:
        sys.exit(run_self_test())

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
