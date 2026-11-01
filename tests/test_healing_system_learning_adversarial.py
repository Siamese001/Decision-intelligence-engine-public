"""Wave 4 Adversarial, Edge-Case & Property-Based Verification Suite.

Covers 5 mandatory edge-case categories per .agents/rules/e2e-and-edge-case-coverage.md:
1. Boundary payloads (empty options, single low-confidence option, exact 0.20 margin boundary, 0.65 floor boundary, Hypothesis property tests).
2. Malformed inputs (corrupt/truncated JSONL in live_outcome_events.jsonl, raw jargon/paths/underscores sanitized for ask_user_question).
3. Concurrency & race (16-worker parallel WAL writes and concurrent reads on HealingDecisionStore without OperationalError: database is locked).
4. Invariant bypass attempts (auto_apply=True proposal rejection, fact-preservation entity deletion rejection, synthetic/unapproved receipt PermissionError).
5. Rollback & recovery (monotonic regression rollback in SelectiveRemediationStateMachine and proposal rollback via engine.rollback_proposal).
"""
from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from dual_pipeline.calibration import create_signed_calibration_approval_receipt as create_calibration_approval_receipt
from dual_pipeline.decision.agents.hitl_arbiter_agent import HitlArbiterAgent
from dual_pipeline.healing_policy import (
    HealingActionClassification,
    arbitrate_decision_margin,
)
from dual_pipeline.hitl_governance_core.question_validator import validate_question_payload
from dual_pipeline.learning_engine import ClosedLoopLearningEngine
from dual_pipeline.learning_loop_core.simulation import simulate_healing_policy_update
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)
from dual_pipeline.storage.healing_decision_store import HealingDecisionStore


# ==============================================================================
# Category 1: Boundary Payloads & Hypothesis Property-Based Tests
# ==============================================================================
def test_boundary_payloads_margin_and_confidence_floor(tmp_path: Path) -> None:
    """Verify exact 0.20 margin boundary, 0.65 floor boundary, empty list, and single low-confidence option."""
    db_path = tmp_path / "learning.sqlite"

    # 1. Empty options -> ESCALATE_HITL
    dec_empty = arbitrate_decision_margin("dec_empty", [], healing_db_path=db_path)
    assert dec_empty.hitl_required is True
    assert dec_empty.action == HealingActionClassification.ESCALATE_HITL

    # 2. Single option below 0.50 -> ESCALATE_HITL
    dec_single_low = arbitrate_decision_margin("dec_single_low", [("Repair option alpha", 0.42)], healing_db_path=db_path)
    assert dec_single_low.hitl_required is True

    # 3. Exact 0.20 margin (0.80 - 0.60 == 0.20) -> ESCALATE_HITL under strict 20% gate
    dec_exact_20 = arbitrate_decision_margin(
        "dec_exact_20",
        [("Primary repair path", 0.80), ("Secondary repair path", 0.60)],
        healing_db_path=db_path,
        calibrate_from_learning=False,
    )
    assert dec_exact_20.hitl_required is True
    assert dec_exact_20.confidence_margin == 0.20
    assert dec_exact_20.ask_user_question_tool_call is not None

    # 4. Margin > 0.20 (0.30) but top confidence < 0.65 floor (0.62 vs 0.32) -> ESCALATE_HITL
    dec_below_floor = arbitrate_decision_margin(
        "dec_below_floor",
        [("Primary repair path", 0.62), ("Secondary repair path", 0.32)],
        healing_db_path=db_path,
        calibrate_from_learning=False,
    )
    assert dec_below_floor.hitl_required is True
    assert dec_below_floor.ask_user_question_tool_call is not None


@settings(max_examples=25, deadline=None)
@given(
    c1=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
    c2=st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
)
def test_property_arbitrate_decision_margin_invariants(c1: float, c2: float) -> None:
    """Property test: for any (c1, c2) in [0, 1]^2, arbitration obeys 20% margin + 65% floor and emits valid ask_user_question when escalated."""
    dec = arbitrate_decision_margin(
        "prop_dec",
        [("Primary repair option", c1), ("Secondary repair option", c2)],
        calibrate_from_learning=False,
        persist_decision=False,
    )
    top_c = max(c1, c2)
    sec_c = min(c1, c2)
    margin = round(top_c - sec_c, 4)
    expected_auto = (margin > 0.20) and (top_c >= 0.65)
    assert dec.hitl_required is (not expected_auto)
    if dec.hitl_required:
        assert dec.question_payload is not None
        assert dec.ask_user_question_tool_call is not None
        val = validate_question_payload(
            question=dec.question_payload["question"],
            options=dec.question_payload["options"],
        )
        assert val.is_valid is True


