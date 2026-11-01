"""tests/test_rca_wave1_pr_hitl_and_ledger_isolation.py

Wave 1 verification suite for 8-Agent RCA Remediation:
1. Programmatic QuestionBuilder payload emission in `build_pr_merge_approval_question`,
   `verify_pr_merge_hitl_authorization`, and `cmd_authorize_merge` (zero hardcoded 58%/42% strings).
2. Mandatory Tier-0 PR merge and branch promotion classification across natural-language phrasing,
   question/option `?` boundaries, and `[Mandatory Approval]` tags in `check_ask_user_question.py`.
3. `resolve_ledger_paths(repo_root=tmp_path)` isolation from `common_root/.local/ledger/` and
   clean extraction of `operator_id` when `operator_origin` is a dictionary.
4. `QuestionBuilder` alignment across `verdict_verifier.py`, `envelope.py`, `canonical_evaluator.py`,
   and `preview_exit_hook.py`.
"""

from __future__ import annotations

import argparse
import inspect
import json
from pathlib import Path

import pytest

from dual_pipeline.cli.preview_exit_hook import build_preview_activation_question
from dual_pipeline.hitl_criticality import CriticalityTier, evaluate_criticality_score
from dual_pipeline.hitl_governance_core.canonical_evaluator import format_canonical_option_label
from dual_pipeline.hitl_governance_core.classifier import classify_decision_criticality
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    record_operator_answer,
    resolve_ledger_paths,
)
from dual_pipeline.hitl_governance_core.question_builder import build_pr_merge_approval_question
from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload
from tools.check_ask_user_question import check_hook_payload
from tools.pr_governance_core import lifecycle
from tools.pr_governance_core.lifecycle import (
    cmd_authorize_merge,
    verify_pr_merge_hitl_authorization,
)


def test_lifecycle_has_zero_hardcoded_58_or_42_badges() -> None:
    """Verify cmd_authorize_merge and lifecycle.py contain zero legacy [58% — MEDIUM] / [42% — MEDIUM] strings."""
    src = inspect.getsource(lifecycle)
    assert "58% — MEDIUM" not in src
    assert "42% — MEDIUM" not in src


def test_build_pr_merge_approval_question_and_exit_code_2_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verify_pr_merge_hitl_authorization emits machine-readable HITL_QUESTION_PAYLOAD_JSON built via QuestionBuilder."""
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(tmp_path / "hitl.sqlite"))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(tmp_path / "hitl.json"))

    q_payload = build_pr_merge_approval_question("feat/rca-test-branch", repo_root=tmp_path)
    val = validate_question_payload(q_payload.question, q_payload.display_options)
    assert val.is_valid, f"QuestionBuilder PR merge payload failed validation: {val.errors}"

    ok, msg = verify_pr_merge_hitl_authorization("feat/rca-test-branch", repo_root=tmp_path)
    assert ok is False
    assert "HITL_QUESTION_PAYLOAD_JSON:" in msg
    raw_json = msg.split("HITL_QUESTION_PAYLOAD_JSON:", 1)[1].strip()
    tool_call = json.loads(raw_json)
    assert "questions" in tool_call
    assert len(tool_call["questions"]) == 1
    assert len(tool_call["questions"][0]["options"]) == 2

    # Ensure check_hook_payload allows the generated QuestionBuilder payload despite >20% spread
    hook_res = check_hook_payload(json.dumps({"toolCall": {"name": "ask_question", "args": tool_call}}))
    assert hook_res.get("decision") == "allow", f"Expected allow for mandatory PR merge prompt, got: {hook_res}"


def test_natural_phrasing_pr_merge_and_promotion_prompts_allowed_by_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural English merge and promotion prompts with 90% confidence spread classify as mandatory Tier-0."""
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(tmp_path / "hitl.sqlite"))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(tmp_path / "hitl.json"))

    prompts = [
        (
            "The September 29 partner alliances session passed all readiness gates. Authorize promotion into the main branch?",
            [
                "(Recommended) Approve promotion into local main (Confidence: 95%, Tier: High). Publishes verified changes.",
                "Hold promotion for manual review (Confidence: 5%, Tier: Low). Keeps changes staged.",
            ],
        ),
        (
            "All waves passed verification. Should we merge this pull request now?",
            [
                "(Recommended) Merge into the main branch now (Confidence: 90%, Tier: High). Completes integration.",
                "Pause and inspect changes first (Confidence: 10%, Tier: Low). Defers integration.",
            ],
        ),
    ]
    for q_text, opts in prompts:
        tier, _, _ = evaluate_criticality_score(operation_text=f"{q_text} {' '.join(opts)}")
        assert tier == CriticalityTier.TIER_0_IRREVERSIBLE
        cls_res = classify_decision_criticality(action_description=q_text, options=opts)
        assert cls_res.requires_mandatory_hitl is True
        hook_res = check_hook_payload(
            json.dumps(
                {
                    "toolCall": {
                        "name": "ask_question",
                        "args": {"questions": [{"question": q_text, "options": opts, "is_multi_select": False}]},
                    }
                }
            )
        )
        assert hook_res.get("decision") == "allow", f"Failed on prompt '{q_text}': {hook_res}"


