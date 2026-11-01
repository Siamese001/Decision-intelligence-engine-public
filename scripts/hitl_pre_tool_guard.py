#!/usr/bin/env python3
"""scripts/hitl_pre_tool_guard.py

Executable PreToolUse lifecycle hook script for Antigravity.
Intercepts:
1. `ask_question` and `ask_user_question` tool calls, delegating validation to
   tools/check_ask_user_question.py, mechanically denying tool execution whenever
   options miss calibrated confidence percentages or tiers.
2. `write_to_file` and `replace_file_content` tool calls targeting files directly in
   the primary repository root on branch 'main' outside `.worktrees/<slug>/`, enforcing
   INV-WT-01 and Master Invariant 8.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

def resolve_primary_repo_root() -> Path:
    """Resolve the canonical primary repository root, even when executed from a worktree."""
    current = Path(__file__).resolve().parents[1]
    if current.parent.name == ".worktrees":
        return current.parents[1]
    return current


ROOT = resolve_primary_repo_root()
CURRENT_WORKTREE_DIR = Path(__file__).resolve().parents[1]
if str(CURRENT_WORKTREE_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_WORKTREE_DIR))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for venv_root in (ROOT / ".venv", ROOT.parent / "decision-intelligence-engine" / ".venv"):
    for sp in (venv_root / "lib").glob("python*/site-packages"):
        if sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))


def resolve_python() -> Path:
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        ROOT.parent / "decision-intelligence-engine" / ".venv" / "bin" / "python",
        ROOT.parent.parent / ".venv" / "bin" / "python",
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return c
    return Path(sys.executable)


def is_on_main_root(repo_root: Path) -> bool:
    """Check if primary repo root is currently checked out to main."""
    try:
        git_dir = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if ".git/worktrees/" in git_dir:
            return False

        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        return branch == "main"
    except Exception:
        return False


def evaluate_file_write_path(
    target_path_str: str, repo_root: Path
) -> Tuple[bool, Optional[str]]:
    """Determine if a file write targets the primary main working tree outside worktrees.

    Returns:
        (is_blocked, rejection_reason)
    """
    if not target_path_str:
        return False, None

    try:
        p = Path(target_path_str)
        if not p.is_absolute():
            resolved = (repo_root / p).resolve()
        else:
            resolved = p.resolve()
    except Exception:
        return False, None

    # Exclude ephemeral runtime state under .local/
    local_dir = (repo_root / ".local").resolve()
    try:
        if resolved == local_dir or local_dir in resolved.parents:
            return False, None
    except Exception:
        pass

    # Exclude files inside .worktrees/<slug>/
    worktrees_dir = (repo_root / ".worktrees").resolve()
    try:
        if resolved == worktrees_dir or worktrees_dir in resolved.parents:
            return False, None
    except Exception:
        pass

    # If file resolves inside primary repository root and primary root is on main
    try:
        if resolved == repo_root or repo_root in resolved.parents:
            if is_on_main_root(repo_root):
                return True, (
                    "MAIN_WORKTREE_WRITE_BLOCKED: Direct modification of tracked files on branch 'main' "
                    f"in the primary root workspace is prohibited ('{resolved}'). "
                    "All changes, hotfixes, and ingestion deliverables must execute within an "
                    "isolated feature worktree under '.worktrees/<slug>' per Master Invariant 8 "
                    "and .agents/rules/git-worktree-governance.md."
                )
    except Exception:
        pass

    return False, None


def _is_unfiltered_assertion_sql(sql_text: str) -> bool:
    """Return True if sql_text queries assertion or legacy claim tables without canonical filters."""
    import re
    if not sql_text or not re.search(r"\bSELECT\b", sql_text, re.IGNORECASE):
        return False
    if re.search(r"\bCOUNT\s*\(", sql_text, re.IGNORECASE) or re.search(r"\bPRAGMA\b", sql_text, re.IGNORECASE):
        return False
    if re.search(r"\b(?:FROM|JOIN)\s+claim\b", sql_text, re.IGNORECASE):
        return True
    if not re.search(r"\b(?:FROM|JOIN)\s+assertion\b", sql_text, re.IGNORECASE):
        return False
    has_status = bool(re.search(r"\bcanonical_status\b", sql_text, re.IGNORECASE))
    has_elig = bool(re.search(r"\bcanonical_eligible\b", sql_text, re.IGNORECASE))
    return not (has_status and has_elig)


def handle_tool_call_guard(raw_input: str) -> Optional[Dict[str, Any]]:
    """Inspect toolCall payload for either HITL question or direct-on-main file write."""
    if not raw_input or not raw_input.strip():
        return None

    try:
        data = json.loads(raw_input)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    tool_call = data.get("toolCall") if isinstance(data.get("toolCall"), dict) else data
    tool_name = tool_call.get("name") or tool_call.get("tool") or ""
    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else {}

    # 1. File write guard
    if tool_name in (
        "write_to_file",
        "replace_file_content",
        "edit_file",
        "write_file",
        "multi_replace_file_content",
    ):
        target_file = (
            args.get("Path")
            or args.get("path")
            or args.get("TargetFile")
            or args.get("target_file")
            or ""
        )
        blocked, reason = evaluate_file_write_path(str(target_file), ROOT)
        if blocked:
            return {"decision": "deny", "reason": reason}
        meta = args.get("ArtifactMetadata")
        if isinstance(meta, dict) and bool(meta.get("UserFacing")):
            from scripts.hitl_post_invocation_guard import detect_layman_jargon_violations
            content = str(args.get("Content") or args.get("Replacement") or "")
            summary = str(meta.get("Summary") or "")
            violations = detect_layman_jargon_violations(f"{summary}\n{content}")
            if violations:
                return {
                    "decision": "deny",
                    "reason": (
                        "PRE_OUTPUT_LAYMAN_GUARD (Rule 6): User-facing artifact contains forbidden technical "
                        f"jargon ({', '.join(violations[:4])}). Rewrite in 100% plain English before writing."
                    ),
                }
        return {"decision": "allow"}

    # 1a. Route 0b live transcript read guard
    if tool_name in ("view_file", "read_file"):
        target_file = str(
            args.get("Path")
            or args.get("path")
            or args.get("AbsolutePath")
            or args.get("TargetFile")
            or ""
        )
        if target_file.endswith("_live_transcript.md"):
            return {
                "decision": "deny",
                "reason": (
                    "LIVE_TRANSCRIPT_DIRECT_READ_PROHIBITED: Direct reading of '*_live_transcript.md' "
                    "is prohibited by Route 0b (Live Copilot). Execute '.venv/bin/python nee.py copilot' instead."
                ),
            }
        return {"decision": "allow"}

    # 1b. MCP graphdb_read_query canonical filter guard
    if tool_name in ("call_mcp_tool", "graphdb_read_query") or tool_name.endswith("graphdb_read_query"):
        mcp_tool = args.get("ToolName") or args.get("tool_name") or tool_name
        mcp_args = args.get("Arguments") if "Arguments" in args else args.get("arguments", args)
        if isinstance(mcp_args, str):
            try:
                mcp_args = json.loads(mcp_args)
            except Exception:
                mcp_args = {}
        if not isinstance(mcp_args, dict):
            mcp_args = {}
        if mcp_tool == "graphdb_read_query" or str(mcp_tool).endswith("graphdb_read_query"):
            sql_q = str(mcp_args.get("query") or "")
            inc_cand = bool(mcp_args.get("include_candidates", False))
            if not inc_cand and _is_unfiltered_assertion_sql(sql_q):
                return {
                    "decision": "deny",
                    "reason": (
                        "CANONICAL_QUERY_FILTER_REQUIRED: Raw SQL queries against 'assertion' (or legacy 'claim') must include "
                        "\"canonical_status = 'canonical' AND canonical_eligible = 1\" "
                        "(or set include_candidates=True for hygiene diagnostics) to prevent unpromoted or superseded candidate records from leaking."
                    ),
                }
        return {"decision": "allow"}

    # 2. Prohibited command / directory enumeration guard
    if tool_name == "run_command":
        cmd = (
            args.get("CommandLine")
            or args.get("command")
            or args.get("cmd")
            or ""
        )
        import re
        if (
            not re.search(r"\b(?:pytest|unittest|run_tests_with_progress|verify_main_readiness|run_ci_gates)\b", str(cmd))
            and _is_unfiltered_assertion_sql(str(cmd))
        ):
            return {
                "decision": "deny",
                "reason": (
                    "CANONICAL_QUERY_FILTER_REQUIRED: Ad-hoc SQL queries against 'assertion' or 'claim' must include "
                    "\"canonical_status = 'canonical' AND canonical_eligible = 1\" to prevent candidate leakage."
                ),
            }
        try:
            from dual_pipeline.query_core.firewall import ProhibitedCommandGuard
            violation = ProhibitedCommandGuard.evaluate_command(str(cmd))
            if violation.is_violation:
                if "WORKTREE_GOVERNANCE_BYPASS_BLOCKED" in violation.rationale or "MAIN_WORKTREE_WRITE_BLOCKED" in violation.rationale:
                    return {
                        "decision": "deny",
                        "reason": violation.rationale,
                    }
                return {
                    "decision": "deny",
                    "reason": (
                        f"GRAPHDB_TRAVERSAL_REQUIRED: Direct filesystem search or enumeration on '{violation.target_path}' "
                        f"is prohibited ('{violation.matched_pattern}'). "
                        "Route all queries through GraphDB using '.venv/bin/python nee.py ask --question ...' "
                        "or MCP 'nee_query'."
                    ),
                }
        except Exception as e:
            import traceback
            traceback.print_exc()
        return {"decision": "allow"}

    # 3. HITL question guard handled via external validator
    if tool_name in ("ask_question", "ask_user_question"):
        return None

    return None


def resolve_target_validator(repo_root: Path) -> Path:
    """Prefer the most recently modified worktree validator when developing HITL governance in an isolated worktree."""
    best_tool = repo_root / "tools" / "check_ask_user_question.py"
    best_mtime = (
        (repo_root / "dual_pipeline" / "hitl_governance_core" / "question_validator.py").stat().st_mtime
        if (repo_root / "dual_pipeline" / "hitl_governance_core" / "question_validator.py").exists()
        else 0.0
    )
    wt_root = repo_root / ".worktrees"
    if wt_root.is_dir():
        for wt in wt_root.iterdir():
            qv = wt / "dual_pipeline" / "hitl_governance_core" / "question_validator.py"
            tool = wt / "tools" / "check_ask_user_question.py"
            if qv.is_file() and tool.is_file():
                mtime = qv.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_tool = tool
    return best_tool


def main() -> int:
    raw_input = sys.stdin.read()

    # Pre-check for file writes or early intercept
    file_guard_res = handle_tool_call_guard(raw_input)
    if file_guard_res is not None:
        if file_guard_res.get("decision") == "deny":
            print(json.dumps(file_guard_res))
            sys.exit(2)
        print(json.dumps(file_guard_res))
        return 0

    py_bin = resolve_python()
    target_tool = resolve_target_validator(ROOT)

    try:
        proc = subprocess.run(
            [str(py_bin), str(target_tool), "--hook"],
            input=raw_input,
            text=True,
            capture_output=True,
            timeout=10,
        )
        output = proc.stdout.strip()
        if output:
            print(output)
            try:
                out_json = json.loads(output)
                if out_json.get("decision") == "deny":
                    sys.exit(2)
            except Exception:
                pass
            return 0
        else:
            err = proc.stderr.strip() or "Empty output from validator"
            print(
                json.dumps(
                    {
                        "decision": "deny",
                        "reason": f"[HITL Governance Error] Validator invocation failed: {err}",
                    }
                )
            )
            sys.exit(2)
    except subprocess.TimeoutExpired:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": "[HITL Governance Timeout] Validation of interactive question timed out.",
                }
            )
        )
        sys.exit(2)
    except Exception as e:
        print(
            json.dumps(
                {
                    "decision": "deny",
                    "reason": f"[HITL Governance Error] Exception executing hook guard: {e}",
                }
            )
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
