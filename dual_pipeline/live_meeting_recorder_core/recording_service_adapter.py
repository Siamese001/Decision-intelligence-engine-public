"""
dual_pipeline/live_meeting_recorder_core/recording_service_adapter.py

Wave 3 & Wave 5: Thin typed local start/status/stop adapter for live meeting recording.
Enforces:
1. Immediate background start dispatch (<250ms) before model-driven reasoning.
2. Monotonic source-bound PCM evidence milestones without synthetic substitution.
3. Separation of capture proof and ASR readiness telemetry.
4. Strict prohibition of false permission explanations without verified OS denial telemetry.
5. Session-scoped process recovery without broad process kills (pkill -f ffmpeg).
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    ASRState,
    DiarizationState,
    RecordingStartRequest,
    RecordingState,
    RecordingStatusResponse,
    RecordingStopRequest,
    RecordingStopResponse,
    extract_verified_os_denial,
)
from dual_pipeline.live_meeting_recorder_core.recording_service_owner import (
    get_recording_service_owner,
)

__all__ = [
    "ASRState",
    "DiarizationState",
    "RecordingServiceAdapter",
    "RecordingSessionHandle",
    "RecordingStartRequest",
    "RecordingState",
    "RecordingStatusResponse",
    "RecordingStopRequest",
    "RecordingStopResponse",
    "extract_verified_os_denial",
    "get_recording_service_adapter",
]

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.recording_service_adapter")


class RecordingSessionHandle:
    """Manages the background execution and lifecycle of a single recording session."""

    def __init__(self, request: RecordingStartRequest, session_id: str, repo_root: Path) -> None:
        self.request = request
        self.session_id = session_id
        self.repo_root = repo_root
        self.lock = threading.Lock()
        self.recording_state = RecordingState.REQUESTED
        self.asr_state = ASRState.ASR_WARMING
        self.diarization_state = DiarizationState.DIARIZATION_WARMING
        self.started_at_iso = datetime.now(timezone.utc).isoformat()
        self.started_at_monotonic = time.monotonic()
        self.intent_received_monotonic = request.intent_received_monotonic
        self.dispatch_monotonic: Optional[float] = None
        self.source_opened_monotonic: Optional[float] = None
        self.first_pcm_monotonic: Optional[float] = None
        self.first_pcm_utc: Optional[str] = None
        self.proof_passed_monotonic: Optional[float] = None
        self.asr_ready_monotonic: Optional[float] = None
        self.recorder: Optional[Any] = None
        self.worker_thread: Optional[threading.Thread] = None
        self.verified_os_denial: bool = False
        self.denial_evidence: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self.remediation: Optional[str] = None
        self._output_dir: Optional[Path] = None
        self._chunks_dir: Optional[Path] = None
        self._master_wav: Optional[Path] = None

    def start_background(self) -> None:
        """Launches capture execution in a background daemon thread."""
        with self.lock:
            self.recording_state = RecordingState.STARTING
            self.dispatch_monotonic = time.monotonic()
        self.worker_thread = threading.Thread(
            target=self._run_capture_worker,
            daemon=True,
            name=f"rec_worker_{self.session_id}",
        )
        self.worker_thread.start()

    def _run_capture_worker(self) -> None:
        try:
            rec = self._init_recorder()
            rec.start_ffmpeg()
            with self.lock:
                self.recording_state = RecordingState.CAPTURING
                self.proof_passed_monotonic = time.monotonic()
            self._poll_capture_loop(rec)
            with self.lock:
                if self.recording_state == RecordingState.CAPTURING:
                    self.recording_state = RecordingState.STOPPED
        except Exception as e:
            self._handle_capture_failure(e)

    def _init_recorder(self) -> Any:
        from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder

        raw_out_dir = getattr(self.request, "output_dir", None)
        if raw_out_dir is None:
            if self.request.mock_mode:
                out_dir = self.repo_root / ".session_registry" / "mock" / self.session_id
            else:
                out_dir = self.repo_root / "output" / (self.request.project_name or "adhoc") / self.session_id
        else:
            out_dir = Path(raw_out_dir)

        rec = LiveMeetingRecorder(
            project_name=self.request.project_name or "adhoc_session",
            meeting_name=self.request.meeting_name or "Live Meeting",
            meeting_date=self.request.meeting_date,
            attendees=self.request.attendees,
            device_index=self.request.device_index,
            segment_seconds=self.request.segment_seconds,
            mock_mode=self.request.mock_mode,
            audio_source=self.request.audio_source,
            whisper_model=self.request.whisper_model,
            fast_start=True,
            output_dir=out_dir,
            session_id=self.session_id,
            host_chat_id=self.request.host_chat_id,
            idempotency_key=self.request.idempotency_key,
            is_cold_start=self.request.is_cold_start,
        )
        with self.lock:
            self.recorder = rec
            self._output_dir = rec.output_dir
            self._chunks_dir = rec.chunks_dir
            self._master_wav = rec.master_wav
            self.source_opened_monotonic = time.monotonic()
        return rec

    def _poll_capture_loop(self, rec: Any) -> None:
        while bool(getattr(rec, "running", False)) and getattr(rec, "_is_stopped", False) is not True:
            self._check_capture_milestones(rec)
            time.sleep(0.2)

    def _check_capture_milestones(self, rec: Any) -> None:
        with self.lock:
            if self.first_pcm_monotonic is None:
                has_chunks = self._chunks_dir and any(c.stat().st_size > 44 for c in self._chunks_dir.glob("chunk_*.wav"))
                if rec.mock_mode or has_chunks:
                    self.first_pcm_monotonic = time.monotonic()
                    self.first_pcm_utc = datetime.now(timezone.utc).isoformat()

            transcriber = getattr(rec, "isolated_transcriber", None)
            if transcriber is not None:
                rcpt_attr = getattr(transcriber, "readiness_receipt", None)
                rcpt = rcpt_attr() if callable(rcpt_attr) else rcpt_attr
                if rcpt and isinstance(rcpt, dict) and rcpt.get("status") == "READY":
                    self.asr_state = ASRState.ASR_READY
                    if self.asr_ready_monotonic is None:
                        self.asr_ready_monotonic = time.monotonic()

    def _handle_capture_failure(self, err: Exception) -> None:
        with self.lock:
            self.recording_state = RecordingState.FAILED
            self.error = str(err)
            rem, denial_ev = extract_verified_os_denial(err)
            self.remediation = rem
            self.verified_os_denial = bool(denial_ev)
            self.denial_evidence = denial_ev
        logger.exception("RecordingSessionHandle failed for session %s: %s", self.session_id, err)

    def recover_session(self) -> Dict[str, Any]:
        """Bounded session-scoped recovery: terminates only owned children without pkill -f ffmpeg."""
        with self.lock:
            reaped_pids = []
            if self.recorder is not None:
                try:
                    self.recorder._early_abort_cleanup()
                    proc = getattr(self.recorder, "ffmpeg_proc", None)
                    if proc and hasattr(proc, "pid"):
                        reaped_pids.append(proc.pid)
                except Exception as e:
                    logger.debug("Error during recorder recovery cleanup: %s", e)
            self.recording_state = RecordingState.FAILED
            return {"session_id": self.session_id, "reaped_pids": reaped_pids, "status": "RECOVERED"}

    def stop(self) -> RecordingStopResponse:
        """Gracefully halts background capture and flushes deliverables."""
        with self.lock:
            self.recording_state = RecordingState.STOPPING

        chunk_count = 0
        master_size = 0
        transcript_path_str: Optional[str] = None

        if self.recorder is not None:
            try:
                self.recorder.stop()
                chunk_count = len(getattr(self.recorder, "processed_chunks", []))
                if getattr(self.recorder, "master_wav", None) and self.recorder.master_wav.exists():
                    master_size = self.recorder.master_wav.stat().st_size
                if getattr(self.recorder, "transcript_md", None):
                    transcript_path_str = str(self.recorder.transcript_md)
            except Exception as e:
                logger.warning("Error stopping recorder in handle %s: %s", self.session_id, e)

        with self.lock:
            self.recording_state = RecordingState.STOPPED

        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)

        return RecordingStopResponse(
            session_id=self.session_id,
            status="STOPPED",
            final_chunk_count=chunk_count,
            master_wav_size_bytes=master_size,
            transcript_path=transcript_path_str,
        )

    def get_status(self) -> RecordingStatusResponse:
        """Returns the real-time status and telemetry of this session."""
        with self.lock:
            elapsed = time.monotonic() - self.started_at_monotonic
            chunks_count = 0
            if self._chunks_dir and self._chunks_dir.is_dir():
                chunks_count = len(list(self._chunks_dir.glob("chunk_*.wav")))
            elif self.recorder and hasattr(self.recorder, "processed_chunks"):
                chunks_count = len(self.recorder.processed_chunks)

            strat = "unknown"
            if self.recorder and hasattr(self.recorder, "capture_manager"):
                strat = getattr(self.recorder.capture_manager, "active_strategy", "unknown")

            # Invariant: is_proven requires CAPTURING state AND verified PCM evidence
            is_proven = (self.recording_state == RecordingState.CAPTURING) and (
                chunks_count > 0 or (self.request.mock_mode and self.first_pcm_monotonic is not None)
            )

            cap_milestones = {
                "intent_received_monotonic": self.intent_received_monotonic,
                "dispatch_monotonic": self.dispatch_monotonic,
                "source_opened_monotonic": self.source_opened_monotonic,
                "first_pcm_monotonic": self.first_pcm_monotonic,
                "proof_passed_monotonic": self.proof_passed_monotonic,
            }
            asr_milestones = {
                "asr_state": self.asr_state.value,
                "asr_ready_monotonic": self.asr_ready_monotonic,
                "diarization_state": self.diarization_state.value,
            }

            return RecordingStatusResponse(
                session_id=self.session_id,
                request_id=self.request.request_id,
                host_chat_id=self.request.host_chat_id,
                recording_state=self.recording_state,
                asr_state=self.asr_state,
                diarization_state=self.diarization_state,
                started_at_iso=self.started_at_iso,
                started_at_monotonic=self.started_at_monotonic,
                elapsed_seconds=round(elapsed, 2),
                captured_chunks_count=chunks_count,
                master_wav_path=str(self._master_wav) if self._master_wav else None,
                chunks_dir=str(self._chunks_dir) if self._chunks_dir else None,
                active_strategy=strat,
                is_proven=is_proven,
                intent_received_monotonic=self.intent_received_monotonic,
                dispatch_monotonic=self.dispatch_monotonic,
                source_opened_monotonic=self.source_opened_monotonic,
                first_pcm_monotonic=self.first_pcm_monotonic,
                first_pcm_utc=self.first_pcm_utc,
                proof_passed_monotonic=self.proof_passed_monotonic,
                is_cold_start=self.request.is_cold_start,
                capture_milestones=cap_milestones,
                asr_milestones=asr_milestones,
                verified_os_denial=self.verified_os_denial,
                denial_evidence=self.denial_evidence,
                error=self.error,
                remediation=self.remediation,
            )


class RecordingServiceAdapter:
    """Thin, typed, local-first service adapter providing background recording authority."""

    def __init__(self, repo_root: Optional[Path] = None) -> None:
        self.repo_root = repo_root or Path.cwd()
        self._lock = threading.Lock()
        self._sessions: Dict[str, RecordingSessionHandle] = {}
        self._idempotency_map: Dict[str, str] = {}
        self._chat_active_sessions: Dict[str, str] = {}

    def _find_idempotent_session(self, request: RecordingStartRequest) -> Optional[RecordingSessionHandle]:
        active_states = (RecordingState.REQUESTED, RecordingState.STARTING, RecordingState.CAPTURING)
        if request.idempotency_key and request.idempotency_key in self._idempotency_map:
            sid = self._idempotency_map[request.idempotency_key]
            handle = self._sessions.get(sid)
            if handle and handle.recording_state in active_states:
                logger.info("Idempotent start match on key %s -> session %s", request.idempotency_key, sid)
                return handle

        if request.host_chat_id and request.host_chat_id in self._chat_active_sessions:
            sid = self._chat_active_sessions[request.host_chat_id]
            handle = self._sessions.get(sid)
            if handle and handle.recording_state in active_states and handle.request.meeting_name == request.meeting_name:
                logger.info("Idempotent match on host chat %s -> session %s", request.host_chat_id, sid)
                return handle
        return None

    def start_recording(self, request: RecordingStartRequest, detached: bool = False) -> RecordingStatusResponse:
        t0 = time.perf_counter()
        with self._lock:
            existing = self._find_idempotent_session(request)
            if existing is not None:
                return existing.get_status()

            # Global MLX Whisper single-session mutex check
            from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import (
                check_active_mlx_session,
            )

            is_active, holder = check_active_mlx_session(self.repo_root)
            if is_active:
                h_pid = getattr(holder, "pid", "unknown")
                h_slug = getattr(holder, "meeting_slug", "unknown")
                err_msg = (
                    f"Global MLX Whisper session collision detected: an active session '{h_slug}' "
                    f"(PID {h_pid}) is currently open. Only one MLX Whisper note session may be active at a time; "
                    f"close the existing session before opening a new one."
                )
                logger.warning("[SERVICE-ADAPTER] Rejecting session start: %s", err_msg)
                raise RuntimeError(err_msg)

            sid = request.session_id or f"rec_{int(time.time())}_{uuid.uuid4().hex[:6]}"
            owner = get_recording_service_owner(sessions_root=self.repo_root / ".local" / "sessions")

            if detached:
                owner.spawn_session_process(request, sid)
                handle = RecordingSessionHandle(request=request, session_id=sid, repo_root=self.repo_root)
                handle.recording_state = RecordingState.STARTING
                handle.dispatch_monotonic = time.monotonic()
            else:
                owner.acquire_session_owner(request, sid)
                handle = RecordingSessionHandle(request=request, session_id=sid, repo_root=self.repo_root)
                handle.start_background()

            self._sessions[sid] = handle
            if request.idempotency_key:
                self._idempotency_map[request.idempotency_key] = sid
            if request.host_chat_id:
                self._chat_active_sessions[request.host_chat_id] = sid

        dispatch_ms = (time.perf_counter() - t0) * 1000.0
        logger.info("[SERVICE-ADAPTER] Dispatched session %s in %.2fms", sid, dispatch_ms)
        return handle.get_status()

    def start_recording_with_receipt(
        self, request: RecordingStartRequest, detached: bool = False
    ) -> Tuple[RecordingStatusResponse, Any]:
        status = self.start_recording(request, detached=detached)
        from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
            build_immediate_start_receipt,
        )

        return status, build_immediate_start_receipt(status)

    def get_status(self, session_id_or_request_id: str) -> Optional[RecordingStatusResponse]:
        with self._lock:
            if session_id_or_request_id in self._sessions:
                return self._sessions[session_id_or_request_id].get_status()
            for handle in self._sessions.values():
                if handle.request.request_id == session_id_or_request_id:
                    return handle.get_status()
                if handle.request.idempotency_key == session_id_or_request_id:
                    return handle.get_status()
            return None

    def stop_recording(self, request: RecordingStopRequest) -> RecordingStopResponse:
        with self._lock:
            handle = self._sessions.get(request.session_id)
            if request.host_chat_id and self._chat_active_sessions.get(request.host_chat_id) == request.session_id:
                self._chat_active_sessions.pop(request.host_chat_id, None)
            owner = get_recording_service_owner(sessions_root=self.repo_root / ".local" / "sessions")
            owner.stop_session(request.session_id)
            if not handle:
                return RecordingStopResponse(session_id=request.session_id, status="STOPPED", final_chunk_count=0, master_wav_size_bytes=0)
        return handle.stop()

    def recover_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            handle = self._sessions.get(session_id)
            if not handle:
                return None
        return handle.recover_session()

    def list_active_sessions(self) -> List[RecordingStatusResponse]:
        with self._lock:
            active = []
            for handle in self._sessions.values():
                if handle.recording_state in (RecordingState.STARTING, RecordingState.CAPTURING):
                    active.append(handle.get_status())
            return active


_DEFAULT_ADAPTER: Optional[RecordingServiceAdapter] = None
_ADAPTER_LOCK = threading.Lock()


def get_recording_service_adapter(repo_root: Optional[Path] = None) -> RecordingServiceAdapter:
    global _DEFAULT_ADAPTER
    with _ADAPTER_LOCK:
        if _DEFAULT_ADAPTER is None:
            _DEFAULT_ADAPTER = RecordingServiceAdapter(repo_root=repo_root)
        return _DEFAULT_ADAPTER
