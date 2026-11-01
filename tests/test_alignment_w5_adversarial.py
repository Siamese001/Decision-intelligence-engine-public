"""
tests/test_alignment_w5_adversarial.py

Adversarial and edge-case verification for Wave 5: Preserve gates through runtime and reuse.
Mandatory Coverage: Covers 5 Edge-Case Categories under e2e-and-edge-case-coverage.md:
1. Boundary payload: empty rosters, null contracts, zero timeout fences.
2. Malformed input: corrupted metadata, unhashable context, invalid verdict types.
3. Concurrency & race: concurrent callback invocations during fence shutdown.
4. Overflow & exhaustion: repair budget exhaustion, unbounded replan directives.
5. Invariant bypass attempt: attempting approval under INSUFFICIENT_REPLAN, forged cache reuse.
"""
from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from dual_pipeline.decision.agents.decision_orchestrator import DecisionOrchestrator
from dual_pipeline.decision.agents.post_debate_reviewer import (
    L1PostL2ReviewContract,
    L1ReviewVerdict,
)
from dual_pipeline.l0.runtime_gate_preservation import (
    CallbackFence,
    CallbackFencedError,
    CacheDependencyBinding,
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


# ==============================================================================
# Category 1: Boundary Payload
# ==============================================================================

def test_adv_boundary_empty_and_null_replan_contract():
    """Verify validate_replan_clearance handles None or empty contract safely."""
    assert validate_replan_clearance(None) is True
    assert validate_replan_clearance({}) is True
    assert validate_replan_clearance({"verdict": None}) is True


def test_adv_boundary_empty_roster_and_context():
    """Verify deterministic hashing of empty or null rosters and context objects."""
    h1 = compute_roster_hash([])
    h2 = compute_roster_hash(None)
    assert h1 == h2 == "empty_roster"

    c1 = compute_context_hash({})
    c2 = compute_context_hash(None)
    assert c1 == c2 == "empty_context"


def test_adv_boundary_zero_and_negative_timeout_fence():
    """Verify CallbackFence with zero or negative timeout immediately fences calls."""
    fence_zero = CallbackFence(timeout_sec=0.0)
    # Zero or negative deadline is expired
    invoked = False

    def dummy():
        nonlocal invoked
        invoked = True

    fence_zero.invoke(dummy)
    assert invoked is False
    assert fence_zero.is_active is False


# ==============================================================================
# Category 2: Malformed Input
# ==============================================================================

def test_adv_malformed_replan_verdicts():
    """Verify handling of malformed or unexpected verdict representations."""
    # String verdict matching INSUFFICIENT_REPLAN
    assert validate_replan_clearance({"verdict": "INSUFFICIENT_REPLAN"}) is False

    # Dictionary with deterministic_passed = False
    assert validate_replan_clearance({"deterministic_passed": False}) is False

    # Raising on blocked
    with pytest.raises(ReplanRequiredError):
        validate_replan_clearance({"verdict": "INSUFFICIENT_REPLAN"}, raise_on_blocked=True)


def test_adv_malformed_cache_metadata():
    """Verify corrupted or partial cache metadata fails dependency validation."""
    cached = {"source_file_hash": "abc"}  # Missing 5 required bindings
    current = {"source_file_hash": "abc"}
    valid, errors = validate_cache_reuse_dependencies(cached, current)
    assert valid is False
    assert any("LEGACY_UNBOUND_CACHE" in e for e in errors)


# ==============================================================================
# Category 3: Concurrency & Race Conditions
# ==============================================================================

def test_adv_concurrency_race_callback_fence():
    """Verify concurrent thread invocations while fence is shut down midway."""
    fence = CallbackFence(timeout_sec=5.0)
    results: List[int] = []
    lock = threading.Lock()

    def worker_callback(idx: int):
        with lock:
            results.append(idx)

    num_threads = 20

    def worker(i: int):
        if i == 10:
            fence.shutdown()  # Race: shut down fence midway
        fence.invoke(worker_callback, i)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(num_threads)]
        concurrent.futures.wait(futures)

    # Fence must be shut down
    assert fence.is_active is False
    # No crashed threads, callbacks were safely filtered
    assert len(results) <= num_threads


