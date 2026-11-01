#!/usr/bin/env python3
"""MCP Tool Schema Token Overhead Auditor.

Inspects all Model Context Protocol (MCP) servers in the repository, tokenizing
their tool definitions and schemas against a strict 1,000-token ceiling per server.
Prevents context window bloat and saturation in agentic reasoning loops.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_MAX_TOKENS_PER_SERVER: int = 1000

REPO_MCP_SERVERS: List[Tuple[str, str]] = [
    ("contract-governance", "tools.mcp_contract_server"),
    ("graphdb-inspector", "tools.mcp_graphdb_server"),
    ("judge-retrieval-eval", "tools.mcp_judge_eval_server"),
    ("speech-transcript-synthesizer", "tools.mcp_speech_server"),
    ("test-orchestrator", "tools.mcp_test_orchestrator_server"),
    ("nee-knowledge-graph", "tools.nee_mcp_server"),
]


@dataclass
class ServerTokenAuditResult:
    server_name: str
    module_name: str
    tool_count: int
    raw_char_length: int
    token_count: int
    max_tokens_limit: int
    is_compliant: bool
    tool_names: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def count_schema_tokens(schema_text: str) -> int:
    """Calculate token count using cl100k_base or approximate 4-char fallback."""
    if tiktoken is not None:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(schema_text))
        except Exception:
            pass
    return max(1, len(schema_text) // 4)


def extract_server_tools_schema(server_obj: Any) -> Tuple[List[Dict[str, Any]], str]:
    """Retrieve tool definitions from an MCPServer instance and serialize to compact JSON."""
    tools = asyncio.run(server_obj.list_tools())
    tools_dict: List[Dict[str, Any]] = []
    for t in tools:
        if hasattr(t, "model_dump"):
            tools_dict.append(t.model_dump())
        elif isinstance(t, dict):
            tools_dict.append(t)
        else:
            tools_dict.append({
                "name": getattr(t, "name", ""),
                "description": getattr(t, "description", ""),
                "inputSchema": getattr(t, "inputSchema", {}),
            })
    compact_json = json.dumps(tools_dict, separators=(",", ":"))
    return tools_dict, compact_json


def audit_single_server(
    server_name: str,
    module_name: str,
    max_tokens: int = DEFAULT_MAX_TOKENS_PER_SERVER,
) -> ServerTokenAuditResult:
    """Audit token count of tool schemas for a single MCP server module."""
    try:
        mod = __import__(module_name, fromlist=["server"])
        server_obj = mod.server
        tools_dict, compact_json = extract_server_tools_schema(server_obj)
        token_count = count_schema_tokens(compact_json)
        tool_names = [t.get("name", "") for t in tools_dict]

        return ServerTokenAuditResult(
            server_name=server_name,
            module_name=module_name,
            tool_count=len(tools_dict),
            raw_char_length=len(compact_json),
            token_count=token_count,
            max_tokens_limit=max_tokens,
            is_compliant=token_count <= max_tokens,
            tool_names=tool_names,
        )
    except Exception as err:
        return ServerTokenAuditResult(
            server_name=server_name,
            module_name=module_name,
            tool_count=0,
            raw_char_length=0,
            token_count=9999,
            max_tokens_limit=max_tokens,
            is_compliant=False,
            tool_names=[f"ERROR: {err}"],
        )


def audit_all_mcp_servers(
    servers: Optional[Sequence[Tuple[str, str]]] = None,
    max_tokens: int = DEFAULT_MAX_TOKENS_PER_SERVER,
) -> List[ServerTokenAuditResult]:
    """Audit all registered MCP servers against the token ceiling."""
    target_servers = servers or REPO_MCP_SERVERS
    return [audit_single_server(name, mod, max_tokens) for name, mod in target_servers]


def build_audit_receipt(
    results: List[ServerTokenAuditResult],
    max_tokens: int,
) -> Dict[str, Any]:
    """Generate a deterministic runtime receipt for the MCP audit."""
    all_passed = all(r.is_compliant for r in results)
    server_metrics = {
        r.server_name: {
            "tool_count": r.tool_count,
            "token_count": r.token_count,
            "is_compliant": r.is_compliant,
        }
        for r in results
    }

    digest = hashlib.sha256(json.dumps(server_metrics, sort_keys=True).encode("utf-8")).hexdigest()

    return {
        "receipt_contract": "urn:nee:dual-pipeline:mcp-token-overhead-receipt:1.0.0",
        "wave_id": "Wave-5",
        "wave_title": "MCP Tool Schema Token Optimization & Static Type Gate",
        "status": "PASS" if all_passed else "FAIL",
        "max_tokens_ceiling": max_tokens,
        "servers_audited": len(results),
        "server_metrics": server_metrics,
        "canonical_digest": digest,
    }


def print_audit_report(results: List[ServerTokenAuditResult], max_tokens: int) -> int:
    """Print human-readable audit diagnostics and return process exit code."""
    print("=" * 80)
    print(f"  MCP TOOL SCHEMA TOKEN OVERHEAD AUDIT (Ceiling: {max_tokens} Tokens)")
    print("=" * 80)
    print(f"{'Server Name':<32} {'Tools':<8} {'Chars':<8} {'Tokens':<8} {'Status'}")
    print("-" * 80)

    violations = []
    for r in results:
        status_str = "✅ PASS" if r.is_compliant else "❌ EXCEEDED"
        print(f"{r.server_name:<32} {r.tool_count:<8} {r.raw_char_length:<8} {r.token_count:<8} {status_str}")
        if not r.is_compliant:
            violations.append(r)

    print("=" * 80)
    if violations:
        print(f"\n❌ FAILED: {len(violations)} MCP server(s) exceed {max_tokens}-token budget:")
        for v in violations:
            print(f"  - {v.server_name}: {v.token_count} tokens (+{v.token_count - max_tokens})")
        print("\nRemediation: Concisely synthesize tool docstrings and parameter descriptions.")
        print("=" * 80)
        return 1

    print("✅ PASSED: All MCP servers comply with context window token limits.")
    print("=" * 80)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit MCP server tool schema token overhead.")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS_PER_SERVER, help="Token limit per server")
    parser.add_argument("--check", action="store_true", help="Exit with code 1 if any server exceeds limit")
    parser.add_argument("--json-report", type=Path, default=None, help="Path to write JSON report")
    parser.add_argument("--receipt", type=Path, default=None, help="Path to write runtime receipt JSON")

    args = parser.parse_args(argv)

    results = audit_all_mcp_servers(max_tokens=args.max_tokens)

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        report_data = {
            "max_tokens_ceiling": args.max_tokens,
            "servers": [r.to_dict() for r in results],
        }
        with open(args.json_report, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)

    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt = build_audit_receipt(results, args.max_tokens)
        with open(args.receipt, "w", encoding="utf-8") as f:
            json.dump(receipt, f, indent=2)

    exit_code = print_audit_report(results, args.max_tokens)
    return exit_code if args.check else 0


if __name__ == "__main__":
    sys.exit(main())
