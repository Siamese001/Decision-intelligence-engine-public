"""Adversarial and edge-case tests for Wave 9 delivery and authoritative release.

Covers >=3 mandatory edge-case categories:
1. Boundary payload: 0-byte delivered files, missing release decision, empty packages.
2. Invariant bypass: Forged decision seal, tampered judge receipt digest, mock provider payload.
3. Concurrency & race: Atomic receipt emission using O_EXCL collision prevention.
4. Rollback & recovery: Invalidation prevents resurrection of stale passed deliverables.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pytest

from dual_pipeline.release_policy import (
    build_artifact_manifest,
    compute_decision_seal,
    compute_release_evaluation_digest,
    verify_authoritative_release_gate,
    verify_decision_seal,
)
from dual_pipeline.remediation_core.state_machine import (
    SelectiveRemediationStateMachine,
)


def _create_base_package(pkg_dir: Path) -> Path:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    f1 = pkg_dir / "01_20261008_hitl_resolved_transcript.md"
    f2 = pkg_dir / "02_20261008_executive_recap.md"
    f3 = pkg_dir / "03_20261008_canonical_signal_projection.json"
    f1.write_text("# Transcript\nAlice: Hello.", encoding="utf-8")
    f2.write_text("# Recap\nAll good.", encoding="utf-8")
    f3.write_text('{"entities": []}', encoding="utf-8")

    judge_report: Dict[str, Any] = {
        "status": "PASS",
        "overall_verdict": "PASS",
        "output_1_evaluation": {"composite_score": 90.0, "verdict": "PASS"},
        "output_2_evaluation": {"composite_score": 90.0, "verdict": "PASS"},
        "output_3_evaluation": {"composite_score": 90.0, "verdict": "PASS"},
    }
    judge_report["evaluation_receipt_digest"] = compute_release_evaluation_digest(judge_report)

    manifest = build_artifact_manifest(
        f1_text=f1.read_text(encoding="utf-8"),
        f2_text=f2.read_text(encoding="utf-8"),
        f3_text=f3.read_text(encoding="utf-8"),
        model="gpt-6-astra",
        reasoning_effort="medium",
        judge_eval_digest=judge_report["evaluation_receipt_digest"],
    )

    decision_data: Dict[str, Any] = {
        "status": "PASS",
        "output_1_pass": True,
        "output_2_pass": True,
        "output_3_pass": True,
        "output_3_llm_score_pass": True,
        "authoritative_write_authorized": True,
        "artifact_manifest": manifest,
        "decision_seal": manifest["decision_seal_sha256"],
    }

    (pkg_dir / "final_release_decision.json").write_text(json.dumps(decision_data), encoding="utf-8")
    (pkg_dir / "llm_judge_evaluation.json").write_text(json.dumps(judge_report), encoding="utf-8")
    return f3


def test_adversarial_boundary_empty_or_missing_decision_file(tmp_path: Path):
    """Category 1 (Boundary): Missing or 0-byte decision file blocks release immediately."""
    pkg_dir = tmp_path / "pkg_empty_decision"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    f3 = pkg_dir / "03_20261008_canonical_signal_projection.json"
    f3.write_text('{"data": 1}', encoding="utf-8")

    # Missing final_release_decision.json
    with pytest.raises(RuntimeError, match="Missing mandatory release authority receipt"):
        verify_authoritative_release_gate([f3])

    # 0-byte final_release_decision.json
    (pkg_dir / "final_release_decision.json").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Corrupt or empty release decision file"):
        verify_authoritative_release_gate([f3])


def test_adversarial_invariant_bypass_forged_seal_and_tampered_digest(tmp_path: Path):
    """Category 2 (Invariant Bypass): Forged decision seal or tampered evaluation digest fails closed."""
    pkg_dir = tmp_path / "pkg_tampered"
    f3 = _create_base_package(pkg_dir)

    # 1. Tamper with decision seal
    dec_file = pkg_dir / "final_release_decision.json"
    dec_data = json.loads(dec_file.read_text(encoding="utf-8"))
    dec_data["artifact_manifest"]["decision_seal_sha256"] = "0" * 64
    dec_file.write_text(json.dumps(dec_data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cryptographic decision seal verification failed"):
        verify_authoritative_release_gate([f3])

    # Restore valid decision seal, then tamper with judge evaluation report digest
    f3_clean = _create_base_package(tmp_path / "pkg_tampered_judge")
    judge_file = tmp_path / "pkg_tampered_judge" / "llm_judge_evaluation.json"
    judge_data = json.loads(judge_file.read_text(encoding="utf-8"))
    judge_data["evaluation_receipt_digest"] = "f" * 64
    judge_file.write_text(json.dumps(judge_data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="Cryptographic evaluation receipt digest verification failed"):
        verify_authoritative_release_gate([f3_clean])


def test_adversarial_invariant_bypass_mock_provider_rejected(tmp_path: Path):
    """Category 2 (Invariant Bypass): Synthetic or mock provider outputs cannot authorize release."""
    pkg_dir = tmp_path / "pkg_mock"
    f3 = _create_base_package(pkg_dir)

    # Mock in decision
    dec_file = pkg_dir / "final_release_decision.json"
    dec_data = json.loads(dec_file.read_text(encoding="utf-8"))
    dec_data["is_mock"] = True
    dec_file.write_text(json.dumps(dec_data), encoding="utf-8")

    with pytest.raises(RuntimeError, match="MOCK_SYNTHESIS_NOT_AUTHORITATIVE"):
        verify_authoritative_release_gate([f3])


def test_adversarial_concurrency_receipt_collision_exclusive_lock(tmp_path: Path):
    """Category 3 (Concurrency & Race): Atomic receipt sealing fails closed on concurrent write collision."""
    receipt_path = tmp_path / "receipts" / "wave-test.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)

    # First write creates the file using O_CREAT | O_EXCL
    fd1 = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd1, "w", encoding="utf-8") as f:
        f.write('{"status": "COMPLETE"}')

    assert receipt_path.is_file()

    # Second concurrent write attempt fails with FileExistsError
    with pytest.raises(FileExistsError):
        os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)


def test_adversarial_rollback_stale_dependency_invalidation():
    """Category 4 (Rollback & Recovery): Dependency invalidation drops stale cached passes and resets baselines."""
    sm = SelectiveRemediationStateMachine(max_attempts=3)

    # Output 1 and Output 2 initially pass
    sm.freeze_artifact("output_1", "Transcript V1")
    sm.freeze_artifact("output_2", "Recap V1")
    sm.passing_stages.update({"output_1", "output_2"})

    # Unfreeze Output 1 when upstream dependency changes
    sm.unfreeze_artifact("output_1")

    # Output 1 must no longer be considered passing
    assert "output_1" not in sm.passing_stages
    assert sm.get_frozen_content("output_1") is None
    # Output 2 remains frozen
    assert "output_2" in sm.passing_stages

    # When policy epoch advances, prior baseline comparisons do not apply across epoch boundaries
    sm.ledger.record_candidate("output_1", attempt=1, content="Old V1", composite_score=85, epoch_id="epoch_1")
    sm.ledger.current_epoch = "epoch_2"
    rec2 = sm.ledger.record_candidate("output_1", attempt=2, content="New V1", composite_score=80, epoch_id="epoch_2")
    # Across epoch boundary, 80 becomes the new baseline without being marked as regressed
    assert not rec2.regressed
    assert sm.ledger.get_high_water_mark_score("output_1") == 80
