"""tests/test_rca_hitl_merge_and_learning_e2e.py

End-to-End lifecycle verification for 8-Agent RCA Remediation:
Covers the complete pipeline from:
1. HealingDecisionStore persistence & JSONL drain (with full SHA-256 event digest)
2. ClosedLoopLearningEngine empirical Brier score simulation & post-learning confidence recalibration
3. QuestionBuilder & arbitrate_decision_margin single-pass calibration with preserved raw option keys
4. PR merge HITL flow: verify_pr_merge_hitl_authorization -> HITL_QUESTION_PAYLOAD_JSON ->
   check_ask_user_question.py (mandatory Tier-0 allow despite >20% spread) ->
   cmd_authorize_merge -> verify_pr_merge_hitl_authorization (PASS).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from dual_pipeline.healing_policy import arbitrate_decision_margin
from dual_pipeline.hitl_governance_core.question_builder import (
    QuestionBuilder,
    build_pr_merge_approval_question,
)
from dual_pipeline.learning_engine import get_learning_engine
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore
from tools.check_ask_user_question import check_hook_payload
from tools.pr_governance_core.lifecycle import (
    cmd_authorize_merge,
    verify_pr_merge_hitl_authorization,
)


def test_full_rca_healing_learning_and_pr_merge_hitl_e2e(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full E2E lifecycle: Healing store -> offline learning -> empirical Brier simulation -> QuestionBuilder -> PR merge HITL."""
    runtime_root = tmp_path / ".local"
    ledger_dir = runtime_root / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    db_path = ledger_dir / "learning.sqlite"
    jsonl_path = ledger_dir / "live_outcome_events.jsonl"
    hitl_sqlite = ledger_dir / "hitl_decision_ledger.sqlite"
    hitl_json = ledger_dir / "hitl_decision_ledger.json"
    nuance_store = runtime_root / "learned_hitl_option_store.json"
    proposals_dir = tmp_path / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_path))
    monkeypatch.setenv("NEE_HITL_LEDGER_SQLITE", str(hitl_sqlite))
    monkeypatch.setenv("NEE_HITL_LEDGER_JSON", str(hitl_json))
    monkeypatch.setenv("NEE_LEARNED_HITL_OPTION_STORE", str(nuance_store))

    # 1. Seed HealingDecisionStore + JSONL events sharing identical 64-char prefix (verifying no prefix collision)
    store = HealingDecisionStore(db_path=db_path)
    for i in range(4):
        store.record_decision(
            {
                "decision_id": f"e2e-heal-{i}",
                "run_id": f"run-{i}",
                "subsystem": "deterministic_healing_mesh",
                "component": "DeterministicHealer",
                "scope": "output_2",
                "repair_class": "REPAIR_DB_SCHEMA",
                "action_taken": "Repaired schema field",
                "outcome_status": "HEALED",
                "before_digest": f"bef-{i}",
                "after_digest": f"aft-{i}",
                "confidence_top": 0.68,
                "confidence_second": 0.32,
                "ambiguity_margin": 0.36,
            }
        )

    shared_prefix_events = [
        json.dumps(
            {
                "outcome_type": "COMPONENT_REPAIR",
                "subsystem": "l2_healer_service",
                "component": "UnifiedL2HealerService",
                "repair_class": "QUOTE_SPAN_REANCHOR",
                "defect_message": f"Reanchored quote span #{idx}",
                "was_successful": True,
            }
        )
        for idx in range(3)
    ]
    jsonl_path.write_text("\n".join(shared_prefix_events) + "\n", encoding="utf-8")

    # 2. Run offline healing learning cycle & verify empirical Brier simulation + zero proposals_dir pollution
    engine = get_learning_engine(runtime_root=runtime_root, proposals_dir=proposals_dir)
    cycle = engine.run_healing_learning_cycle(
        db_path=db_path, outcome_ledger_path=jsonl_path, min_occurrences=2, generate_proposals=True
    )
    assert cycle["signals_count"] == 7
    assert len(cycle["proposals"]) >= 2
    for sim in cycle["simulations"]:
        assert sim["success"] is True
        assert sim["details"]["simulated_brier_score"] < sim["details"]["current_brier_score"]
        assert sim["details"]["current_brier_score"] != 0.184
    assert not (proposals_dir / "learned_healing_posteriors.json").exists()

    # 3. Verify arbitrate_decision_margin & QuestionBuilder calibrate from learned store without double-blending
    arb = arbitrate_decision_margin(
        "dec-e2e-arb",
        [("REPAIR_DB_SCHEMA", 0.54), ("DEFER_MANUAL_INSPECTION", 0.46)],
        question="Which database schema repair option should be applied?",
        category="healing_policy",
        healing_db_path=db_path,
        ledger_path=hitl_json,
    )
    assert arb.top_confidence > 0.65

    # 4. Verify end-to-end PR merge HITL flow without manual typing
    branch = "fix/rca-hitl-merge-and-question-builder-gaps"
    ok_before, block_msg = verify_pr_merge_hitl_authorization(branch, repo_root=tmp_path)
    assert ok_before is False
    assert "HITL_QUESTION_PAYLOAD_JSON:" in block_msg

    # Parse emitted tool call and verify PreToolUse hook allows it
    emitted_tool_call = json.loads(block_msg.split("HITL_QUESTION_PAYLOAD_JSON:", 1)[1].strip())
    hook_res = check_hook_payload(json.dumps({"toolCall": {"name": "ask_question", "args": emitted_tool_call}}))
    assert hook_res.get("decision") == "allow"

    # Authorize merge via cmd_authorize_merge using QuestionBuilder defaults and verify clearance
    q_payload = build_pr_merge_approval_question(branch, repo_root=tmp_path)
    args = argparse.Namespace(
        branch=branch,
        selected_option=q_payload.display_options[0],
        question=q_payload.question,
        options=q_payload.display_options,
    )
    assert cmd_authorize_merge(args) == 0

    ok_after, auth_msg = verify_pr_merge_hitl_authorization(branch, repo_root=tmp_path)
    assert ok_after is True
    assert "Verified human PR merge authorization" in auth_msg
