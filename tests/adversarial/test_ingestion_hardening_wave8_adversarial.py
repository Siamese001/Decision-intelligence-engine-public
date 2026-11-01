"""
tests/adversarial/test_ingestion_hardening_wave8_adversarial.py

Wave 8 Adversarial Contract Test Suite.
Validates:
  - E01: Frozen source-family/session holdouts and blind reference review qualify quality separately for documents/transcripts/notes.
  - E02: Report failures/timeouts/inconclusive samples; document token gains never establish transcript parity or host savings.
  - E03: Feature off and rollback restore known-good route without invalidating valid captures or bypassing approvals.
"""
from __future__ import annotations

import json
from pathlib import Path
import pytest

from dual_pipeline.canonical_pipeline.modalities import CanonicalModality
from dual_pipeline.canonical_pipeline.quality_aggregates import (
    ModalityQualificationReport,
    assert_savings_metrics_modality_scoped,
    validate_modality_qualification_isolation,
    verify_rollback_safety,
)


def test_e01_contract() -> None:
    """E01: Frozen source-family/session holdouts and blind reference review qualify quality separately for documents/transcripts/notes."""
    # 1. Fully qualified DOCUMENT benchmark report
    doc_report = ModalityQualificationReport(
        modality=CanonicalModality.DOCUMENT,
        benchmark_id="doc_bench_v2_holdout",
        sample_count=20,
        passed_count=20,
        failed_count=0,
        timeout_count=0,
        inconclusive_count=0,
        quality_score=0.96,
        token_savings_pct=0.45,
    )
    assert doc_report.is_qualified is True

    # 2. DOCUMENT qualification cannot certify or proxy MEETING_TRANSCRIPT
    reports = {CanonicalModality.DOCUMENT: doc_report}
    ok_trans, msg_trans = validate_modality_qualification_isolation(
        reports=reports,
        target_modality=CanonicalModality.MEETING_TRANSCRIPT,
    )
    assert ok_trans is False
    assert "Missing qualification report for target modality: MEETING_TRANSCRIPT" in msg_trans

    # 3. MEETING_TRANSCRIPT requires its own independent qualified benchmark
    trans_report = ModalityQualificationReport(
        modality=CanonicalModality.MEETING_TRANSCRIPT,
        benchmark_id="transcript_blind_holdout_v1",
        sample_count=15,
        passed_count=15,
        failed_count=0,
        timeout_count=0,
        inconclusive_count=0,
        quality_score=0.92,
    )
    assert trans_report.is_qualified is True

    reports[CanonicalModality.MEETING_TRANSCRIPT] = trans_report
    ok_trans_valid, msg_trans_valid = validate_modality_qualification_isolation(
        reports=reports,
        target_modality=CanonicalModality.MEETING_TRANSCRIPT,
    )
    assert ok_trans_valid is True
    assert "MEETING_TRANSCRIPT independently qualified" in msg_trans_valid


def test_e02_contract() -> None:
    """E02: Report failures/timeouts/inconclusive samples; document token gains never establish transcript parity or host savings."""
    # 1. Honest reporting: benchmark with 1 failure or 1 timeout is NOT qualified
    flawed_report = ModalityQualificationReport(
        modality=CanonicalModality.DOCUMENT,
        benchmark_id="doc_flawed_bench",
        sample_count=20,
        passed_count=19,
        failed_count=1,
        timeout_count=0,
        inconclusive_count=0,
        quality_score=0.95,
    )
    assert flawed_report.is_qualified is False

    timeout_report = ModalityQualificationReport(
        modality=CanonicalModality.DOCUMENT,
        benchmark_id="doc_timeout_bench",
        sample_count=20,
        passed_count=19,
        failed_count=0,
        timeout_count=1,
        inconclusive_count=0,
        quality_score=0.95,
    )
    assert timeout_report.is_qualified is False

    inconclusive_report = ModalityQualificationReport(
        modality=CanonicalModality.DOCUMENT,
        benchmark_id="doc_inconclusive_bench",
        sample_count=20,
        passed_count=19,
        failed_count=0,
        timeout_count=0,
        inconclusive_count=1,
        quality_score=0.95,
    )
    assert inconclusive_report.is_qualified is False

    # 2. Modality savings isolation: DOCUMENT savings claiming global/host savings fails closed
    with pytest.raises(ValueError, match="improperly claims global/host scope"):
        assert_savings_metrics_modality_scoped(
            reported_metrics={"document_tokens": 1200, "global_host_savings_pct": 0.50},
            modality=CanonicalModality.DOCUMENT,
        )

    # 3. DOCUMENT savings claiming transcript savings or parity fails closed
    with pytest.raises(ValueError, match="cannot claim transcript parity or transcript savings"):
        assert_savings_metrics_modality_scoped(
            reported_metrics={"document_tokens": 1200, "transcript_token_savings": 0.40},
            modality=CanonicalModality.DOCUMENT,
        )

    # 4. Correctly scoped document savings pass
    assert_savings_metrics_modality_scoped(
        reported_metrics={"document_tokens": 1200, "document_savings_pct": 0.45},
        modality=CanonicalModality.DOCUMENT,
    )


def test_e03_contract(tmp_path: Path) -> None:
    """E03: Feature off and rollback restore known-good route without invalidating valid captures or bypassing approvals."""
    # Create test deliverables
    f1 = tmp_path / "01_transcript.md"
    f1.write_text("Authoritative Master Transcript", encoding="utf-8")
    f2 = tmp_path / "02_recap.md"
    f2.write_text("Executive Recap", encoding="utf-8")
    f3 = tmp_path / "03_graph.json"
    f3.write_text(json.dumps({"nodes": [1, 2, 3]}), encoding="utf-8")

    deliverables = {"01": f1, "02": f2, "03": f3}

    # 1. Safe rollback with all deliverables intact and approval required
    ok, msg = verify_rollback_safety(
        flags={"fast_pilot_budget": False, "enable_parallel_synthesis": False},
        deliverables=deliverables,
        requires_approval=True,
    )
    assert ok is True
    assert "Rollback verified safe" in msg

    # 2. Rollback that loses an artifact fails closed
    missing_deliverables = {"01": f1, "02": tmp_path / "lost_recap.md", "03": f3}
    ok_lost, msg_lost = verify_rollback_safety(
        flags={"fast_pilot_budget": False},
        deliverables=missing_deliverables,
        requires_approval=True,
    )
    assert ok_lost is False
    assert "ROLLBACK_ARTIFACT_LOST" in msg_lost

    # 3. Rollback attempting to bypass approval fails closed
    ok_bypass, msg_bypass = verify_rollback_safety(
        flags={"fast_pilot_budget": False},
        deliverables=deliverables,
        requires_approval=False,
    )
    assert ok_bypass is False
    assert "ROLLBACK_GATE_BYPASS" in msg_bypass
