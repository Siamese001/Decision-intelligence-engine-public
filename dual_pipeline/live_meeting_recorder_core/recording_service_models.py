"""
dual_pipeline/live_meeting_recorder_core/recording_service_models.py

Typed data models, enums, and OS denial telemetry extraction for the
recording service adapter under Wave 3 & Wave 5 governance.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
import uuid


class ClockDomain(str, Enum):
    HOST_MONOTONIC = "host_monotonic"
    WORKER_MONOTONIC = "worker_monotonic"
    FILESYSTEM_STAT = "filesystem_stat"
    UTC_AUDIT = "utc_audit"


class StartupEventName(str, Enum):
    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    DISPATCH_INITIATED = "DISPATCH_INITIATED"
    SERVICE_OPENED = "SERVICE_OPENED"
    SOURCE_OPENED = "SOURCE_OPENED"
    FIRST_VALID_PCM = "FIRST_VALID_PCM"
    BOOTSTRAP_CHUNK_SPLICED = "BOOTSTRAP_CHUNK_SPLICED"
    FILE_BIRTH_CHUNK_MASTER = "FILE_BIRTH_CHUNK_MASTER"
    PROOF_CONFIRMED = "PROOF_CONFIRMED"
    RECEIPT_PUBLISHED = "RECEIPT_PUBLISHED"
    HOST_ACKNOWLEDGED = "HOST_ACKNOWLEDGED"
    ASR_WARMING = "ASR_WARMING"
    ASR_READY = "ASR_READY"
    ASR_FAILED = "ASR_FAILED"
    DIARIZATION_WARMING = "DIARIZATION_WARMING"
    DIARIZATION_READY = "DIARIZATION_READY"
    DIARIZATION_FAILED = "DIARIZATION_FAILED"


class AudioSourceType(str, Enum):
    MIC = "microphone"
    SYSTEM_AUDIO = "system_audio"
    MIXED = "mixed"
    HOST = "host"
    ASR = "asr"
    DIARIZATION = "diarization"


@dataclass
class CorrelatedStartupEvent:
    event_name: StartupEventName
    session_id: str
    request_id: str
    source: str
    clock_domain: ClockDomain
    monotonic_timestamp: Optional[float] = None
    utc_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    build_id: Optional[str] = None
    is_pcm_proven: bool = False
    is_file_birth: bool = False
    pcm_bytes: int = 0
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["event_name"] = self.event_name.value
        d["clock_domain"] = self.clock_domain.value
        return d


StartupEventCallback = Callable[[CorrelatedStartupEvent], None]


class StartupEventChronology:
    """Append-only correlated startup event log with clock-domain enforcement."""

    def __init__(
        self,
        session_id: str,
        request_id: str,
        build_id: Optional[str] = None,
    ) -> None:
        self.session_id = session_id
        self.request_id = request_id
        self.build_id = build_id
        self._events: List[CorrelatedStartupEvent] = []
        self._callbacks: List[StartupEventCallback] = []

    def register_callback(self, callback: StartupEventCallback) -> None:
        self._callbacks.append(callback)

    def add_event(self, event: CorrelatedStartupEvent) -> None:
        if event.session_id != self.session_id:
            raise ValueError(
                f"STALE_SESSION_REJECTED: Event session_id '{event.session_id}' "
                f"does not match chronology session_id '{self.session_id}'."
            )

        if event.monotonic_timestamp is not None:
            prev_events = [
                e for e in self._events
                if e.clock_domain == event.clock_domain and e.monotonic_timestamp is not None
            ]
            if prev_events:
                last_mono = prev_events[-1].monotonic_timestamp
                if event.monotonic_timestamp < last_mono:  # type: ignore[operator]
                    raise ValueError(
                        f"EVENT_ORDER_VIOLATION: Monotonic timestamp {event.monotonic_timestamp} "
                        f"is earlier than preceding event in domain '{event.clock_domain.value}' ({last_mono})."
                    )

        self._events.append(event)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                pass

    @property
    def events(self) -> List[CorrelatedStartupEvent]:
        return list(self._events)

    def _find_first_mono(self, name: StartupEventName, source: Optional[str] = None) -> Optional[float]:
        for e in self._events:
            if e.event_name == name:
                if source is None or e.source == source:
                    return e.monotonic_timestamp
        return None

    @property
    def request_received_monotonic(self) -> Optional[float]:
        return self._find_first_mono(StartupEventName.REQUEST_RECEIVED)

    @property
    def dispatch_initiated_monotonic(self) -> Optional[float]:
        return self._find_first_mono(StartupEventName.DISPATCH_INITIATED)

    @property
    def host_acknowledged_monotonic(self) -> Optional[float]:
        return self._find_first_mono(StartupEventName.HOST_ACKNOWLEDGED)

    @property
    def mic_first_pcm_monotonic(self) -> Optional[float]:
        for e in self._events:
            if e.event_name == StartupEventName.FIRST_VALID_PCM and e.source == AudioSourceType.MIC.value:
                return e.monotonic_timestamp
            if e.event_name == StartupEventName.BOOTSTRAP_CHUNK_SPLICED and e.pcm_bytes > 0:
                return e.monotonic_timestamp
        return None

    @property
    def system_audio_first_pcm_monotonic(self) -> Optional[float]:
        for e in self._events:
            if e.event_name == StartupEventName.FIRST_VALID_PCM and e.source == AudioSourceType.SYSTEM_AUDIO.value:
                return e.monotonic_timestamp
        return None

    @property
    def proof_confirmed_monotonic(self) -> Optional[float]:
        return self._find_first_mono(StartupEventName.PROOF_CONFIRMED)

    @property
    def receipt_published_monotonic(self) -> Optional[float]:
        return self._find_first_mono(StartupEventName.RECEIPT_PUBLISHED)

    @property
    def asr_ready_monotonic(self) -> Optional[float]:
        for e in self._events:
            if e.event_name == StartupEventName.ASR_READY and e.payload.get("asr_readiness_receipt"):
                return e.monotonic_timestamp
        return None

    def validate_clock_domain_subtraction(self, domain1: ClockDomain, domain2: ClockDomain) -> bool:
        """Cross-process clock domain safety check."""
        return domain1 == domain2

    def calculate_pre_dispatch_latency(self) -> Optional[float]:
        req_mono = self.request_received_monotonic
        disp_mono = self.dispatch_initiated_monotonic
        if req_mono is not None and disp_mono is not None:
            return disp_mono - req_mono
        return None

    def calculate_total_confirmation_latency(self) -> Optional[float]:
        req_mono = self.request_received_monotonic
        ack_mono = self.host_acknowledged_monotonic
        if req_mono is not None and ack_mono is not None:
            return ack_mono - req_mono
        return None

    def is_dual_source_pcm_proven(self) -> bool:
        """Verifies both microphone and system audio wrote valid non-zero PCM."""
        mic_proven = False
        sys_proven = False
        for e in self._events:
            if e.is_file_birth:
                continue
            if e.is_pcm_proven and e.pcm_bytes > 0:
                if e.source in (AudioSourceType.MIC.value, "microphone", "mic"):
                    mic_proven = True
                elif e.source in (AudioSourceType.SYSTEM_AUDIO.value, "system_audio", "mixed_sck", "sck"):
                    sys_proven = True
        return mic_proven and sys_proven

    def is_asr_ready(self) -> bool:
        """Verifies ASR has a validated readiness receipt."""
        for e in self._events:
            if e.event_name == StartupEventName.ASR_READY:
                receipt = e.payload.get("asr_readiness_receipt")
                if receipt and isinstance(receipt, dict) and receipt.get("status") == "READY":
                    return True
        return False

    def validate_qualification_completeness(self) -> Tuple[bool, List[str]]:
        """Validates that all required host and source startup events exist."""
        reasons: List[str] = []
        if self.request_received_monotonic is None:
            reasons.append("MISSING_EVENT: REQUEST_RECEIVED was not recorded.")
        if self.dispatch_initiated_monotonic is None:
            reasons.append("MISSING_EVENT: DISPATCH_INITIATED was not recorded.")
        if self.host_acknowledged_monotonic is None:
            reasons.append("MISSING_EVENT: HOST_ACKNOWLEDGED was not recorded.")
        if not self.is_dual_source_pcm_proven():
            reasons.append("DUAL_SOURCE_DEFICIT: Both microphone and system audio must write verified non-zero PCM.")
        return len(reasons) == 0, reasons

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "request_id": self.request_id,
            "build_id": self.build_id,
            "events_count": len(self._events),
            "events": [e.to_dict() for e in self._events],
            "is_dual_source_proven": self.is_dual_source_pcm_proven(),
            "is_asr_ready": self.is_asr_ready(),
            "pre_dispatch_latency": self.calculate_pre_dispatch_latency(),
            "total_confirmation_latency": self.calculate_total_confirmation_latency(),
        }


class RecordingState(str, Enum):
    REQUESTED = "REQUESTED"
    STARTING = "STARTING"
    CAPTURING = "CAPTURING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ASRState(str, Enum):
    ASR_OFF = "ASR_OFF"
    ASR_WARMING = "ASR_WARMING"
    ASR_READY = "ASR_READY"
    ASR_DEGRADED = "ASR_DEGRADED"
    ASR_FAILED = "ASR_FAILED"


class DiarizationState(str, Enum):
    DIARIZATION_OFF = "DIARIZATION_OFF"
    DIARIZATION_WARMING = "DIARIZATION_WARMING"
    DIARIZATION_READY = "DIARIZATION_READY"
    DIARIZATION_FAILED = "DIARIZATION_FAILED"


def extract_verified_os_denial(err: Exception) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Extracts verified OS permission denial telemetry.

    In accordance with Wave 5 RCA acceptance criteria:
    Generic errors, PCM deficits, digital zeros, or buffer stalls must NOT produce
    false permission diagnoses or recommend granting permissions in System Settings.
    Permission explanation is strictly permitted ONLY when explicit OS denial telemetry is present.
    """
    reason_code = getattr(err, "reason_code", None)
    err_str = str(err)
    err_lower = err_str.lower()

    if reason_code in ("SCK_TCC_DENIED", "CAPTURE_STDERR_DENIED"):
        rem = getattr(err, "remediation", None) or "Grant Screen & System Audio Recording in macOS System Settings."
        return rem, {"reason_code": reason_code, "detail": getattr(err, "detail", err_str)}

    # Disallow false attribution if error is specifically an unproven deficit/zero/stall
    if reason_code in ("PCM_BYTE_RATE_DEFICIT", "MIC_DIGITAL_ZERO", "AUDIO_GROWTH_STALLED", "DEVICE_NOT_FOUND"):
        return None, None

    # Explicit OS permission denial markers (TCC / PermissionError / denied)
    is_os_denial = (
        isinstance(err, PermissionError)
        or "tcc" in err_lower
        or "permission denied" in err_lower
        or "access denied" in err_lower
        or "not permitted" in err_lower
    )

    if is_os_denial:
        if "screencapturekit" in err_lower or "sck" in err_lower:
            return "Grant Screen & System Audio Recording in macOS System Settings.", {"reason_code": "SCK_TCC_DENIED", "detail": getattr(err, "detail", err_str)}
        if "avfoundation" in err_lower or "microphone" in err_lower:
            return "Grant Microphone access in macOS System Settings.", {"reason_code": "MIC_TCC_DENIED", "detail": getattr(err, "detail", err_str)}
        return "Grant permission in macOS System Settings → Privacy & Security.", {"reason_code": "OS_TCC_DENIED", "detail": getattr(err, "detail", err_str)}

    return None, None


