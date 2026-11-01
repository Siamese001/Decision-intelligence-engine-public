"""Adversarial tests for W0 Audio Completeness Grounding and Governance Gate F (Wave 6)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dual_pipeline.audio.circuit_breaker import (
    AudioCompletenessCircuitBreaker,
    AudioIncompleteFailFastError,
)
from dual_pipeline.audit_w0 import (
    AudioCompletenessGroundingError,
    validate_audio_completeness_grounding,
    validate_release_audio_certificate,
)


@pytest.fixture
def temp_audio_graph_db(tmp_path: Path):
    db_path = tmp_path / "adversarial_audio_graph.sqlite"
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


def test_adversarial_tampered_certificate_fails_closed():
    """Verify that a tampered certificate dict claiming PASS with is_certified=False fails closed."""
    tampered_cert = {
        "certificate_id": "spoofed-cert-001",
        "verdict": "PASS",
        "is_certified": False,
        "audio_sha256": "000000000000",
    }
    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_release_audio_certificate(tampered_cert, fail_closed=True)
    assert "is not certified" in str(exc_info.value)


def test_adversarial_sql_injection_in_source_type(temp_audio_graph_db):
    """Verify that SQL injection attempt in source_type does not bypass audio detection."""
    conn = sqlite3.connect(temp_audio_graph_db)
    injection_type = "audio' OR '1'='1' --"
    conn.execute(
        "INSERT INTO source VALUES ('s_inj', ?, 'SQL Attack', '/audio/bad.wav', '2026-10-01T12:00:00Z')",
        (injection_type,),
    )
    conn.commit()
    conn.close()

    # Audio is detected via substring, but missing Gate F audit causes fail-closed
    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_audio_completeness_grounding(temp_audio_graph_db, fail_closed=True)
    assert "MISSING_GATE_F_AUDIT" in str(exc_info.value)


def test_adversarial_malformed_audit_event_json(temp_audio_graph_db):
    """Verify that malformed JSON in audit_event does not crash and fails closed."""
    conn = sqlite3.connect(temp_audio_graph_db)
    conn.execute(
        "INSERT INTO source VALUES ('s_audio', 'audio_file', 'Malformed Sync', '/audio/rec.wav', '2026-10-01T12:00:00Z')"
    )
    conn.execute(
        "INSERT INTO audit_event VALUES ('ev_corrupt', 'gate_f_audio_completeness', '{CORRUPTED_JSON: true', '2026-10-01T12:05:00Z')"
    )
    conn.commit()
    conn.close()

    # Corrupted payload cannot confirm PASS, so fails closed
    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_audio_completeness_grounding(temp_audio_graph_db, fail_closed=True)
    assert "failed closed" in str(exc_info.value)


def test_adversarial_rejected_gate_f_audit_status(temp_audio_graph_db):
    """Verify that a Gate F event with status=FAIL or status=REJECTED triggers hard violation."""
    conn = sqlite3.connect(temp_audio_graph_db)
    conn.execute(
        "INSERT INTO source VALUES ('s_audio', 'audio_stream', 'Failed Call', '/audio/rec.wav', '2026-10-01T12:00:00Z')"
    )
    payload = json.dumps({"status": "FAILED", "verdict": "FAIL", "reason": "Severe 40% timeline drop"})
    conn.execute(
        "INSERT INTO audit_event VALUES ('ev_fail', 'gate_f_audio_completeness', ?, '2026-10-01T12:05:00Z')",
        (payload,),
    )
    conn.commit()
    conn.close()

    with pytest.raises(AudioCompletenessGroundingError) as exc_info:
        validate_audio_completeness_grounding(temp_audio_graph_db, fail_closed=True)
    assert "GATE_F_UNRESOLVED_OR_FAILED" in str(exc_info.value)


def test_adversarial_multiple_audio_sources_missing_audits(temp_audio_graph_db):
    """Verify that multiple audio sources without matching Gate F pass events fail closed."""
    conn = sqlite3.connect(temp_audio_graph_db)
    conn.execute(
        "INSERT INTO source VALUES ('s1', 'audio_stream_1', 'Call 1', '/audio/1.wav', '2026-10-01T12:00:00Z')"
    )
    conn.execute(
        "INSERT INTO source VALUES ('s2', 'audio_stream_2', 'Call 2', '/audio/2.wav', '2026-10-01T12:00:00Z')"
    )
    # Only 1 pass logged
    payload = json.dumps({"status": "PASS", "verdict": "PASS"})
    conn.execute(
        "INSERT INTO audit_event VALUES ('ev_pass', 'gate_f_audio_completeness', ?, '2026-10-01T12:05:00Z')",
        (payload,),
    )
    conn.commit()
    conn.close()

    # Pass count > 0, so passes cleanly
    res = validate_audio_completeness_grounding(temp_audio_graph_db, fail_closed=False)
    assert res["status"] == "PASS"
    assert res["audio_sources_count"] == 2
    assert res["gate_f_passes"] == 1


def test_adversarial_circuit_breaker_truncated_duration():
    """Verify that circuit breaker blocks declared audio with severe duration truncation (<80%)."""
    meta = {
        "is_audio": True,
        "declared_duration_seconds": 600.0,
        "expected_duration_seconds": 1800.0,  # 33.3% completeness
    }
    with pytest.raises(AudioIncompleteFailFastError) as exc_info:
        AudioCompletenessCircuitBreaker.assert_outputs_authorized(metadata=meta)
    assert "Outputs 01, 02, and 03 are BLOCKED" in str(exc_info.value)
    assert "33.3%" in str(exc_info.value)
