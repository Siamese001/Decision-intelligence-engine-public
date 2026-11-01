"""End-to-end and adversarial tests for HITL PR merge ledger persistence and Tier-0 hook classification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.hitl_criticality import CriticalityTier, evaluate_criticality_score
from dual_pipeline.hitl_governance_core.classifier import classify_decision_criticality
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin
from scripts.hitl_post_tool_ledger_writer import (
    infer_branch_from_text,
    infer_decision_category,
    normalize_selected_answer,
    process_post_tool_payload,
)
from tools.check_ask_user_question import check_hook_payload
from tools.pr_governance_core.lifecycle import (
    cmd_authorize_merge,
    verify_pr_merge_hitl_authorization,
)


def test_tier0_pr_merge_never_triggers_margin_bypass_e2e() -> None:
    """PR merge authorization questions with intervening branch names classify as Tier 0 and bypass margin rejection."""
    question = (
        "Branch feat/doc-ingestion-signal-framework passed all 33 readiness checks and 31 tests across Waves 1 to 4. "
        "Authorize merging feat/doc-ingestion-signal-framework into local main?"
    )
    options = [
        "(Recommended) [92% — HIGH] Approve PR merge of feat/doc-ingestion-signal-framework into local main now",
        "[8% — LOW] Hold merge of feat/doc-ingestion-signal-framework for manual review first",
    ]
    tier, score, _ = evaluate_criticality_score(operation_text=f"{question} {' '.join(options)}")
    assert tier == CriticalityTier.TIER_0_IRREVERSIBLE
    assert score == 1.0

    cls_res = classify_decision_criticality(action_description=question, options=options)
    assert cls_res.requires_mandatory_hitl is True

    hook_input = {
        "toolCall": {
            "name": "ask_question",
            "args": {"questions": [{"question": question, "options": options, "is_multi_select": False}]},
        }
    }
    decision = check_hook_payload(json.dumps(hook_input))
    assert decision.get("decision") == "allow", f"Expected allow, got: {decision}"


def test_post_tool_ledger_writer_parses_a1_and_verifies_pr_merge_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostToolUse hook parses 'A1: ...', extracts branch, persists to isolated ledger, and satisfies PR merge verification."""
    sqlite_p = tmp_path / "hitl_decision_ledger.sqlite"
    json_p = tmp_path / "hitl_decision_ledger.json"
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(sqlite_p))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(json_p))

    question = (
        "Branch feat/doc-ingestion-signal-framework passed all 33 readiness checks and 31 tests across Waves 1 to 4. "
        "Authorize merging feat/doc-ingestion-signal-framework into local main?"
    )
    options = [
        "(Recommended) [58% — MEDIUM] Approve PR merge of feat/doc-ingestion-signal-framework into local main now",
        "[42% — MEDIUM] Hold merge of feat/doc-ingestion-signal-framework for manual inspection first",
    ]
    raw_output = "A1: (Recommended) [58% — MEDIUM] Approve PR merge of feat/doc-ingestion-signal-framework into local main now"

    payload: Dict[str, Any] = {
        "toolCall": {
            "name": "ask_question",
            "args": {"questions": [{"question": question, "options": options, "is_multi_select": False}]},
        },
        "output": raw_output,
    }
    res = process_post_tool_payload(json.dumps(payload))
    assert res["status"] == "RECORDED"
    assert res["category"] == "pr_merge_authorization"
    assert sqlite_p.is_file() and json_p.is_file()

    auth_ok, auth_msg = verify_pr_merge_hitl_authorization("feat/doc-ingestion-signal-framework", repo_root=tmp_path)
    assert auth_ok is True
    assert res["entry_id"] in auth_msg


def test_cmd_authorize_merge_and_approval_origin_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """validate_approval_origin accepts ask_question/ask_user_question and cmd_authorize_merge persists approval."""
    sqlite_p = tmp_path / "hitl_decision_ledger.sqlite"
    json_p = tmp_path / "hitl_decision_ledger.json"
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(sqlite_p))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(json_p))

    for src in ("ask_question", "ask_user_question", "human_operator", "user_input"):
        ok, _ = validate_approval_origin({"source": src, "operator_id": "human_operator"})
        assert ok is True

    args = argparse.Namespace(
        branch="fix/rca-hitl-pr-merge-ledger-hook",
        selected_option="(Recommended) [58% — MEDIUM] Approve PR merge of fix/rca-hitl-pr-merge-ledger-hook into local main now",
        question="Authorize merging fix/rca-hitl-pr-merge-ledger-hook into local main?",
    )
    rc = cmd_authorize_merge(args)
    assert rc == 0

    auth_ok, _ = verify_pr_merge_hitl_authorization("fix/rca-hitl-pr-merge-ledger-hook", repo_root=tmp_path)
    assert auth_ok is True


def test_adversarial_malformed_skipped_and_declined_merge_payloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adversarial checks: boundary/empty payloads, malformed JSON, skipped answers, and 'Hold merge' rejections."""
    sqlite_p = tmp_path / "hitl_decision_ledger.sqlite"
    json_p = tmp_path / "hitl_decision_ledger.json"
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(sqlite_p))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(json_p))

    # 1. Boundary / empty payload
    assert process_post_tool_payload("")["status"] == "SKIPPED"
    # 2. Malformed JSON
    assert process_post_tool_payload("{not-valid-json")["status"] == "SKIPPED"
    # 3. Cancelled / skipped selection
    skip_payload = {
        "toolCall": {"name": "ask_question", "args": {"question": "Merge feat/x into main?", "options": ["A", "B"]}},
        "output": "A1: Skip",
    }
    assert process_post_tool_payload(json.dumps(skip_payload))["status"] == "SKIPPED"

    # 4. Invariant bypass attempt: operator selects 'Hold merge' -> must NOT authorize merge
    hold_payload = {
        "toolCall": {
            "name": "ask_question",
            "args": {
                "question": "Authorize merging feat/blocked-branch into local main?",
                "options": [
                    "(Recommended) [58% — MEDIUM] Approve PR merge of feat/blocked-branch into local main now",
                    "[42% — MEDIUM] Hold merge of feat/blocked-branch for manual inspection first",
                ],
            },
        },
        "output": "A1: [42% — MEDIUM] Hold merge of feat/blocked-branch for manual inspection first",
    }
    res = process_post_tool_payload(json.dumps(hold_payload))
    assert res["status"] == "RECORDED"
    auth_ok, _ = verify_pr_merge_hitl_authorization("feat/blocked-branch", repo_root=tmp_path)
    assert auth_ok is False


@settings(max_examples=25, deadline=None)
@given(
    prefix=st.sampled_from(["feat", "fix", "enhance", "chore", "ingest", "audio", "transcript", "refactor"]),
    slug=st.from_regex(r"[a-z][a-z0-9\-]{2,20}", fullmatch=True),
    q_idx=st.integers(min_value=1, max_value=9),
)
def test_property_branch_and_answer_normalization(prefix: str, slug: str, q_idx: int) -> None:
    """Property test: any conventional branch token and A<N>: prefix is normalized deterministically."""
    branch = f"{prefix}/{slug}"
    q_text = f"Branch {branch} passed readiness. Authorize merging {branch} into local main?"
    raw_ans = f"A{q_idx}: (Recommended) [58% — MEDIUM] Approve PR merge of {branch} into local main now"
    norm = normalize_selected_answer(raw_ans)
    assert not norm.startswith(f"A{q_idx}:")
    assert infer_branch_from_text(q_text, [norm], norm) == branch
    assert infer_decision_category(q_text, [norm]) == "pr_merge_authorization"
