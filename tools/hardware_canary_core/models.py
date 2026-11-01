"""
tools/hardware_canary_core/models.py

Data models and host environment inspection for Wave 6 Hardware Qualification Canary.
Enforces:
1. Distinct mock and representative hardware evidence tiers.
2. Complete failure denominator tracking across warm and cold cohorts.
3. Model comparison parity matrix (Gemini Pro vs Flash tool invocation replay).
4. Zero live user meeting targets.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import os
import platform
import sys
from typing import Any, Dict, List, Optional


@dataclass
class HostEnvironmentMetadata:
    platform_name: str
    darwin_version: str
    architecture: str
    python_version: str
    python_executable: str
    is_apple_silicon: bool
    sck_tap_authorized: bool
    audio_devices: List[Dict[str, Any]]
    metal_inference_available: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanaryTopologyResult:
    topology_name: str
    warm_samples_requested: int
    cold_samples_requested: int
    total_attempts: int
    successful_starts: int
    failed_starts: int
    missing_pcm_count: int
    p95_dispatch_s: float
    p95_first_pcm_s: float
    p95_confirmed_warm_s: float
    p95_confirmed_cold_s: float
    is_hardware_distinct: bool
    qualified: bool
    topology_type: str = "SIMULATED"
    max_duration_s: float = 0.0
    sample_durations: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ModelComparisonRecord:
    model_id: str
    dispatch_latency_ms: float
    silent_hint_intake_success: bool
    chat_replies_emitted: int
    capture_ownership_retained: bool
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HardwareCanaryReport:
    timestamp_utc: str
    host: HostEnvironmentMetadata
    topologies: Dict[str, CanaryTopologyResult]
    model_comparison: List[ModelComparisonRecord]
    all_preceding_receipts_passed: bool
    overall_qualification_passed: bool
    rollback_policy_verified: bool
    receipt_sha256: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["host"] = self.host.to_dict()
        d["topologies"] = {k: v.to_dict() for k, v in self.topologies.items()}
        d["model_comparison"] = [m.to_dict() for m in self.model_comparison]
        return d


def inspect_host_hardware() -> HostEnvironmentMetadata:
    """Inspects the local macOS host, audio HAL, and Apple Silicon accelerator."""
    is_arm64 = platform.machine().lower() in ("arm64", "aarch64")
    is_darwin = sys.platform == "darwin"

    # CoreAudio device discovery
    audio_devs: List[Dict[str, Any]] = []
    sck_auth = False
    try:
        from pipeline_a_intake.mac_system_audio import CoreAudioDeviceManager
        devices = CoreAudioDeviceManager.list_devices()
        for d in devices:
            audio_devs.append({
                "id": getattr(d, "device_id", 0),
                "name": getattr(d, "name", "unknown"),
                "is_input": getattr(d, "is_input", False),
            })
    except Exception:
        pass

    try:
        from dual_pipeline.live_meeting_recorder_core.audio_capture import is_sck_tap_available
        sck_auth = is_sck_tap_available(check_tcc=True)
    except Exception:
        sck_auth = False

    # Metal inference capability probe
    metal_ok = False
    if is_darwin and is_arm64:
        try:
            import mlx.core as mx
            metal_ok = mx.metal.is_available()
        except Exception:
            metal_ok = False

    return HostEnvironmentMetadata(
        platform_name=sys.platform,
        darwin_version=platform.release(),
        architecture=platform.machine(),
        python_version=sys.version.split()[0],
        python_executable=sys.executable,
        is_apple_silicon=is_darwin and is_arm64,
        sck_tap_authorized=sck_auth,
        audio_devices=audio_devs,
        metal_inference_available=metal_ok,
    )


def evaluate_canary_qualification(
    topologies: List[CanaryTopologyResult],
    model_comparisons: List[ModelComparisonRecord],
    preceding_receipts_ok: bool,
    require_hardware: bool = False,
    max_duration_ceiling_s: float = 30.0,
) -> bool:
    """Evaluates strict Wave 4/6 qualification criteria:

    1. All preceding wave receipts pass.
    2. Zero missing attempts (total_attempts >= requested samples).
    3. Zero failed starts (failed_starts == 0).
    4. Zero missing PCM (missing_pcm_count == 0).
    5. All individual samples strictly < 30.0s (no p95 masking).
    6. All tested topologies qualify.
    7. A simulated PASS cannot become hardware PASS (require_hardware demands qualified HARDWARE topology).
    8. Model comparison verifies 0 chat replies and clean ownership retention across models.
    """
    if not preceding_receipts_ok:
        return False

    if not topologies:
        return False

    has_qualified_hardware = False
    for top in topologies:
        if not top.qualified:
            return False
        if top.failed_starts > 0 or top.missing_pcm_count > 0:
            return False
        req_total = top.warm_samples_requested + top.cold_samples_requested
        if top.total_attempts < req_total:
            return False
        if top.max_duration_s >= max_duration_ceiling_s:
            return False
        if any(d >= max_duration_ceiling_s for d in top.sample_durations):
            return False
        if getattr(top, "topology_type", "SIMULATED") == "HARDWARE":
            has_qualified_hardware = True

    if require_hardware and not has_qualified_hardware:
        return False

    for mc in model_comparisons:
        if mc.chat_replies_emitted > 0:
            return False
        if not mc.silent_hint_intake_success:
            return False
        if not mc.capture_ownership_retained:
            return False

    return True
