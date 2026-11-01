"""
dual_pipeline/audio_capture_certificate.py

Cryptographic proof-of-completeness model for audio ingestion.
Binds raw audio SHA-256 digest, expected vs actual duration, silence gap audits,
and Gate F evaluation verdict before synthesis plane authorization.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field


class AudioSourceType(str, Enum):
    TEAMS = "TEAMS"
    WEBEX = "WEBEX"
    ZOOM = "ZOOM"
    BLUETOOTH = "BLUETOOTH"
    SCK = "SCK"
    LOOPBACK = "LOOPBACK"
    MIC = "MIC"
    GENERIC = "GENERIC"


class CertificateVerdict(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class SilenceGapRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    start_sec: float
    end_sec: float
    duration_sec: float
    severity: Literal["info", "warning", "blocking"]
    annotated: bool = False


class GateFEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    expected_duration_s: float
    actual_duration_s: float
    completeness_pct: float
    silence_gaps: Tuple[SilenceGapRecord, ...] = ()
    dropout_count: int = 0
    total_dropout_duration_s: float = 0.0
    source_type: AudioSourceType = AudioSourceType.GENERIC
    audio_sha256: str
    remediation_hint: Optional[str] = None


class AudioCaptureCertificate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    certificate_id: str
    audio_sha256: str
    verdict: CertificateVerdict
    gate_f_evidence: GateFEvidence
    issued_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expected_duration_s: float
    actual_duration_s: float
    source_type: AudioSourceType = AudioSourceType.GENERIC
    hitl_approved: bool = False
    hitl_receipt_id: Optional[str] = None

    @classmethod
    def create(
        cls,
        audio_sha256: str,
        verdict: CertificateVerdict | str,
        gate_f_evidence: GateFEvidence,
        expected_duration_s: float,
        actual_duration_s: float,
        source_type: AudioSourceType | str = AudioSourceType.GENERIC,
        hitl_approved: bool = False,
        hitl_receipt_id: Optional[str] = None,
    ) -> AudioCaptureCertificate:
        clean_verdict = CertificateVerdict(verdict) if isinstance(verdict, str) else verdict
        clean_source = AudioSourceType(source_type) if isinstance(source_type, str) else source_type
        cert_data = f"{audio_sha256}:{clean_verdict.value}:{expected_duration_s}:{actual_duration_s}:{clean_source.value}"
        cert_id = f"cert_af_{hashlib.sha256(cert_data.encode('utf-8')).hexdigest()[:16]}"
        return cls(
            certificate_id=cert_id,
            audio_sha256=audio_sha256,
            verdict=clean_verdict,
            gate_f_evidence=gate_f_evidence,
            expected_duration_s=expected_duration_s,
            actual_duration_s=actual_duration_s,
            source_type=clean_source,
            hitl_approved=hitl_approved,
            hitl_receipt_id=hitl_receipt_id,
        )
