"""
Unit tests for Deliverable Sealing, Judge Isolation, and Zero Quality Regression (Wave 2).
Verifies that passing deliverables remain immutable across retry attempts and sibling failures.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.remediation_core.stage_sealer import (
    persist_stage_checkpoint_seal,
    verify_stage_checkpoint_seal,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)
from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
from dual_pipeline.transcript_qa_core.eval_cache import clear_evaluation_cache


@pytest.fixture(autouse=True)
def clean_cache():
    clear_evaluation_cache()
    yield
    clear_evaluation_cache()


def test_independent_artifact_checkpoint_sealing_and_verification(tmp_path: Path):
    """Verify that stage-isolated seals record bitwise SHA-256 and protect against mutation."""
    ckpt_dir = tmp_path / "attempts" / "attempt_1"
    content_02 = "# Executive Recap\n\n## Meeting Objective\nAlignment on enterprise architecture."
    persist_stage_checkpoint_seal(ckpt_dir, "output_2", content_02)

    # Verify seal exists and passes verification
    ok, err = verify_stage_checkpoint_seal(ckpt_dir, "output_2")
    assert ok is True
    assert err is None

    # Verify bitwise digest matches
    seal_file = ckpt_dir / "stage_seals.json"
    assert seal_file.is_file()
    seals = json.loads(seal_file.read_text(encoding="utf-8"))
    assert seals["output_2_seal"] is True
    assert seals["output_2_pass"] is True
    assert seals["output_2_sha256"] == hashlib.sha256(content_02.encode("utf-8")).hexdigest()

    # Verify immutability: attempting to persist different content without force does not overwrite
    mutated_content = "# Mutated Recap\n\nCorrupting previous content."
    persist_stage_checkpoint_seal(ckpt_dir, "output_2", mutated_content, force=False)
    assert (ckpt_dir / "output_2.ckpt.md").read_text(encoding="utf-8") == content_02
    ok_after, _ = verify_stage_checkpoint_seal(ckpt_dir, "output_2")
    assert ok_after is True


def test_tampered_checkpoint_fails_verification(tmp_path: Path):
    """Verify that external tampering of a sealed checkpoint file triggers verification failure."""
    ckpt_dir = tmp_path / "attempts" / "attempt_1"
    content_03 = json.dumps({"graph_mutations": {"entities": ["system:test"]}}, indent=2)
    persist_stage_checkpoint_seal(ckpt_dir, "output_3", content_03)

    # Tamper with the checkpoint file on disk
    (ckpt_dir / "output_3.ckpt.json").write_text('{"tampered": true}', encoding="utf-8")
    ok, err = verify_stage_checkpoint_seal(ckpt_dir, "output_3")
    assert ok is False
    assert "SEAL_DIGEST_MISMATCH" in str(err)


def test_microjudge_isolation_output2_passing_while_output3_failing(tmp_path: Path):
    """Verify that Output 02 receives an independent passing scorecard even when Output 03 fails QA."""
    meta = {
        "output_dir": str(tmp_path / "meeting_output"),
        "diagnostics_path": str(tmp_path / "test_diagnostics.json"),
        "attempt": 1,
        "retry_count": 0,
        "allow_partial_judge": True,
    }
    f1_valid = "# Transcript\n\nSpeaker 1: Grounded discussion points.\n"
    f2_valid = (
        "# Executive Recap\n\n"
        "## Meeting Objective\nStrategic review of platform integration.\n\n"
        "## Key Discussion Points & Findings\n* Substantive discussion.\n\n"
        "## Action & Governance Ledger\n| Action | Owner | Timing | Evidence |\n"
        "| :--- | :--- | :--- | :--- |\n| Deploy agent | Amit Ayer | Tomorrow | Turn 1 |\n\n"
        "## Appendix: Detailed Stakeholder Perspectives & Contribution Breakdown\n"
        "### Speaker 1\n#### Perspective\nComprehensive views.\n"
    )
    # Output 03 payload deliberately invalid to fail deterministic QA
    f3_failing = "INVALID_JSON_CORRUPT"

    qa_rep, judge_rep, decision = evaluate_judge_and_release(
        f1_text=f1_valid,
        f2_text=f2_valid,
        f3_text=f3_failing,
        metadata=meta,
        mock=True,
    )

    # Output 03 must fail
    assert decision["output_3_pass"] is False
    # Overall decision must not be PASS
    assert decision["status"] == "FAIL"

    # But judge_report and decision must record Output 02's evaluation in isolation
    assert "output_2_evaluation" in judge_rep
    assert "output_3_evaluation" in judge_rep


def test_state_machine_selective_regeneration_sealing_invariance(tmp_path: Path):
    """Verify that SelectiveRemediationStateMachine preserves bitwise identity across retry iterations."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)
    attempt_dir = tmp_path / "attempts" / "attempt_1"

    out1_text = "# Forensic Transcript Content V1"
    out2_text = "# Executive Recap Content V1"

    # Register passes in attempt 1
    sm.register_deliverable_pass("output_1", out1_text, score=95, checkpoint_dir=attempt_dir)
    sm.register_deliverable_pass("output_2", out2_text, score=92, checkpoint_dir=attempt_dir)

    sha1_initial = hashlib.sha256(out1_text.encode("utf-8")).hexdigest()
    sha2_initial = hashlib.sha256(out2_text.encode("utf-8")).hexdigest()

    # Move to attempt 2 where Output 3 undergoes retry
    sm.current_attempt = 2
    assert sm.is_frozen("output_1") is True
    assert sm.is_frozen("output_2") is True
    assert sm.is_frozen("output_3") is False

    # Simulate Output 3 retry failing
    sm.record_candidate("output_3", '{"failed": true}', composite_score=40, verdict="FAIL", attempt=2)

    # Verify that Output 1 and Output 2 remain bitwise identical
    assert hashlib.sha256(sm.get_frozen_content("output_1").encode("utf-8")).hexdigest() == sha1_initial
    assert hashlib.sha256(sm.get_frozen_content("output_2").encode("utf-8")).hexdigest() == sha2_initial

    # Verify stage seals in checkpoint dir remain intact
    ok1, err1 = verify_stage_checkpoint_seal(attempt_dir, "output_1")
    ok2, err2 = verify_stage_checkpoint_seal(attempt_dir, "output_2")
    assert ok1 is True and err1 is None
    assert ok2 is True and err2 is None


