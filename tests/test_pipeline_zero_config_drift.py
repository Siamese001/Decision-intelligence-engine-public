"""
tests/test_pipeline_zero_config_drift.py

Verifies that the Unified Agentic Pipeline enforces zero configuration drift:
1. Captures cryptographic configuration hashes (config_sha256 and parameters_digest).
2. Pins active configuration to PipelineExecutionReceipt.
3. Detects configuration drift during stage transitions and fails closed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from dual_pipeline.config_hasher import compute_config_canonical_sha256
from dual_pipeline.parameters import compute_parameters_digest
from dual_pipeline.pipeline_runtime_config import (
    capture_runtime_config,
    verify_zero_config_drift,
)
from dual_pipeline.unified_agentic_pipeline import (
    PipelineSequencingViolationError,
    PipelineStage,
    UnifiedAgenticPipelineTracker,
)


def test_pipeline_runtime_config_capture() -> None:
    """Verify that capture_runtime_config captures valid, reproducible hashes."""
    cfg = capture_runtime_config()
    assert len(cfg.config_sha256) == 64, f"Invalid config_sha256: {cfg.config_sha256}"
    assert len(cfg.parameters_digest) == 64, f"Invalid parameters_digest: {cfg.parameters_digest}"

    expected_sha = compute_config_canonical_sha256()
    assert cfg.config_sha256 == expected_sha

    expected_params = compute_parameters_digest()
    assert cfg.parameters_digest == expected_params


def test_pipeline_runtime_config_drift_detection() -> None:
    """Verify that verify_zero_config_drift catches divergent hashes."""
    cfg = capture_runtime_config()

    # Affirmative case: matches
    valid, msg = verify_zero_config_drift(cfg.config_sha256, cfg.parameters_digest)
    assert valid is True
    assert msg == "ZERO_CONFIG_DRIFT_CONFIRMED"

    # Drift case: modified config hash
    bogus_hash = "0" * 64
    valid, msg = verify_zero_config_drift(bogus_hash, cfg.parameters_digest)
    assert valid is False
    assert "CONFIG_DRIFT_DETECTED" in msg

    # Drift case: modified parameters digest
    valid_p, msg_p = verify_zero_config_drift(cfg.config_sha256, bogus_hash)
    assert valid_p is False
    assert "PARAMETERS_DRIFT_DETECTED" in msg_p


def test_pipeline_tracker_captures_config_in_receipt(tmp_path: Path) -> None:
    """Verify that UnifiedAgenticPipelineTracker captures config hashes in execution receipt."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="TestPipeline",
        input_path=str(tmp_path / "input.txt"),
    )

    assert len(tracker.config_sha256) == 64
    assert len(tracker.parameters_digest) == 64

    tracker.start_stage(PipelineStage.U0_INGRESS)
    tracker.complete_stage(PipelineStage.U0_INGRESS, details={"input_hash": "abc12345"})

    receipt = tracker.build_receipt(output_dir=tmp_path)
    assert receipt.config_sha256 == tracker.config_sha256
    assert receipt.parameters_digest == tracker.parameters_digest

    receipt_dict = receipt.to_dict()
    assert "config_sha256" in receipt_dict
    assert "parameters_digest" in receipt_dict
    assert receipt_dict["config_sha256"] == tracker.config_sha256


def test_pipeline_tracker_fails_closed_on_config_drift(tmp_path: Path) -> None:
    """Verify tracker raises PipelineSequencingViolationError when config changes mid-run."""
    tracker = UnifiedAgenticPipelineTracker(
        pipeline_name="TestPipeline",
        input_path=str(tmp_path / "input.txt"),
    )

    tracker.start_stage(PipelineStage.U0_INGRESS)

    # Intentionally corrupt the pinned config hash to simulate mid-pipeline config tampering
    tracker.config_sha256 = "f" * 64

    with pytest.raises(PipelineSequencingViolationError, match="Configuration drift violation"):
        tracker.complete_stage(PipelineStage.U0_INGRESS)