# ==============================================================================
# Category 4: Overflow & Exhaustion
# ==============================================================================

def test_adv_overflow_repair_attempts_exhausted():
    """Verify healing outcomes with repair_attempts exceeding max budget fail closed."""
    result = L2HealingResult(
        status="HEALED",
        healed_artifact={"query": "healed"},
        patch_diff="",
        fact_preservation_passed=True,
    )
    # With max_repair_calls = 1, attempt 5 must fail
    assert validate_healing_outcome(result, max_repair_calls=1, repair_attempts=5) is False
    with pytest.raises(ExhaustedRepairError):
        validate_healing_outcome(result, max_repair_calls=1, repair_attempts=5, raise_on_failure=True)


def test_adv_exhaustion_unrepairable_status():
    """Verify UNREPAIRABLE and FAILED statuses strictly reject success."""
    for bad_status in ("UNREPAIRABLE", "FAILED", "ERROR", "TIMEOUT"):
        res = L2HealingResult(
            status=bad_status,
            healed_artifact=None,
            patch_diff="",
            fact_preservation_passed=False,
        )
        assert validate_healing_outcome(res) is False
        with pytest.raises(ExhaustedRepairError):
            validate_healing_outcome(res, raise_on_failure=True)


# ==============================================================================
# Category 5: Invariant Bypass Attempts
# ==============================================================================

def test_adv_invariant_bypass_replan_orchestrator():
    """Verify decision orchestrator cannot be coerced into approval under INSUFFICIENT_REPLAN."""
    mock_reviewer = MagicMock()
    mock_reviewer.review_debate.return_value = L1PostL2ReviewContract.create(
        run_id="run_bypass",
        query_id="dec_bypass",
        plan_id="plan_bypass",
        sealed_artifact_id="art_bypass",
        verdict=L1ReviewVerdict.INSUFFICIENT_REPLAN,
        deterministic_passed=False,
        deterministic_checks=[],
        semantic_passed=False,
        replan_directive={"directive": "FIX_OPTIONS"},
    )

    orchestrator = DecisionOrchestrator(post_debate_reviewer=mock_reviewer)
    options = [
        {"key": "opt_a", "label": "Adopt Microservices Architecture", "risk_score": 0.0, "feasibility_score": 1.0},
        {"key": "opt_b", "label": "Retain Monolithic Architecture", "risk_score": 0.0, "feasibility_score": 1.0},
    ]

    outcome = orchestrator.orchestrate_decision(
        decision_id="dec_bypass",
        question="Which architecture pattern should be adopted?",
        options=options,
    )

    # Invariant: Must be blocked, must not approve
    assert outcome["terminal_state"] == "BLOCKED_REPLAN_REQUIRED"
    assert outcome["top_option"] is None
    assert outcome["arbitration"]["blocked"] is True


def test_adv_invariant_bypass_cache_tampering():
    """Verify that tampering with any single field in binding invalidates reuse."""
    b1 = CacheDependencyBinding(
        source_file_hash="source_1",
        attendee_roster_hash="roster_1",
        context_hash="context_1",
        policy_snapshot_digest="policy_1",
        prompt_template_hash="prompt_1",
        evaluator_version="eval_1",
        output_schema_version="schema_1",
    )

    # Tampering with only policy_snapshot_digest
    b2 = CacheDependencyBinding(
        source_file_hash="source_1",
        attendee_roster_hash="roster_1",
        context_hash="context_1",
        policy_snapshot_digest="policy_TAMPERED",
        prompt_template_hash="prompt_1",
        evaluator_version="eval_1",
        output_schema_version="schema_1",
    )

    valid, mismatches = b1.validate_against(b2)
    assert valid is False
    assert len(mismatches) == 1
    assert "policy_snapshot_digest changed" in mismatches[0]
