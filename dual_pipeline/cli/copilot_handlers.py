"""
dual_pipeline/cli/copilot_handlers.py

CLI handlers for `nee copilot` and `nee session-inspect`.
Provides on-demand, GraphDB-grounded live meeting advisory across canonical intents.
"""
from __future__ import annotations

import argparse
import re
from typing import Any, Dict, List

from dual_pipeline.core import ROOT
from dual_pipeline.live_meeting_recorder_core.copilot_engine import (
    _record_and_render_copilot_output,
    execute_copilot_request,
)
from dual_pipeline.live_meeting_recorder_core.copilot_retriever import (
    load_copilot_graph,
)
from dual_pipeline.live_meeting_recorder_core.live_buffer_reader import LiveBufferReader

_GLOBAL_VALUE_FLAGS = frozenset({
    "--log-format", "--log-level", "--traceparent", "--profile-output", "--ingestion-reasoning", "--ingestion-reasoning-policy",
})
_GLOBAL_BOOL_FLAGS = frozenset({"--profile", "-h", "--help"})
_COPILOT_VALUE_FLAGS = _GLOBAL_VALUE_FLAGS | frozenset({
    "--intent", "--request", "--query", "--prompt", "--topic", "--target",
    "--words", "--meeting-dir", "--project", "--engagement", "--session", "--runtime-root", "--graph",
})
_COPILOT_TEXT_FLAGS = frozenset({"--request", "--query", "--prompt", "--topic", "--target"})
_COPILOT_ALL_FLAGS = _COPILOT_VALUE_FLAGS | _GLOBAL_BOOL_FLAGS | frozenset({"--json", "--bypass-graph-cache", "--active"})
_LEADING_COPILOT_CMD_RE = re.compile(r"^(?:[-/]{1,2}\s*)copilot(?:\b|[:\s-])(.*)$", re.IGNORECASE)
_LEADING_PROMPT_TOKEN_RE = re.compile(r"^(?:[-/]{1,2}\s*copilot\b|-\s+\S|[-/]{1,2}[^=\s]+\s+\S)", re.IGNORECASE)


def _find_subcommand_index(tokens: List[str]) -> int:
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        key = tok.split("=", 1)[0]
        if tok in _GLOBAL_VALUE_FLAGS and idx + 1 < len(tokens):
            idx += 2
        elif (key in _GLOBAL_VALUE_FLAGS and "=" in tok) or tok in _GLOBAL_BOOL_FLAGS:
            idx += 1
        else:
            return idx
    return len(tokens)


def _consume_copilot_token(rest: List[str], j: int) -> tuple[List[str], int]:
    tok = rest[j]
    has_next = j + 1 < len(rest)
    if tok in _COPILOT_TEXT_FLAGS and has_next:
        nxt = rest[j + 1]
        if nxt.startswith("-") and nxt.split("=", 1)[0] not in _COPILOT_ALL_FLAGS:
            return [f"{tok}={nxt}"], j + 2
    if tok in _COPILOT_VALUE_FLAGS and has_next:
        return [tok, rest[j + 1]], j + 2
    if tok.startswith("-") and tok.split("=", 1)[0] not in _COPILOT_ALL_FLAGS and _LEADING_PROMPT_TOKEN_RE.match(tok):
        return [f"--request={tok}"], j + 1
    return [tok], j + 1


def _normalize_copilot_tail(rest: List[str]) -> List[str]:
    out: List[str] = []
    j = 0
    while j < len(rest):
        if rest[j] == "--":
            rem = " ".join(rest[j + 1:]).strip()
            if rem:
                out.append(f"--request={rem}")
            break
        chunk, j = _consume_copilot_token(rest, j)
        out.extend(chunk)
    return out


def normalize_copilot_argv(argv: List[str]) -> List[str]:
    """Normalizes `-copilot` / leading-hyphen prompt tokens so argparse never rejects live operator prompts."""
    tokens = list(argv)
    cmd_idx = _find_subcommand_index(tokens)
    if cmd_idx >= len(tokens):
        return tokens
    head = tokens[cmd_idx]
    prefix = tokens[:cmd_idx]
    tail = tokens[cmd_idx + 1:]
    if head == "copilot":
        return [*prefix, "copilot", *_normalize_copilot_tail(tail)]
    m = _LEADING_COPILOT_CMD_RE.match(head.strip())
    if not m:
        return tokens
    inline_prompt = m.group(1).strip(" :-")
    extra = [f"--request={head}"] if inline_prompt else []
    return [*prefix, "copilot", *extra, *_normalize_copilot_tail(tail)]


def handle_session_inspect(args: argparse.Namespace) -> Dict[str, Any]:
    """Handles `nee session-inspect`."""
    repo_root = getattr(args, "runtime_root", None) or ROOT
    reader = LiveBufferReader(repo_root)
    manifest = None
    if getattr(args, "session", None):
        manifest = reader.get_session(args.session)
        if not manifest:
            raise ValueError(f"Session {args.session} not found.")
    elif getattr(args, "active", False):
        manifest = reader.get_active_session()
        if not manifest:
            raise ValueError("No active session found.")
    else:
        raise ValueError("Must specify --session or --active")

    return {"status": "PASS", "command": "session-inspect", "session": manifest.to_dict()}


def handle_copilot(args: argparse.Namespace) -> Dict[str, Any]:
    """Handles `nee copilot`."""
    return execute_copilot_request(args)
