"""
dual_pipeline/live_meeting_recorder_core/recording_service_owner.py

Wave 2: Bounded local service ownership module and process lifecycle manager.
Enforces:
1. Persistent capture process lifecycle decoupled from caller/chat exit.
2. Exclusive session file locking preventing duplicate concurrent capture owners.
3. Idempotent connection to already active session owners.
4. Session-scoped termination and recovery without broad system process kills.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.recording_service_models import (
    RecordingStartRequest,
    RecordingState,
    RecordingStatusResponse,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.recording_service_owner")


@dataclass
class SessionOwnerRecord:
    session_id: str
    request_id: str
    pid: int
    started_at_iso: str
    started_at_monotonic: float
    status: str
    lock_file: str
    state_file: str
    is_mock: bool = False
    output_dir: Optional[str] = None
    master_wav_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)



def _atomic_write_json(target_path: Path, data: Dict[str, Any]) -> None:
    """Writes JSON dictionary to target_path atomically via temporary file replacement."""
    tmp_path = target_path.parent / f".tmp_{target_path.stem}_{time.time_ns()}.json"
    try:
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(target_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


class RecordingServiceOwner:
    """Manages persistent session-owned capture processes and file-lock state."""

    def __init__(self, sessions_root: Optional[Path] = None) -> None:
        self.sessions_root = sessions_root or (Path.cwd() / ".local" / "sessions")
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self._open_locks: Dict[str, int] = {}

    def _get_session_dir(self, session_id: str) -> Path:
        s_dir = self.sessions_root / session_id
        s_dir.mkdir(parents=True, exist_ok=True)
        return s_dir

    def _is_pid_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
                if wpid == pid:
                    return False
            except ChildProcessError:
                pass
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def get_session_record(self, session_id: str) -> Optional[SessionOwnerRecord]:
        """Reads persistent session state if present and active."""
        s_dir = self.sessions_root / session_id
        state_file = s_dir / "session_state.json"
        if not state_file.is_file():
            return None
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            pid = data.get("pid", 0)
            if not self._is_pid_alive(pid):
                data["status"] = RecordingState.STOPPED.value
            return SessionOwnerRecord(**data)
        except Exception:
            return None

    def acquire_session_owner(
        self,
        request: RecordingStartRequest,
        session_id: str,
        output_dir: Optional[Path] = None,
        master_wav: Optional[Path] = None,
    ) -> Tuple[SessionOwnerRecord, bool]:
        """Acquires exclusive ownership for a session.

        Returns (SessionOwnerRecord, is_new_owner).
        If an existing active owner is running, connects to it idempotently.
        """
        s_dir = self._get_session_dir(session_id)
        lock_file = s_dir / "owner.lock"
        state_file = s_dir / "session_state.json"

        # Check existing state first
        existing = self.get_session_record(session_id)
        if existing and existing.pid != os.getpid() and existing.status in (RecordingState.STARTING.value, RecordingState.CAPTURING.value):
            if self._is_pid_alive(existing.pid):
                logger.info("Found existing active session owner for %s (PID: %d)", session_id, existing.pid)
                return existing, False

        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._open_locks[session_id] = lock_fd
        except (BlockingIOError, OSError):
            os.close(lock_fd)
            # Another process holds lock; re-read state
            existing = self.get_session_record(session_id)
            if existing:
                return existing, False
            raise RuntimeError(f"SESSION_LOCK_HELD: Session {session_id} is locked by another process.")

        now_iso = datetime.now(timezone.utc).isoformat()
        now_mono = time.monotonic()
        pid = os.getpid()

        record = SessionOwnerRecord(
            session_id=session_id,
            request_id=request.request_id,
            pid=pid,
            started_at_iso=now_iso,
            started_at_monotonic=now_mono,
            status=RecordingState.STARTING.value,
            lock_file=str(lock_file),
            state_file=str(state_file),
            is_mock=request.mock_mode,
            output_dir=str(output_dir) if output_dir else None,
            master_wav_path=str(master_wav) if master_wav else None,
        )

        _atomic_write_json(state_file, record.to_dict())
        return record, True

    def update_session_status(self, session_id: str, new_status: RecordingState) -> None:
        s_dir = self._get_session_dir(session_id)
        state_file = s_dir / "session_state.json"
        if state_file.is_file():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                data["status"] = new_status.value
                _atomic_write_json(state_file, data)
            except Exception:
                pass

    def spawn_session_process(
        self,
        request: RecordingStartRequest,
        session_id: str,
    ) -> SessionOwnerRecord:
        """Spawns an independent background session process decoupled from caller exit."""
        existing = self.get_session_record(session_id)
        if existing and existing.status in (RecordingState.STARTING.value, RecordingState.CAPTURING.value):
            if self._is_pid_alive(existing.pid):
                return existing

        s_dir = self._get_session_dir(session_id)
        req_file = s_dir / "request.json"
        req_file.write_text(json.dumps(request.to_dict(), indent=2), encoding="utf-8")
        log_file = s_dir / "service.log"
        log_fd = os.open(str(log_file), os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o644)

        cmd = [
            sys.executable,
            "-m",
            "dual_pipeline.live_meeting_recorder_core.recording_service_owner",
            "--session-id",
            session_id,
            "--request-file",
            str(req_file),
            "--sessions-root",
            str(self.sessions_root),
        ]
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
            close_fds=True,
        )
        os.close(log_fd)

        state_file = s_dir / "session_state.json"
        record = SessionOwnerRecord(
            session_id=session_id,
            request_id=request.request_id,
            pid=proc.pid,
            started_at_iso=datetime.now(timezone.utc).isoformat(),
            started_at_monotonic=time.monotonic(),
            status=RecordingState.STARTING.value,
            lock_file=str(s_dir / "owner.lock"),
            state_file=str(state_file),
            is_mock=request.mock_mode,
        )
        _atomic_write_json(state_file, record.to_dict())

        for _ in range(25):
            rec = self.get_session_record(session_id)
            if rec and rec.pid == proc.pid:
                return rec
            time.sleep(0.01)

        return record

    def stop_session(self, session_id: str) -> Dict[str, Any]:
        """Stops the session owner gracefully, signaling its owned process only."""
        record = self.get_session_record(session_id)
        if not record:
            return {"session_id": session_id, "status": "NOT_FOUND"}

        target_pid = record.pid
        if self._is_pid_alive(target_pid) and target_pid != os.getpid():
            try:
                os.kill(target_pid, signal.SIGTERM)
                # Wait up to 45s for final chunk drain, stitch, and receipt seal
                grace_seconds = float(os.environ.get("NEE_DETACHED_STOP_GRACE_SECONDS", "45.0"))
                deadline = time.monotonic() + max(0.5, grace_seconds)
                while time.monotonic() < deadline:
                    if not self._is_pid_alive(target_pid):
                        break
                    time.sleep(0.1)
                if self._is_pid_alive(target_pid):
                    os.kill(target_pid, signal.SIGKILL)
            except Exception as e:
                logger.debug("Error signaling process %d: %s", target_pid, e)

        self.update_session_status(session_id, RecordingState.STOPPED)

        # Release lock file descriptor if owned by this process
        if session_id in self._open_locks:
            fd = self._open_locks.pop(session_id)
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except Exception:
                pass

        return {"session_id": session_id, "status": "STOPPED", "pid": target_pid}


def _sanitize_standard_streams() -> None:
    """Ensures FDs 0, 1, 2 are valid open descriptors so child processes never fail init_sys_streams."""
    for fd, flags in ((0, os.O_RDONLY), (1, os.O_WRONLY), (2, os.O_WRONLY)):
        try:
            os.fstat(fd)
        except OSError:
            devnull_fd = os.open(os.devnull, flags)
            if devnull_fd != fd:
                os.dup2(devnull_fd, fd)
                os.close(devnull_fd)


def run_service_worker(session_id: str, request_file: str, sessions_root: Optional[str] = None) -> None:
    _sanitize_standard_streams()
    path = Path(request_file)
    if not path.is_file():
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    req = RecordingStartRequest(
        request_id=data.get("request_id") or f"req_{session_id[:8]}",
        idempotency_key=data.get("idempotency_key"),
        session_id=session_id,
        host_chat_id=data.get("host_chat_id"),
        project_name=data.get("project_name"),
        meeting_name=data.get("meeting_name"),
        meeting_date=data.get("meeting_date"),
        attendees=data.get("attendees", []),
        audio_source=data.get("audio_source", "mixed"),
        mock_mode=data.get("mock_mode", False),
        device_index=data.get("device_index", ":0"),
        segment_seconds=data.get("segment_seconds", 15),
        whisper_model=data.get("whisper_model"),
        intent_received_monotonic=data.get("intent_received_monotonic", time.monotonic()),
        intent_received_utc=data.get("intent_received_utc", datetime.now(timezone.utc).isoformat()),
        is_cold_start=data.get("is_cold_start", False),
        output_dir=data.get("output_dir"),
    )
    s_root = Path(sessions_root) if sessions_root else None
    owner = get_recording_service_owner(sessions_root=s_root)
    _, is_new = owner.acquire_session_owner(req, session_id)
    if not is_new:
        sys.exit(0)

    stop_requested = False

    def _sig_handler(signum: int, frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    owner.update_session_status(session_id, RecordingState.CAPTURING)

    if req.mock_mode:
        while not stop_requested:
            rec = owner.get_session_record(session_id)
            if rec and rec.status == RecordingState.STOPPED.value:
                break
            time.sleep(0.1)
        owner.update_session_status(session_id, RecordingState.STOPPED)
        sys.exit(0)

    from dual_pipeline.live_meeting_recorder import LiveMeetingRecorder  # noqa: lazy-decouple

    rec = LiveMeetingRecorder(
        project_name=req.project_name or "adhoc_session", meeting_name=req.meeting_name or "Live Meeting",
        meeting_date=req.meeting_date, attendees=req.attendees, device_index=req.device_index,
        segment_seconds=req.segment_seconds, whisper_model=req.whisper_model,
        output_dir=Path(req.output_dir) if req.output_dir else None, audio_source=req.audio_source,
        mock_mode=req.mock_mode, fast_start=True, session_id=req.session_id,
        host_chat_id=req.host_chat_id, idempotency_key=req.idempotency_key, is_cold_start=req.is_cold_start,
    )
    rec.run()
    owner.update_session_status(session_id, RecordingState.STOPPED)


_GLOBAL_OWNER: Optional[RecordingServiceOwner] = None
_GLOBAL_OWNERS: Dict[str, RecordingServiceOwner] = {}


def get_recording_service_owner(sessions_root: Optional[Path] = None) -> RecordingServiceOwner:
    global _GLOBAL_OWNER
    target_root = Path(sessions_root or (Path.cwd() / ".local" / "sessions")).resolve()
    key = str(target_root)
    if key not in _GLOBAL_OWNERS:
        _GLOBAL_OWNERS[key] = RecordingServiceOwner(sessions_root=target_root)
    _GLOBAL_OWNER = _GLOBAL_OWNERS[key]
    return _GLOBAL_OWNERS[key]


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Recording service owner daemon worker")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--request-file", required=True)
    parser.add_argument("--sessions-root", default=None)
    parsed = parser.parse_args()
    run_service_worker(parsed.session_id, parsed.request_file, sessions_root=parsed.sessions_root)