# ==============================================================================
# Category 2: Malformed Inputs & Jargon Sanitization
# ==============================================================================
def test_malformed_jsonl_and_jargon_injection_resilience(tmp_path: Path) -> None:
    """Verify corrupted JSONL lines in live_outcome_events.jsonl are skipped cleanly and jargon in HITL cards is sanitized."""
    runtime_root = tmp_path / ".local"
    ledger_dir = runtime_root / "ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    db_path = ledger_dir / "learning.sqlite"
    outcome_jsonl = ledger_dir / "live_outcome_events.jsonl"

    # Write mix of truncated JSON, non-dict JSON, empty lines, and 1 valid event
    outcome_jsonl.write_text(
        "\n"
        "{truncated_json_line: 123\n"
        "[1, 2, 3]\n"
        + json.dumps({
            "event_id": "evt-valid-01",
            "run_id": "run-valid-01",
            "subsystem": "healing_mesh",
            "repair_class": "FORENSIC_HEADER_DATE",
            "scope": "output_1",
            "outcome_status": "HEALED",
        })
        + "\n",
        encoding="utf-8",
    )

    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=tmp_path / "proposals")
    decisions = engine.ingest_healing_decisions(db_path=db_path, outcome_ledger_path=outcome_jsonl)
    assert len(decisions) == 1
    assert decisions[0]["repair_class"] == "FORENSIC_HEADER_DATE"

    # Inject raw file paths, snake_case identifiers, and markdown into HitlArbiterAgent
    arbiter = HitlArbiterAgent(ledger_path=ledger_dir / "hitl.json", healing_db_path=db_path)
    res = arbiter.arbitrate_decision(
        decision_id="dec_jargon_01",
        category="healing_policy",
        question="Resolve `dual_pipeline/l2_healer_service.py` SQLite WAL lock_contention?",
        options=[
            {"key": "STRIP_WHITESPACE_OR_PAD", "label": "STRIP_WHITESPACE_OR_PAD", "confidence_score": 0.60},
            {"key": "NORMALIZE_DATE_FORMAT", "label": "NORMALIZE_DATE_FORMAT", "confidence_score": 0.52},
        ],
    )
    assert res["should_surface"] is True
    q_payload = res["atomic_card"]["question_payload"]
    v_res = validate_question_payload(question=q_payload["question"], options=q_payload["options"])
    assert v_res.is_valid is True


# ==============================================================================
# Category 3: 16-Worker Concurrency & WAL Race Stress
# ==============================================================================
def test_healing_decision_store_16_worker_wal_concurrency(tmp_path: Path) -> None:
    """Verify 16 concurrent threads writing and reading HealingDecisionStore encounter zero database lock errors."""
    db_path = tmp_path / "concurrent_learning.sqlite"
    store = HealingDecisionStore(db_path=db_path)

    def _worker(worker_id: int) -> int:
        w_store = HealingDecisionStore(db_path=db_path)
        for i in range(5):
            w_store.record_decision({
                "run_id": f"run-w{worker_id}-{i}",
                "subsystem": "concurrent_healer",
                "component": f"Worker-{worker_id}",
                "scope": "output_1",
                "repair_class": "CONCURRENT_REPAIR",
                "action_taken": "Applied concurrent patch",
                "outcome_status": "HEALED",
                "confidence_top": 0.90,
                "confidence_second": 0.10,
                "ambiguity_margin": 0.80,
            })
        _ = w_store.compute_empirical_posteriors()
        return 5

    with ThreadPoolExecutor(max_workers=16) as pool:
        counts = list(pool.map(_worker, range(16)))

    assert sum(counts) == 80
    all_rows = store.list_decisions(limit=200)
    assert len(all_rows) == 80

    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


