"""
dual_pipeline/live_meeting_recorder_core/instant_spooler_contracts.py

Layer 1: Typed dataclass contracts for InstantAudioSpooler.
Enforces typed session tracking, chunk inventory, and handoff receipts
for zero-latency ephemeral capture and splicing.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class SpoolStatus(str, Enum):
    INITIALIZING = "INITIALIZING"
    ACTIVE_SPOOLING = "ACTIVE_SPOOLING"
    SPLICING_HANDOFF = "SPLICING_HANDOFF"
    HANDOFF_COMPLETE = "HANDOFF_COMPLETE"
    STOPPED = "STOPPED"
    DISCARDED = "DISCARDED"
    FAILED = "FAILED"


@dataclass
class SpoolChunkDescriptor:
    chunk_index: int
    chunk_filename: str
    file_path: str
    created_at_iso: str
    created_at_monotonic: float
    file_size_bytes: int
    duration_seconds: float = 0.0
    rms_energy: float = 0.0
    migrated: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpoolSessionManifest:
    session_id: str
    spool_dir: str
    started_at_iso: str
    started_at_monotonic: float
    pid: int
    device_index: str
    segment_seconds: int
    status: SpoolStatus = SpoolStatus.INITIALIZING
    chunks: List[SpoolChunkDescriptor] = field(default_factory=list)
    stopped_at_iso: Optional[str] = None
    target_chunks_dir: Optional[str] = None
    startup_latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value if isinstance(self.status, SpoolStatus) else str(self.status)
        return d


@dataclass
class SpoolHandoffReceipt:
    session_id: str
    source_spool_dir: str
    target_chunks_dir: str
    migrated_chunk_count: int
    migrated_chunk_indices: List[int]
    total_audio_duration_seconds: float
    handoff_at_iso: str
    handoff_latency_ms: float
    is_gapless: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def hydrate_spooler_from_manifest(
    manifest_path: Path,
) -> Tuple[Optional[int], bool, SpoolStatus, str, float, float]:
    """Reads saved manifest to recover external capture PID and metadata if process is alive."""
    if not manifest_path.is_file():
        return None, False, SpoolStatus.INITIALIZING, "", 0.0, 0.0
    try:
        import json
        import os
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        saved_pid = m.get("pid")
        if saved_pid and int(saved_pid) != os.getpid():
            os.kill(int(saved_pid), 0)
            st_val = m.get("status")
            status = SpoolStatus(st_val) if st_val in SpoolStatus._value2member_map_ else SpoolStatus.ACTIVE_SPOOLING
            return (
                int(saved_pid),
                True,
                status,
                m.get("started_at_iso", ""),
                float(m.get("started_at_monotonic", 0.0)),
                float(m.get("startup_latency_ms", 0.0)),
            )
    except Exception:
        pass
    return None, False, SpoolStatus.INITIALIZING, "", 0.0, 0.0


def migrate_single_chunk(cf: Path, target_chunks_dir: Path) -> Tuple[Optional[int], float]:
    """Copies a spooled WAV chunk to the target directory and extracts duration."""
    import re
    import shutil
    import wave
    dest = target_chunks_dir / cf.name
    if not dest.exists() or dest.stat().st_size != cf.stat().st_size:
        shutil.copy2(cf, dest)

    m = re.search(r"chunk_(\d+)\.wav", cf.name)
    idx = int(m.group(1)) if m else None
    dur = 0.0
    if cf.stat().st_size > 44:
        try:
            with wave.open(str(cf), "rb") as w:
                rate = w.getframerate()
                if rate > 0:
                    dur = w.getnframes() / float(rate)
        except Exception:
            dur = 0.0
    return idx, dur


def start_multi_source_spooler_capture(spooler: Any) -> Tuple[Any, Optional[Any], Optional[Any]]:
    """Starts multi-source capture manager for instant audio spooling."""
    from dual_pipeline.live_meeting_recorder_core.audio_capture import LiveAudioCaptureManager  # noqa: lazy-decouple
    mgr = LiveAudioCaptureManager(
        chunks_dir=spooler.spool_dir,
        master_wav=spooler.master_wav,
        source_mode=spooler.source_mode,
        device_index=spooler.device_index,
        segment_seconds=spooler.segment_seconds,
        start_chunk_idx=spooler.start_chunk_idx,
        mock_mode=False,
        skip_device_discovery=True,
    )
    mgr.start(run_proof=False)
    return mgr, mgr._ffmpeg_proc, mgr._sck_proc


def stop_spooler_capture_manager(capture_mgr: Optional[Any]) -> None:
    """Stops and cleans up spooler capture manager."""
    if capture_mgr is not None:
        try:
            capture_mgr.stop_processes(fast=True)
        except Exception:
            pass


def save_spooler_manifest(spooler: Any, target_chunks_dir: Optional[str] = None) -> None:
    """Serializes SpoolSessionManifest to disk."""
    import json
    import logging
    import os
    try:
        pid_val = getattr(spooler._ffmpeg_proc, "pid", None) or getattr(spooler, "_captured_pid", None) or os.getpid()
        manifest = SpoolSessionManifest(
            session_id=spooler.session_id,
            spool_dir=str(spooler.spool_dir),
            started_at_iso=spooler._started_at_iso,
            started_at_monotonic=spooler._started_at_monotonic,
            pid=int(pid_val),
            device_index=spooler.device_index,
            segment_seconds=spooler.segment_seconds,
            status=spooler._status,
            startup_latency_ms=round(spooler._startup_latency_ms, 2),
            target_chunks_dir=target_chunks_dir,
        )
        spooler.manifest_path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    except Exception as e:
        logging.getLogger("dual_pipeline.live_meeting_recorder_core.instant_spooler").debug("Could not write spool manifest: %s", e)


def terminate_spooler_ffmpeg(proc: Optional[Any], captured_pid: Optional[int]) -> Tuple[Optional[Any], Optional[int]]:
    """Terminates active FFmpeg or captured PID process."""
    import contextlib
    import os
    import signal
    import subprocess
    if proc is not None:
        try:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
        except Exception:
            pass
        return None, captured_pid
    if captured_pid is not None and captured_pid != os.getpid():
        with contextlib.suppress(OSError):
            os.kill(captured_pid, signal.SIGINT)
            for _ in range(20):
                try:
                    os.kill(captured_pid, 0)
                    import time
                    time.sleep(0.05)
                except OSError:
                    break
            else:
                with contextlib.suppress(OSError):
                    os.kill(captured_pid, signal.SIGTERM)
    return None, None


def adopt_spooler_into_capture_manager(mgr: Any, spooler: Any) -> None:
    """Adopts spooler capture state into LiveAudioCaptureManager."""
    import contextlib
    with mgr._state_lock:
        cap_mgr = getattr(spooler, "_capture_mgr", None)
        if cap_mgr is not None:
            if hasattr(cap_mgr, "_watchdog"):
                with contextlib.suppress(Exception):
                    cap_mgr._watchdog.stop()
            mgr._ffmpeg_proc = getattr(cap_mgr, "_ffmpeg_proc", None)
            mgr._sck_proc = getattr(cap_mgr, "_sck_proc", None)
            mgr._mock_thread = getattr(cap_mgr, "_mock_thread", None)
            mgr._active_strategy = getattr(cap_mgr, "active_strategy", getattr(spooler, "source_mode", "spooler_adopted"))
            mgr.device_index = getattr(cap_mgr, "device_index", mgr.device_index)
            mgr._detected_headphones = getattr(cap_mgr, "_detected_headphones", False)
            mgr._detected_mute = getattr(cap_mgr, "_detected_mute", False)
        else:
            mgr._ffmpeg_proc = getattr(spooler, "_ffmpeg_proc", None)
            mgr._sck_proc = getattr(spooler, "_sck_proc", None)
            mgr._mock_thread = getattr(spooler, "_mock_thread", None)
            mgr._captured_pid = getattr(spooler, "_captured_pid", None)
            mgr._active_strategy = "spooler_adopted"

        mgr._is_running = True
        if hasattr(spooler, "spool_dir") and spooler.spool_dir:
            mgr.chunks_dir = Path(spooler.spool_dir)
            if hasattr(spooler, "master_wav") and spooler.master_wav:
                mgr.master_wav = Path(spooler.master_wav)

        if hasattr(spooler, "mark_adopted"):
            spooler.mark_adopted()

        procs = [p for p in (mgr._sck_proc, mgr._ffmpeg_proc) if p is not None]
        if procs and hasattr(mgr, "_watchdog"):
            with contextlib.suppress(Exception):
                mgr._watchdog.register_processes(procs, mgr.chunks_dir)
                mgr._watchdog.start()



