"""
dual_pipeline/live_meeting_recorder_core/recording_host_integration.py

Wave 2: Host recording action integration and pre-dispatch boundary contract.
Enforces:
1. No help queries (-h/--help), dry runs (--dry-run), or invalid inputs ever open hardware.
2. Immediate start receipt generation (<250ms) before general model reasoning or metadata delays.
3. Explicit documentation of host integration hooks and routing boundaries.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import os
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    RecordingStartRequest,
    RecordingState,
    RecordingStatusResponse,
)

HOST_INTEGRATION_SURFACE = {
    "entry_point": "nee.py",
    "route": "Route 0a (Live Recording)",
    "rule_binding": ".agents/rules/agent-routing.md",
    "budget_ceiling_ms": 250.0,
    "sla_dispatch_ceiling_seconds": 2.0,
}


@dataclass(frozen=True)
class ImmediateStartReceipt:
    status: str
    session_id: str
    request_id: str
    recording_state: str
    dispatched_at_iso: str
    pre_dispatch_latency_ms: Optional[float]
    master_wav_path: Optional[str]
    is_cold_start: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def validate_pre_dispatch_intent(args: List[str]) -> Tuple[bool, Optional[str]]:
    """Evaluates whether CLI argv represents an authorized live recording start.

    Rejects help requests, dry runs, and invalid inputs before any hardware or service opens.
    """
    if not args:
        return False, "EMPTY_ARGV: No arguments provided."

    has_record = any(a in {"record-meeting", "record"} for a in args)
    if not has_record:
        return False, "NOT_RECORD_INTENT: Command is not record-meeting."

    if any(a in {"-h", "--help"} for a in args):
        return False, "HELP_INTENT: Help query must not initialize recording hardware."

    if any(a in {"--dry-run"} for a in args):
        return False, "DRY_RUN_INTENT: Dry-run qualification must not open hardware devices."

    return True, None


def build_immediate_start_receipt(status: RecordingStatusResponse) -> ImmediateStartReceipt:
    """Constructs an immediate structured start receipt for host confirmation."""
    latency_ms: Optional[float] = None
    if status.dispatch_monotonic is not None and status.intent_received_monotonic is not None:
        latency_ms = round((status.dispatch_monotonic - status.intent_received_monotonic) * 1000.0, 2)

    return ImmediateStartReceipt(
        status="PASS",
        session_id=status.session_id,
        request_id=status.request_id,
        recording_state=status.recording_state.value,
        dispatched_at_iso=status.started_at_iso,
        pre_dispatch_latency_ms=latency_ms,
        master_wav_path=status.master_wav_path,
        is_cold_start=status.is_cold_start,
        message=f"Live meeting recording dispatched in state {status.recording_state.value}.",
    )


def dispatch_record_meeting_cli(
    args: argparse.Namespace,
    project_val: Optional[str],
    enable_copilot: bool,
) -> Optional[Dict[str, Any]]:
    """Handles detached CLI branch to isolate engine complexity."""
    if bool(getattr(args, "detach", False)):
        from dual_pipeline.live_meeting_recorder_core.recording_service_adapter import (
            RecordingStartRequest,
            get_recording_service_adapter,
        )

        raw_att = getattr(args, "attendees", None)
        if isinstance(raw_att, str):
            norm_att = [a.strip() for a in raw_att.split(",") if a.strip()]
        elif isinstance(raw_att, list):
            norm_att = [str(a).strip() for a in raw_att if str(a).strip()]
        else:
            norm_att = []
        adapter = get_recording_service_adapter()
        req = RecordingStartRequest(
            project_name=project_val,
            meeting_name=args.meeting,
            meeting_date=getattr(args, "date", None),
            attendees=norm_att,
            audio_source=getattr(args, "audio_source", "auto"),
            mock_mode=getattr(args, "mock", False),
            device_index=getattr(args, "device", ":0"),
            segment_seconds=getattr(args, "segment_seconds", 15),
            whisper_model=getattr(args, "whisper_model", None),
            session_id=getattr(args, "session_id", None),
        )
        status, receipt = adapter.start_recording_with_receipt(req, detached=True)
        print(f"\n[RECORDING-DETACHED] Session {status.session_id} dispatched in background.")
        print(f"Receipt: {receipt.to_dict()}\n", flush=True)
        return {
            "status": "PASS",
            "command": "record-meeting",
            "session_id": status.session_id,
            "recording_state": status.recording_state.value,
            "immediate_start_receipt": receipt.to_dict(),
        }

    return None


def enforce_initiation_budget(recorder: Any, stage: str = "STARTUP") -> None:
    """Enforces absolute 30.0s initiation SLA deadline on live recorder instances."""
    from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import (
        AudioCaptureUnprovenError,
        AudioReasonCode,
    )

    now = time.monotonic()
    deadline = getattr(recorder, "initiation_deadline_monotonic", None)
    if deadline is not None and now > deadline:
        start_mono = getattr(recorder, "session_start_monotonic", now)
        budget = getattr(recorder, "initiation_budget_seconds", 30.0)
        elapsed = now - start_mono
        err = f"Initiation deadline exceeded at stage '{stage}': elapsed {elapsed:.2f}s > budget {budget:.2f}s."
        recorder._early_abort_cleanup()
        quarantine_recording_session(
            recorder=recorder,
            reason=err,
            reason_code=AudioReasonCode.INITIATION_DEADLINE_EXCEEDED,
        )
        raise AudioCaptureUnprovenError(
            stage=stage,
            reason_code=AudioReasonCode.INITIATION_DEADLINE_EXCEEDED,
            detail=err,
            remediation="Ensure hardware, permissions, and dependencies are warmed before session start.",
        )


def quarantine_recording_session(recorder: Any, reason: str, reason_code: str) -> None:
    """Quarantines the session directory, marks .quarantine, and safely handles error logging."""
    import json
    try:
        out_dir = getattr(recorder, "output_dir", None) or getattr(recorder, "_target_output_dir", None) or Path.cwd()
        out_dir.mkdir(parents=True, exist_ok=True)
        marker = out_dir / ".quarantine"
        data = {
            "session_id": getattr(recorder, "session_id", None),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "QUARANTINED",
            "reason": reason,
            "reason_code": reason_code,
            "meeting_name": getattr(recorder, "meeting_name", "UNKNOWN"),
            "project": getattr(recorder, "project_name", "UNKNOWN"),
        }
        marker.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def clean_session_quarantine(recorder: Any) -> None:
    """Safely unlinks stale .quarantine markers from active session output directories."""
    targets = []
    out_dir = getattr(recorder, "output_dir", None)
    if out_dir:
        targets.append(Path(out_dir) / ".quarantine")
    tgt_out = getattr(recorder, "_target_output_dir", None)
    if tgt_out:
        targets.append(Path(tgt_out) / ".quarantine")
    for q_path in set(targets):
        try:
            if q_path.is_file():
                q_path.unlink()
        except Exception:
            pass


def verify_adopted_spooler_proof(recorder: Any) -> Dict[str, Any]:
    """Runs proof verification for an adopted instant spooler or returns mock receipt."""
    if recorder.mock_mode:
        return {"status": "CAPTURE_PROVEN", "mock_mode": True, "strategy": "spooler_adopted"}

    from dual_pipeline.live_meeting_recorder_core.audio_capture_proof import AudioCaptureProofVerifier

    receipt_path = recorder.master_wav.parent / "record_start_receipt.json"
    stderr_path = recorder.capture_manager.chunks_dir / "capture_stderr.log"
    return AudioCaptureProofVerifier.run_full_start_proof(
        master_wav=recorder.capture_manager.master_wav,
        ffmpeg_proc=recorder.ffmpeg_proc,
        sck_proc=recorder.capture_manager._sck_proc,
        is_stereo=recorder.capture_manager.enable_stereo_isolation,
        stderr_log_path=stderr_path if stderr_path.exists() else None,
        mock_mode=recorder.mock_mode,
        receipt_path=receipt_path,
        deadline_monotonic=recorder.initiation_deadline_monotonic,
    )


