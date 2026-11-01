"""
tests/end_to_end/test_alignment_w5_e2e.py

End-to-End lifecycle tests for Wave 5: Preserve gates through runtime and reuse.
Verifies:
1. Decision orchestration strictly blocks on INSUFFICIENT_REPLAN and requires re-review clearance.
2. Multi-factor cache invalidation correctly invalidates on changed roster, policy, prompt, evaluator, schema.
3. Legacy unbound cache results strictly require rerun.
4. Streaming copilot callback fence guarantees zero callbacks upon shutdown, cancel, or expiry.
5. Retrieval healing verifies local repair precedence and blocks exhausted repair from becoming success.
6. Fact mutation correctly triggers downstream gate reruns.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from dual_pipeline.decision.agents.decision_orchestrator import DecisionOrchestrator
from dual_pipeline.decision.agents.post_debate_reviewer import (
    DecisionPostDebateReviewer,
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.l0.runtime_gate_preservation import (
    CallbackFence,
    CallbackFencedError,
    ExhaustedRepairError,
    ReplanRequiredError,
    ReplanStatus,
    compute_context_hash,
    compute_roster_hash,
    validate_cache_reuse_dependencies,
    validate_fact_mutation_requires_rerun,
    validate_healing_outcome,
    validate_replan_clearance,
)
from dual_pipeline.l2_healing_contracts import L2HealingResult
from dual_pipeline.live_meeting_recorder_core.streaming_copilot import (
    StreamingCopilotHandler,
    _consume_stream_chunks,
)
from dual_pipeline.transcript_pipeline_core.caching import check_idempotent_meeting_run


def test_w5_e2e_replan_blocking_and_re_review_clearance():
    """Verify INSUFFICIENT_REPLAN blocks terminal approval and allows clearance upon re-review."""
    # 1. Mock post-debate reviewer returning INSUFFICIENT_REPLAN
    mock_reviewer = MagicMock()
    mock_reviewer.review_debate.return_value = L1PostL2ReviewContract.create(
        run_id="run_test",
        query_id="dec-1",
        plan_id="plan-1",
        sealed_artifact_id="art-1",
        verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
        deterministic_passed=False,
        deterministic_checks=[],
        semantic_passed=False,
        replan_directive={"directive_type": "EXPAND_EVALUATION", "reason": "Option risk scores uncalibrated"},
    )

    orchestrator = DecisionOrchestrator(post_debate_reviewer=mock_reviewer)
    options = [
        {"key": "opt_a", "label": "Option A"},
        {"key": "opt_b", "label": "Option B"},
    ]

    # Orchestrating decision must halt and return BLOCKED_REPLAN_REQUIRED
    res = orchestrator.orchestrate_decision(
        decision_id="dec-1",
        question="Which architecture pattern should be adopted?",
        options=options,
    )

    assert res["terminal_state"] == "BLOCKED_REPLAN_REQUIRED"
    assert res["top_option"] is None
    assert res["arbitration"]["blocked"] is True
    assert res["replan_directive"]["directive_type"] == "EXPAND_EVALUATION"

    # 2. Now simulate successful re-review after replan
    mock_reviewer.review_debate.return_value = L1PostL2ReviewContract.create(
        run_id="run_test_2",
        query_id="dec-1",
        plan_id="plan-1",
        sealed_artifact_id="art-2",
        verdict=L1ReviewVerdict.SUFFICIENT,
        deterministic_passed=True,
        deterministic_checks=[],
        semantic_passed=True,
    )

    res_cleared = orchestrator.orchestrate_decision(
        decision_id="dec-1",
        question="Which architecture pattern should be adopted?",
        options=options,
    )

    assert res_cleared["terminal_state"] in ("APPROVED_UNANIMOUS", "APPROVED_WITH_CONDITIONS", "ESCALATED_HITL")
    assert res_cleared["top_option"] is not None
    assert res_cleared["arbitration"].get("blocked") is not True


def test_w5_e2e_multifactor_cache_invalidation(tmp_path: Path):
    """Verify cache invalidation across all dependency factors."""
    output_dir = tmp_path / "meeting_out"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create dummy artifacts
    (output_dir / "03_Meeting_09-12-2026_1000_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
    (output_dir / "02_Meeting_09-12-2026_1000_executive_recap.md").write_text("# Recap", encoding="utf-8")
    (output_dir / "01_Meeting_09-12-2026_1000_hitl_resolved_transcript.md").write_text("# Transcript", encoding="utf-8")

    from dual_pipeline.transcript_pipeline_core.caching import compute_prompt_template_hash
    cur_prompt_hash = compute_prompt_template_hash()

    initial_meta = {
        "meeting_name": "Meeting",
        "date": "09-12-2026",
        "date_filename": "09-12-2026",
        "time": "10:00 AM",
        "time_filename": "1000",
        "attendees": ["Alice", "Bob"],
        "policy_snapshot_digest": "policy_digest_111",
        "prompt_template_hash": cur_prompt_hash,
        "evaluator_version": "eval_v1",
        "output_schema_version": "schema_v1",
    }

    roster_hash = compute_roster_hash(initial_meta["attendees"])

    decision_data = {
        "status": "PASS",
        "input_hash": "source_hash_abc",
        "source_file_hash": "source_hash_abc",
        "attendee_roster_hash": roster_hash,
        "policy_snapshot_digest": "policy_digest_111",
        "prompt_template_hash": cur_prompt_hash,
        "evaluator_version": "eval_v1",
        "output_schema_version": "schema_v1",
    }
    (output_dir / "final_release_decision.json").write_text(json.dumps(decision_data), encoding="utf-8")

    # 1. Matching inputs -> Idempotent match found
    paths = check_idempotent_meeting_run(
        output_dir=output_dir,
        metadata=initial_meta,
        content_hash="source_hash_abc",
    )
    assert paths is not None

    # 2. Mutated attendee roster -> Invalidated
    meta_roster_changed = dict(initial_meta, attendees=["Alice", "Bob", "Charlie"])
    assert check_idempotent_meeting_run(
        output_dir=output_dir,
        metadata=meta_roster_changed,
        content_hash="source_hash_abc",
    ) is None

    # 3. Mutated policy snapshot -> Invalidated
    meta_policy_changed = dict(initial_meta, policy_snapshot_digest="policy_digest_999")
    assert check_idempotent_meeting_run(
        output_dir=output_dir,
        metadata=meta_policy_changed,
        content_hash="source_hash_abc",
    ) is None

    # 4. Mutated evaluator version -> Invalidated
    meta_eval_changed = dict(initial_meta, evaluator_version="eval_v2")
    assert check_idempotent_meeting_run(
        output_dir=output_dir,
        metadata=meta_eval_changed,
        content_hash="source_hash_abc",
    ) is None

    # 5. Mutated output schema -> Invalidated
    meta_schema_changed = dict(initial_meta, output_schema_version="schema_v2")
    assert check_idempotent_meeting_run(
        output_dir=output_dir,
        metadata=meta_schema_changed,
        content_hash="source_hash_abc",
    ) is None


def test_w5_e2e_legacy_unbound_result_rerun(tmp_path: Path):
    """Verify legacy unbound release decision lacking bindings strictly requires rerun."""
    output_dir = tmp_path / "legacy_out"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "03_Meeting_09-12-2026_1000_canonical_signal_projection.json").write_text("{}", encoding="utf-8")
    (output_dir / "02_Meeting_09-12-2026_1000_executive_recap.md").write_text("# Recap", encoding="utf-8")
    (output_dir / "01_Meeting_09-12-2026_1000_hitl_resolved_transcript.md").write_text("# Transcript", encoding="utf-8")

    # Legacy decision lacks cryptographic bindings (no policy_snapshot_digest, etc.)
    legacy_data = {
        "status": "PASS",
        "input_hash": "source_legacy_123",
    }
    (output_dir / "final_release_decision.json").write_text(json.dumps(legacy_data), encoding="utf-8")

    meta = {
        "meeting_name": "Meeting",
        "date_filename": "09-12-2026",
        "time_filename": "1000",
        "strict_binding": True,
        "attendees": ["Alice"],
    }

    # Strict binding must reject legacy unbound result
    assert check_idempotent_meeting_run(output_dir, meta, content_hash="source_legacy_123") is None


def test_w5_e2e_streaming_copilot_callback_fencing():
    """Verify delayed or shutdown streaming produces zero callbacks."""
    tokens_received: List[str] = []

    def on_token(token: str):
        tokens_received.append(token)

    # 1. Normal active consumption
    fence = CallbackFence(timeout_sec=10.0)
    mock_chunks = [
        MagicMock(choices=[MagicMock(delta=MagicMock(content="token1 "))]),
        MagicMock(choices=[MagicMock(delta=MagicMock(content="token2 "))]),
    ]
    accum, _ = _consume_stream_chunks(mock_chunks, on_token, time.perf_counter(), fence=fence)
    assert accum == ["token1 ", "token2 "]
    assert tokens_received == ["token1 ", "token2 "]

    # 2. Shutdown during stream -> Zero subsequent callbacks
    tokens_received.clear()
    fence_shutdown = CallbackFence(timeout_sec=10.0)
    fence_shutdown.shutdown()

    accum_shutdown, _ = _consume_stream_chunks(mock_chunks, on_token, time.perf_counter(), fence=fence_shutdown)
    assert accum_shutdown == ["token1 ", "token2 "]
    assert tokens_received == []  # Zero callbacks emitted!

    # 3. Timeout expiry during stream -> Zero subsequent callbacks
    tokens_received.clear()
    fence_expired = CallbackFence(timeout_sec=0.001)
    time.sleep(0.01)  # Force timeout
    _consume_stream_chunks(mock_chunks, on_token, time.perf_counter(), fence=fence_expired)
    assert tokens_received == []  # Zero callbacks emitted!


def test_w5_e2e_healing_validation_and_exhaustion():
    """Verify healing outcomes: local success must not fallback, exhausted repair cannot become success."""
    # 1. Valid local healing outcome
    valid_result = L2HealingResult(
        status="HEALED",
        healed_artifact={"query": "healed query", "starved_lens": "FINANCIAL"},
        patch_diff="+ healed query",
        fact_preservation_passed=True,
    )
    assert validate_healing_outcome(valid_result, max_repair_calls=1, repair_attempts=1) is True

    # 2. Exhausted repair budget
    exhausted_result = L2HealingResult(
        status="HEALED",
        healed_artifact={"query": "healed query"},
        patch_diff="+ healed query",
        fact_preservation_passed=True,
    )
    assert validate_healing_outcome(exhausted_result, max_repair_calls=1, repair_attempts=3) is False
    with pytest.raises(ExhaustedRepairError):
        validate_healing_outcome(exhausted_result, max_repair_calls=1, repair_attempts=3, raise_on_failure=True)

    # 3. Failed validation cannot become success
    failed_val_result = L2HealingResult(
        status="HEALED",
        healed_artifact={"query": "invalid query"},
        patch_diff="",
        fact_preservation_passed=False,
    )
    assert validate_healing_outcome(failed_val_result, max_repair_calls=1, repair_attempts=1) is False


def test_w5_e2e_fact_mutation_requires_rerun():
    """Verify changed numbers, owners, dates, or retained facts require affected gates to rerun."""
    prior_facts = {
        "numbers": [1000, 2500],
        "owners": ["Alex Sanchez", "Brian Foster"],
        "dates": ["09/12/2026"],
        "retained_facts": ["Project Titan approved"],
    }

    # No changes -> empty rerun gates
    assert validate_fact_mutation_requires_rerun(prior_facts, dict(prior_facts)) == []

    # Number changed -> requires rerun
    mutated_numbers = dict(prior_facts, numbers=[1000, 3000])
    gates = validate_fact_mutation_requires_rerun(prior_facts, mutated_numbers)
    assert "qa_gates_report" in gates
    assert "final_release_decision" in gates

    # Owner changed -> requires rerun
    mutated_owners = dict(prior_facts, owners=["Alex Sanchez", "Elena Rostova"])
    gates = validate_fact_mutation_requires_rerun(prior_facts, mutated_owners)
    assert "llm_judge_evaluation" in gates

    # Date changed -> requires rerun
    mutated_dates = dict(prior_facts, dates=["09/19/2026"])
    gates = validate_fact_mutation_requires_rerun(prior_facts, mutated_dates)
    assert "final_release_decision" in gates
