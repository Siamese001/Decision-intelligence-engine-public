"""Apple Silicon Darwin Thermal Pressure Governor & Load-Shedding Engine.

Monitors macOS NSProcessInfo thermal states and sheds background batch tasks
(such as heavy BGE embeddings and offline LLM summarization) when thermal
pressure reaches Level 2 (SERIOUS) or Level 3 (CRITICAL), preserving CoreAudio
HAL real-time buffers and interactive responsiveness.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import platform
import subprocess
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.runtime.thermal_governor")


class ThermalState(IntEnum):
    """Darwin NSProcessInfoThermalState mapping."""

    NOMINAL = 0
    FAIR = 1
    SERIOUS = 2
    CRITICAL = 3


@dataclass(frozen=True)
class ThermalSnapshot:
    """Immutable point-in-time thermal telemetry record."""

    state: ThermalState
    raw_level: int
    timestamp: float
    should_throttle_background: bool
    should_pause_embeddings: bool
    audio_priority_preserved: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.name,
            "raw_level": self.raw_level,
            "timestamp": self.timestamp,
            "should_throttle_background": self.should_throttle_background,
            "should_pause_embeddings": self.should_pause_embeddings,
            "audio_priority_preserved": self.audio_priority_preserved,
        }


class DarwinThermalGovernor:
    """Governs background compute workloads based on Darwin kernel thermal pressure."""

    def __init__(self, check_interval_sec: float = 2.0) -> None:
        self.check_interval_sec = check_interval_sec
        self._override_state: Optional[ThermalState] = None
        self._last_snapshot: Optional[ThermalSnapshot] = None
        self._last_check_time: float = 0.0
        self._callbacks: List[Callable[[ThermalSnapshot], None]] = []
        self._shed_count: int = 0
        self._poll_count: int = 0
        self._is_darwin: bool = platform.system() == "Darwin"
        self._objc_available: bool = False
        self._init_objc()

    def _init_objc(self) -> None:
        if not self._is_darwin:
            return
        try:
            lib = ctypes.util.find_library("objc")
            if not lib:
                return
            self._objc = ctypes.cdll.LoadLibrary(lib)
            self._objc.objc_getClass.restype = ctypes.c_void_p
            self._objc.sel_registerName.restype = ctypes.c_void_p
            self._objc.objc_msgSend.restype = ctypes.c_void_p
            self._objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            self._NSProcessInfo = self._objc.objc_getClass(b"NSProcessInfo")
            self._processInfoSel = self._objc.sel_registerName(b"processInfo")
            self._thermalStateSel = self._objc.sel_registerName(b"thermalState")
            self._msgSend_int = ctypes.cast(
                self._objc.objc_msgSend,
                ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p),
            )
            self._objc_available = True
        except Exception as exc:
            logger.debug("Failed initializing native objc NSProcessInfo: %s", exc)
            self._objc_available = False

    def set_override_state(self, state: Optional[ThermalState]) -> None:
        """Sets a test fixture or simulated override state."""
        self._override_state = state

    def register_callback(self, callback: Callable[[ThermalSnapshot], None]) -> None:
        """Registers a load-shedding callback invoked when thermal state reaches SERIOUS or CRITICAL."""
        self._callbacks.append(callback)

    def _query_objc_thermal_state(self) -> Optional[int]:
        if not self._objc_available:
            return None
        try:
            info = self._objc.objc_msgSend(self._NSProcessInfo, self._processInfoSel)
            if info:
                val = self._msgSend_int(info, self._thermalStateSel)
                if 0 <= val <= 3:
                    return int(val)
        except Exception as exc:
            logger.debug("Native NSProcessInfo query failed: %s", exc)
        return None

    def _query_pmset_thermal_state(self) -> int:
        if not self._is_darwin:
            return int(ThermalState.NOMINAL)
        try:
            res = subprocess.run(["pmset", "-g", "therm"], timeout=1.0, capture_output=True, text=True, check=False)
            output = res.stdout.lower()
            if "critical" in output or "trapping" in output:
                return int(ThermalState.CRITICAL)
            if "heavy" in output or "serious" in output:
                return int(ThermalState.SERIOUS)
            if "moderate" in output or "fair" in output:
                return int(ThermalState.FAIR)
        except Exception:
            pass
        return int(ThermalState.NOMINAL)

    def _query_native_thermal_state(self) -> int:
        if self._override_state is not None:
            return int(self._override_state)
        objc_val = self._query_objc_thermal_state()
        if objc_val is not None:
            return objc_val
        return self._query_pmset_thermal_state()

    def _dispatch_load_shed(self, snapshot: ThermalSnapshot, prev: Optional[ThermalSnapshot]) -> None:
        self._shed_count += 1
        if prev is not None and prev.should_throttle_background:
            return
        logger.warning(
            "Thermal pressure reached Level %s (%s). Shedding background load.",
            snapshot.raw_level,
            snapshot.state.name,
        )
        for cb in self._callbacks:
            try:
                cb(snapshot)
            except Exception as exc:
                logger.error("Error invoking load-shed callback: %s", exc)

    def poll_thermal_state(self, force: bool = False) -> ThermalSnapshot:
        """Evaluates current thermal pressure and determines load-shedding actions."""
        now = time.time()
        if not force and self._last_snapshot is not None:
            if (now - self._last_check_time) < self.check_interval_sec:
                return self._last_snapshot

        self._poll_count += 1
        raw_val = self._query_native_thermal_state()
        try:
            state = ThermalState(raw_val)
        except ValueError:
            state = ThermalState.NOMINAL

        throttle = state >= ThermalState.SERIOUS
        snapshot = ThermalSnapshot(
            state=state,
            raw_level=int(state),
            timestamp=now,
            should_throttle_background=throttle,
            should_pause_embeddings=throttle,
            audio_priority_preserved=True,
        )

        prev = self._last_snapshot
        self._last_snapshot = snapshot
        self._last_check_time = now

        if throttle:
            self._dispatch_load_shed(snapshot, prev)

        return snapshot

    def can_admit_background_task(self, task_name: str = "batch_embedding") -> bool:
        """Determines if a non-critical background task can be admitted."""
        snapshot = self.poll_thermal_state()
        if snapshot.should_throttle_background:
            logger.info("Rejected task %s due to thermal pressure %s", task_name, snapshot.state.name)
            return False
        return True

    def get_telemetry(self) -> Dict[str, Any]:
        """Returns structured thermal governor telemetry."""
        snapshot = self.poll_thermal_state()
        return {
            "current_state": snapshot.to_dict(),
            "total_polls": self._poll_count,
            "shed_events": self._shed_count,
            "check_interval_sec": self.check_interval_sec,
            "objc_native_active": self._objc_available,
        }


__all__ = [
    "DarwinThermalGovernor",
    "ThermalSnapshot",
    "ThermalState",
]