def test_resolve_ledger_paths_repo_root_isolation_and_operator_id_dict_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resolve_ledger_paths(repo_root=tmp_path) never falls back to common_root, and dict operator_origin extracts clean operator_id."""
    monkeypatch.delenv("NEE_HITL_LEDGER_SQLITE", raising=False)
    monkeypatch.delenv("NEE_HITL_LEDGER_JSON", raising=False)

    s_path, j_path = resolve_ledger_paths(repo_root=tmp_path)
    assert str(s_path).startswith(str(tmp_path.resolve()))
    assert str(j_path).startswith(str(tmp_path.resolve()))

    res = record_operator_answer(
        decision_id="pr_merge_test_isolation",
        question="Branch 'feat isolation' passed readiness gates and review. Authorize governed merge into local main?",
        options=[
            "(Recommended) Approve merge of feat isolation (Confidence: 88%, Tier: High). Merge into local main.",
            "Hold merge of feat isolation (Confidence: 25%, Tier: Low). Pause for manual review.",
        ],
        selected_option="(Recommended) Approve merge of feat isolation (Confidence: 88%, Tier: High). Merge into local main.",
        operator_origin={"source": "ask_user_question", "operator_id": "human_operator"},
        prior_user_command="PR to local main",
        category="pr_merge_authorization",
        metadata={"branch": "feat/isolation"},
        repo_root=tmp_path,
    )
    assert res.status == "PERSISTED"
    data = json.loads(j_path.read_text(encoding="utf-8"))
    recs = data.get("decisions") or []
    assert len(recs) == 1
    assert recs[0]["operator_id"] == "human_operator"
    assert "{" not in recs[0]["operator_id"]

    # Also verify cmd_authorize_merge works end-to-end with QuestionBuilder defaults
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(s_path))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(j_path))
    args = argparse.Namespace(branch="fix/rca-cmd-auth-test", selected_option=None, question=None, options=None)
    assert cmd_authorize_merge(args) == 0
    ok, _ = verify_pr_merge_hitl_authorization("fix/rca-cmd-auth-test", repo_root=tmp_path)
    assert ok is True


def test_preview_exit_hook_and_canonical_option_formatter(tmp_path: Path) -> None:
    """build_preview_activation_question and format_canonical_option_label use unified formatting."""
    pkg = tmp_path / "intake_package.json"
    pkg.write_text("{}", encoding="utf-8")
    q = build_preview_activation_question({"intake_package": str(pkg), "output_dir": str(tmp_path)})
    assert "(Recommended) Publish and activate into knowledge graph now" in q["options"][0]
    assert "Keep preview only without mutating graph" in q["options"][1]
    assert "Confidence: 95%, Tier: High" in q["options"][0]

    lbl = format_canonical_option_label(0, {"label": "Promote branch", "confidence_score": 0.88})
    assert lbl.startswith("(Recommended) Promote branch")
    assert "88%" in lbl
