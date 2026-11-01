"""
Memory Governor for Apple Silicon Metal Unified Memory (M5 Pro 48GB).

Enforces the 48 GB hardware memory envelope:
- 6.0 GB reserved for macOS, Metal GPU drivers, Python, and system safety headroom.
- 42.0 GB maximum hard limit for concurrent resident weights and inference KV cache.
- 38.0 GB recommended safe operational threshold to prevent swapping or OS throttling.
- Process-incarnation and distinct allocation tracking (duplicate keys cannot undercount).
- Resident vs transient budget separation and in-flight work reference counting.
- Reclamation invariant: cannot reclaim allocation while in-flight references > 0 unless worker is dead.
- Strict separation of framework allocations vs host OS memory counters.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import psutil

logger = logging.getLogger("dual_pipeline.runtime.memory_governor")

TOTAL_UNIFIED_MEMORY_GB: float = 48.0
OS_RESERVED_MEMORY_GB: float = 6.0
MAX_ALLOCATABLE_MEMORY_GB: float = 42.0
RECOMMENDED_SAFE_MEMORY_GB: float = 38.0

HARDWARE_MEMORY_PROFILES: Dict[str, Dict[str, Any]] = {
    "m5_pro_48gb": {
        "chip": "Apple M5 Pro",
        "unified_memory_gb": 48.0,
        "metal_gpu_enabled": True,
        "os_reserved_memory_gb": 6.0,
        "max_allocatable_memory_gb": 42.0,
        "recommended_safe_memory_gb": 38.0,
    },
    "m5_pro_64gb": {
        "chip": "Apple M5 Pro / Max",
        "unified_memory_gb": 64.0,
        "metal_gpu_enabled": True,
        "os_reserved_memory_gb": 10.0,
        "max_allocatable_memory_gb": 54.0,
        "recommended_safe_memory_gb": 48.0,
    },
    "m5_max_128gb": {
        "chip": "Apple M5 Max",
        "unified_memory_gb": 128.0,
        "metal_gpu_enabled": True,
        "os_reserved_memory_gb": 16.0,
        "max_allocatable_memory_gb": 112.0,
        "recommended_safe_memory_gb": 96.0,
    },
}

KV_CACHE_PROFILES: Dict[str, Dict[str, Any]] = {
    "llama_3_1_8b_fp16": {
        "model_key": "llama_3_1_8b",
        "kv_format": "fp16",
        "base_model_gb": 4.5,
        "kv_cache_peak_gb": 1.72,
        "total_peak_gb": 6.22,
        "tokens_per_sec": 32.4,
    },
    "llama_3_1_8b_4bit_kv": {
        "model_key": "llama_3_1_8b",
        "kv_format": "4bit_kv",
        "base_model_gb": 3.65,
        "kv_cache_peak_gb": 0.43,
        "total_peak_gb": 4.08,
        "tokens_per_sec": 32.8,
    },
    "whisper_large_v3_turbo": {
        "model_key": "whisper_large_v3_turbo",
        "total_peak_gb": 4.36,
    },
    "bge_m3": {
        "model_key": "bge_m3",
        "total_peak_gb": 2.8,
    },
}



def detect_host_capacity() -> float:
    """Returns detected host total system RAM in GB."""
    try:
        vm = psutil.virtual_memory()
        return round(vm.total / (1024**3), 2)
    except Exception:
        return TOTAL_UNIFIED_MEMORY_GB


def derive_memory_limits(host_total_gb: Optional[float] = None) -> Dict[str, Any]:
    """Derives hardware memory profile and limits based on host total capacity."""
    total_gb = host_total_gb if host_total_gb is not None else detect_host_capacity()
    if total_gb >= 110.0:
        return HARDWARE_MEMORY_PROFILES["m5_max_128gb"]
    elif total_gb >= 56.0:
        return HARDWARE_MEMORY_PROFILES["m5_pro_64gb"]
    else:
        return HARDWARE_MEMORY_PROFILES["m5_pro_48gb"]


class MemoryGovernorError(Exception):
    """Base exception for memory governance failures."""
    pass


class MemoryLimitExceededError(MemoryGovernorError):
    """Raised when requested allocation violates the 48 GB unified RAM ceiling."""
    pass


class ReclamationBlockedError(MemoryGovernorError):
    """Raised when attempting to reclaim memory while in-flight references are outstanding."""
    pass


# Compatibility alias
MemoryCeilingBreachError = MemoryLimitExceededError


def is_worker_alive(pid: int) -> bool:
    """Checks whether the process with PID is currently alive on the host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@dataclass(frozen=True)
