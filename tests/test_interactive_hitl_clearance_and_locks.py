"""
tests/test_interactive_hitl_clearance_and_locks.py

Unit tests for Wave 3: Interactive HITL Clearance Protocol and Stage Execution Lock.
Verifies no fake operator resolutions, fail-closed options, and stage dependencies.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dual_pipeline.ambiguity_core.models import (
    AmbiguityDomain,
    AmbiguityRecord,
    AmbiguityStatus,
    CandidateOption,
)
from dual_pipeline.clearance_lifecycle_core.c10_register import AmbiguityLockRegister
from dual_pipeline.clearance_lifecycle_core.phase2_clearance import Phase2ClearanceCoordinator
from dual_pipeline.transcript_pipeline_core.stage_coordinator import _execute_l2_concurrent_fork


def _make_ambiguity_record(amb_id: str = "amb_test_01", margin: float = 0.10) -> AmbiguityRecord:
    c1 = CandidateOption(option_id="opt_a", text="Candidate A", confidence=0.55, rationale="Rationale A", is_recommended=True)
    c2 = CandidateOption(option_id="opt_b", text="Candidate B", confidence=0.45, rationale="Rationale B")
    return AmbiguityRecord.create(
        ambiguity_id=amb_id,
        domain=AmbiguityDomain.D1_SPEAKER_ATTRIBUTION,
        segment_id="turn_01",
        source_excerpt="Speaker: Hello world",
        candidates=[c1, c2],
        is_material=True,
        threshold=0.25,
    )


def test_hitl_coordinator_authentic_operator_callback():
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger_path = Path(tmp_dir) / "hitl_decision_ledger.json"
        reg = AmbiguityLockRegister()
        rec = _make_ambiguity_record()
        reg.append_lock(rec)

        def mock_callback(r: AmbiguityRecord) -> str:
            return "opt_b"

        coordinator = Phase2ClearanceCoordinator(
            register=reg,
            ledger_path=ledger_path,
            hitl_callback=mock_callback,
        )

        cleared = coordinator.clear_queue([rec])
        assert len(cleared) == 1
        assert cleared[0].status == AmbiguityStatus.RESOLVED_HITL
        assert cleared[0].resolved_by == "operator"
        assert cleared[0].selected_option_id == "opt_b"
        assert reg.is_cleared() is True
        assert reg.has_open_locks() is False


def test_hitl_coordinator_unhandled_without_callback_no_fake_operator():
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger_path = Path(tmp_dir) / "hitl_decision_ledger.json"
        reg = AmbiguityLockRegister()
        rec = _make_ambiguity_record()
        reg.append_lock(rec)

        coordinator = Phase2ClearanceCoordinator(
            register=reg,
            ledger_path=ledger_path,
            hitl_callback=None,
            fail_closed_on_unhandled=False,
        )

        cleared = coordinator.clear_queue([rec])
        assert len(cleared) == 1
        assert cleared[0].status == AmbiguityStatus.UNRESOLVED_EXPLICIT
        assert cleared[0].resolved_by == "fallback_heuristic"
        assert cleared[0].resolved_by != "operator"


def test_hitl_coordinator_fail_closed_on_unhandled():
    with tempfile.TemporaryDirectory() as tmp_dir:
        ledger_path = Path(tmp_dir) / "hitl_decision_ledger.json"
        reg = AmbiguityLockRegister()
        rec = _make_ambiguity_record()
        reg.append_lock(rec)

        coordinator = Phase2ClearanceCoordinator(
            register=reg,
            ledger_path=ledger_path,
            hitl_callback=None,
            fail_closed_on_unhandled=True,
        )

        with pytest.raises(RuntimeError, match="HITL resolution failed closed"):
            coordinator.clear_queue([rec])


def test_stage_execution_lock_blocks_downstream_on_open_locks():
    reg = AmbiguityLockRegister()
    rec = _make_ambiguity_record()
    reg.append_lock(rec)

    assert reg.has_open_locks() is True

    meta = {"c10_register": reg}
    mock_pipeline = MagicMock()
    mock_recorder = MagicMock()

    with pytest.raises(RuntimeError, match="Stage Execution Lock"):
        _execute_l2_concurrent_fork(
            pipeline=mock_pipeline,
            input_s2_s3="sample text",
            pa_context={},
            meta=meta,
            rerun_s2=True,
            rerun_s3=True,
            recorder=mock_recorder,
            notify_step=lambda *args: None,
            attempt=1,
            current_recap="",
            current_graph="",
            artifacts={},
        )


def test_mutation_authorization_publication_lock_enforcement():
    from dual_pipeline.hitl_governance_core.host_approval_dispatcher import (
        MutationProposal,
        persist_approval_request,
    )
    from dual_pipeline.hitl_governance_core.mutation_authorization import (
        MutationAuthorizationError,
        mark_mutation_activated,
        validate_mutation_authorization,
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        rt_root = Path(tmp_dir)
        out_dir = rt_root / "output"
        out_dir.mkdir(parents=True)

        meta = {"meeting_name": "Test Meeting", "runtime_root": str(rt_root)}
        written = {}

        # 1. Missing approval fails closed
        with pytest.raises(MutationAuthorizationError, match="AUTHORIZATION_DENIED_MISSING_APPROVAL"):
            validate_mutation_authorization(out_dir, meta, written, runtime_root=rt_root)

        # 2. Pending approval fails closed
        rec = {
            "request_id": "req-1",
            "meeting_name": "Test Meeting",
            "status": "AWAITING_APPROVAL",
            "operator_origin": "USER_INPUT",
            "proposal_digest": "dig-1",
        }
        persist_approval_request(rec, rt_root)
        with pytest.raises(MutationAuthorizationError, match="AUTHORIZATION_DENIED_PENDING_APPROVAL"):
            validate_mutation_authorization(out_dir, meta, written, runtime_root=rt_root, approval_receipt=rec)

        # 3. Approved proposal succeeds and transitions to APPLYING
        rec["status"] = "APPROVED"
        persist_approval_request(rec, rt_root)
        res = validate_mutation_authorization(out_dir, meta, written, runtime_root=rt_root, approval_receipt=rec)
        assert res["authorized"] is True
        assert res["status"] == "APPLYING"

        # 4. Mark activated and verify idempotent recovery
        mark_mutation_activated("req-1", rt_root, activated_generation_id="gen-42")
        # Write active_pair matching gen-42
        (rt_root / "active_pair.json").write_text('{"generation_id": "gen-42"}', encoding="utf-8")
        rec_act = {"request_id": "req-1", "status": "ACTIVATED", "activated_generation_id": "gen-42", "operator_origin": "USER_INPUT"}
        recov = validate_mutation_authorization(out_dir, meta, written, runtime_root=rt_root, approval_receipt=rec_act)
        assert recov["authorized"] is True
        assert recov["idempotent"] is True
        assert recov["status"] == "ACTIVATED"