@dataclass
class RecordingStartRequest:
    request_id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:8]}")
    idempotency_key: Optional[str] = None
    session_id: Optional[str] = None
    host_chat_id: Optional[str] = None
    project_name: Optional[str] = None
    meeting_name: Optional[str] = None
    meeting_date: Optional[str] = None
    attendees: List[str] = field(default_factory=list)
    audio_source: str = "mixed"
    mock_mode: bool = False
    device_index: str = ":0"
    segment_seconds: int = 5
    whisper_model: Optional[str] = None
    intent_received_monotonic: float = field(default_factory=time.monotonic)
    intent_received_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_cold_start: bool = False
    output_dir: Optional[Any] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecordingStatusResponse:
    session_id: str
    request_id: str
    host_chat_id: Optional[str]
    recording_state: RecordingState
    asr_state: ASRState
    diarization_state: DiarizationState
    started_at_iso: str
    started_at_monotonic: float
    elapsed_seconds: float
    captured_chunks_count: int
    master_wav_path: Optional[str] = None
    chunks_dir: Optional[str] = None
    active_strategy: str = "unknown"
    is_proven: bool = False
    intent_received_monotonic: Optional[float] = None
    dispatch_monotonic: Optional[float] = None
    source_opened_monotonic: Optional[float] = None
    first_pcm_monotonic: Optional[float] = None
    first_pcm_utc: Optional[str] = None
    proof_passed_monotonic: Optional[float] = None
    is_cold_start: bool = False
    capture_milestones: Dict[str, Optional[float]] = field(default_factory=dict)
    asr_milestones: Dict[str, Any] = field(default_factory=dict)
    verified_os_denial: bool = False
    denial_evidence: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    remediation: Optional[str] = None
    startup_chronology: Optional[Dict[str, Any]] = None
    startup_events: List[Dict[str, Any]] = field(default_factory=list)
    mic_first_pcm_monotonic: Optional[float] = None
    system_audio_first_pcm_monotonic: Optional[float] = None
    mic_pcm_bytes: int = 0
    system_audio_pcm_bytes: int = 0
    asr_readiness_receipt: Optional[Dict[str, Any]] = None
    host_acknowledged_monotonic: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["recording_state"] = self.recording_state.value
        d["asr_state"] = self.asr_state.value
        d["diarization_state"] = self.diarization_state.value
        return d


@dataclass
class RecordingStopRequest:
    session_id: str
    host_chat_id: Optional[str] = None
    reason: Optional[str] = None


@dataclass
class RecordingStopResponse:
    session_id: str
    status: str
    final_chunk_count: int
    master_wav_size_bytes: int
    transcript_path: Optional[str] = None
    stopped_at_iso: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
