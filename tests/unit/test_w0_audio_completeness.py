"""Unit tests for W0 Audio Completeness Grounding and Governance Constraint C-30 (Wave 6)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.audio_capture_certificate import (
    AudioCaptureCertificate,
    AudioSourceType,
    CertificateVerdict,
    GateFEvidence,
)
from dual_pipeline.audit_w0 import (
    AudioCompletenessGroundingError,
    validate_audio_completeness_grounding,
    validate_release_audio_certificate,
)


@pytest.fixture
def temp_audio_graph_db(tmp_path: Path):
    db_path = tmp_path / "test_audio_graph.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE source (
            source_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            location TEXT,
            created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE audit_event (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    conn.commit()
    conn.close()
    return db_path


def test_w0_non_audio_release_passes():
    """Verify that releases without audio certificates pass without error."""
    res = validate_release_audio_certificate(None)
    assert res["status"] == "PASS"
    assert res["is_certified"] is True


def test_w0_certified_audio_certificate_passes():
    """Verify that an approved AudioCaptureCertificate passes release validation."""
    evidence = GateFEvidence(
        expected_duration_s=3600.0,
        actual_duration_s=3598.5,
        completeness_pct=98.5,
        dropout_count=2,
        total_dropout_duration_s=0.1,
        source_type=AudioSourceType.ZOOM,
        audio_sha256="abcdef1234567890",
    )
    cert = AudioCaptureCertificate.create(
        audio_sha256="abcdef1234567890",
        verdict=CertificateVerdict.PASS,
        gate_f_evidence=evidence,
        expected_duration_s=3600.0,
        actual_duration_s=3598.5,
        source_type=AudioSourceType.ZOOM,
    )

    res = validate_release_audio_certificate(cert)
    assert res["status"] == "PASS"
    assert res["is_certified"] is True
    assert res["verdict"] == "PASS"


def test_w0_failed_audio_certificate_raises():
    """Verify that a rejected or uncertified AudioCaptureCertificate fails closed."""
    evidence = GateFEvidence(
        expected_duration_s=1800.0,
        actual_duration_s=1200.0,
        completeness_pct=65.0,
        dropout_count=15,
        total_dropout_duration_s=12.5,
        source_type=AudioSourceType.TEAMS,
        audio_sha256="123456abcdef",
        remediation_hint="Meeting duration was truncated prematurely",
    )
    cert = AudioCaptureCertificate.create(
        audio_sha256="123456abcdef",
        verdict=CertificateVerdict.FAIL,
        gate_f_evidence=evidence,
        expected_duration_s=1800.0,
        actual_duration_s=1200.0,
        source_type=AudioSourceType.TEAMS,
    )

    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_release_audio_certificate(cert, fail_closed=True)
    assert "is not certified" in str(exc_info.value)

    res = validate_release_audio_certificate(cert, fail_closed=False)
    assert res["status"] == "VIOLATION"
    assert res["is_certified"] is False


def test_w0_database_audio_source_with_gate_f_audit_passes(temp_audio_graph_db):
    """Verify that SQLite graph containing audio source and Gate F pass audit passes."""
    conn = sqlite3.connect(temp_audio_graph_db)
    conn.execute(
        "INSERT INTO source VALUES ('s1', 'meeting_recording_wav', 'Design Review', '/audio/rec.wav', '2026-10-01T12:00:00Z')"
    )
    payload = json.dumps({"verdict": "PASS", "duration_coverage_pct": 99.2, "status": "PASS"})
    conn.execute(
        f"INSERT INTO audit_event VALUES ('ev1', 'gate_f_audio_completeness', '{payload}', '2026-10-01T12:05:00Z')"
    )
    conn.commit()
    conn.close()

    res = validate_audio_completeness_grounding(temp_audio_graph_db)
    assert res["status"] == "PASS"
    assert res["audio_sources_count"] == 1
    assert res["gate_f_passes"] == 1
    assert len(res["anomalies"]) == 0


def test_w0_database_audio_source_without_gate_f_fails(temp_audio_graph_db):
    """Verify that SQLite graph containing audio source without Gate F audit fails closed."""
    conn = sqlite3.connect(temp_audio_graph_db)
    conn.execute(
        "INSERT INTO source VALUES ('s2', 'audio_stream', 'Sprint Planning', '/audio/sprint.wav', '2026-10-01T12:00:00Z')"
    )
    conn.commit()
    conn.close()

    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_audio_completeness_grounding(temp_audio_graph_db, fail_closed=True)
    assert "MISSING_GATE_F_AUDIT" in str(exc_info.value)

    res = validate_audio_completeness_grounding(temp_audio_graph_db, fail_closed=False)
    assert res["status"] == "VIOLATION"
    assert res["audio_sources_count"] == 1
    assert res["gate_f_passes"] == 0
    assert len(res["anomalies"]) > 0


def test_w0_prompt_constraint_c30_present():
    """Verify Constraint C-30 is present across synthesis prompt surfaces."""
    prompt_path = Path("governance/decision_intelligence/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md")
    assert prompt_path.is_file(), f"{prompt_path} does not exist"
    text = prompt_path.read_text(encoding="utf-8")
    assert "C-30" in text
    assert "Audio Capture Completeness & Gate F Certification" in text

    # Verify mirror
    mirror_path = Path("config/prompts/synthesis/RAW_TRANSCRIPT_SYNTHESIS_PROMPT_V5_7.md")
    assert mirror_path.is_file()
    mirror_text = mirror_path.read_text(encoding="utf-8")
    assert "C-30" in mirror_text

    # Verify stage prompts
    stage1 = Path("config/prompts/synthesis/stages/01_forensic_normalizer.md").read_text(encoding="utf-8")
    assert "Constraint C-30" in stage1

    stage2 = Path("config/prompts/synthesis/stages/02_executive_recap.md").read_text(encoding="utf-8")
    assert "Constraint C-30" in stage2

    stage3 = Path("config/prompts/synthesis/stages/03_graph_extractor.md").read_text(encoding="utf-8")
    assert "Constraint C-30" in stage3
