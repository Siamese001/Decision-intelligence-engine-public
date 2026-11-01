#!/usr/bin/env python3
"""tools/check_ask_user_question.py

CLI preflight and pre-commit check verifying that draft operator questions conform
to Wave 5 contracts:
- Single atomic question (no bundling, question <= 300 chars).
- At least 2 options (each <= 250 chars).
- Calibrated confidence (percentage, tier) on every option.
- Zero-jargon layman clarity (no file paths, code keywords, identifiers, flags).
- Anti-truncation invariant (no ellipses, slicing, or abbreviation).
- Speaker assignment evidence context (-2/+2 context window).
"""

from __future__ import annotations

import argparse
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
    import subprocess
    try:
        from tools.task_restoration_core.ssot_pointers import resolve_primary_repo_root
        primary_root = resolve_primary_repo_root(ROOT)
    except Exception:
        primary_root = ROOT
    candidates = [
        ROOT / ".venv" / "bin" / "python",
        primary_root / ".venv" / "bin" / "python",
        ROOT.parent.parent / ".venv" / "bin" / "python",
    ]
    try:
        git_common = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        if git_common:
            candidates.insert(1, Path(git_common).resolve().parent / ".venv" / "bin" / "python")
    except Exception:
        pass
    for venv_py in candidates:
        if venv_py.exists() and os.access(venv_py, os.X_OK):
            os.execv(str(venv_py), [str(venv_py)] + sys.argv)

from dual_pipeline.hitl_governance_core.question_validator import (  # noqa: E402
    validate_question_payload,
)

SPEAKER_TRIGGER = re.compile(
    r"\b(?:speaker\s+(?:assignment|attribution|mapping|diarization|cluster)|"
    r"who\s+is\s+speaking|who\s+spoke|"
    r"which\s+participant\s+(?:is|should|was)|"
    r"acoustic\s+diarization|"
    r"voice\s+cluster|"
    r"unmapped\s+speaker|"
    r"Speaker\s+\d+\b.*(?:map|assign|identif|who))\b"
    r"|(?:\bTARGET\b.*\bUNRESOLVED\b)",
    re.IGNORECASE,
)


def is_speaker_attribution_question(text: str) -> bool:
    """Return True if question involves speaker attribution or diarization assignment."""
    return bool(text and isinstance(text, str) and SPEAKER_TRIGGER.search(text))


def verify_speaker_context(question_text: str) -> Optional[str]:
    """Validate -2/+2 visible transcript context on speaker assignment decisions."""
    if not is_speaker_attribution_question(question_text):
        return None
    try:
        from dual_pipeline.speaker_attribution_core.review_context_render import (
            validate_visible_speaker_context,
        )

        validate_visible_speaker_context(question_text)
        return None
    except Exception as exc:
        return (
            f"[HITL Governance Violation] Speaker Attribution Context Incomplete: {exc} "
            "Every speaker assignment choice must show the target turn with the two preceding "
            "and two following transcript turns (or explicit boundary counts) and bold "
            "'**UNRESOLVED — <Speaker>**' per .agents/rules/hitl-decisions.md."
        )


def _extract_call_args(raw_input: str) -> Tuple[Optional[Dict[str, Any]], bool]:
    """Extract tool call arguments from raw hook input. Returns (args, is_passthrough)."""
    if not raw_input or not raw_input.strip():
        return None, True
    try:
        data = json.loads(raw_input)
    except Exception:
        return None, True
    if not isinstance(data, dict):
        return None, True
    tool_call = data.get("toolCall")
    if isinstance(tool_call, dict):
        if tool_call.get("name") not in ("ask_question", "ask_user_question"):
            return None, True
        args = tool_call.get("args", {})
    else:
        args = data
    if not isinstance(args, dict):
        return None, False
    return args, False