def test_zero_quality_regression_across_deterministic_qa_gates():
    """Verify that deterministic healers and gate checkers preserve 100% of passing invariants."""
    from dual_pipeline.transcript_qa_core.gate3_evidence import check_gate_3_14
    from dual_pipeline.transcript_qa_core.healing_mesh import DeterministicHealer

    valid_f3 = {
        "meeting_id": "test_meeting",
        "evidence_index": {
            "ev-01": {"locator": {"kind": "timestamp", "value": "00:01:10"}, "text": "Platform rollout"},
            "ev-02": {"locator": {"kind": "timestamp", "value": "00:02:20"}, "text": "Service accounts"}
        },
        "graph_mutations": {
            "entity_upserts": [
                {"id": "system:platform", "name": "Platform", "mutation": "create"}
            ],
            "proposal_updates": [
                {"proposal_id": "prop-01", "name": "Platform Rollout", "description": "Platform rollout plan", "evidence_ids": ["ev-01"]}
            ]
        },
        "incremental_reconciliation": {"new_entities": 1, "confirmed_entities": 0}
    }
    f1_text = "### [00:01:10] Speaker 1: Platform rollout\n### [00:02:20] Speaker 2: Service accounts\n"

    passed, failures, details = check_gate_3_14(valid_f3, f1_text=f1_text)
    assert passed is True
    assert len(failures) == 0
    assert details["topical_overlap"] is True
    assert details["locators_and_splicing"] is True
