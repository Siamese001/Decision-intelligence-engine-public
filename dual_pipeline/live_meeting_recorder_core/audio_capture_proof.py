"""
dual_pipeline/live_meeting_recorder_core/audio_capture_proof.py

Proof-of-Capture Gate & Continuous Audio Verification for Apple Silicon macOS.
Target: Fail-fast, fail-closed audio capture verification with ZERO fallback (D1).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import math
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_capture_proof")


class AudioReasonCode:
    """Closed enum of audio proof-of-capture failure reason codes."""
    FFMPEG_SPAWN_FAILED = "FFMPEG_SPAWN_FAILED"
    FFMPEG_EARLY_EXIT = "FFMPEG_EARLY_EXIT"
    CAPTURE_STDERR_DENIED = "CAPTURE_STDERR_DENIED"
    PCM_BYTE_RATE_DEFICIT = "PCM_BYTE_RATE_DEFICIT"
    MIC_DIGITAL_ZERO = "MIC_DIGITAL_ZERO"
    AUDIO_GROWTH_STALLED = "AUDIO_GROWTH_STALLED"
    SCK_TAP_FAILED = "SCK_TAP_FAILED"
    SCK_TCC_DENIED = "SCK_TCC_DENIED"
    DEVICE_NOT_FOUND = "DEVICE_NOT_FOUND"
    INITIATION_DEADLINE_EXCEEDED = "INITIATION_DEADLINE_EXCEEDED"
    MOCK_AUDIO_PROHIBITED = "MOCK_AUDIO_PROHIBITED"
    VOICE_ISOLATION_ACTIVE = "VOICE_ISOLATION_ACTIVE"


EXIT_AUDIO_UNPROVEN: int = 11


class AudioCaptureUnprovenError(RuntimeError):
    """Raised when audio capture readiness or continuous proof fails.
    Carries structured metadata, stage, reason code, and operator remediation.
    """

    def __init__(
        self, stage: str, reason_code: str, detail: str, remediation: Optional[str] = None
    ) -> None:
        self.stage = stage
        self.reason_code = reason_code
        self.detail = detail
        self.remediation = remediation
        msg = f"[{reason_code}] Stage '{stage}' failed: {detail}"
        if remediation:
            msg += f"\n👉 Remediation: {remediation}"
        super().__init__(msg)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "AUDIO_CAPTURE_UNPROVEN", "stage": self.stage,
            "reason_code": self.reason_code, "detail": self.detail, "remediation": self.remediation,
        }


from dual_pipeline.live_meeting_recorder_core.chunk_health import find_wav_data_offset


class AudioCaptureProofVerifier:
    """Verifies empirical audio capture proof (P0, P1, P2, P3) without fallback."""

    # 16kHz s16le expected byte rates
    MONO_BYTE_RATE_BPS: float = 32000.0   # 16,000 samples/s * 2 bytes
    STEREO_BYTE_RATE_BPS: float = 64000.0 # 32,000 samples/s * 2 bytes
    MIN_RATE_RATIO: float = 0.80          # Minimum 80% throughput
    MIN_MIC_RMS_FLOOR: float = 1.0        # Above digital floor (> 0.5 LSB)

    @staticmethod
    def _check_deadline(deadline_monotonic: Optional[float], stage: str) -> None:
        if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
            raise AudioCaptureUnprovenError(
                stage=stage, reason_code=AudioReasonCode.INITIATION_DEADLINE_EXCEEDED,
                detail=f"Initiation deadline exceeded at {stage}.",
            )

    @classmethod
    def verify_sck_preflight(cls, sck_bin: Optional[Path] = None) -> None:
        """Preflight verification of ScreenCaptureKit binary and macOS TCC authorization."""
        from dual_pipeline.live_meeting_recorder_core.audio_preflight import (
            AudioPreflightValidator,
        )
        from pipeline_a_intake.mac_system_audio import SCK_COMPILED_BIN

        target_bin = sck_bin or SCK_COMPILED_BIN
        if sys.platform != "darwin":
            return  # In non-darwin (e.g. CI or Linux dev), skip native binary check

        if not target_bin.exists():
            raise AudioCaptureUnprovenError(
                stage="SCK_PREFLIGHT", reason_code=AudioReasonCode.SCK_TAP_FAILED,
                detail=f"ScreenCaptureKit audio tap binary not found at {target_bin}.",
                remediation="Compile the tap binary via 'python tools/build_mac_audio_tap.py'.",
            )
        if not AudioPreflightValidator.check_sck_tcc_permission(target_bin):
            raise AudioCaptureUnprovenError(
                stage="SCK_PREFLIGHT", reason_code=AudioReasonCode.SCK_TCC_DENIED,
                detail="ScreenCaptureKit screen & system audio recording permission is not authorized.",
                remediation="Open System Settings → Privacy & Security → Screen & System Audio Recording, and toggle ON access.",
            )

    @classmethod
    def verify_mic_mode_preflight(cls, is_speakerphone_source: bool = True) -> None:
        """Preflight verification ensuring Voice Isolation is not active for speakerphone audio."""
        from dual_pipeline.live_meeting_recorder_core.audio_preflight import (
            AudioPreflightValidator,
        )
        AudioPreflightValidator.check_mic_mode_for_speakerphone(is_speakerphone_source=is_speakerphone_source)

    @classmethod
    def _check_sck_process(cls, sck_proc: Optional[subprocess.Popen]) -> None:
        if sck_proc is None:
            return
        sck_code = sck_proc.poll()
        if sck_code is None:
            return
        err = ""
        if sck_proc.stderr:
            with contextlib.suppress(Exception):
                err = sck_proc.stderr.read().decode("utf-8", errors="replace").strip()
        if "Permission denied" in err or "TCC" in err or sck_code == 1:
            raise AudioCaptureUnprovenError(
                stage="P0_PROCESS_ALIVE", reason_code=AudioReasonCode.SCK_TCC_DENIED,
                detail=f"ScreenCaptureKit process terminated early (exit code {sck_code}): {err}",
                remediation="Grant Screen & System Audio Recording permission in macOS System Settings.",
            )
        raise AudioCaptureUnprovenError(
            stage="P0_PROCESS_ALIVE", reason_code=AudioReasonCode.SCK_TAP_FAILED,
            detail=f"ScreenCaptureKit tap process exited unexpectedly (exit code {sck_code}): {err}",
        )

    @classmethod
    def _check_ffmpeg_process(cls, ffmpeg_proc: Optional[subprocess.Popen], stderr_log_path: Optional[Path]) -> None:
        if ffmpeg_proc is None:
            return
        ff_code = ffmpeg_proc.poll()
        if ff_code is None:
            return
        err_text = ""
        if stderr_log_path and stderr_log_path.exists():
            with contextlib.suppress(Exception):
                err_text = stderr_log_path.read_text(encoding="utf-8", errors="replace").strip()
        if not err_text and ffmpeg_proc.stderr:
            with contextlib.suppress(Exception):
                err_text = ffmpeg_proc.stderr.read().decode("utf-8", errors="replace").strip()

        err_lower = err_text.lower()
        if "permission denied" in err_lower or "not permitted" in err_lower:
            raise AudioCaptureUnprovenError(
                stage="P0_PROCESS_ALIVE", reason_code=AudioReasonCode.CAPTURE_STDERR_DENIED,
                detail=f"FFmpeg audio capture denied by OS (exit code {ff_code}): {err_text}",
                remediation="Grant Microphone access in System Settings → Privacy & Security → Microphone.",
            )
        if "unknown input format" in err_lower or "device not found" in err_lower:
            raise AudioCaptureUnprovenError(
                stage="P0_PROCESS_ALIVE", reason_code=AudioReasonCode.DEVICE_NOT_FOUND,
                detail=f"Requested audio device could not be opened by FFmpeg: {err_text}",
            )
        raise AudioCaptureUnprovenError(
            stage="P0_PROCESS_ALIVE", reason_code=AudioReasonCode.FFMPEG_EARLY_EXIT,
            detail=f"FFmpeg capture process exited prematurely (exit code {ff_code}): {err_text}",
        )

    @classmethod
    def verify_processes_alive(
        cls,
        ffmpeg_proc: Optional[subprocess.Popen],
        sck_proc: Optional[subprocess.Popen] = None,
        stderr_log_path: Optional[Path] = None,
        wait_ms: int = 300,
    ) -> None:
        """P0: Verifies capture processes remain alive at +wait_ms and stderr is clean."""
        time.sleep(float(wait_ms) / 1000.0)
        cls._check_sck_process(sck_proc)
        cls._check_ffmpeg_process(ffmpeg_proc, stderr_log_path)

    @staticmethod
    def _probe_wav_header_offset(master_wav: Path, file_size: int, default_offset: int) -> int:
        if file_size < 12:
            return default_offset
        with contextlib.suppress(Exception):
            with open(master_wav, "rb") as fh:
                parsed = find_wav_data_offset(fh.read(min(4096, file_size)))
            if parsed > 0:
                return parsed
        return default_offset

    @classmethod
    def _sample_pcm_stream(
        cls,
        master_wav: Path,
        deadline: float,
        min_expected_pcm_bytes: int,
        expected_bps: float,
        min_rate_ratio: float,
        t_start: float,
    ) -> Tuple[int, int, Optional[float]]:
        file_size, header_offset, first_data_time = 0, 44, None
        while time.perf_counter() < deadline:
            if not master_wav.exists():
                time.sleep(0.05)
                continue
            file_size = master_wav.stat().st_size
            header_offset = cls._probe_wav_header_offset(master_wav, file_size, header_offset)
            if file_size > header_offset and first_data_time is None:
                first_data_time = time.perf_counter()
            pcm_bytes_count = max(0, file_size - header_offset)
            if pcm_bytes_count >= min_expected_pcm_bytes:
                stream_elapsed = max(0.001, time.perf_counter() - (first_data_time or t_start))
                if (pcm_bytes_count / (stream_elapsed * expected_bps)) >= min_rate_ratio:
                    break
            time.sleep(0.05)
        return file_size, header_offset, first_data_time

    @classmethod
    def verify_pcm_byte_rate(
        cls,
        master_wav: Path,
        is_stereo: bool = False,
        min_duration_sec: float = 1.0,
        deadline_sec: float = 2.0,
        mock_mode: bool = False,
    ) -> Dict[str, Any]:
        """P1: Verifies PCM data is flowing past WAV header at >= 80% expected byte rate."""
        if mock_mode:
            raise AudioCaptureUnprovenError(
                "P1_PCM_BYTE_RATE", AudioReasonCode.MOCK_AUDIO_PROHIBITED,
                "Mock audio capture verification is prohibited; authentic PCM capture proof is required.",
            )

        expected_bps = cls.STEREO_BYTE_RATE_BPS if is_stereo else cls.MONO_BYTE_RATE_BPS
        min_expected_pcm_bytes = int(min_duration_sec * expected_bps * cls.MIN_RATE_RATIO)

        t_start = time.perf_counter()
        deadline = t_start + deadline_sec
        file_size, header_offset, first_data_time = cls._sample_pcm_stream(
            master_wav, deadline, min_expected_pcm_bytes, expected_bps, cls.MIN_RATE_RATIO, t_start
        )

        elapsed = max(0.001, time.perf_counter() - (first_data_time or t_start))
        data_bytes = max(0, file_size - header_offset)
        measured_bps = float(data_bytes) / elapsed
        rate_ratio = measured_bps / expected_bps

        if data_bytes < min_expected_pcm_bytes or rate_ratio < cls.MIN_RATE_RATIO:
            raise AudioCaptureUnprovenError(
                stage="P1_PCM_BYTE_RATE", reason_code=AudioReasonCode.PCM_BYTE_RATE_DEFICIT,
                detail=(
                    f"PCM byte rate deficit: master WAV has {file_size}B ({data_bytes}B PCM past {header_offset}B header) after {elapsed:.2f}s "
                    f"({measured_bps:.1f} B/s vs expected {expected_bps:.1f} B/s, ratio: {rate_ratio:.2f} < {cls.MIN_RATE_RATIO:.2f})."
                ),
            )

        return {
            "proven": True, "bytes_read": file_size, "header_offset": header_offset, "pcm_data_bytes": data_bytes,
            "duration_sec": round(elapsed, 3), "measured_bps": round(measured_bps, 1),
            "expected_bps": round(expected_bps, 1), "rate_ratio": round(rate_ratio, 3),
        }

    @classmethod
    def verify_mic_non_digital_zero(
        cls, pcm_bytes: bytes, is_stereo: bool = False, min_rms: float = MIN_MIC_RMS_FLOOR, mock_mode: bool = False,
    ) -> Dict[str, Any]:
        """P2: Verifies microphone PCM samples are non-digital-zero (peak-to-peak > 0, RMS >= floor)."""
        if mock_mode:
            raise AudioCaptureUnprovenError(
                "P2_MIC_NON_DIGITAL_ZERO", AudioReasonCode.MOCK_AUDIO_PROHIBITED,
                "Mock microphone verification is prohibited; authentic PCM capture proof is required.",
            )
        data_offset = find_wav_data_offset(pcm_bytes)
        raw_pcm = pcm_bytes[data_offset:]
        if is_stereo:
            raw_pcm = raw_pcm[: (len(raw_pcm) // 4) * 4]
        n_samples = len(raw_pcm) // 2
        if n_samples < 160:
            raise AudioCaptureUnprovenError(
                stage="P2_MIC_NON_DIGITAL_ZERO", reason_code=AudioReasonCode.PCM_BYTE_RATE_DEFICIT,
                detail=f"Insufficient PCM samples to evaluate microphone activity ({n_samples} samples past {data_offset}B header).",
            )
        samples = struct.unpack(f"<{n_samples}h", raw_pcm[: n_samples * 2])
        mic_samples = samples[1::2] if is_stereo else samples
        sck_samples = samples[0::2] if is_stereo else ()
        if not mic_samples:
            raise AudioCaptureUnprovenError(
                stage="P2_MIC_NON_DIGITAL_ZERO", reason_code=AudioReasonCode.MIC_DIGITAL_ZERO,
                detail="Microphone stream has 0 extracted audio samples.",
            )
        min_val, max_val = min(mic_samples), max(mic_samples)
        peak_to_peak = max_val - min_val
        rms = math.sqrt(sum(float(s) * float(s) for s in mic_samples) / len(mic_samples))
        sck_rms = math.sqrt(sum(float(s) * float(s) for s in sck_samples) / len(sck_samples)) if sck_samples else 0.0
        if peak_to_peak == 0 or rms < min_rms:
            raise AudioCaptureUnprovenError(
                stage="P2_MIC_NON_DIGITAL_ZERO", reason_code=AudioReasonCode.MIC_DIGITAL_ZERO,
                detail=(
                    f"Microphone channel is digital zero or lacks acoustic variance "
                    f"(peak-to-peak: {peak_to_peak}, RMS: {rms:.3f} < {min_rms:.1f} floor across {len(mic_samples)} samples). "
                    "This occurs when macOS grants a silent zero-filled stream due to missing TCC microphone permissions."
                ),
                remediation="Open System Settings → Privacy & Security → Microphone, and ensure Microphone permission is enabled.",
            )
        return {
            "proven": True, "mic_rms": round(rms, 2), "sck_rms": round(sck_rms, 2),
            "header_data_offset": data_offset, "peak_to_peak": peak_to_peak,
            "min_sample": min_val, "max_sample": max_val, "samples_analyzed": len(mic_samples),
        }

    @classmethod
    def verify_continuous_growth(
        cls, master_wav: Path, last_size: int, last_growth_time: float,
        stall_threshold_sec: float = 3.0, mock_mode: bool = False,
    ) -> Tuple[int, float]:
        """P3: Verifies ongoing audio growth and detects mid-session pipeline stalls."""
        if mock_mode:
            raise AudioCaptureUnprovenError(
                "P3_CONTINUOUS_GROWTH", AudioReasonCode.MOCK_AUDIO_PROHIBITED,
                "Mock continuous growth verification is prohibited; authentic growth measurement is required.",
            )
        now = time.time()
        curr_size = master_wav.stat().st_size if master_wav.exists() else 0
        if curr_size > last_size:
            return curr_size, now
        if (now - last_growth_time) > stall_threshold_sec:
            raise AudioCaptureUnprovenError(
                stage="P3_CONTINUOUS_GROWTH", reason_code=AudioReasonCode.AUDIO_GROWTH_STALLED,
                detail=f"Continuous audio growth stalled at {curr_size}B for {now - last_growth_time:.2f}s (> {stall_threshold_sec:.1f}s).",
            )
        return last_size, last_growth_time

    @classmethod
    def run_full_start_proof(
        cls, master_wav: Path, ffmpeg_proc: Optional[subprocess.Popen],
        sck_proc: Optional[subprocess.Popen] = None, is_stereo: bool = False,
        stderr_log_path: Optional[Path] = None, mock_mode: bool = False,
        receipt_path: Optional[Path] = None, deadline_monotonic: Optional[float] = None,
        strategy: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Runs the complete staged proof-of-capture sequence (P0 -> P1 -> P2)."""
        t0 = time.perf_counter()
        transitions: List[str] = ["SPAWNED"]
        if mock_mode:
            raise AudioCaptureUnprovenError(
                "START_PROOF", AudioReasonCode.MOCK_AUDIO_PROHIBITED,
                "Mock audio capture verification is prohibited; authentic capture proof is required.",
            )
        cls._check_deadline(deadline_monotonic, "START_PROOF")
        cls.verify_processes_alive(
            ffmpeg_proc=ffmpeg_proc, sck_proc=sck_proc, stderr_log_path=stderr_log_path, wait_ms=50 if not mock_mode else 10,
        )
        transitions.append("P0_ALIVE")
        cls._check_deadline(deadline_monotonic, "P0_PROCESS_ALIVE")
        p1_res = cls.verify_pcm_byte_rate(
            master_wav=master_wav, is_stereo=is_stereo,
            min_duration_sec=0.30 if not mock_mode else 0.1, deadline_sec=0.2 if mock_mode else 6.0, mock_mode=mock_mode,
        )
        transitions.append("P1_RATE_OK")
        pcm_bytes = b""
        if master_wav.exists():
            with contextlib.suppress(Exception):
                pcm_bytes = master_wav.read_bytes()
        p2_res = cls.verify_mic_non_digital_zero(pcm_bytes=pcm_bytes, is_stereo=is_stereo, mock_mode=mock_mode)
        transitions.extend(("P2_MIC_ACOUSTIC_PROVEN", "CAPTURE_PROVEN"))
        receipt = {
            "status": "CAPTURE_PROVEN", "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_proof_ms": round((time.perf_counter() - t0) * 1000.0, 2),
            "strategy": strategy or ("mixed_sck" if (sck_proc is not None or is_stereo) else "microphone"),
            "is_stereo": is_stereo, "ffmpeg_pid": ffmpeg_proc.pid if ffmpeg_proc else None,
            "sck_pid": sck_proc.pid if sck_proc else None, "transitions": transitions,
            "p1_byte_rate": p1_res, "p2_mic_acoustic": p2_res, "master_wav": str(master_wav),
            "master_wav_sha256": hashlib.sha256(pcm_bytes[:4096]).hexdigest() if pcm_bytes else None,
        }
        if receipt_path is not None:
            try:
                receipt_path.parent.mkdir(parents=True, exist_ok=True)
                receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning("Could not persist audio capture receipt to %s: %s", receipt_path, e)
        return receipt
