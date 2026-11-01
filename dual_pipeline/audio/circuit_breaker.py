"""
dual_pipeline/audio/circuit_breaker.py

Fail-Fast Circuit Breaker for Meeting Deliverables (Outputs 01, 02, 03).
Guarantees that downstream synthesis is strictly blocked if full audio
cannot be confirmed by Gate F evaluation.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, Callable, Dict, Optional

from dual_pipeline.audio_capture_certificate import AudioCaptureCertificate, CertificateVerdict
from dual_pipeline.exceptions import DualPipelineError

logger = logging.getLogger("dual_pipeline.audio.circuit_breaker")


class AudioIncompleteFailFastError(DualPipelineError):
    """
    Raised when Output 01, 02, or 03 generation is blocked because audio completeness
    could not be confirmed (Gate F failure).
    """
    pass


def _mark_gate_f_certified(
    metadata: Dict[str, Any], combined_meta: Dict[str, Any], pa_context: Any, certified: bool = True
) -> None:
    metadata["gate_f_certified"] = certified
    combined_meta["gate_f_certified"] = certified
    if pa_context and isinstance(pa_context.get("metadata"), dict):
        pa_context["metadata"]["gate_f_certified"] = certified


def _extract_audio_certificate(combined_meta: Dict[str, Any]) -> Optional[AudioCaptureCertificate]:
    raw_cert = combined_meta.get("audio_certificate")
    if isinstance(raw_cert, AudioCaptureCertificate):
        return raw_cert
    if isinstance(raw_cert, dict):
        try:
            return AudioCaptureCertificate(**raw_cert)
        except Exception as exc:
            logger.debug("Failed to construct AudioCaptureCertificate from dict: %s", exc)
    qa_rep = combined_meta.get("qa_report") or combined_meta.get("upstream_qa_report")
    if qa_rep and hasattr(qa_rep, "audio_certificate"):
        return qa_rep.audio_certificate
    return None


def _enforce_certificate_verdict(cert: AudioCaptureCertificate, combined_meta: Dict[str, Any]) -> None:
    if cert.verdict == CertificateVerdict.FAIL:
        hint = cert.gate_f_evidence.remediation_hint or "Audio duration incomplete or corrupted."
        msg = (
            f"FAIL-FAST CIRCUIT BREAKER: Outputs 01, 02, and 03 are BLOCKED. "
            f"Audio capture failed Gate F verification (completeness: {cert.gate_f_evidence.completeness_pct:.1f}% "
            f"< threshold). Root cause: {hint}"
        )
        logger.error("[CIRCUIT-BREAKER-BLOCKED] %s", msg)
        raise AudioIncompleteFailFastError(msg)
    if cert.verdict == CertificateVerdict.WARN and not cert.hitl_approved and not combined_meta.get("hitl_audio_override"):
        msg = (
            f"CIRCUIT BREAKER: Audio capture received WARN verdict (completeness: "
            f"{cert.gate_f_evidence.completeness_pct:.1f}%). Outputs 01, 02, and 03 are "
            f"held pending explicit operator HITL approval receipt."
        )
        logger.warning("[CIRCUIT-BREAKER-HITL-REQUIRED] %s", msg)
        from dual_pipeline.transcript_stages import HITLRequiredError
        raise HITLRequiredError(msg)


def _check_unverified_audio_duration(combined_meta: Dict[str, Any]) -> None:
    expected_dur = combined_meta.get("expected_duration") or combined_meta.get("expected_duration_seconds")
    actual_dur = combined_meta.get("declared_duration_seconds")
    if expected_dur and actual_dur and float(expected_dur) > 0:
        pct = (float(actual_dur) / float(expected_dur)) * 100.0
        if pct < 80.0:
            msg = (
                f"FAIL-FAST CIRCUIT BREAKER: Outputs 01, 02, and 03 are BLOCKED. "
                f"Audio duration {actual_dur:.1f}s is only {pct:.1f}% of expected {expected_dur:.1f}s (< 80%)."
            )
            logger.error("[CIRCUIT-BREAKER-BLOCKED] %s", msg)
            raise AudioIncompleteFailFastError(msg)
    if combined_meta.get("require_audio_certificate", True) and not combined_meta.get("mock", False) and not combined_meta.get("mock_mode", False):
        raise AudioIncompleteFailFastError(
            "FAIL-FAST CIRCUIT BREAKER: Audio run requires a verified Gate F AudioCaptureCertificate, "
            "but cert is None."
        )


class AudioCompletenessCircuitBreaker:
    """
    Fail-Fast Circuit Breaker for synthesis deliverables (Outputs 01, 02, 03).
    Ensures that downstream synthesis is strictly blocked if audio integrity,
    completeness, or cryptographic hash verification fails.
    """

    @classmethod
    def assert_outputs_authorized(
        cls,
        metadata: Optional[Dict[str, Any]] = None,
        pa_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[AudioCaptureCertificate]:
        metadata = metadata if metadata is not None else {}
        pa_context = pa_context if pa_context is not None else {}
        combined_meta = {**pa_context.get("metadata", {}), **metadata}
        is_audio = bool(
            pa_context.get("audio_path")
            or any(combined_meta.get(k) for k in ("is_audio", "audio_path", "audio_certificate", "qa_report", "declared_duration_seconds"))
        )
        if not is_audio:
            return None

        cert = _extract_audio_certificate(combined_meta)
        if cert:
            _enforce_certificate_verdict(cert, combined_meta)
            _mark_gate_f_certified(metadata, combined_meta, pa_context, certified=True)
            return cert

        _mark_gate_f_certified(metadata, combined_meta, pa_context, certified=False)
        _check_unverified_audio_duration(combined_meta)
        return None

    @classmethod
    def verify_upstream_certificate(
        cls,
        cert: Optional[AudioCaptureCertificate],
        metadata: Optional[Dict[str, Any]] = None,
        mock_mode: bool = False,
    ) -> Optional[AudioCaptureCertificate]:
        """Enforces that an upstream AudioCaptureCertificate is present and valid (fails closed if cert is None unless mock_mode=True)."""
        meta = dict(metadata or {})
        if cert is None:
            if mock_mode or meta.get("mock") or meta.get("mock_mode"):
                return None
            raise AudioIncompleteFailFastError(
                "FAIL-FAST CIRCUIT BREAKER: Missing Gate F AudioCaptureCertificate (cert is None) for audio source."
            )
        meta["audio_certificate"] = cert
        return cls.assert_outputs_authorized(meta)


def require_audio_certificate(func: Callable) -> Callable:
    """
    Decorator for stage executors to enforce audio certificate validation before execution.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        meta = kwargs.get("metadata")
        if not meta and len(args) > 1 and isinstance(args[1], dict):
            meta = args[1].get("metadata")
        if not meta:
            u0 = kwargs.get("u0_context")
            if isinstance(u0, dict):
                meta = u0.get("metadata")

        pa_context = kwargs.get("pa_context")
        AudioCompletenessCircuitBreaker.assert_outputs_authorized(meta, pa_context)
        return func(*args, **kwargs)
    return wrapper
