"""
dual_pipeline/live_meeting_recorder_core/audio_preflight.py

Layer 1: Preflight validation of macOS permissions, binaries, and hardware interfaces.
Validates ScreenCaptureKit TCC authorization and AVFoundation device availability
to prevent silent capture failures.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple, Union

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_preflight")


class AudioPreflightValidator:
    """Preflight validator checking ScreenCaptureKit TCC permissions and AVFoundation devices."""

    @staticmethod
    def _is_headless_ci() -> bool:
        import os
        return bool(
            (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
            and os.environ.get("NEE_ALLOW_CI_AUDIO_PREFLIGHT") != "1"
        )

    @staticmethod
    def _check_cg_tcc_access() -> bool:
        try:
            import ctypes
            cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
            return bool(cg.CGPreflightScreenCaptureAccess())
        except Exception as e:
            logger.debug("[PREFLIGHT] Native CGPreflightScreenCaptureAccess check failed: %s", e)
            return False

    @staticmethod
    def _probe_sck_binary(sck_bin: Path, timeout_sec: float) -> bool:
        if not sck_bin.exists():
            return False
        try:
            res = subprocess.run(
                [str(sck_bin), "--probe"],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            if res.returncode == 2:
                logger.critical(
                    "[PREFLIGHT_FATAL] mac_audio_tap binary drift / CLI syntax error (code 2): %s. "
                    "Recompile immediately via `python tools/build_mac_audio_tap.py`.",
                    res.stderr.strip(),
                )
                raise RuntimeError(f"ScreenCaptureKit binary drift detected: {res.stderr.strip()}")
            if res.returncode != 0:
                logger.debug("[PREFLIGHT] SCK binary probe exited with code %s: %s", res.returncode, res.stderr)
                return False
        except subprocess.TimeoutExpired as exc:
            logger.critical(
                "[PREFLIGHT_FATAL] SCK probe timed out (>%.1fs). Binary is hanging (likely legacy binary without --probe).",
                timeout_sec,
            )
            raise RuntimeError("ScreenCaptureKit binary probe timed out; possible legacy binary hang.") from exc
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            logger.debug("[PREFLIGHT] SCK binary probe error: %s", e)
            return False
        return True

    @staticmethod
    def check_sck_tcc_permission(sck_bin: Optional[Path] = None, timeout_sec: float = 2.5) -> bool:
        """
        Executes non-blocking probe of ScreenCaptureKit authorization.
        Uses native macOS CoreGraphics API CGPreflightScreenCaptureAccess and optional binary probe.
        Returns True only if ScreenCaptureKit / Screen Recording is authorized.
        Raises RuntimeError if the probe times out (hanging legacy binary) or exits with code 2 (binary drift).
        """
        if sys.platform != "darwin":
            return False
        if AudioPreflightValidator._is_headless_ci():
            return False
        if not AudioPreflightValidator._check_cg_tcc_access():
            return False
        if sck_bin is not None:
            return AudioPreflightValidator._probe_sck_binary(sck_bin, timeout_sec)
        return True

    @staticmethod
    def check_mic_mode_for_speakerphone(is_speakerphone_source: bool = True) -> bool:
        """Preflight checks that macOS Mic Mode is not set to Voice Isolation for speakerphone audio."""
        from dual_pipeline.live_meeting_recorder_core.mic_mode_preflight import MicModePreflightChecker
        res = MicModePreflightChecker.verify_mic_mode_for_speakerphone(is_speakerphone_source=is_speakerphone_source)
        return bool(res.get("is_speakerphone_compatible", True))

    _CACHED_FFMPEG_LINES: Optional[List[str]] = None
    _CACHED_FFMPEG_TIME: float = 0.0

    @classmethod
    def clear_cache(cls) -> None:
        """Invalidates preflight device line cache."""
        cls._CACHED_FFMPEG_LINES = None
        cls._CACHED_FFMPEG_TIME = 0.0

    @classmethod
    def _get_ffmpeg_device_lines(cls, ffmpeg_bin: str, stderr_text: Optional[str]) -> Optional[List[str]]:
        if stderr_text is not None:
            return stderr_text.splitlines()
        if cls._CACHED_FFMPEG_LINES is not None and (time.time() - cls._CACHED_FFMPEG_TIME) < 15.0:
            return cls._CACHED_FFMPEG_LINES
        if sys.platform != "darwin":
            return []
        try:
            res = subprocess.run(
                [ffmpeg_bin, "-f", "avfoundation", "-list_devices", "true", "-i", ""],
                capture_output=True,
                text=True,
                timeout=1.5,
            )
            lines = res.stderr.splitlines()
            cls._CACHED_FFMPEG_LINES = lines
            cls._CACHED_FFMPEG_TIME = time.time()
            return lines
        except Exception as e:
            logger.debug("[PREFLIGHT] FFmpeg device query failed: %s", e)
            return None

    @classmethod
    def _is_device_index_valid(cls, lines: List[str], target_tag: str) -> bool:
        from dual_pipeline.live_meeting_recorder_core.hardware_classifier import (
            NON_STREAMING_DEVICE_KEYWORDS,
        )
        in_audio = False
        for line in lines:
            if "AVFoundation audio devices:" in line:
                in_audio = True
                continue
            if in_audio and target_tag in line:
                line_lower = line.lower()
                if any(k in line_lower for k in NON_STREAMING_DEVICE_KEYWORDS):
                    logger.warning(
                        "[PREFLIGHT] Rejected non-streaming AVFoundation device at %s: %s",
                        target_tag,
                        line.strip(),
                    )
                    return False
                return True
        return False

    @classmethod
    def validate_avfoundation_index(
        cls,
        ffmpeg_bin: str,
        index_str: str,
        stderr_text: Optional[str] = None,
    ) -> bool:
        """Verifies that the requested AVFoundation audio index exists in ffmpeg -list_devices and is not a non-streaming BLE device."""
        if sys.platform != "darwin" and stderr_text is None:
            return True

        clean_idx = index_str.replace(":", "").strip()
        target_tag = f"[{clean_idx}]"

        lines = cls._get_ffmpeg_device_lines(ffmpeg_bin, stderr_text)
        if lines is None:
            return False
        if not lines and sys.platform != "darwin":
            return True

        return cls._is_device_index_valid(lines, target_tag)

    @classmethod
    def _compute_pcm_rms(cls, data: bytes) -> float:
        import math
        import struct
        n_samples = len(data) // 2
        if n_samples == 0:
            return 0.0
        samples = struct.unpack(f"{n_samples}h", data[: n_samples * 2])
        return math.sqrt(sum(s**2 for s in samples) / n_samples)

    @classmethod
    def _probe_wav_file(cls, p: Path, min_rms: float) -> Tuple[bool, float]:
        import wave
        try:
            if p.stat().st_size < 100:
                return False, 0.0
            with wave.open(str(p), "rb") as w:
                n = w.getnframes()
                if n == 0:
                    return False, 0.0
                data = w.readframes(n)
                rms = cls._compute_pcm_rms(data)
                return (rms >= min_rms, rms)
        except Exception as e:
            logger.debug("[PREFLIGHT-ACOUSTIC] WAV probe error on %s: %s", p, e)
            return False, 0.0

    @classmethod
    def _probe_ffmpeg_device(
        cls,
        ffmpeg_bin: str,
        target_str: str,
        duration_sec: float,
        timeout_sec: float,
        min_rms: float,
    ) -> Tuple[bool, float]:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f",
            "avfoundation",
            "-t",
            str(duration_sec),
            "-i",
            target_str,
            "-f",
            "s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "pipe:1",
        ]
        try:
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=timeout_sec,
            )
            if res.returncode != 0 or not res.stdout:
                return False, 0.0
            rms = cls._compute_pcm_rms(res.stdout)
            return (rms >= min_rms, rms)
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.debug("[PREFLIGHT-ACOUSTIC] Probe error on %s: %s", target_str, e)
            return False, 0.0

    @classmethod
    def probe_acoustic_signal(
        cls,
        target: Union[str, Path],
        duration_sec: float = 0.3,
        min_rms: float = 0.0001,
        ffmpeg_bin: str = "ffmpeg",
        timeout_sec: float = 1.0,
    ) -> Tuple[bool, float]:
        """Probes an audio target (AVFoundation device index or audio file) for active acoustic signal.

        Returns (has_signal, rms_energy). If target is a BLE beacon, flatline silence, or invalid,
        returns (False, 0.0).
        """
        p = Path(str(target))
        if p.exists() and p.is_file():
            return cls._probe_wav_file(p, min_rms)

        target_str = str(target)
        if not cls.validate_avfoundation_index(ffmpeg_bin, target_str):
            logger.warning(
                "[PREFLIGHT-ACOUSTIC] Target device %s rejected by preflight validation (BLE beacon or unavailable).",
                target_str,
            )
            return False, 0.0

        if cls._is_headless_ci():
            return True, 100.0

        return cls._probe_ffmpeg_device(ffmpeg_bin, target_str, duration_sec, timeout_sec, min_rms)

    @classmethod
    def resolve_safe_strategy(
        cls,
        requested_strategy: str,
        sck_available: bool,
        ffmpeg_bin: str,
        preferred_mic_index: str = ":0",
        stderr_text: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Validates requested strategy against TCC permissions and hardware availability.

        Downgrades instantaneously to a safe alternative if digital tap is blocked.
        Returns: (safe_strategy, safe_device_index)
        """
        from pipeline_a_intake.mac_system_audio import CoreAudioDeviceManager

        safe_fallback = CoreAudioDeviceManager.get_default_input_avfoundation_index()
        if not cls.validate_avfoundation_index(ffmpeg_bin, safe_fallback, stderr_text=stderr_text):
            safe_fallback = ":1"

        if requested_strategy in ("screen_capture_kit", "mixed_sck") and not sck_available:
            logger.warning(
                "[PREFLIGHT_FAILOVER] SCK Screen Recording permission missing/denied. "
                "Instantaneous fallback to AVFoundation microphone (%s).",
                safe_fallback,
            )
            safe_idx = (
                preferred_mic_index
                if cls.validate_avfoundation_index(ffmpeg_bin, preferred_mic_index, stderr_text=stderr_text)
                else safe_fallback
            )
            return "microphone", safe_idx

        if requested_strategy == "microphone" and not cls.validate_avfoundation_index(
            ffmpeg_bin, preferred_mic_index, stderr_text=stderr_text
        ):
            logger.warning(
                "[PREFLIGHT_FAILOVER] Requested mic %s unavailable or non-streaming; fallback to %s",
                preferred_mic_index,
                safe_fallback,
            )
            return "microphone", safe_fallback

        return requested_strategy, preferred_mic_index