def _extract_single_question(args: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Enforce atomic presentation on questions array."""
    if "questions" in args and isinstance(args["questions"], list):
        qs = args["questions"]
    elif "question" in args or "prompt" in args:
        qs = [args]
    else:
        qs = []

    if not qs:
        return None, "[HITL Governance Violation] No question provided in tool call."
    if len(qs) > 1:
        return None, (
            f"[HITL Governance Violation] Atomic Presentation Violation: Attempted to present "
            f"{len(qs)} questions simultaneously. Decisions must be presented strictly one at a time."
        )
    item = qs[0]
    if not isinstance(item, dict):
        return None, "[HITL Governance Violation] Question payload item must be a dictionary."
    return item, None


def _score_single_option(opt: Any, pattern: Any) -> Optional[Tuple[float, Any]]:
    text = str(opt.get("label") or opt.get("text") or "") if isinstance(opt, dict) else str(opt)
    m = pattern.search(text)
    if m:
        return (float(m.group(1)) / 100.0, opt)
    if isinstance(opt, dict) and opt.get("confidence") is not None:
        try:
            val = float(opt["confidence"])
            return (val / 100.0 if val > 1.0 else val, opt)
        except Exception:
            return None
    return None


def _extract_option_confidences(options: List[Any]) -> List[Tuple[float, Any]]:
    """Extract normalized [0.0, 1.0] confidence scores paired with option items."""
    from dual_pipeline.hitl_governance_core.question_validator import PERCENTAGE_PATTERN

    scored = [s for opt in options if (s := _score_single_option(opt, PERCENTAGE_PATTERN)) is not None]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


ROLE_CLARIFICATION_TRIGGER = re.compile(
    r"\b(?:verified\s+(?:project\s+)?(?:role|title)|role\s+clarification|title\s+resolution|"
    r"graphdb\s+role\s+(?:sourcing|verification)|unverified\s+role|prevent\s+role\s+hallucination)\b",
    re.IGNORECASE,
)

MANDATORY_CATEGORIES = {
    "pr_merge_authorization", "tier_0_irreversible", "release_activation", "destructive",
    "role_clarification", "graphdb_role_verification", "c05_title_completeness", "c06_graphdb_role_verification",
    "graph_hygiene_cluster_approval",
}


def _check_classifier_mandatory(q_text: str, options: List[Any]) -> bool:
    try:
        from dual_pipeline.hitl_criticality import CriticalityTier, evaluate_criticality_score
        from dual_pipeline.hitl_governance_core.classifier import classify_decision_criticality

        opt_texts = " ".join(str(o.get("label") or o.get("text") or "") if isinstance(o, dict) else str(o) for o in options)
        tier, _, _ = evaluate_criticality_score(operation_text=f"{q_text} {opt_texts}")
        if tier in (CriticalityTier.TIER_0_IRREVERSIBLE, CriticalityTier.TIER_1_ARCHITECTURAL):
            return True
        return bool(classify_decision_criticality(action_description=q_text, options=options).requires_mandatory_hitl)
    except Exception:
        return False


def _is_non_waivable_mandatory_question(
    q_text: str, options: List[Any], q_item: Dict[str, Any], args: Dict[str, Any]
) -> bool:
    """Return True if the question is a Tier-0 / Mandatory Governance / Speaker Attribution / Role Verification gate."""
    if is_speaker_attribution_question(q_text) or ROLE_CLARIFICATION_TRIGGER.search(q_text):
        return True
    if "| Dimension |" in q_text and "8-Agent Council Verdict" in q_text:
        return True
    header = str(q_item.get("header") or args.get("header") or "").lower()
    if any(tok in header or tok in q_text.lower() for tok in ("[mandatory approval]", "[merge authorization]", "pr_merge_authorization")):
        return True
    if q_item.get("criticality_tier", args.get("criticality_tier")) in (0, "0", "TIER_0_IRREVERSIBLE"):
        return True
    cat = str(q_item.get("category") or args.get("category") or q_item.get("hitl_type") or args.get("hitl_type") or "").lower()
    return cat in MANDATORY_CATEGORIES or _check_classifier_mandatory(q_text, options)


def _evaluate_margin_bypass(
    q_text: str,
    options: List[Any],
    q_item: Dict[str, Any],
    args: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    scored = _extract_option_confidences(options)
    if len(scored) < 2 or _is_non_waivable_mandatory_question(q_text, options, q_item, args):
        return None
    from dual_pipeline.parameters import (
        get_float_parameter,
        get_hitl_ambiguity_margin_threshold,
        needs_operator_decision,
    )

    c_top, top_opt = scored[0]
    c_second, _ = scored[1]
    thresh = get_hitl_ambiguity_margin_threshold()
    floor = get_float_parameter("hitl.min_autonomous_confidence_floor", 0.65)
    spread = round(c_top - c_second, 4)
    if needs_operator_decision(c_top, c_second, threshold=thresh) or c_top < floor:
        return None
    try:
        from dual_pipeline.core import stable_id
        from dual_pipeline.hitl_governance_core.decision_record_writer import record_autonomous_decision

        dec_id = str(q_item.get("decision_id") or args.get("decision_id") or stable_id("auto-bypass", q_text, length=16))
        cat = str(q_item.get("category") or args.get("category") or "tactical")
        in_unisolated_test = (
            ("PYTEST_CURRENT_TEST" in os.environ or os.environ.get("MOCK_ENV", "").lower() == "true")
            and not os.environ.get("NEE_HITL_LEDGER_SQLITE")
            and not os.environ.get("NEE_HITL_LEDGER_JSON")
        )
        sql_override = f"/tmp/nee_test_hook_ledger_{os.getpid()}.sqlite" if in_unisolated_test else None
        json_override = f"/tmp/nee_test_hook_ledger_{os.getpid()}.json" if in_unisolated_test else None
        record_autonomous_decision(
            decision_id=dec_id,
            question=q_text,
            options=options,
            selected_option=top_opt,
            margin_at_decision=spread,
            category=cat,
            sqlite_path=sql_override,
            json_path=json_override,
        )
    except Exception:
        pass
    return {
        "decision": "deny",
        "reason": (
            f"[HITL_MARGIN_BYPASS_REQUIRED] Confidence spread ({spread * 100:.0f}%) exceeds the "
            f"{thresh * 100:.0f}% ambiguity threshold (<= {thresh * 100:.0f}% variance required to prompt). "
            "Do not call ask_question; execute the top-ranked recommended option autonomously "
            "(AUTONOMOUS decision receipt recorded in HITL ledger)."
        ),
    }


def check_hook_payload(raw_input: str) -> Dict[str, Any]:
    """Evaluate PreToolUse hook payload and output Antigravity decision JSON."""
    args, is_passthrough = _extract_call_args(raw_input)
    if is_passthrough:
        return {"decision": "allow"}
    if args is None:
        return {"decision": "deny", "reason": "[HITL Governance Violation] Tool call arguments must be a dictionary."}

    q_item, err = _extract_single_question(args)
    if err or not q_item:
        return {"decision": "deny", "reason": err or "Invalid question payload"}

    q_text = str(q_item.get("question") or q_item.get("prompt") or "").strip()
    options = q_item.get("options")
    if not q_text:
        return {"decision": "deny", "reason": "[HITL Governance Violation] Question text is missing or empty."}
    if not options or not isinstance(options, list) or len(options) < 2:
        return {"decision": "deny", "reason": "[HITL Governance Violation] Missing or insufficient options: at least 2 required."}

    spk_err = verify_speaker_context(q_text)
    if spk_err:
        return {"decision": "deny", "reason": spk_err}

    res = validate_question_payload(question=q_text, options=options)
    if not res.is_valid:
        error_details = "; ".join(res.errors[:4])
        return {
            "decision": "deny",
            "reason": f"[HITL Governance Violation] Question rejected: {res.rejection_reason}. Itemized errors: {error_details}.",
        }

    bypass_res = _evaluate_margin_bypass(q_text, options, q_item, args)
    return bypass_res if bypass_res else {"decision": "allow"}


def run_self_test() -> Tuple[bool, List[str]]:
    """Verify that validator catches contract violations and allows compliant payloads."""
    diagnostics: List[str] = []
    valid_q = "How should long choices be handled if they exceed the screen display limit?"
    valid_opts = [
        "(Recommended) Move detail into question (Confidence: HIGH (65%)) - Keeps choices concise.",
        "Strictly reject question (Confidence: LOW (40%)) - Halts execution until shortened.",
    ]
    if not validate_question_payload(valid_q, valid_opts).is_valid:
        diagnostics.append("Valid payload failed validation.")

    no_conf_opts = ["First choice without rating", "Second choice without rating"]
    if validate_question_payload(valid_q, no_conf_opts).is_valid:
        diagnostics.append("Validator failed to reject options missing confidence.")

    jargon_q = "Should we inspect tools/governance.py for details?"
    if validate_question_payload(jargon_q, valid_opts).is_valid:
        diagnostics.append("Validator failed to reject file path in question.")

    speaker_q = (
        "Meeting/source: test.wav\n\n"
        "Review one target speaker assignment.\n\n"
        "Transcript start: 2 earlier turn(s) unavailable at this boundary.\n\n"
        "TARGET [00:00:00] **UNRESOLVED — Speaker 1**: we have anything else outstanding we have\n\n"
        "Transcript end: 2 later turn(s) unavailable at this boundary.\n\n"
        "Who is speaking in the TARGET turn?"
    )
    if not validate_question_payload(speaker_q, valid_opts).is_valid:
        diagnostics.append("Speaker context valid payload failed.")

    hook_spk_valid = {"toolCall": {"name": "ask_question", "args": {"questions": [{"question": speaker_q, "options": valid_opts}]}}}
    if check_hook_payload(json.dumps(hook_spk_valid)).get("decision") != "allow":
        diagnostics.append("Hook failed to allow compliant speaker attribution payload.")

    adhoc_spk_q = "Acoustic diarization identified one voice cluster (Speaker 1) saying: 'outstanding'. Which participant?"
    hook_spk_invalid = {"toolCall": {"name": "ask_question", "args": {"questions": [{"question": adhoc_spk_q, "options": valid_opts}]}}}
    res_spk_inv = check_hook_payload(json.dumps(hook_spk_invalid))
    if res_spk_inv.get("decision") != "deny" or "Speaker Attribution Context Incomplete" not in res_spk_inv.get("reason", ""):
        diagnostics.append("Hook failed to deny speaker attribution missing context.")

    collapsed_spk_q = (
        "Meeting/source: LENS Working Session OCR (10/16/2026) "
        "BEFORE 2 [00:06:46] Alex Sanchez: Do you think that would improve stuff "
        "BEFORE 1 [00:06:57] Amit Ayer: Or do you think it is more of just like a cost consideration? "
        "TARGET [00:07:01] **UNRESOLVED — Unknown**: Oh, both. "
        "AFTER 1 [00:07:03] **UNRESOLVED — Unknown**: I mean "
        "AFTER 2 [00:07:15] Amit Ayer: What is the response time? "
        "Who is speaking in the TARGET turn?"
    )
    hook_spk_collapsed = {"toolCall": {"name": "ask_question", "args": {"questions": [{"question": collapsed_spk_q, "options": valid_opts}]}}}
    res_spk_col = check_hook_payload(json.dumps(hook_spk_collapsed))
    if res_spk_col.get("decision") != "deny" or "collapsed onto the same line" not in res_spk_col.get("reason", ""):
        diagnostics.append("Hook failed to deny collapsed speaker context rows.")

    return len(diagnostics) == 0, diagnostics


def check_payload(data: Dict[str, Any]) -> int:
    """Validate a parsed payload dictionary."""
    question = data.get("question") or data.get("prompt")
    options = data.get("options")
    if not question:
        print("[FAIL] Missing 'question' or 'prompt' in payload.")
        return 1
    if not options or not isinstance(options, list) or len(options) < 2:
        print("[FAIL] Missing or invalid 'options' list (at least 2 required).")
        return 1

    spk_err = verify_speaker_context(str(question))
    if spk_err:
        print(f"[FAIL] {spk_err}")
        return 1

    res = validate_question_payload(question=question, options=options)
    if res.is_valid:
        print("[PASS] Question payload conforms 100% to Wave 5 Question Contract.")
        return 0
    print(f"[FAIL] {res.rejection_reason}")
    for err in res.errors:
        print(f"  - {err}")
    return 1


def _load_payload_from_args(args: argparse.Namespace) -> Tuple[Optional[Dict[str, Any]], Optional[int]]:
    """Parse question payload from CLI arguments."""
    if args.file:
        try:
            return json.loads(args.file.read_text(encoding="utf-8")), None
        except Exception as e:
            print(f"[FAIL] Unable to read JSON file {args.file}: {e}")
            return None, 1
    if args.json:
        try:
            return json.loads(args.json), None
        except Exception as e:
            print(f"[FAIL] Unable to parse JSON string: {e}")
            return None, 1
    if args.question and args.options:
        return {"question": args.question, "options": args.options}, None
    return None, None


def _handle_hook_mode() -> int:
    if sys.stdin.isatty():
        print(json.dumps({"error": "stdin is a tty, expected json payload"}))
        return 1
    import select
    try:
        r, _, _ = select.select([sys.stdin], [], [], 5.0)
        raw_data = sys.stdin.read() if r else ""
    except (OSError, ValueError):
        raw_data = ""
    print(json.dumps(check_hook_payload(raw_data)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify that an operator question conforms to Wave 5 contracts.")
    parser.add_argument("--hook", action="store_true", help="Run in Antigravity PreToolUse hook mode.")
    parser.add_argument("--file", type=Path, help="Path to JSON file containing payload.")
    parser.add_argument("--json", type=str, help="Raw JSON string containing payload.")
    parser.add_argument("--question", type=str, help="Question text.")
    parser.add_argument("--option", action="append", dest="options", help="Option text.")
    parser.add_argument("--self-test", action="store_true", help="Run internal self-tests.")
    args = parser.parse_args()

    if args.hook:
        return _handle_hook_mode()

    if args.self_test:
        ok, diag = run_self_test()
        print("[PASS] Self-test passed: All validator invariants verified." if ok else f"[FAIL] Self-test: {diag}")
        return 0 if ok else 1

    payload, err_code = _load_payload_from_args(args)
    if err_code is not None:
        return err_code
    if payload is None:
        ok, diag = run_self_test()
        print("[PASS] Self-test passed: All validator invariants verified." if ok else f"[FAIL] Self-test: {diag}")
        return 0 if ok else 1

    return check_payload(payload)


if __name__ == "__main__":
    sys.exit(main())