class AllocationRecord:
    """Immutable record of an active model memory allocation with process binding."""
    model_key: str
    allocated_gb: float
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    process_id: int = 0
    incarnation_id: str = "default_inc"
    allocation_id: str = ""
    resident_budget_gb: float = 0.0
    transient_budget_gb: float = 0.0
    in_flight_references: int = 0

    @property
    def total_budget_gb(self) -> float:
        return round(self.resident_budget_gb + self.transient_budget_gb, 3)


@dataclass(frozen=True)
class ZeroCopyBufferRecord:
    """Immutable record tracking shared unified memory pointer without reallocations."""
    buffer_id: str
    source_model: str
    target_model: str
    size_bytes: int
    pointer_address: Optional[int] = None
    reallocated: bool = False
    timestamp: str = ""


class MemoryGovernor:
    """Thread-safe unified memory tracking and allocation manager."""

    def __init__(
        self,
        total_memory_gb: Optional[float] = None,
        reserved_memory_gb: Optional[float] = None,
        max_allocatable_gb: Optional[float] = None,
        recommended_safe_gb: Optional[float] = None,
        *,
        total_unified_memory_gb: Optional[float] = None,
        max_active_model_budget_gb: Optional[float] = None,
        auto_detect: bool = False,
    ):
        if total_unified_memory_gb is not None:
            total_memory_gb = total_unified_memory_gb
        if max_active_model_budget_gb is not None:
            max_allocatable_gb = max_active_model_budget_gb

        ref_total = total_memory_gb if total_memory_gb is not None else (detect_host_capacity() if auto_detect else None)
        derived = derive_memory_limits(ref_total)

        self.total_memory_gb = float(total_memory_gb if total_memory_gb is not None else derived["unified_memory_gb"])
        self.reserved_memory_gb = float(reserved_memory_gb if reserved_memory_gb is not None else derived["os_reserved_memory_gb"])
        self.max_allocatable_gb = float(max_allocatable_gb if max_allocatable_gb is not None else derived["max_allocatable_memory_gb"])
        self.recommended_safe_gb = float(recommended_safe_gb if recommended_safe_gb is not None else derived["recommended_safe_memory_gb"])
        self._lock = threading.RLock()
        self._allocations_by_id: Dict[str, AllocationRecord] = {}
        self._zero_copy_buffers: Dict[str, ZeroCopyBufferRecord] = {}


    @property
    def _active_allocations(self) -> Dict[str, AllocationRecord]:
        """Compatibility view of allocations."""
        return self._allocations_by_id

    @property
    def current_allocated_gb(self) -> float:
        """Returns total active model allocations in GB."""
        with self._lock:
            return round(sum(r.allocated_gb for r in self._allocations_by_id.values()), 3)

    @property
    def active_allocated_gb(self) -> float:
        """Alias for current_allocated_gb."""
        return self.current_allocated_gb

    @property
    def available_memory_gb(self) -> float:
        """Returns remaining allocatable memory before hitting the hard limit."""
        with self._lock:
            return round(max(0.0, self.max_allocatable_gb - self.current_allocated_gb), 3)

    def _resolve_record(self, key_or_id: str) -> Optional[AllocationRecord]:
        """Resolves record by allocation_id, or by matching model_key."""
        if key_or_id in self._allocations_by_id:
            return self._allocations_by_id[key_or_id]
        # Match by model_key (preferring current process)
        current_pid = os.getpid()
        matches = [r for r in self._allocations_by_id.values() if r.model_key == key_or_id]
        if not matches:
            return None
        pid_matches = [r for r in matches if r.process_id == current_pid]
        return pid_matches[-1] if pid_matches else matches[-1]

    def reclaim_dead_workers(self) -> List[AllocationRecord]:
        """Scans active allocations and purges any belonging to dead host processes."""
        with self._lock:
            dead_alloc_ids = [
                alloc_id
                for alloc_id, rec in self._allocations_by_id.items()
                if rec.process_id > 0 and not is_worker_alive(rec.process_id)
            ]
            reclaimed: List[AllocationRecord] = []
            for alloc_id in dead_alloc_ids:
                rec = self._allocations_by_id.pop(alloc_id)
                logger.warning(
                    f"[MEMORY_GOVERNOR] Auto-reclaimed {rec.allocated_gb:.2f} GB from dead worker PID {rec.process_id} "
                    f"(allocation '{alloc_id}')."
                )
                reclaimed.append(rec)
            return reclaimed

    def can_allocate(self, model_key: str, required_gb: float, allocation_id: Optional[str] = None) -> bool:
        """Checks whether an allocation can be accommodated without violating limits."""
        with self._lock:
            self.reclaim_dead_workers()
            if required_gb <= 0:
                return True
            existing = self._allocations_by_id.get(allocation_id) if allocation_id else None
            existing_gb = existing.allocated_gb if existing else 0.0
            additional_gb = required_gb - existing_gb
            return (self.current_allocated_gb + additional_gb) <= self.max_allocatable_gb

    def register_allocation(
        self,
        model_key: str,
        required_gb: float,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        process_id: Optional[int] = None,
        incarnation_id: Optional[str] = None,
        allocation_id: Optional[str] = None,
        resident_budget_gb: Optional[float] = None,
        transient_budget_gb: Optional[float] = None,
    ) -> AllocationRecord:
        """Registers a model memory allocation with process-incarnation binding."""
        pid = process_id if process_id is not None else os.getpid()
        inc_id = incarnation_id or f"inc_{pid}_{uuid.uuid4().hex[:6]}"
        alloc_id = allocation_id or f"{model_key}_{pid}_{uuid.uuid4().hex[:8]}"

        res_gb = resident_budget_gb if resident_budget_gb is not None else required_gb
        trans_gb = transient_budget_gb or 0.0
        total_req_gb = round(res_gb + trans_gb, 3)

        with self._lock:
            if not self.can_allocate(model_key, total_req_gb, allocation_id=alloc_id):
                current = self.current_allocated_gb
                raise MemoryLimitExceededError(
                    f"Cannot allocate {total_req_gb:.2f} GB for '{model_key}' (alloc_id: {alloc_id}). "
                    f"Current active: {current:.2f} GB, Hard limit: {self.max_allocatable_gb:.2f} GB "
                    f"(Total envelope: {self.total_memory_gb:.2f} GB, Reserved: {self.reserved_memory_gb:.2f} GB)."
                )

            record = AllocationRecord(
                model_key=model_key,
                allocated_gb=total_req_gb,
                timestamp=datetime.now(timezone.utc).isoformat(),
                metadata=dict(metadata or {}),
                process_id=pid,
                incarnation_id=inc_id,
                allocation_id=alloc_id,
                resident_budget_gb=round(res_gb, 3),
                transient_budget_gb=round(trans_gb, 3),
                in_flight_references=0,
            )
            self._allocations_by_id[alloc_id] = record

            if self.current_allocated_gb > self.recommended_safe_gb:
                logger.warning(
                    f"[MEMORY_GOVERNOR] Allocation for '{model_key}' ({total_req_gb:.2f} GB) pushed total to "
                    f"{self.current_allocated_gb:.2f} GB, exceeding safe threshold ({self.recommended_safe_gb:.2f} GB)."
                )
            return record

    def acquire_in_flight(self, allocation_id_or_key: str) -> str:
        """Increments in-flight reference count for an allocation."""
        with self._lock:
            rec = self._resolve_record(allocation_id_or_key)
            if not rec:
                raise KeyError(f"Allocation '{allocation_id_or_key}' not found.")
            new_rec = AllocationRecord(
                model_key=rec.model_key,
                allocated_gb=rec.allocated_gb,
                timestamp=rec.timestamp,
                metadata=rec.metadata,
                process_id=rec.process_id,
                incarnation_id=rec.incarnation_id,
                allocation_id=rec.allocation_id,
                resident_budget_gb=rec.resident_budget_gb,
                transient_budget_gb=rec.transient_budget_gb,
                in_flight_references=rec.in_flight_references + 1,
            )
            self._allocations_by_id[rec.allocation_id] = new_rec
            return rec.allocation_id

    def release_in_flight(self, allocation_id_or_key: str) -> None:
        """Decrements in-flight reference count for an allocation."""
        with self._lock:
            rec = self._resolve_record(allocation_id_or_key)
            if not rec:
                return
            new_count = max(0, rec.in_flight_references - 1)
            new_rec = AllocationRecord(
                model_key=rec.model_key,
                allocated_gb=rec.allocated_gb,
                timestamp=rec.timestamp,
                metadata=rec.metadata,
                process_id=rec.process_id,
                incarnation_id=rec.incarnation_id,
                allocation_id=rec.allocation_id,
                resident_budget_gb=rec.resident_budget_gb,
                transient_budget_gb=rec.transient_budget_gb,
                in_flight_references=new_count,
            )
            self._allocations_by_id[rec.allocation_id] = new_rec

    def release_allocation(self, allocation_id_or_key: str, force_reclaim: bool = False) -> Optional[AllocationRecord]:
        """Releases memory allocated to a model, strictly enforcing the reclamation invariant."""
        with self._lock:
            rec = self._resolve_record(allocation_id_or_key)
            if not rec:
                return None

            # Enforce reclamation invariant: in-flight work blocks reclamation unless worker is dead
            if rec.in_flight_references > 0 and not force_reclaim:
                if is_worker_alive(rec.process_id):
                    raise ReclamationBlockedError(
                        f"Cannot reclaim allocation '{rec.allocation_id}' for '{rec.model_key}': "
                        f"{rec.in_flight_references} in-flight reference(s) active on worker PID {rec.process_id}. "
                        "Outstanding work must complete or worker must be confirmed dead."
                    )
                else:
                    logger.warning(
                        f"[MEMORY_GOVERNOR] Worker PID {rec.process_id} confirmed dead; "
                        f"reclaiming {rec.allocated_gb:.2f} GB despite in-flight references."
                    )

            del self._allocations_by_id[rec.allocation_id]
            logger.info(f"[MEMORY_GOVERNOR] Released {rec.allocated_gb:.2f} GB for '{rec.model_key}'.")
            return rec

    def register_model_allocation(self, model_name: str, allocated_gb: float, metadata: Optional[Dict[str, Any]] = None) -> AllocationRecord:
        return self.register_allocation(model_key=model_name, required_gb=allocated_gb, metadata=metadata)

    def release_model_allocation(self, model_name: str) -> Optional[AllocationRecord]:
        return self.release_allocation(allocation_id_or_key=model_name)

    def register_zero_copy_buffer(
        self,
        buffer_id: str,
        source_model: str,
        target_model: str,
        size_bytes: int,
        pointer_address: Optional[int] = None,
    ) -> ZeroCopyBufferRecord:
        """Registers a shared unified memory buffer between models without memory reallocation."""
        with self._lock:
            rec = ZeroCopyBufferRecord(
                buffer_id=buffer_id,
                source_model=source_model,
                target_model=target_model,
                size_bytes=size_bytes,
                pointer_address=pointer_address,
                reallocated=False,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
            self._zero_copy_buffers[buffer_id] = rec
            return rec

    def is_zero_copy_active(self, buffer_id: str) -> bool:
        """Checks if a zero-copy buffer is currently registered."""
        with self._lock:
            return buffer_id in self._zero_copy_buffers

    def release_zero_copy_buffer(self, buffer_id: str) -> bool:
        """Releases a zero-copy buffer registration."""
        with self._lock:
            return self._zero_copy_buffers.pop(buffer_id, None) is not None

    def get_concurrent_model_residency_gb(self, profile_keys: Optional[List[str]] = None) -> float:
        """Computes total peak resident unified memory across concurrent model profiles."""
        keys = profile_keys or ["llama_3_1_8b_4bit_kv", "whisper_large_v3_turbo", "bge_m3"]
        total = 0.0
        for k in keys:
            if k in KV_CACHE_PROFILES:
                total += KV_CACHE_PROFILES[k].get("total_peak_gb", 0.0)
        return round(total, 3)

    def reset(self) -> None:
        """Clears all active allocations."""
        with self._lock:
            self._allocations_by_id.clear()
            self._zero_copy_buffers.clear()
            logger.info("[MEMORY_GOVERNOR] All allocations cleared.")


    def get_status(self) -> Dict[str, Any]:
        """Returns structured telemetry strictly separating framework allocations vs host counters."""
        with self._lock:
            current = self.current_allocated_gb
            resident = round(sum(r.resident_budget_gb for r in self._allocations_by_id.values()), 3)
            transient = round(sum(r.transient_budget_gb for r in self._allocations_by_id.values()), 3)

            vm = psutil.virtual_memory()
            sm = psutil.swap_memory()
            host_telemetry = {
                "total_system_memory_gb": round(vm.total / (1024**3), 2),
                "host_used_memory_gb": round(vm.used / (1024**3), 2),
                "host_available_memory_gb": round(vm.available / (1024**3), 2),
                "host_memory_pressure_pct": float(vm.percent),
                "swap_used_mb": round(sm.used / (1024**2), 2),
            }

            framework_telemetry = {
                "total_unified_memory_gb": self.total_memory_gb,
                "os_reserved_memory_gb": self.reserved_memory_gb,
                "max_allocatable_memory_gb": self.max_allocatable_gb,
                "recommended_safe_memory_gb": self.recommended_safe_gb,
                "current_allocated_gb": current,
                "resident_allocated_gb": resident,
                "transient_allocated_gb": transient,
                "available_memory_gb": self.available_memory_gb,
                "utilization_ratio": round(current / self.max_allocatable_gb, 4) if self.max_allocatable_gb > 0 else 0.0,
                "is_safe_headroom": current <= self.recommended_safe_gb,
                "active_allocations_count": len(self._allocations_by_id),
                "active_models": {
                    rec.allocation_id: {
                        "model_key": rec.model_key,
                        "process_id": rec.process_id,
                        "incarnation_id": rec.incarnation_id,
                        "allocated_gb": rec.allocated_gb,
                        "resident_budget_gb": rec.resident_budget_gb,
                        "transient_budget_gb": rec.transient_budget_gb,
                        "in_flight_references": rec.in_flight_references,
                        "timestamp": rec.timestamp,
                        "metadata": rec.metadata,
                    }
                    for rec in self._allocations_by_id.values()
                },
            }

            return {
                "framework_memory": framework_telemetry,
                "host_memory": host_telemetry,
                "counters_strictly_separated": True,
                # Backwards compatibility fields
                "total_unified_memory_gb": self.total_memory_gb,
                "os_reserved_memory_gb": self.reserved_memory_gb,
                "max_allocatable_memory_gb": self.max_allocatable_gb,
                "recommended_safe_memory_gb": self.recommended_safe_gb,
                "current_allocated_gb": current,
                "available_memory_gb": self.available_memory_gb,
                "utilization_ratio": framework_telemetry["utilization_ratio"],
                "is_safe_headroom": current <= self.recommended_safe_gb,
                "active_models": framework_telemetry["active_models"],
            }


_GLOBAL_GOVERNOR: Optional[MemoryGovernor] = None
_GOVERNOR_LOCK = threading.Lock()


def get_memory_governor() -> MemoryGovernor:
    """Returns the singleton MemoryGovernor instance."""
    global _GLOBAL_GOVERNOR
    with _GOVERNOR_LOCK:
        if _GLOBAL_GOVERNOR is None:
            _GLOBAL_GOVERNOR = MemoryGovernor()
        return _GLOBAL_GOVERNOR


# Compatibility alias
UnifiedMemoryGovernor = MemoryGovernor
