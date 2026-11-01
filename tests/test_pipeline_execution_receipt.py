"""
tests/test_pipeline_execution_receipt.py

Verifies that the Unified Agentic Pipeline emits an immutable runtime execution receipt
(pipeline_execution_receipt.json) documenting the complete traversal of all 7 stages:
U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 (exit) -> evals.
"""
from __future__ import annotations

import json
from pathlib import Path

from dual_pipeline.transcript_pipeline_core.pipeline import RawTranscriptPipeline
from dual_pipeline.unified_agentic_pipeline import PIPELINE_STAGE_SEQUENCE


def test_pipeline_run_emits_complete_execution_receipt(tmp_path: Path):
    """Verify that RawTranscriptPipeline.run creates and populates pipeline_execution_receipt.json."""
    pipeline = RawTranscriptPipeline(provider_type="offline")
    metadata = {
        "meeting_name": "Unified Pipeline Execution Audit",
        "date": "09/17/2026",
        "time": "10:00 AM - 10:05 AM",
        "classification": "internal",
    }
    input_file = tmp_path / "transcript.txt"
    input_file.write_text(
        "**Amit Ayer:** Initiating unified pipeline execution receipt verification.\n"
        "**Reid Schenk:** Confirmed, all 7 stages active.",
        encoding="utf-8",
    )
    out_dir = tmp_path / "receipt_output"

    result = pipeline.run(str(input_file), metadata, str(out_dir))

    # Result dict must contain execution_receipt pointer
    assert "execution_receipt" in result, "execution_receipt missing from pipeline result"
    receipt_path = Path(result["execution_receipt"])
    assert receipt_path.exists(), f"Receipt file does not exist at {receipt_path}"
    assert receipt_path.name == "pipeline_execution_receipt.json"

    receipt_data = json.loads(receipt_path.read_text(encoding="utf-8"))

    # Verify receipt attributes
    assert receipt_data["pipeline_name"] == "RawTranscriptPipeline"
    assert receipt_data["terminal_outcome"] == "COMPLETE"
    assert receipt_data["framework"] == "U0 -> L1 -> L0 -> C0-PA -> L2 -> X1-X3 (exit) -> evals"
    assert receipt_data["input_hash"] != ""

    # Verify all 7 stages are explicitly recorded with PASS status and durations
    stages = receipt_data["stages"]
    assert len(stages) == 7, f"Expected 7 stages in receipt, got {len(stages)}: {list(stages.keys())}"

    for stage in PIPELINE_STAGE_SEQUENCE:
        assert stage.value in stages, f"Stage {stage.value} missing from receipt"
        stage_record = stages[stage.value]
        assert stage_record["status"] == "PASS", f"Stage {stage.value} status was {stage_record['status']}"
        assert stage_record["duration_ms"] >= 0.0
        assert "started_at" in stage_record
