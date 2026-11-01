"""Adversarial and edge-case tests for Wave 2 Checkpoint Memoizer & Release Gate (CAT-4)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dual_pipeline.canonical_pipeline.checkpoint_memoizer import (
    _digest_matches,
    _is_decision_passing,
    _is_decision_valid_and_matching,
    _is_unverified_partial_dir,
)


# Category 1: Boundary payload (empty string digest vs None digest)
def test_adversarial_empty_string_digest_rejected_when_expected_present() -> None:
    """CAT4-P1-2: Explicit empty string '' digest must not match a non-empty expected digest."""
    assert _digest_matches("", "expected_sha256_abc") is False
    assert _digest_matches("expected_sha256_abc", "expected_sha256_abc") is True
    assert _digest_matches(None, "expected_sha256_abc") is True


# Category 2: Invariant bypass attempt (short_circuit / SKIPPED_DETERMINISTIC_QA_FAILURE without stage seal)
def test_adversarial_unsealed_short_circuit_cannot_pass_decision() -> None:
    """CAT4-P1-2: Unsealed decision with short_circuit=True or SKIPPED_DETERMINISTIC_QA_FAILURE must not pass."""
    dec_short_circuit = {
        "output_1_deterministic_gates_pass": True,
        "short_circuit": True,
        "validation_status": "SKIPPED_DETERMINISTIC_QA_FAILURE",
        "status": "FAIL",
    }
    assert _is_decision_passing(dec_short_circuit, "output_1_pass") is False

    dec_sealed = {
        "output_1_deterministic_gates_pass": True,
        "short_circuit": True,
        "output_1_seal": True,
    }
    assert _is_decision_passing(dec_sealed, "output_1_pass") is True


# Category 3: Malformed input (corrupt JSON in metadata.json and final_release_decision.json)
def test_adversarial_corrupt_metadata_and_decision_json_handled_safely(tmp_path: Path) -> None:
    """Corrupt JSON in metadata.json or final_release_decision.json does not crash _is_unverified_partial_dir."""
    out_p = tmp_path / "corrupt_dir"
    out_p.mkdir()
    (out_p / "metadata.json").write_text("{corrupt json", encoding="utf-8")
    (out_p / "final_release_decision.json").write_text("{also corrupt", encoding="utf-8")
    assert _is_unverified_partial_dir(out_p, "output_1") is False


# Category 4: Rollback & recovery (stage_seals.json PASS overrides failed final_release_decision.json)
def test_adversarial_stage_seals_pass_overrides_failed_release_decision(tmp_path: Path) -> None:
    """When stage_seals.json has PASS for output_1, _is_unverified_partial_dir allows recovery even if overall run failed."""
    out_p = tmp_path / "partial_pass_dir"
    out_p.mkdir()
    (out_p / "metadata.json").write_text(
        json.dumps({"status": "PARTIAL_QA_FAILURE", "partial_deliverables_saved": True}),
        encoding="utf-8",
    )
    (out_p / "stage_seals.json").write_text(
        json.dumps({"stages": {"output_1": {"status": "PASS"}}}),
        encoding="utf-8",
    )
    assert _is_unverified_partial_dir(out_p, "output_1") is False
    assert _is_unverified_partial_dir(out_p, "output_2") is True
