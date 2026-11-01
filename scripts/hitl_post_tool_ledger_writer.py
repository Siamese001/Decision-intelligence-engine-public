#!/usr/bin/env python3
"""scripts/hitl_post_tool_ledger_writer.py

PostToolUse lifecycle hook for ask_question and ask_user_question.
Parses the interactive question payload and the operator's selected answer from stdin,
infers a domain category when unspecified, and persists a Merkle-linked decision record
to hitl_decision_ledger.sqlite and hitl_decision_ledger.json via record_operator_answer().
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if sys.prefix == sys.base_prefix:
    for venv_py in (
        ROOT / ".venv" / "bin" / "python",
        ROOT.parent.parent / ".venv" / "bin" / "python",
    ):
        if venv_py.exists() and os.access(venv_py, os.X_OK):
            os.execv(str(venv_py), [str(venv_py)] + sys.argv)


_BRANCH_TOKEN_RE = re.compile(
    r"(?<![a-zA-Z0-9_\-\./])((?:feat|fix|enhance|chore|ingest|audio|transcript|refactor)/[a-zA-Z0-9_\-\.]+)"
)


def infer_branch_from_text(question: str, options: List[Any], selected: Any = None) -> Optional[str]:
    """Extract conventional branch name from question, selected option, or options list."""
    opt_text = " ".join(
        str(o.get("label") or o.get("text") or "") if isinstance(o, dict) else str(o)
        for o in (options or [])
    )
    corpus = f"{question} {selected or ''} {opt_text}"
    m = _BRANCH_TOKEN_RE.search(corpus)
    return m.group(1) if m else None


def normalize_selected_answer(raw_selected: Any) -> Any:
    """Strip leading 'A1: ' / 'Q1: ' answer prefixes emitted by interactive ask_question tool."""
    if not isinstance(raw_selected, str):
        return raw_selected
    cleaned = raw_selected.strip()
    cleaned = re.sub(r"^(?:A|Q)\d+\s*:\s*", "", cleaned)
    return cleaned.strip()


def infer_decision_category(question: str, options: List[Any], explicit_category: Optional[str] = None) -> str:
    """Infer a specific domain category from question/options so records do not collapse into 'general'."""
    if explicit_category and str(explicit_category).strip().lower() not in ("", "general", "default", "none"):
        return str(explicit_category).strip().lower()

    opt_text = " ".join(
        str(o.get("label") or o.get("text") or "") if isinstance(o, dict) else str(o)
        for o in (options or [])
    )
    corpus = f"{question} {opt_text}".lower()

    if re.search(
        r"\b(?:(?:merge|merging|promote|promoting)\s+.*\bmain\b|merge\s+pull\s+request|pr_merge_authorization|approve\s+pr\s+merge)\b",
        corpus,
    ):
        return "pr_merge_authorization"
    if re.search(r"\b(?:speaker|diarization|voice\s+cluster|who\s+is\s+speaking|target\s+\[)\b", corpus):
        return "speaker_attribution"
    if re.search(r"\b(?:rubric|transcript|output\s+0[123]|stage\s+(?:0[123]|one|two|three)|scrubber|judge)\b", corpus):
        return "transcript_rubric"
    if re.search(r"\b(?:database|cleanup|archive|prune|worktree|decommission)\b", corpus):
        return "maintenance_governance"
    if re.search(r"\b(?:release|activate|candidate\s+disposition|intake|publish)\b", corpus):
        return "release_activation"
    if re.search(r"\b(?:architecture|schema|layer|contract|refactor)\b", corpus):
        return "architecture"
    return "tactical_execution"


def _extract_post_tool_fields(data: Dict[str, Any]) -> Tuple[Optional[str], List[Any], Optional[Any], Dict[str, Any]]:
    """Extract (question, options, selected_option, metadata) from a PostToolUse payload."""
    tool_call = data.get("toolCall") if isinstance(data.get("toolCall"), dict) else data
    tool_name = str(tool_call.get("name") or data.get("tool_name") or data.get("toolName") or "")
    if tool_name and not any(k in tool_name for k in ("ask_question", "ask_user_question")):
        return None, [], None, {}

    args = tool_call.get("args") if isinstance(tool_call.get("args"), dict) else data.get("args")
    if not isinstance(args, dict):
        args = tool_call if isinstance(tool_call, dict) else data

    q_item: Dict[str, Any] = {}
    if isinstance(args.get("questions"), list) and args["questions"]:
        first_q = args["questions"][0]
        if isinstance(first_q, dict):
            q_item = first_q
    else:
        q_item = args

    question = str(q_item.get("question") or q_item.get("prompt") or "").strip()
    options = q_item.get("options") if isinstance(q_item.get("options"), list) else []

    # Extract selected answer from toolResult / response / output / selected_option
    raw_result = (
        data.get("toolResult")
        or data.get("result")
        or data.get("response")
        or data.get("output")
        or data.get("selected_option")
        or q_item.get("selected_option")
    )

    selected: Optional[Any] = None
    if isinstance(raw_result, str) and raw_result.strip():
        try:
            parsed_res = json.loads(raw_result)
            raw_result = parsed_res
        except Exception:
            selected = raw_result.strip()

    if isinstance(raw_result, dict):
        for k in ("selected_option", "selected", "answer", "choice", "response", "value", "output"):
            val = raw_result.get(k)
            if val:
                selected = val[0] if isinstance(val, list) and val else val
                break
        if selected is None and isinstance(raw_result.get("answers"), (list, dict)):
            ans = raw_result["answers"]
            if isinstance(ans, list) and ans:
                first_a = ans[0]
                if isinstance(first_a, dict):
                    sel_val = first_a.get("selected") or first_a.get("answer") or first_a.get("options")
                    selected = sel_val[0] if isinstance(sel_val, list) and sel_val else sel_val
                else:
                    selected = first_a
            elif isinstance(ans, dict) and ans:
                first_v = next(iter(ans.values()))
                selected = first_v[0] if isinstance(first_v, list) and first_v else first_v

    meta: Dict[str, Any] = {}
    for k in ("decision_id", "category", "branch", "rationale"):
        if k in q_item and q_item[k] is not None:
            meta[k] = q_item[k]
        elif k in args and args[k] is not None:
            meta[k] = args[k]
        elif k in data and data[k] is not None:
            meta[k] = data[k]

    return (question or None), options, selected, meta


def process_post_tool_payload(raw_input: str) -> Dict[str, Any]:
    """Process PostToolUse JSON payload and record operator answer into HITL ledger."""
    if not raw_input or not raw_input.strip():
        return {"status": "SKIPPED", "reason": "Empty input"}
    try:
        data = json.loads(raw_input)
    except Exception as exc:
        return {"status": "SKIPPED", "reason": f"Invalid JSON: {exc}"}
    if not isinstance(data, dict):
        return {"status": "SKIPPED", "reason": "Payload is not a JSON object"}

    question, options, selected, meta = _extract_post_tool_fields(data)
    selected = normalize_selected_answer(selected)
    if not question or not options or selected is None:
        return {"status": "SKIPPED", "reason": "Incomplete question, options, or operator selection"}

    sel_str = str(selected).strip()
    if not sel_str or sel_str.lower() in ("skip", "skipped", "cancel", "cancelled", "none"):
        return {"status": "SKIPPED", "reason": "Operator skipped or cancelled question"}

    from dual_pipeline.core import stable_id
    from dual_pipeline.hitl_governance_core.decision_record_writer import record_operator_answer

    decision_id = str(meta.pop("decision_id", None) or stable_id("hitl-post-tool", question, length=20))
    explicit_cat = meta.pop("category", None)
    category = infer_decision_category(question, options, explicit_category=explicit_cat)
    if "branch" not in meta or not meta["branch"]:
        inferred_branch = infer_branch_from_text(question, options, selected)
        if inferred_branch:
            meta["branch"] = inferred_branch
    rationale = str(meta.pop("rationale", None) or f"Interactive operator resolution via ask_question ({category}).")

    rec = record_operator_answer(
        decision_id=decision_id,
        question=question,
        options=options,
        selected_option=selected,
        operator_origin="human_operator",
        category=category,
        rationale=rationale,
        metadata=meta,
        runtime="post_tool_use_hook",
    )
    return {
        "status": "RECORDED",
        "entry_id": rec.entry_id,
        "decision_id": rec.decision_id,
        "category": rec.category,
        "outcome_type": rec.outcome_type,
        "margin_at_decision": rec.margin_at_decision,
        "entry_digest": rec.entry_digest,
    }


def main() -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        res = process_post_tool_payload(raw)
        print(json.dumps(res))
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
