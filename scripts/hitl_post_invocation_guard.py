#!/usr/bin/env python3
"""scripts/hitl_post_invocation_guard.py

Post-invocation lifecycle hook enforcing INV-DECISIVE-RECOMMENDATION.
Inspects the turn transcript at post_invocation time:
1. Emits prompt-caching telemetry when cached_tokens > 0.
2. Checks whether the current turn already invoked ask_question / ask_user_question.
3. If no interactive question tool was called, scans the terminal assistant text
   for plain-text disjunctive option prose (e.g. "Ready for next direction: we can X, or Y").
4. When detected, returns a non-empty injectSteps directive forcing an immediate
   calibrated ask_question tool call before yielding to the operator.
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

DISJUNCTIVE_PROSE_PATTERNS = (
    re.compile(r"\bready\s+for\s+next\s+direction\b", re.IGNORECASE),
    re.compile(
        r"\bwe\s+can\s+(?:either\s+)?[^.?!;\n]{3,140},?\s+or\s+(?:we\s+can\s+|proceed\b|address\b|tune\b|run\b|archive\b|close\b|open\b|create\b|continue\b|defer\b|skip\b)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:would\s+you\s+like\s+(?:me\s+)?to|should\s+we\s+(?:proceed\s+with|address|execute|run))\s+[^?\n]{3,140}\s+or\s+",
        re.IGNORECASE,
    ),
)

INTERCEPT_PROMPT = (
    "[HITL GOVERNANCE INTERCEPT — INV-DECISIVE-RECOMMENDATION] "
    "You concluded your turn with a plain-text disjunctive choice without invoking ask_question. "
    "Immediately invoke ask_question with one atomic question and mutually exclusive, "
    "confidence-scored options (Confidence: TIER (N%))."
)

POST_EDIT_PROMOTION_PROMPT = (
    "[HITL GOVERNANCE INTERCEPT — POST-EDIT PROMOTION GATE] "
    "You modified repository files or applied a live remediation/ingestion promotion during this turn "
    "without completing a governed PR merge to local main or asking the operator to authorize "
    "branch commit, rebase, and promotion to local main. "
    "Under Master Invariant 8 and hitl-decisions.md Rule 12, immediately commit, rebase, and merge "
    "under operator authorization or invoke ask_question with one atomic question presenting the "
    "recommended branch commit, rebase, and promotion option with calibrated confidence (Confidence: TIER (N%))."
)

QUERY_TRAVERSAL_FORCE_PROMPT = (
    "[GRAPHDB TRAVERSAL INTERCEPT — MANDATORY QUERY GROUNDING] "
    "This turn requires knowledge graph traversal (due to '-query' / 'query-' trigger or NEE domain entity query), "
    "but was completed with zero graph query tool calls. "
    "You are strictly prohibited from generating answers from parametric memory or ungrounded drafts. "
    "Immediately execute 'nee_query' (or '.venv/bin/python nee.py ask --question ...') against CANONICAL_GRAPH.db "
    "to retrieve verified facts before delivering your grounded response."
)

LAYMAN_INTERCEPT_MARKER = "[LAYMAN ZERO-JARGON GOVERNANCE INTERCEPT"

PRE_INVOCATION_LAYMAN_CHECKLIST = (
    "[PRE-OUTPUT LAYMAN CLARITY GATE ACTIVE (Rule 6 — Enforce BEFORE Emitting Output)]:\n"
    "Before generating any user-facing text, self-audit every sentence so ZERO technical tokens appear in prose:\n"
    "1. BANNED OVERLAP WORDS: Never write 'import', 'class', 'def', 'lambda', 'async', 'await', '__init__', "
    "'kwargs', or 'isinstance' in prose (even as English words like 'import files' or 'class A' — use "
    "'reference files', 'bring in', 'category', or 'group' instead).\n"
    "2. BANNED IDENTIFIERS & FILENAMES: Zero snake_case tokens (words with underscores), zero raw filenames "
    "(.py, .sqlite, .jsonl, .yaml, .toml, .sh, .sql) in prose or link labels (use plain-English link labels "
    "like '[Plain Language Guard Script](file:///...)'), zero class.method names, and zero '*Error'/'*Exception' names.\n"
    "3. BANNED PLUMBING & SQL: Zero inline SQL ('SELECT ... FROM') and zero acronyms ('AST', 'WAL', 'ACID', "
    "'JSONL', 'DDL', 'DML', 'PRAGMA'). Explain cause and effect in clear layman English with exact numbers."
)

LAYMAN_JARGON_INTERCEPT_PROMPT = (
    "[LAYMAN ZERO-JARGON GOVERNANCE INTERCEPT — STAKEHOLDER PLAIN-LANGUAGE INVARIANT] "
    "Your response contained raw technical jargon, code identifiers, database column/table names, "
    "or internal plumbing terms ({violations}). "
    "Under engineering-output.md Section 1.10 and AGENTS.md Principle 3, rewrite your response "
    "immediately in 100% clear layman terms with zero technical jargon, while preserving full "
    "technical and operational rigor (exact numbers, counts, verified outcomes, and plain-English "
    "cause-and-effect explanations). Use plain-English labels for any clickable file links."
)

MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((?:file:///|https?://|conversation://)[^)]+\)")
FENCED_BLOCK_RE = re.compile(r"```[\s\S]*?```")
PROSE_JARGON_PATTERNS = (
    (re.compile(r"\bSELECT\s+.+?\s+FROM\s+[a-zA-Z0-9_]+", re.IGNORECASE | re.DOTALL), "inline SQL query"),
    (re.compile(r"\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b"), "snake_case identifier"),
    (re.compile(r"\b(?:def|class|import|lambda|async|await|__init__|kwargs|isinstance)\b"), "code keyword"),
    (re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\._?[a-zA-Z0-9_]+"), "class/method reference"),
    (re.compile(r"\b[A-Za-z0-9]+(?:Error|Exception)\b"), "exception class name"),
    (
        re.compile(
            r"(?:^|[\s`\"'(\[{])(?:[a-zA-Z0-9_\-./]+/)?[a-zA-Z0-9_\-]+\.(?:py|sqlite|jsonl|yaml|yml|toml|sh|sql)\b"
        ),
        "raw code or database filename",
    ),
    (re.compile(r"\b(?:WAL|ACID|JSONL|AST|DDL|DML|PRAGMA)\b"), "plumbing acronym"),
)

QUERY_TRIGGER_PATTERNS = (
    re.compile(r"(?:^|\s)-query\b", re.IGNORECASE),
    re.compile(r"(?:^|\s)query-", re.IGNORECASE),
    re.compile(r"^query\s*:", re.IGNORECASE),
    re.compile(r"^/query\b", re.IGNORECASE),
)

DOMAIN_ENTITY_PATTERNS = re.compile(
    r"\b(NEE|Apex Global|Dan\s+Okray|Stephan|Amit\s+Ayer|Rewire|Project\s+Lens|Project\s+Nexus|Project\s+Jarvis|APEX_TRANS|NEA|Digital\s+Sherpa|DocIQ|HRIT|Conduit)\b",
    re.IGNORECASE,
)

COMMAND_ONLY_PATTERNS = re.compile(
    r"^(?:continue\b|status\b|git\b|test\b|run\b|pr\b|commit\b|rebase\b|wave\b|w\d+\b|rca\b|did\s+this\b|explain\b|does\b|show\b)",
    re.IGNORECASE,
)

GRAPH_QUERY_TOOLS = {
    "nee_query",
    "graphdb_read_query",
    "graphdb_snapshot_state",
    "graphdb_diff_breakdown",
    "nee_runtime_evidence_inventory",
    "nee_active_pair",
}



def _load_transcript_steps(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    inline_steps = payload.get("steps")
    if isinstance(inline_steps, list):
        return [s for s in inline_steps if isinstance(s, dict)]

    transcript_path = payload.get("transcriptPath")
    if not transcript_path or not isinstance(transcript_path, str) or not os.path.exists(transcript_path):
        return []

    steps: List[Dict[str, Any]] = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if line:
                    steps.append(json.loads(line))
    except Exception:
        return []
    return steps


def _emit_prompt_cache_telemetry(steps: List[Dict[str, Any]]) -> None:
    if not steps:
        return
    last_step = steps[-1]
    usage = last_step.get("usage")
    if not isinstance(usage, dict):
        return
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
    if isinstance(cached, int) and cached > 0:
        sys.stderr.write(f"[TELEMETRY] Prompt caching active: {cached} tokens cached.\n")


def _current_turn_steps(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    last_user_idx = -1
    for idx, step in enumerate(steps):
        step_type = str(step.get("type", "")).upper()
        source = str(step.get("source", "")).upper()
        content = str(step.get("content") or "")
        if (step_type == "USER_INPUT" or source == "USER_EXPLICIT") and LAYMAN_INTERCEPT_MARKER not in content:
            last_user_idx = idx
    return steps[last_user_idx + 1 :] if last_user_idx >= 0 else steps


def detect_layman_jargon_violations(text: str) -> List[str]:
    if not text or not isinstance(text, str):
        return []
    scannable = MARKDOWN_LINK_RE.sub(r"\1", text)
    scannable_no_fences = FENCED_BLOCK_RE.sub(" ", scannable)
    violations: List[str] = []
    for pattern, label in PROSE_JARGON_PATTERNS:
        matches = pattern.findall(scannable_no_fences)
        if matches:
            sample = str(matches[0]).strip(" `\"'()[]{}")
            violations.append(f"{label} ('{sample}')")
    return violations


def _step_invoked_question_tool(step: Dict[str, Any]) -> bool:
    for tc in _iter_step_tool_calls(step):
        name = str(tc.get("name") or tc.get("tool_name") or tc.get("recipient_name") or "")
        if name.endswith("ask_question") or name.endswith("ask_user_question"):
            return True
    return False


def _step_invoked_graph_query(step: Dict[str, Any]) -> bool:
    for tc in _iter_step_tool_calls(step):
        name = str(tc.get("name") or tc.get("tool_name") or "")
        base_name = name.split(":")[-1]
        if base_name in GRAPH_QUERY_TOOLS:
            return True
        if base_name == "run_command":
            args = tc.get("args") or {}
            cmd = str(args.get("CommandLine") or args.get("command") or "")
            if any(q in cmd for q in ("nee.py ask", "nee ask", "nee.py query", "nee query", "decision-query", "graphdb_read_query")):
                return True
    return False


def _requires_graph_traversal(steps: List[Dict[str, Any]], turn_steps: List[Dict[str, Any]]) -> bool:
    user_steps = [
        s for s in steps
        if str(s.get("type", "")).upper() == "USER_INPUT" or str(s.get("source", "")).upper() == "USER_EXPLICIT"
    ]
    if not user_steps:
        return False

    current_user_content = str(user_steps[-1].get("content") or "")

    # Skip pure workflow / administrative / git commands
    if COMMAND_ONLY_PATTERNS.search(current_user_content.strip()):
        return False

    # 1. Explicit query trigger in current turn
    if any(p.search(current_user_content) for p in QUERY_TRIGGER_PATTERNS):
        return True

    # 2. Sticky thread memory: if an earlier user step in recent turns had explicit query trigger
    recent_user_steps = user_steps[-5:]
    if any(any(p.search(str(s.get("content") or "")) for p in QUERY_TRIGGER_PATTERNS) for s in recent_user_steps):
        return True

    # 3. Domain entity mentions
    if DOMAIN_ENTITY_PATTERNS.search(current_user_content):
        return True

    return False


FILE_MUTATING_TOOLS = {
    "write_file",
    "edit_file",
    "write_to_file",
    "replace_file_content",
    "multi_replace_file_content",
}


def _extract_mutated_path(tool_call: Dict[str, Any]) -> Optional[str]:
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        return None
    raw_path = (
        args.get("Path")
        or args.get("path")
        or args.get("TargetFile")
        or args.get("target_file")
        or ""
    )
    return str(raw_path).strip() if raw_path else None


def _is_exempt_planning_write(path_str: str) -> bool:
    if not path_str:
        return False
    norm = path_str.replace("\\", "/")
    # Exempt ephemeral conversation brain artifacts
    if "/brain/" in norm or norm.startswith(".gemini/antigravity/brain/"):
        return True
    # Exempt implementation plan markdown files during planning mode
    if norm.endswith("implementation_plan.md") or "/docs/plans/" in norm:
        return True
    return False


def _iter_step_tool_calls(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []
    tool_calls = step.get("tool_calls") or step.get("toolCalls") or []
    if isinstance(tool_calls, list):
        calls.extend(tc for tc in tool_calls if isinstance(tc, dict))
    single_call = step.get("toolCall")
    if isinstance(single_call, dict):
        calls.append(single_call)
    return calls


def _is_cli_mutation_command(cmd: str) -> bool:
    if not cmd or "--dry-run" in cmd or "--help" in cmd:
        return False
    if "apply_graph_hygiene_remediation.py" in cmd or "storage_hygiene_core/apply.py" in cmd:
        return True
    if ("ingest-resolve" in cmd and "--decision approve" in cmd) or "release-activate" in cmd:
        return True
    return False


def _is_governed_pr_merge_command(cmd: str) -> bool:
    if not cmd or "pr_governance.py" not in cmd:
        return False
    if any(tok in cmd for tok in ("pr_governance.py merge", "pr_governance.py submit")):
        return True
    return "pr_governance.py finish" in cmd and "--ingest" in cmd


def _step_mutated_repository_code(step: Dict[str, Any]) -> bool:
    for tc in _iter_step_tool_calls(step):
        name = str(tc.get("name") or tc.get("tool_name") or "")
        base_name = name.split(":")[-1]
        if base_name in FILE_MUTATING_TOOLS:
            path_str = _extract_mutated_path(tc)
            if not path_str or not _is_exempt_planning_write(path_str):
                return True
        elif base_name == "run_command":
            args = tc.get("args") or {}
            cmd = str(args.get("CommandLine") or args.get("command") or "")
            if _is_cli_mutation_command(cmd):
                return True
    return False


def _step_completed_governed_pr_merge(step: Dict[str, Any]) -> bool:
    for tc in _iter_step_tool_calls(step):
        name = str(tc.get("name") or tc.get("tool_name") or "")
        if name.split(":")[-1] == "run_command":
            args = tc.get("args") or {}
            cmd = str(args.get("CommandLine") or args.get("command") or "")
            if _is_governed_pr_merge_command(cmd):
                return True
    return False


def has_disjunctive_fence_sitting(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    return any(pattern.search(text) for pattern in DISJUNCTIVE_PROSE_PATTERNS)


def _needs_post_edit_promotion(turn_steps: List[Dict[str, Any]]) -> bool:
    last_mutation_idx = -1
    last_question_idx = -1
    last_merge_idx = -1
    for idx, s in enumerate(turn_steps):
        if _step_mutated_repository_code(s):
            last_mutation_idx = idx
        if _step_invoked_question_tool(s):
            last_question_idx = idx
        if _step_completed_governed_pr_merge(s):
            last_merge_idx = idx
    return last_mutation_idx >= 0 and max(last_question_idx, last_merge_idx) < last_mutation_idx


def _force_continue(message: str, ephemeral: bool = False) -> Dict[str, Any]:
    key = "ephemeralMessage" if ephemeral else "userMessage"
    return {"injectSteps": [{key: message}], "terminationBehavior": "force_continue"}


def evaluate_post_invocation_payload(raw_stdin: str) -> Dict[str, Any]:
    if not raw_stdin or not raw_stdin.strip():
        return {"injectSteps": []}
    try:
        payload = json.loads(raw_stdin)
    except Exception:
        return {"injectSteps": []}
    if not isinstance(payload, dict):
        return {"injectSteps": []}

    steps = _load_transcript_steps(payload)
    _emit_prompt_cache_telemetry(steps)
    turn_steps = _current_turn_steps(steps)
    if not turn_steps:
        if "invocationNum" in payload or "initialNumSteps" in payload:
            return {"injectSteps": [{"ephemeralMessage": PRE_INVOCATION_LAYMAN_CHECKLIST}]}
        return {"injectSteps": []}

    # PreInvocation stage (before model generates its response): inject pre-output layman checklist
    if ("invocationNum" in payload or "initialNumSteps" in payload) and not (
        str(turn_steps[-1].get("type", "")).upper() in ("PLANNER_RESPONSE", "ASSISTANT", "MODEL_RESPONSE", "")
        and not _iter_step_tool_calls(turn_steps[-1])
    ):
        return {"injectSteps": [{"ephemeralMessage": PRE_INVOCATION_LAYMAN_CHECKLIST}]}

    if _needs_post_edit_promotion(turn_steps):
        return _force_continue(POST_EDIT_PROMOTION_PROMPT)

    if any(_step_invoked_question_tool(s) for s in turn_steps):
        return {"injectSteps": []}

    if _requires_graph_traversal(steps, turn_steps) and not any(_step_invoked_graph_query(s) for s in turn_steps):
        return _force_continue(QUERY_TRAVERSAL_FORCE_PROMPT)

    last_step = turn_steps[-1]
    last_content = str(last_step.get("content") or "")
    if has_disjunctive_fence_sitting(last_content):
        return _force_continue(INTERCEPT_PROMPT)

    last_type = str(last_step.get("type", "")).upper()
    last_source = str(last_step.get("source", "")).upper()
    is_model_reply = (last_type in ("PLANNER_RESPONSE", "ASSISTANT", "MODEL_RESPONSE", "") or last_source == "MODEL")
    already_intercepted = any(LAYMAN_INTERCEPT_MARKER in str(s.get("content") or "") for s in turn_steps)
    if is_model_reply and not already_intercepted and not _iter_step_tool_calls(last_step):
        jargon_violations = detect_layman_jargon_violations(last_content)
        if jargon_violations:
            return _force_continue(
                LAYMAN_JARGON_INTERCEPT_PROMPT.format(violations=", ".join(jargon_violations[:4])),
                ephemeral=True,
            )
    return {"injectSteps": []}



def main() -> int:
    raw = sys.stdin.read()
    result = evaluate_post_invocation_payload(raw)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