# ==============================================================================
# Category 4: Invariant Bypass Attempts
# ==============================================================================
def test_invariant_bypass_attempts_rejected_fail_closed(tmp_path: Path) -> None:
    """Verify auto_apply=True, entity deletion in healing simulation, and synthetic approval receipts fail closed."""
    # 1. auto_apply=True rejected by simulate_healing_policy_update
    bad_auto_apply = {
        "proposal_id": "prop-bypass-auto",
        "proposal_type": "workflow",
        "auto_apply": True,
        "extensions": {
            "com.partner_advisory.nee.learning": {
                "category": "healing_policy",
                "proposed_value": 0.20,
                "invariants_preserved": True,
                "entity_delta": 0,
            }
        },
    }
    sim1 = simulate_healing_policy_update(bad_auto_apply)
    assert sim1["success"] is False
    assert "auto_apply" in sim1["details"]["error"]

    # 2. Fact-preservation violation (entity_delta < 0) rejected
    bad_fact_drop = {
        "proposal_id": "prop-bypass-fact",
        "proposal_type": "workflow",
        "auto_apply": False,
        "extensions": {
            "com.partner_advisory.nee.learning": {
                "category": "healing_policy",
                "proposed_value": 0.20,
                "invariants_preserved": True,
                "entity_delta": -2,
            }
        },
    }
    sim2 = simulate_healing_policy_update(bad_fact_drop)
    assert sim2["success"] is False
    assert "Fact-preservation" in sim2["details"]["error"]

    # 3. Unapproved or synthetic approval receipt raises PermissionError in apply_approved_proposal
    runtime_root = tmp_path / ".local"
    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=tmp_path / "proposals")
    db_path = runtime_root / "ledger" / "learning.sqlite"
    h_store = HealingDecisionStore(db_path=db_path)
    for i in range(2):
        h_store.record_decision({
            "run_id": f"r-{i}",
            "subsystem": "l2_healer_service",
            "repair_class": "STRIP_WHITESPACE_OR_PAD",
            "scope": "l2_extraction",
            "outcome_status": "HEALED",
            "confidence_top": 0.90,
            "confidence_second": 0.10,
            "ambiguity_margin": 0.80,
        })
    cycle = engine.run_healing_learning_cycle(db_path=db_path, min_occurrences=2)
    prop = cycle["proposals"][0]

    with pytest.raises(PermissionError):
        engine.apply_approved_proposal(prop, None)

    forged_receipt = {"receipt_id": "forged", "approved_by": "auto_approved_by_system_message"}
    with pytest.raises(PermissionError):
        engine.apply_approved_proposal(prop, forged_receipt)


# ==============================================================================
# Category 5: Rollback & Recovery
# ==============================================================================
def test_monotonic_regression_rollback_and_proposal_rollback(tmp_path: Path, monkeypatch) -> None:
    """Verify SelectiveRemediationStateMachine rolls back regressed candidates and ClosedLoopLearningEngine rolls back applied proposals."""
    runtime_root = tmp_path / ".local"
    db_path = runtime_root / "ledger" / "learning.sqlite"
    monkeypatch.setenv("NEE_TEST_LEARNING_SQLITE", str(db_path))
    monkeypatch.setenv("NEE_LEARNING_SQLITE_PATH", str(db_path))

    # 1. Monotonic regression rollback in SelectiveRemediationStateMachine
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    sm.record_candidate("output_1", "Good content v1", 92, verdict="PASS")
    sm.current_attempt = 2
    sm.record_candidate("output_1", "Regressed content v2", 65, verdict="FAIL")
    sm._check_monotonic_regressions({"output_1": "Regressed content v2"})

    h_store = HealingDecisionStore(db_path=db_path)
    rollbacks = h_store.list_decisions(repair_class="MONOTONIC_REGRESSION_ROLLBACK")
    assert len(rollbacks) >= 1
    assert any(r["outcome_status"] == "HEALED" and r["subsystem"] == "remediation_state_machine" for r in rollbacks)

    # 2. Proposal rollback via engine.rollback_proposal()
    for i in range(2):
        h_store.record_decision({
            "run_id": f"r-rb-{i}",
            "subsystem": "surgical_remediator",
            "repair_class": "SURGICAL_APPENDIX",
            "scope": "output_2",
            "outcome_status": "HEALED",
            "confidence_top": 0.93,
            "confidence_second": 0.07,
            "ambiguity_margin": 0.86,
        })
    engine = ClosedLoopLearningEngine(runtime_root=runtime_root, proposals_dir=tmp_path / "proposals")
    cycle = engine.run_healing_learning_cycle(db_path=db_path, min_occurrences=2)
    prop = next(p for p in cycle["proposals"] if p["proposal_type"] == "workflow")
    receipt = create_calibration_approval_receipt(prop, approved_by="operator:amit.ayer", rationale="Approve then test rollback")
    engine.apply_approved_proposal(prop, receipt)

    rb_res = engine.rollback_proposal(prop["proposal_id"])
    assert rb_res["status"] == "ROLLED_BACK"
