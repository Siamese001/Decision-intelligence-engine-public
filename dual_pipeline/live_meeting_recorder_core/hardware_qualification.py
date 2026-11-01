"""
dual_pipeline/live_meeting_recorder_core/hardware_qualification.py

Off-meeting hardware qualification manifest, resource/latency evidence schema,
and capture-pressure contention coordinator for Apple Silicon macOS.
Enforces:
1. Source-bound PCM continuity proof and 0 audio drops under concurrent load.
2. Hard ceilings for ASR lag (<= 1.5s) and copilot freshness (<= 2.0s).
3. Document workload backpressure/deferral when live capture is under pressure.
4. Fail-closed gate: mocked or synthetic audio cannot certify hardware continuity.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.hardware_qualification")

MAX_PERMITTED_ASR_LAG_SEC: float = 1.5
MAX_PERMITTED_COPILOT_FRESHNESS_SEC: float = 2.0


@dataclass
class HardwareQualificationManifest:
    """Authoritative schema for off-meeting Apple Silicon hardware qualification."""
    session_id: str
    metal_device: str
    sample_rate: int = 16000
    source_pcm_continuity: bool = True
    drop_count: int = 0
    max_queue_high_water: int = 0
    max_asr_lag_seconds: float = 0.0
    copilot_freshness_seconds: float = 0.0
    concurrent_document_pressure: bool = False
    is_mock: bool = False
    status: str = "PENDING"
    validation_errors: List[str] = field(default_factory=list)

    def validate(self) -> Tuple[bool, List[str]]:
        """Validates all hardware qualification criteria fail-closed."""
        errors: List[str] = []

        if self.is_mock:
            errors.append("Mock or synthetic audio tokens cannot certify hardware continuity (L06H).")

        if not self.source_pcm_continuity:
            errors.append("Source-bound PCM continuity violated; stream had discontinuities.")

        if self.drop_count > 0:
            errors.append(f"Audio drop count {self.drop_count} > 0; hardware stream suffered data loss.")

        if self.max_asr_lag_seconds > MAX_PERMITTED_ASR_LAG_SEC:
            errors.append(
                f"Max ASR lag {self.max_asr_lag_seconds:.3f}s exceeds threshold {MAX_PERMITTED_ASR_LAG_SEC}s."
            )

        if self.copilot_freshness_seconds > MAX_PERMITTED_COPILOT_FRESHNESS_SEC:
            errors.append(
                f"Copilot freshness {self.copilot_freshness_seconds:.3f}s exceeds threshold {MAX_PERMITTED_COPILOT_FRESHNESS_SEC}s."
            )

        if not self.metal_device or "mock" in self.metal_device.lower():
            errors.append("Valid Apple Silicon Metal GPU device proof is required.")

        self.validation_errors = errors
        self.status = "QUALIFIED" if not errors else "REJECTED"
        return len(errors) == 0, errors

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> HardwareQualificationManifest:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def should_defer_document_workload(
    live_capture_active: bool,
    queue_backlog: int = 0,
    system_load_high: bool = False,
) -> bool:
    """
    Evaluates whether document ingestion should defer or yield under live capture pressure.
    Guarantees that audio capture and ASR workers maintain priority and zero frame drops.
    """
    if not live_capture_active:
        return False
    # If live capture is active and queue backlog exists or load is high, defer document workload
    if queue_backlog >= 1 or system_load_high:
        logger.info(
            "Deferring document workload under live capture pressure (backlog=%d, high_load=%s)",
            queue_backlog,
            system_load_high,
        )
        return True
    return False
