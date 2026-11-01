"""dual_pipeline/live_meeting_recorder_core/audio_capture_strategy.py

Audio capture strategy profiling cache, starvation detection, and ScreenCaptureKit tap availability.
Extracts strategy caching, starvation tracking, and tap helpers from audio_capture.py
to maintain strict modular boundaries and monotonic file budget invariants.
"""
from __future__ import annotations

import contextlib
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
from typing import Dict, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.audio_preflight import AudioPreflightValidator
from pipeline_a_intake.mac_system_audio import (
    AudioDeviceInfo,
    AudioDeviceType,
    CoreAudioDeviceManager,
    SCK_COMPILED_BIN,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_capture_strategy")


def is_sck_tap_available(check_tcc: bool = False) -> bool:
    """Checks whether ScreenCaptureKit audio tap binary is available on macOS."""
    if sys.platform != "darwin" or not SCK_COMPILED_BIN.exists():
        return False
    return AudioPreflightValidator.check_sck_tcc_permission(SCK_COMPILED_BIN) if check_tcc else True


def _ensure_sck_binary() -> Path:
    bin_path = SCK_COMPILED_BIN
    if not bin_path.exists():
        logger.warning(
            "ScreenCaptureKit binary not found at %s. Runtime swiftc compilation is prohibited; precompile ahead of time.",
            bin_path,
        )
    return bin_path


class AudioStarvationDetector:
    """Detects audio starvation (e.g. during headphone connect/disconnect or mic routing failure).
    When audio RMS < threshold (0.005) persists for > starvation_seconds (10s),
    flags AUDIO_STARVATION_PAUSE to halt ingestion into Whisper rather than producing hallucinations.
    """

    def __init__(self, threshold_rms: float = 0.005, starvation_seconds: float = 10.0):
        self.threshold_rms = threshold_rms
        self.starvation_seconds = starvation_seconds
        self.silence_duration: float = 0.0
        self.is_starved: bool = False

    def record_chunk(self, rms: float, duration_seconds: float) -> str:
        """Evaluates chunk energy. Returns 'AUDIO_STARVATION_PAUSE' if starved, else 'ACTIVE'."""
        if rms < self.threshold_rms:
            self.silence_duration += duration_seconds
            if self.silence_duration >= self.starvation_seconds:
                self.is_starved = True
                return "AUDIO_STARVATION_PAUSE"
        else:
            self.silence_duration = 0.0
            self.is_starved = False
        return "ACTIVE"

    def reset(self) -> None:
        self.silence_duration = 0.0
        self.is_starved = False


class AudioStrategyProfileCache:
    """Topology-keyed profile cache avoiding repeated ~200-500ms device discovery on start."""
    _cache: Dict[str, Tuple[str, Optional[AudioDeviceInfo], str, bool, bool]] = {}
    _cache_file: Path = Path(tempfile.gettempdir()) / "nee_audio_strategy_cache.json"
    _cache_ttl_sec: float = 120.0

    @classmethod
    def _disk_load(cls, key: str) -> Optional[Tuple[str, Optional[AudioDeviceInfo], str, bool, bool]]:
        if not cls._cache_file.exists():
            return None
        try:
            with open(cls._cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            entry = data.get(key)
            if not entry or (time.time() - float(entry.get("timestamp", 0.0))) > cls._cache_ttl_sec:
                return None
            dev_data = entry.get("req_dev")
            req_dev = None
            if dev_data:
                req_dev = AudioDeviceInfo(
                    device_id=dev_data.get("device_id", ""),
                    name=dev_data.get("name", ""),
                    device_type=AudioDeviceType(dev_data.get("device_type", "input")),
                    input_channels=dev_data.get("input_channels", 0),
                    output_channels=dev_data.get("output_channels", 0),
                    sample_rate=dev_data.get("sample_rate", 48000),
                    is_default_input=dev_data.get("is_default_input", False),
                    is_default_output=dev_data.get("is_default_output", False),
                    transport=dev_data.get("transport", "builtin"),
                    avfoundation_index=dev_data.get("avfoundation_index"),
                )
            result = (
                entry["safe_strategy"],
                req_dev,
                entry["safe_idx"],
                entry.get("headphones", False),
                entry.get("mute", False),
            )
            cls._cache[key] = result
            return result
        except Exception:
            return None

    @classmethod
    def _disk_save(
        cls,
        key: str,
        safe_strategy: str,
        req_dev: Optional[AudioDeviceInfo],
        safe_idx: str,
        headphones: bool,
        mute: bool,
    ) -> None:
        try:
            data = {}
            if cls._cache_file.exists():
                with contextlib.suppress(Exception):
                    with open(cls._cache_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
            dev_dict = None
            if req_dev is not None:
                dev_dict = {
                    "device_id": getattr(req_dev, "device_id", ""),
                    "name": getattr(req_dev, "name", ""),
                    "device_type": getattr(req_dev, "device_type", "input"),
                    "input_channels": getattr(req_dev, "input_channels", 0),
                    "output_channels": getattr(req_dev, "output_channels", 0),
                    "sample_rate": getattr(req_dev, "sample_rate", 48000),
                    "is_default_input": getattr(req_dev, "is_default_input", False),
                    "is_default_output": getattr(req_dev, "is_default_output", False),
                    "transport": getattr(req_dev, "transport", "builtin"),
                    "avfoundation_index": getattr(req_dev, "avfoundation_index", None),
                }
            data[key] = {
                "timestamp": time.time(),
                "safe_strategy": safe_strategy,
                "req_dev": dev_dict,
                "safe_idx": safe_idx,
                "headphones": headphones,
                "mute": mute,
            }
            with open(cls._cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    @classmethod
    def lookup_fast(cls, source_mode: str, preferred_device_idx: str) -> Optional[Tuple[str, Optional[AudioDeviceInfo], str, bool, bool]]:
        key = f"fast_mode={source_mode}|dev={preferred_device_idx}"
        return cls._cache.get(key) or cls._disk_load(key)

    @classmethod
    def store_fast(
        cls,
        source_mode: str,
        preferred_device_idx: str,
        safe_strategy: str,
        req_dev: Optional[AudioDeviceInfo],
        safe_idx: str,
        headphones: bool,
        mute: bool,
    ) -> None:
        key = f"fast_mode={source_mode}|dev={preferred_device_idx}"
        cls._cache[key] = (safe_strategy, req_dev, safe_idx, headphones, mute)
        cls._disk_save(key, safe_strategy, req_dev, safe_idx, headphones, mute)

    @classmethod
    def get_topology_fingerprint(cls, source_mode: str, preferred_device_idx: str, sck_tcc: bool = True) -> str:
        try:
            hp = CoreAudioDeviceManager.is_headphones_connected()
            muted = CoreAudioDeviceManager.is_system_muted()
            def_dev = CoreAudioDeviceManager.get_default_input_device()
            dev_uid = getattr(def_dev, "uid", "none") or getattr(def_dev, "name", "none")
            return f"mode={source_mode}|dev={preferred_device_idx}|hp={hp}|muted={muted}|def_uid={dev_uid}|sck_tcc={sck_tcc}"
        except Exception:
            return f"mode={source_mode}|dev={preferred_device_idx}|fallback"

    @classmethod
    def lookup(cls, fingerprint: str) -> Optional[Tuple[str, Optional[AudioDeviceInfo], str, bool, bool]]:
        return cls._cache.get(fingerprint)

    @classmethod
    def store(
        cls,
        fingerprint: str,
        safe_strategy: str,
        req_dev: Optional[AudioDeviceInfo],
        safe_idx: str,
        headphones: bool,
        mute: bool,
    ) -> None:
        cls._cache[fingerprint] = (safe_strategy, req_dev, safe_idx, headphones, mute)

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()
        with contextlib.suppress(Exception):
            if cls._cache_file.exists():
                cls._cache_file.unlink()
