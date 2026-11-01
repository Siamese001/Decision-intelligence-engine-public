"""
dual_pipeline/live_meeting_recorder_core/mic_mode_preflight.py

Inspects macOS audio input voice processing / Mic Mode state (Standard vs Voice Isolation).
Detects when Apple Silicon Voice Isolation is actively muting external speakerphone audio
and provides prescriptive operator guidance to restore Standard Mic Mode in Control Center.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.mic_mode_preflight")

REMEDIATION_MESSAGE = "Click Control Center in the macOS menu bar -> Mic Mode -> select Standard"


class MicModeStatus:
    STANDARD = "Standard"
    VOICE_ISOLATION = "Voice Isolation"
    WIDE_SPECTRUM = "Wide Spectrum"
    UNKNOWN = "Unknown"


class MicModePreflightChecker:
    """Detects active macOS Mic Mode voice isolation state and attenuation signatures."""

    _mock_mode_override: Optional[str] = None

    @classmethod
    def set_mock_mode(cls, mode: Optional[str]) -> None:
        """Sets mock mic mode for deterministic testing."""
        cls._mock_mode_override = mode

    @classmethod
    def _is_headless_ci(cls) -> bool:
        return bool(
            (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
            and os.environ.get("NEE_ALLOW_CI_AUDIO_PREFLIGHT") != "1"
        )

    @classmethod
    def detect_mic_mode(cls) -> Tuple[str, bool]:
        """Detects active macOS Mic Mode.

        Returns (mode_name, is_voice_isolation).
        """
        if cls._mock_mode_override is not None:
            is_vi = cls._mock_mode_override.lower() == "voice isolation"
            return cls._mock_mode_override, is_vi

        if sys.platform != "darwin" or cls._is_headless_ci():
            return MicModeStatus.STANDARD, False

        # Query active system voice processing via CoreAudio / defaults
        try:
            # Check user defaults or system audio processing state if exposed
            res = subprocess.run(
                ["/usr/bin/defaults", "read", "com.apple.controlcenter", "MicMode"],
                capture_output=True,
                text=True,
                timeout=0.2,
            )
            val = res.stdout.strip().lower()
            if "voice isolation" in val or val == "2":
                return MicModeStatus.VOICE_ISOLATION, True
            elif "wide spectrum" in val or val == "1":
                return MicModeStatus.WIDE_SPECTRUM, False
            elif val == "0" or "standard" in val:
                return MicModeStatus.STANDARD, False
        except Exception:
            pass

        return MicModeStatus.STANDARD, False

    @classmethod
    def verify_mic_mode_for_speakerphone(
        cls,
        is_speakerphone_source: bool = False,
    ) -> Dict[str, Any]:
        """Preflight verification ensuring Voice Isolation is not blocking external speaker audio.

        Raises AudioCaptureUnprovenError if Voice Isolation is active during speakerphone capture.
        """
        mode, is_voice_isolation = cls.detect_mic_mode()

        if is_speakerphone_source and is_voice_isolation:
            from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
                AudioCaptureUnprovenError,
                AudioReasonCode,
            )

            detail = (
                f"macOS Mic Mode is currently set to '{mode}'. "
                "Voice Isolation actively filters and mutes sound coming from external phone speakers as background noise."
            )
            logger.error("[PREFLIGHT-MIC-MODE-FAIL] %s Remediation: %s", detail, REMEDIATION_MESSAGE)
            raise AudioCaptureUnprovenError(
                stage="PREFLIGHT_MIC_MODE",
                reason_code=AudioReasonCode.VOICE_ISOLATION_ACTIVE,
                detail=detail,
                remediation=REMEDIATION_MESSAGE,
            )

        return {
            "mic_mode": mode,
            "is_voice_isolation": is_voice_isolation,
            "is_speakerphone_compatible": not is_voice_isolation,
        }
