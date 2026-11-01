"""
tests/test_screenshot_collateral_adversarial.py

Adversarial and edge-case test suite for screenshot collateral pipeline covering 3 mandatory categories:
1. Boundary payload: 0-byte image files, empty transcripts, non-image files.
2. Malformed input: Corrupted drop log JSON, malformed timestamps, missing keys.
3. Rollback & recovery: Stale candidate cache invalidation on input/attachment deltas vs clean reuse on identical hashes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dual_pipeline.attachment_core.aligner import align_attachments_with_transcript
from dual_pipeline.attachment_core.detector import discover_attachments, stage_attachments_into_output
from dual_pipeline.attachment_core.image_extractor import extract_image_visual
from dual_pipeline.attachment_core.review_provenance import (
    is_authenticated_drop,
    parse_hms_to_seconds,
)
from dual_pipeline.remediation_core.state_machine import SelectiveRemediationStateMachine


def test_adversarial_boundary_zero_byte_attachment(tmp_path: Path) -> None:
    """Category 1: Boundary payload - 0-byte image handling."""
    zero_file = tmp_path / "empty_screenshot.png"
    zero_file.write_bytes(b"")

    res = extract_image_visual(zero_file)
    assert res["status"] == "FAILED"
    assert res["error_type"] == "EMPTY_FILE"
    assert "0 bytes" in res["error_reason"]


def test_adversarial_boundary_empty_transcript(tmp_path: Path) -> None:
    """Category 1: Boundary payload - empty or whitespace-only transcript."""
    img_file = tmp_path / "system_diag.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nvalid")

    records = stage_attachments_into_output([img_file], tmp_path)
    assert len(records) == 1

    alignment = align_attachments_with_transcript("", records)
    assert len(alignment.mentions) == 0
    assert len(alignment.unreferenced_attachments) == 1


def test_adversarial_malformed_drop_log(tmp_path: Path) -> None:
    """Category 2: Malformed input - corrupted drop log JSON and missing keys."""
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True)
    drop_log = out_dir / "collateral_drop_log.json"
    drop_log.write_text("{this is not valid json!}", encoding="utf-8")

    img_file = tmp_path / "metric.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\ncontent")

    # Staging must survive a corrupted drop log without raising exceptions
    records = stage_attachments_into_output([img_file], out_dir)
    assert len(records) == 1
    # Defaults to BACKGROUND when drop log is malformed
    assert records[0].attribution_mode == "BACKGROUND"


def test_adversarial_malformed_timestamps_and_entries() -> None:
    """Category 2: Malformed input - unparseable timestamps and partial drop entries."""
    assert parse_hms_to_seconds("not-a-timestamp") is None
    assert parse_hms_to_seconds("::") is None
    assert parse_hms_to_seconds(None) is None
    assert parse_hms_to_seconds(1234) is None  # type: ignore

    # Missing fields in drop entry
    assert is_authenticated_drop({}) is False
    assert is_authenticated_drop(None) is False
    assert is_authenticated_drop({"synthetic": True}) is False
    assert is_authenticated_drop({"attribution_mode": "BACKGROUND"}) is False
    assert is_authenticated_drop({"dropped_at_elapsed": "invalid"}) is False


def test_adversarial_rollback_and_cache_invalidation(tmp_path: Path) -> None:
    """Category 3: Rollback & recovery - stale output_3 cache invalidated on delta, preserved when identical."""
    out_dir = tmp_path / "meeting_run"
    out_dir.mkdir(parents=True)

    prior_meta = {
        "meeting_name": "Review Session",
        "input_hash": "sha256:baseline111",
        "attachments": [{"filename": "doc1.pdf", "sha256": "sha_doc1"}],
    }
    (out_dir / "metadata.json").write_text(json.dumps(prior_meta), encoding="utf-8")
    (out_dir / "final_release_decision.json").write_text(
        json.dumps({"output_3_pass": True, "output_1_pass": True}), encoding="utf-8"
    )
    (out_dir / "03_review_session_projection.json").write_text(
        json.dumps({"entities": ["ent1"]}), encoding="utf-8"
    )
    (out_dir / "01_review_session_transcript.md").write_text(
        "# Transcript content", encoding="utf-8"
    )

    # 1. Test identical inputs: prior artifacts should be successfully seeded
    identical_meta = dict(prior_meta)
    sm1 = SelectiveRemediationStateMachine()
    sm1.seed_from_output_dir(str(out_dir), meeting_name="Review Session", metadata=identical_meta)
    assert sm1.is_frozen("output_3") is True
    assert sm1.is_frozen("output_1") is True

    # 2. Test input hash delta: invalidates prior artifacts
    changed_input_meta = dict(prior_meta)
    changed_input_meta["input_hash"] = "sha256:recovered_audio_longer_33m"
    sm2 = SelectiveRemediationStateMachine()
    sm2.seed_from_output_dir(str(out_dir), meeting_name="Review Session", metadata=changed_input_meta)
    assert sm2.is_frozen("output_3") is False
    assert sm2.is_frozen("output_1") is False

    # 3. Test attachment added delta: invalidates prior artifacts
    added_att_meta = dict(prior_meta)
    added_att_meta["attachments"] = [
        {"filename": "doc1.pdf", "sha256": "sha_doc1"},
        {"filename": "screenshot2.png", "sha256": "sha_scr2"},
    ]
    sm3 = SelectiveRemediationStateMachine()
    sm3.seed_from_output_dir(str(out_dir), meeting_name="Review Session", metadata=added_att_meta)
    assert sm3.is_frozen("output_3") is False
