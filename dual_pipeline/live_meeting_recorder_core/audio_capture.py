"""Live Meeting Recorder Core: Multi-Source Audio Capture Manager on Apple macOS.
Supports system digital audio (ScreenCaptureKit/loopback), acoustic mic, mixed, and mock modes.
Guarantees anti-headphone trap prevention and zero-loss continuous raw audio spooling.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
    AudioCaptureProofVerifier,
    AudioCaptureUnprovenError,
    AudioReasonCode,
)
from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    generate_synthetic_speech_chunk as _generate_synthetic_speech_chunk,
)
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioSubprocessWatchdog
from dual_pipeline.live_meeting_recorder_core.hardware_classifier import HardwareDeviceClassifier
from dual_pipeline.live_meeting_recorder_core.process_cleanup import (
    close_process_pipes,
    detect_and_reap_device_contention,
    reap_descendants,
    send_sigint_flush,
    start_pipe_drain_thread,
    stop_capture_session,
    terminate_process,
)
from dual_pipeline.live_meeting_recorder_core.audio_capture_strategy import (
    AudioStarvationDetector,
    AudioStrategyProfileCache,
    _ensure_sck_binary,
    is_sck_tap_available,
)
from pipeline_a_intake.coreaudio_hal import CoreAudioHAL
from pipeline_a_intake.mac_system_audio import (
    FFMPEG_BIN,
    SCK_COMPILED_BIN,
    AudioDeviceInfo,
    CoreAudioDeviceManager,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_capture")


class LiveAudioCaptureManager:
    """Supervises live hardware and digital audio streams, piping audio into FFmpeg segmenters."""

    def __init__(
        self,
        chunks_dir: Path,
        master_wav: Path,
        source_mode: str = "auto",
        device_index: str = ":0",
        segment_seconds: int = 15,
        start_chunk_idx: int = 0,
        mock_mode: bool = False,
        enable_stereo_isolation: Optional[bool] = None,
        deadline_monotonic: Optional[float] = None,
        skip_device_discovery: bool = False,
    ):
        self.chunks_dir, self.master_wav = Path(chunks_dir), Path(master_wav)
        self.source_mode = source_mode.lower().strip() or "auto"
        self.deadline_monotonic = deadline_monotonic
        prefer_builtin = self.source_mode in ("iphone-speaker", "iphone_speaker")
        self.skip_device_discovery = bool(skip_device_discovery)
        if (not device_index or device_index == ":0") and not self.skip_device_discovery:
            self.device_index = HardwareDeviceClassifier.resolve_safe_device_index(
                preferred_index=":0",
                devices=CoreAudioDeviceManager.list_devices(),
                prefer_builtin_mic=prefer_builtin,
            )
        else:
            self.device_index = device_index or ":0"
        self.segment_seconds, self.start_chunk_idx = segment_seconds, start_chunk_idx
        self.mock_mode = bool(mock_mode or (self.source_mode == "mock"))
        self.enable_stereo_isolation = (not prefer_builtin) if enable_stereo_isolation is None else bool(enable_stereo_isolation)
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.master_wav.parent.mkdir(parents=True, exist_ok=True)

        self._ffmpeg_proc, self._sck_proc, self._mock_thread = None, None, None
        self._ffmpeg_stderr_file: Optional[Any] = None
        self._is_running, self._active_strategy = False, "unknown"
        self._capture_epoch: int = 0
        self._detected_headphones, self._detected_mute = False, False
        self._sck_permanently_unavailable = False
        self._start_receipt: Optional[Dict[str, Any]] = None
        self._startup_exception: Optional[Exception] = None
        self._watchdog_exception: Optional[AudioCaptureUnprovenError] = None
        self._startup_failure_record: Optional[Dict[str, Any]] = None
        self.last_growth_time, self.last_master_size = time.time(), 0
        self._state_lock, self._failover_in_flight = threading.RLock(), False
        self.CIRCUIT_BREAKER_MAX_FAILOVERS, self.CIRCUIT_BREAKER_WINDOW_SEC, self.SENTINEL_BASE_COOLDOWN_SEC = 3, 60.0, 30.0
        self._failover_timestamps, self._last_failover_time, self._sck_failure_count = [], 0.0, 0
        self._watchdog = AudioSubprocessWatchdog(
            on_failover_callback=self._handle_watchdog_failover,
            segment_seconds=self.segment_seconds,
            zero_byte_stall_threshold_sec=max(10.0, float(self.segment_seconds) + 5.0),
        )
        self.starvation_detector = AudioStarvationDetector(threshold_rms=0.005, starvation_seconds=10.0)
        self._drain_threads, self._drain_stop_event = [], threading.Event()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def active_strategy(self) -> str:
        return self._active_strategy

    @property
    def startup_exception(self) -> Optional[Exception]:
        return self._startup_exception

    @property
    def watchdog_exception(self) -> Optional[AudioCaptureUnprovenError]:
        return self._watchdog_exception

    @property
    def startup_failure_record(self) -> Optional[Dict[str, Any]]:
        return self._startup_failure_record

    @property
    def capture_state(self) -> str:
        if self._startup_exception or self._watchdog_exception:
            return "FAILED"
        if self._is_running:
            return "CAPTURING"
        return "STOPPED"

    def get_lifecycle_status(self) -> Dict[str, Any]:
        return {
            "capture_state": self.capture_state,
            "active_strategy": self.active_strategy,
            "is_running": self._is_running,
            "has_startup_failure": self._startup_exception is not None,
            "startup_failure_record": self._startup_failure_record,
            "ffmpeg_pid": self._ffmpeg_proc.pid if self._ffmpeg_proc else None,
            "sck_pid": self._sck_proc.pid if self._sck_proc else None,
        }

    def _persist_startup_failure(self, exc: Exception, stage: str = "startup") -> Optional[Path]:
        """Persists startup failure record to disk before cleanup cascades begin."""
        try:
            import traceback
            from datetime import datetime, timezone
            failure_path = self.master_wav.parent / "record_start_failure.json"
            failure_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(), "stage": stage,
                "reason_code": getattr(exc, "reason_code", None), "error": str(exc),
                "exception_type": type(exc).__name__, "exception_message": str(exc),
                "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                "master_wav": str(self.master_wav), "strategy": getattr(self, "_active_strategy", None),
                "device_index": getattr(self, "device_index", None),
                "ffmpeg_pid": getattr(self._ffmpeg_proc, "pid", None) if self._ffmpeg_proc else None,
                "sck_pid": getattr(self._sck_proc, "pid", None) if self._sck_proc else None,
            }
            failure_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
            self._startup_failure_record, self._startup_exception = record, exc
            logger.info("Persisted startup failure record to %s", failure_path)
            return failure_path
        except Exception as persist_err:
            logger.warning("Failed to persist startup failure record: %s", persist_err)
            self._startup_exception = exc
            return None

    def _assert_system_audio_viable(self) -> None:
        if self._detected_headphones or self._detected_mute:
            self._sck_permanently_unavailable = True
            raise AudioCaptureUnprovenError(
                stage="STRATEGY_RESOLUTION", reason_code=AudioReasonCode.SCK_TAP_FAILED,
                detail=(
                    "Headphones are connected or system is muted, but digital system audio capture "
                    "(ScreenCaptureKit/Loopback) is unavailable or denied. Silent degradation to microphone "
                    "is strictly prohibited by Operator Decision D1."
                ),
                remediation="Grant Screen & System Audio Recording in macOS System Settings.",
            )

    def _resolve_system_strategy(
        self, loopback: Optional[AudioDeviceInfo], sck: bool, mic: Optional[AudioDeviceInfo]
    ) -> Tuple[str, Optional[AudioDeviceInfo]]:
        if loopback: return "loopback", loopback
        if sck: return "screen_capture_kit", None
        self._assert_system_audio_viable()
        raise AudioCaptureUnprovenError(
            stage="STRATEGY_RESOLUTION", reason_code=AudioReasonCode.SCK_TAP_FAILED,
            detail="System audio capture requested, but ScreenCaptureKit tap is unavailable and no loopback device found.",
            remediation="Grant Screen & System Audio Recording in macOS System Settings.",
        )

    def _resolve_mixed_strategy(
        self, loopback: Optional[AudioDeviceInfo], sck: bool, mic: Optional[AudioDeviceInfo]
    ) -> Tuple[str, Optional[AudioDeviceInfo]]:
        if sck: return "mixed_sck", mic
        if loopback: return "mixed_loopback", loopback
        self._assert_system_audio_viable()
        raise AudioCaptureUnprovenError(
            stage="STRATEGY_RESOLUTION", reason_code=AudioReasonCode.SCK_TAP_FAILED,
            detail="Dual mixed audio requested, but ScreenCaptureKit tap is unavailable and no loopback device found.",
            remediation="Grant Screen & System Audio Recording in macOS System Settings.",
        )

    def adopt_spooler(self, spooler: Any) -> None:
        """Adopts an existing, actively running InstantAudioSpooler to enforce single capture authority (F9)."""
        from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import adopt_spooler_into_capture_manager
        adopt_spooler_into_capture_manager(self, spooler)
        logger.info(
            "[ADOPT-SPOOLER] Adopted capture authority from spooler %s (FFmpeg PID: %s, SCK PID: %s)",
            getattr(spooler, "session_id", "unknown"), getattr(self._ffmpeg_proc, "pid", None), getattr(self._sck_proc, "pid", None),
        )

    def _lookup_cached_strategy(self, requested_device_index: Optional[str]) -> Tuple[Optional[Tuple[str, Optional[AudioDeviceInfo]]], str]:
        fast_cached = AudioStrategyProfileCache.lookup_fast(self.source_mode, requested_device_index)
        if fast_cached and (fast_cached[3], fast_cached[4]) == (self._detected_headphones, self._detected_mute):
            self.device_index = fast_cached[2]
            return (fast_cached[0], fast_cached[1]), ""
        fp = AudioStrategyProfileCache.get_topology_fingerprint(self.source_mode, requested_device_index)
        cached = AudioStrategyProfileCache.lookup(fp)
        if cached and (cached[3], cached[4]) == (self._detected_headphones, self._detected_mute):
            safe_strategy, req_dev, safe_idx, _, _ = cached
            self.device_index = safe_idx
            AudioStrategyProfileCache.store_fast(self.source_mode, requested_device_index, safe_strategy, req_dev, safe_idx, self._detected_headphones, self._detected_mute)
            return (safe_strategy, req_dev), fp
        return None, fp

    def resolve_source_strategy(self) -> Tuple[str, Optional[AudioDeviceInfo]]:
        """Determines optimal audio capture strategy based on hardware state."""
        if self.deadline_monotonic and time.monotonic() > self.deadline_monotonic:
            raise AudioCaptureUnprovenError(
                stage="STRATEGY_RESOLUTION",
                reason_code=AudioReasonCode.INITIATION_DEADLINE_EXCEEDED,
                detail="Device probe and strategy resolution exceeded initiation deadline.",
                remediation="Ensure hardware and permissions are warmed before session start.",
            )
        if self.mock_mode: return "mock", None
        requested_device_index = self.device_index
        self._detected_headphones = CoreAudioDeviceManager.is_headphones_connected()
        self._detected_mute = CoreAudioDeviceManager.is_system_muted()
        hit, fp = self._lookup_cached_strategy(requested_device_index)
        if hit is not None:
            return hit

        loopbacks = CoreAudioDeviceManager.get_loopback_devices()
        loopback, sck = (loopbacks[0] if loopbacks else None), is_sck_tap_available(check_tcc=True)
        mic = CoreAudioDeviceManager.get_default_input_device()

        prefer_builtin = self.source_mode in ("iphone-speaker", "iphone_speaker")
        if prefer_builtin:
            AudioPreflightValidator.check_mic_mode_for_speakerphone(True)
            builtin = CoreAudioHAL.find_builtin_microphone()
            if builtin:
                CoreAudioHAL.set_default_input_device_id(builtin.device_id)
                mic = builtin
            req_strategy, req_dev = "iphone_speaker", mic
        elif self.source_mode == "system":
            req_strategy, req_dev = self._resolve_system_strategy(loopback, sck, mic)
        elif self.source_mode in ("mic", "microphone"):
            req_strategy, req_dev = "microphone", mic
        elif self.source_mode in ("mixed", "both") or self._detected_headphones or self._detected_mute:
            req_strategy, req_dev = self._resolve_mixed_strategy(loopback, sck, mic)
        else:
            # Under Operator Decision D1: default strategy is ALWAYS mixed_sck
            req_strategy, req_dev = self._resolve_mixed_strategy(loopback, sck, mic)

        devices = [] if self.skip_device_discovery else CoreAudioDeviceManager.list_devices()
        if (not self.device_index or self.device_index == ":0" or HardwareDeviceClassifier.is_ble_or_non_streaming(self.device_index) or prefer_builtin) and devices:
            self.device_index = HardwareDeviceClassifier.resolve_safe_device_index(
                preferred_index=self.device_index or ":0", devices=devices, prefer_builtin_mic=prefer_builtin,
            )

        safe_strategy, safe_idx = AudioPreflightValidator.resolve_safe_strategy(
            req_strategy, sck_available=sck, ffmpeg_bin=FFMPEG_BIN, preferred_mic_index=self.device_index,
        )
        if safe_strategy != req_strategy:
            raise AudioCaptureUnprovenError(
                stage="PREFLIGHT_RESOLUTION",
                reason_code=AudioReasonCode.SCK_TCC_DENIED if not sck else AudioReasonCode.DEVICE_NOT_FOUND,
                detail=f"Requested capture strategy '{req_strategy}' cannot be satisfied without falling back to '{safe_strategy}'. Falling back is strictly prohibited by Operator Decision D1.",
                remediation="Grant Screen & System Audio Recording or Microphone permissions in macOS System Settings.",
            )
        self.device_index = safe_idx
        AudioStrategyProfileCache.store(fp, safe_strategy, req_dev, safe_idx, self._detected_headphones, self._detected_mute)
        for cache_idx in {requested_device_index, safe_idx}:
            AudioStrategyProfileCache.store_fast(self.source_mode, cache_idx, safe_strategy, req_dev, safe_idx, self._detected_headphones, self._detected_mute)
        return safe_strategy, req_dev

    def _get_chunk_pattern(self) -> str:
        epoch = int(getattr(self, "_capture_epoch", 0) or 0)
        if epoch > 0:
            return str(self.chunks_dir / f"chunk_e{epoch}_%05d.wav")
        return str(self.chunks_dir / "chunk_%05d.wav")

    def advance_capture_epoch(self) -> int:
        """Increments capture epoch on CoreAudio route change so restarted segmenters never overwrite chunk_00000.wav."""
        with self._state_lock:
            self._capture_epoch = int(getattr(self, "_capture_epoch", 0) or 0) + 1
            return self._capture_epoch

    def _dispatch_strategy(self, strategy: str, device: Optional[AudioDeviceInfo]) -> None:
        try:
            if strategy == "mock":
                self._start_mock_capture()
            elif strategy == "screen_capture_kit":
                self._start_sck_capture()
            elif strategy == "mixed_sck":
                self._start_mixed_capture(device)
            elif strategy == "mixed_loopback":
                self._start_mixed_loopback_capture(device)
            elif strategy == "loopback":
                self._start_loopback_capture(device)
            else:
                self._start_microphone_capture(device)
        except Exception as exc:
            self._persist_startup_failure(exc, stage="process_spawning")
            with contextlib.suppress(Exception):
                self.stop_processes(fast=True)
            self._is_running = False
            raise exc

    def _run_start_proof(self, strategy: str) -> Dict[str, Any]:
        receipt_path = self.master_wav.parent / "record_start_receipt.json"
        stderr_path = self.chunks_dir / "capture_stderr.log"
        try:
            is_stereo_mode = bool(strategy in ("mixed_sck", "mixed_loopback") and self.enable_stereo_isolation)
            receipt = AudioCaptureProofVerifier.run_full_start_proof(
                master_wav=self.master_wav, ffmpeg_proc=self._ffmpeg_proc, sck_proc=self._sck_proc,
                is_stereo=is_stereo_mode, stderr_log_path=stderr_path if stderr_path.exists() else None,
                mock_mode=self.mock_mode, receipt_path=receipt_path, deadline_monotonic=self.deadline_monotonic, strategy=strategy,
            )
            self._start_receipt = receipt
            return receipt
        except Exception as exc:
            self._persist_startup_failure(exc, stage="proof_verification")
            with contextlib.suppress(Exception):
                self.stop_processes(fast=True)
            self._is_running = False
            raise exc

    def start(self, run_proof: bool = True) -> Dict[str, Any]:
        """Starts capture pipeline asynchronously and runs empirical proof-of-readiness."""
        if self._is_running:
            return self._start_receipt or {"status": "ALREADY_RUNNING"}
        if self.deadline_monotonic and time.monotonic() > self.deadline_monotonic:
            raise AudioCaptureUnprovenError(
                stage="STARTUP", reason_code=AudioReasonCode.INITIATION_DEADLINE_EXCEEDED,
                detail="Initiation deadline exceeded before capture start.",
                remediation="Ensure hardware and permissions are warmed before session start.",
            )
        strategy, device = self.resolve_source_strategy()
        self._active_strategy, self._is_running = strategy, True
        logger.info("Starting LiveAudioCaptureManager: strategy=%s, headphones=%s", strategy, self._detected_headphones)
        if strategy != "mock" and self.detect_device_contention(self.device_index):
            logger.warning("[DEVICE-CONTENTION-WARNING] Competing FFmpeg process detected accessing %s.", self.device_index)
        self._dispatch_strategy(strategy, device)
        if run_proof and strategy != "mock" and not self.mock_mode:
            return self._run_start_proof(strategy)
        return {"status": "SPAWNED", "strategy": strategy}

    def _start_mock_capture(self) -> None:
        def _mock_loop():
            idx = self.start_chunk_idx
            while self._is_running:
                target_chunk = self.chunks_dir / f"chunk_{idx:05d}.wav"
                target_chunk_4d = self.chunks_dir / f"chunk_{idx:04d}.wav"
                if not target_chunk.exists() and not target_chunk_4d.exists():
                    _generate_synthetic_speech_chunk(target_chunk, duration_sec=float(self.segment_seconds))
                idx += 1
                time.sleep(float(self.segment_seconds) * 0.1)
        self._mock_thread = threading.Thread(target=_mock_loop, daemon=True)
        self._mock_thread.start()

    def _verify_sck_process(self, device: Optional[AudioDeviceInfo] = None) -> bool:
        if not self._sck_proc: return False
        # Immediate check if child process crashed on launch
        retcode = self._sck_proc.poll()
        if retcode is None:
            # Healthy running process
            return True

        err_msg = getattr(self, "_last_sck_stderr", "") or ""
        if not err_msg and self._sck_proc.stderr and not getattr(self, "_drain_threads", None):
            try: err_msg = self._sck_proc.stderr.read().decode("utf-8", errors="replace").strip()
            except Exception: pass
        self._sck_proc = None
        if self._ffmpeg_proc:
            self._terminate_process(self._ffmpeg_proc)
            self._ffmpeg_proc = None

        self._sck_permanently_unavailable = True

        reason = (
            AudioReasonCode.SCK_TCC_DENIED
            if ("permission denied" in err_msg.lower() or "tcc" in err_msg.lower() or retcode == 1)
            else AudioReasonCode.SCK_TAP_FAILED
        )
        raise AudioCaptureUnprovenError(
            stage="P0_SCK_VERIFY",
            reason_code=reason,
            detail=f"ScreenCaptureKit audio tap initialization failed (exit code {retcode}: {err_msg}). Silent fallback to microphone is strictly prohibited (Operator Decision D1).",
            remediation="Grant Screen & System Audio Recording permissions in macOS System Settings.",
        )

    def _start_sck_stderr_drain(self, proc: subprocess.Popen) -> None:
        """Drains ScreenCaptureKit stderr in background thread to avoid OS pipe deadlock."""
        def _cb(line: str) -> None:
            self._last_sck_stderr = line

        t = start_pipe_drain_thread(
            proc,
            getattr(self, "_drain_stop_event", None),
            _cb,
            name=f"sck-stderr-drain-{getattr(proc, 'pid', 'unknown')}",
        )
        if t and hasattr(self, "_drain_threads"):
            self._drain_threads.append(t)

    def _get_capture_stderr_file(self) -> Any:
        with self._state_lock:
            if getattr(self, "_ffmpeg_stderr_file", None) is None or self._ffmpeg_stderr_file.closed:
                log_path = self.chunks_dir / "capture_stderr.log"
                self._ffmpeg_stderr_file = open(log_path, "a", encoding="utf-8")
            return self._ffmpeg_stderr_file

    def _start_sck_capture(self) -> None:
        bin_path = _ensure_sck_binary()
        if not bin_path.exists():
            raise AudioCaptureUnprovenError(
                stage="SCK_START", reason_code=AudioReasonCode.SCK_TAP_FAILED,
                detail=f"ScreenCaptureKit binary unavailable at {bin_path}.",
                remediation="Compile the tap binary via 'python tools/build_mac_audio_tap.py'.",
            )
        chunk_pat = self._get_chunk_pattern()
        ffmpeg_cmd = [
            FFMPEG_BIN, "-y", "-fflags", "nobuffer", "-flags", "low_delay",
            "-probesize", "32", "-analyzeduration", "0", "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0",
            "-ar", "16000", "-ac", "1", "-flush_packets", "1", str(self.master_wav),
            "-ar", "16000", "-ac", "1", "-f", "segment", "-segment_time", str(self.segment_seconds),
            "-segment_start_number", str(self.start_chunk_idx), "-reset_timestamps", "1", "-flush_packets", "1", chunk_pat,
        ]
        try:
            self._sck_proc = subprocess.Popen([str(bin_path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            self._start_sck_stderr_drain(self._sck_proc)
            self._ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=self._sck_proc.stdout, stdout=subprocess.DEVNULL, stderr=self._get_capture_stderr_file(), start_new_session=True)
            if self._sck_proc and self._sck_proc.stdout:
                with contextlib.suppress(Exception):
                    self._sck_proc.stdout.close()
            self._watchdog.register_processes([self._sck_proc, self._ffmpeg_proc], self.chunks_dir)
            self._watchdog.start()
            self._verify_sck_process(None)
        except Exception as e:
            if isinstance(e, AudioCaptureUnprovenError):
                raise
            logger.error("Failed to spawn ScreenCaptureKit capture: %s", e)
            self._sck_permanently_unavailable = True
            raise AudioCaptureUnprovenError(
                stage="SCK_START", reason_code=AudioReasonCode.FFMPEG_SPAWN_FAILED,
                detail=f"Failed to spawn ScreenCaptureKit capture: {e}",
            ) from e

    def build_mixed_ffmpeg_command(self, mic_index: str) -> List[str]:
        """Construct the FFmpeg command line for mixed ScreenCaptureKit + mic capture."""
        chunk_pat = self._get_chunk_pattern()
        resample_mono = "aresample=async=1000:min_hard_comp=0.100000,aformat=channel_layouts=mono"
        mix_op = "amerge=inputs=2" if self.enable_stereo_isolation else "amix=inputs=2:duration=longest:dropout_transition=0:weights=1 1"
        filter_str = f"[0:a]{resample_mono}[a0];[1:a]{resample_mono}[a1];[a0][a1]{mix_op},asetpts=PTS-STARTPTS,asplit=2[aout0][aout1]"
        out_ac = "2" if self.enable_stereo_isolation else "1"
        return [
            FFMPEG_BIN, "-y", "-fflags", "nobuffer", "-flags", "low_delay",
            "-probesize", "32", "-analyzeduration", "0", "-use_wallclock_as_timestamps", "1",
            "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0",
            "-probesize", "32", "-analyzeduration", "0", "-use_wallclock_as_timestamps", "1",
            "-f", "avfoundation", "-i", mic_index, "-filter_complex", filter_str,
            "-map", "[aout0]", "-ar", "16000", "-ac", out_ac, "-flush_packets", "1", str(self.master_wav),
            "-map", "[aout1]", "-ar", "16000", "-ac", out_ac, "-f", "segment", "-segment_time", str(self.segment_seconds),
            "-segment_start_number", str(self.start_chunk_idx), "-reset_timestamps", "1", "-flush_packets", "1", chunk_pat,
        ]

    def _start_mixed_capture(self, device: Optional[AudioDeviceInfo]) -> None:
        bin_path = _ensure_sck_binary()
        if not bin_path.exists():
            raise AudioCaptureUnprovenError(
                stage="MIXED_START", reason_code=AudioReasonCode.SCK_TAP_FAILED,
                detail=f"ScreenCaptureKit binary unavailable at {bin_path}.",
                remediation="Compile the tap binary via 'python tools/build_mac_audio_tap.py'.",
            )
        ffmpeg_cmd = self.build_mixed_ffmpeg_command(self._resolve_avfoundation_index(device))
        try:
            self._sck_proc = subprocess.Popen([str(bin_path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            self._start_sck_stderr_drain(self._sck_proc)
            self._ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=self._sck_proc.stdout, stdout=subprocess.DEVNULL, stderr=self._get_capture_stderr_file(), start_new_session=True)
            if self._sck_proc and self._sck_proc.stdout:
                with contextlib.suppress(Exception):
                    self._sck_proc.stdout.close()
            self._watchdog.register_processes([self._sck_proc, self._ffmpeg_proc], self.chunks_dir)
            self._watchdog.start()
            self._verify_sck_process(device)
        except Exception as e:
            if isinstance(e, AudioCaptureUnprovenError):
                raise
            logger.warning("Mixed capture failed to initialize (%s); fail-closed without fallback.", e)
            self._sck_permanently_unavailable = True
            raise AudioCaptureUnprovenError(
                stage="MIXED_START", reason_code=AudioReasonCode.FFMPEG_SPAWN_FAILED,
                detail=f"Mixed capture failed to initialize: {e}",
            ) from e

    def _start_mixed_loopback_capture(self, loopback_device: Optional[AudioDeviceInfo]) -> None:
        """Starts dual-source AVFoundation capture combining loopback (Ch0) + microphone (Ch1)."""
        loop_idx = f":{loopback_device.avfoundation_index}" if (loopback_device and loopback_device.avfoundation_index is not None) else ":1"
        mic_idx = self._resolve_avfoundation_index(None)
        if mic_idx == loop_idx:
            mic_idx = ":0"
        chunk_pat = self._get_chunk_pattern()
        resample_mono = "aresample=async=1000:min_hard_comp=0.100000,aformat=channel_layouts=mono"
        mix_op = "amerge=inputs=2" if self.enable_stereo_isolation else "amix=inputs=2:duration=longest:dropout_transition=0:weights=1 1"
        filter_str = f"[0:a]{resample_mono}[a0];[1:a]{resample_mono}[a1];[a0][a1]{mix_op},asetpts=PTS-STARTPTS,asplit=2[aout0][aout1]"
        out_ac = "2" if self.enable_stereo_isolation else "1"
        cmd = [
            FFMPEG_BIN, "-y", "-fflags", "nobuffer", "-flags", "low_delay",
            "-probesize", "32", "-analyzeduration", "0", "-f", "avfoundation", "-i", loop_idx,
            "-probesize", "32", "-analyzeduration", "0", "-f", "avfoundation", "-i", mic_idx,
            "-filter_complex", filter_str,
            "-map", "[aout0]", "-ar", "16000", "-ac", out_ac, "-flush_packets", "1", str(self.master_wav),
            "-map", "[aout1]", "-ar", "16000", "-ac", out_ac, "-f", "segment", "-segment_time", str(self.segment_seconds),
            "-segment_start_number", str(self.start_chunk_idx), "-reset_timestamps", "1", "-flush_packets", "1", chunk_pat,
        ]
        self._ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self._get_capture_stderr_file(), start_new_session=True)
        if self._ffmpeg_proc:
            self._watchdog.register_processes([self._ffmpeg_proc], self.chunks_dir)
            self._watchdog.start()

    def _start_avf_capture(self, idx: str) -> None:
        chunk_pat = self._get_chunk_pattern()
        cmd = [
            FFMPEG_BIN, "-y",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-probesize", "32", "-analyzeduration", "0",
            "-f", "avfoundation", "-i", idx,
            "-ar", "16000", "-ac", "1", "-flush_packets", "1", str(self.master_wav),
            "-ar", "16000", "-ac", "1", "-f", "segment", "-segment_time", str(self.segment_seconds), "-segment_start_number", str(self.start_chunk_idx), "-reset_timestamps", "1", "-flush_packets", "1", chunk_pat,
        ]
        self._ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=self._get_capture_stderr_file(), start_new_session=True)
        if self._ffmpeg_proc:
            self._watchdog.register_processes([self._ffmpeg_proc], self.chunks_dir)
            self._watchdog.start()

    def _start_loopback_capture(self, device: Optional[AudioDeviceInfo]) -> None:
        idx = f":{device.avfoundation_index}" if (device and device.avfoundation_index is not None) else self.device_index
        self._start_avf_capture(idx)

    def _start_microphone_capture(self, device: Optional[AudioDeviceInfo]) -> None:
        self._start_avf_capture(self._resolve_avfoundation_index(device))

    def _resolve_avfoundation_index(self, device: Optional[AudioDeviceInfo]) -> str:
        prefer_builtin = self.source_mode in ("iphone-speaker", "iphone_speaker")
        if self.device_index and self.device_index != ":0" and not HardwareDeviceClassifier.is_ble_or_non_streaming(self.device_index):
            return self.device_index
        if device and device.avfoundation_index is not None and not HardwareDeviceClassifier.is_ble_or_non_streaming(device):
            if not (prefer_builtin and getattr(device, "is_headphones", False)):
                return f":{device.avfoundation_index}"
        return HardwareDeviceClassifier.resolve_safe_device_index(
            preferred_index=self.device_index or ":0",
            prefer_builtin_mic=prefer_builtin,
        )

    def _handle_watchdog_failover(self, reason: str, exit_code: Optional[int]) -> None:
        logger.error("[AUDIO-WATCHDOG-FAILOVER] %s (exit=%s). Audio capture unproven, halting session without fallback.", reason, exit_code)
        err = AudioCaptureUnprovenError(
            stage="WATCHDOG", reason_code=AudioReasonCode.AUDIO_GROWTH_STALLED,
            detail=f"Audio watchdog detected stall or early exit: {reason} (exit code: {exit_code}).",
        )
        self._watchdog_exception = err
        self._persist_startup_failure(err, stage="watchdog")
        self.stop_processes(fast=True)
        self._is_running = False
        raise err

    def fallback_to_microphone(
        self, next_chunk_idx: Optional[int] = None, reason: str = "Process failure / stream stall"
    ) -> bool:
        logger.critical("🛑 [FALLBACK-PROHIBITED] fallback_to_microphone called (%s) but prohibited by Operator Decision D1.", reason)
        self.stop_processes(fast=True)
        self._sck_permanently_unavailable, self._is_running = True, False
        raise AudioCaptureUnprovenError(
            stage="FALLBACK_GUARD", reason_code=AudioReasonCode.AUDIO_GROWTH_STALLED,
            detail=f"Audio fallback to microphone is strictly prohibited by repository governance (Operator Decision D1). Triggered by: {reason}",
        )

    def failover_to_screen_capture_kit(self, next_chunk_idx: Optional[int] = None) -> bool:
        """Disabled per Operator Decision D1: dual mixed_sck captures both sources from start."""
        logger.debug("[FAILOVER-SUPPRESSED] Mid-session source switching is disabled under Operator Decision D1.")
        return False

    def check_headphone_trap_sentinel(self, next_chunk_idx: Optional[int] = None) -> bool:
        """Disabled per Operator Decision D1: dual mixed_sck captures both sources from start."""
        return False

    @classmethod
    def detect_device_contention(cls, device_index: str = ":0", auto_reap: bool = True) -> bool:
        """Detects whether competing FFmpeg processes are actively capturing from avfoundation."""
        return detect_and_reap_device_contention(device_index=device_index, auto_reap=auto_reap)

    _reap_descendants = staticmethod(reap_descendants)
    _close_process_pipes = staticmethod(close_process_pipes)
    _terminate_process = staticmethod(terminate_process)

    def stop_processes(self, fast: bool = False) -> None:
        self._is_running = False
        stop_capture_session(
            getattr(self, "_watchdog", None), getattr(self, "_drain_stop_event", None),
            getattr(self, "_drain_threads", None), (self._sck_proc, self._ffmpeg_proc), fast=fast,
        )
        self._sck_proc, self._ffmpeg_proc = None, None
        self._finalize_riff()
        with self._state_lock:
            if getattr(self, "_ffmpeg_stderr_file", None) is not None and not self._ffmpeg_stderr_file.closed:
                with contextlib.suppress(Exception):
                    self._ffmpeg_stderr_file.flush()
                    self._ffmpeg_stderr_file.close()
                self._ffmpeg_stderr_file = None

    def _finalize_riff(self) -> None:
        """Repairs RIFF/data size headers on master WAV and all finalized chunk WAVs after FFmpeg exits."""
        with contextlib.suppress(Exception):
            from dual_pipeline.live_meeting_recorder_core.process_cleanup import finalize_wav_riff_header
            if getattr(self, "master_wav", None) and Path(self.master_wav).is_file():
                finalize_wav_riff_header(self.master_wav)
            if getattr(self, "chunks_dir", None) and Path(self.chunks_dir).is_dir():
                for chunk_wav in Path(self.chunks_dir).glob("chunk_*.wav"):
                    finalize_wav_riff_header(chunk_wav)

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.stop_processes()

    def graceful_stop(self) -> None:
        """Sends SIGINT to capture subprocesses to allow FFmpeg to flush final segment trailers before shutdown."""
        self._is_running = False
        if self._mock_thread and self._mock_thread.is_alive():
            self._mock_thread.join(timeout=1.0)
            self._mock_thread = None
        if hasattr(self, "_watchdog"):
            with contextlib.suppress(Exception):
                self._watchdog.stop()
        for p in (self._sck_proc, self._ffmpeg_proc):
            send_sigint_flush(p)
        # Wait briefly for FFmpeg to finish trailer writes before repairing RIFF headers
        if self._ffmpeg_proc is not None and hasattr(self._ffmpeg_proc, "wait"):
            with contextlib.suppress(Exception):
                self._ffmpeg_proc.wait(timeout=1.0)
        if self._ffmpeg_proc is None or (hasattr(self._ffmpeg_proc, "poll") and self._ffmpeg_proc.poll() is not None):
            self._finalize_riff()
        with self._state_lock:
            if getattr(self, "_ffmpeg_stderr_file", None) is not None and not self._ffmpeg_stderr_file.closed:
                with contextlib.suppress(Exception):
                    self._ffmpeg_stderr_file.flush()

    def stop(self) -> None:
        self._is_running = False
        self.stop_processes()
        if self._mock_thread and self._mock_thread.is_alive():
            self._mock_thread.join(timeout=1.0)
            self._mock_thread = None

MultiSourceAudioCaptureManager = LiveAudioCaptureManager
