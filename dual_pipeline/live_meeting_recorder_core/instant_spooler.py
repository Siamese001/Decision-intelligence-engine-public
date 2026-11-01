"""
dual_pipeline/live_meeting_recorder_core/instant_spooler.py

Layer 1: Zero-Latency Instant Audio Spooler Daemon.
Launches OS audio capture in <200ms into an ephemeral buffer completely
decoupled from worktree locks, directory scaffolding, attendee inputs, or network/disk latency.
Supports seamless runtime splicing into target worktree directories upon handoff.
"""
from __future__ import annotations

import atexit
import contextlib
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    register_active_recorder,
)
from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    generate_synthetic_speech_chunk,
)
from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import (
    SpoolChunkDescriptor,
    SpoolHandoffReceipt,
    SpoolSessionManifest,
    SpoolStatus,
    hydrate_spooler_from_manifest,
    migrate_single_chunk,
    save_spooler_manifest,
    terminate_spooler_ffmpeg,
)
from pipeline_a_intake.mac_system_audio import (
    FFMPEG_BIN,
    CoreAudioDeviceManager,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.instant_spooler")


class InstantAudioSpooler:
    """Supervises zero-latency ephemeral audio spooling with dynamic handoff splicing."""

    def __init__(
        self,
        spool_dir: Optional[Path] = None,
        segment_seconds: int = 15,
        device_index: str = ":0",
        mock_mode: bool = False,
        session_id: Optional[str] = None,
        start_chunk_idx: int = 0,
        source_mode: str = "mixed",
    ) -> None:
        self.session_id = session_id or f"spool_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.segment_seconds = int(segment_seconds)
        self.device_index = device_index or ":0"
        self.mock_mode = mock_mode
        self.start_chunk_idx = start_chunk_idx
        self.source_mode = source_mode.lower().strip() if source_mode else "mixed"

        # Ephemeral staging directory
        if spool_dir is not None:
            self.spool_dir = Path(spool_dir)
        else:
            self.spool_dir = Path(tempfile.gettempdir()) / f"nee_instant_spool_{self.session_id}"

        self.master_wav = self.spool_dir / "master_spool.wav"
        self.manifest_path = self.spool_dir / "spool_manifest.json"

        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._sck_proc: Optional[subprocess.Popen] = None
        self._mock_thread: Optional[threading.Thread] = None
        self._capture_mgr: Optional[Any] = None
        self._status = SpoolStatus.INITIALIZING
        self._lock = threading.RLock()
        self._is_running = False
        self._started_at_monotonic = 0.0
        self._started_at_iso = ""
        self._startup_latency_ms = 0.0
        self._known_chunks: Dict[int, SpoolChunkDescriptor] = {}
        self._registered_with_atexit = False
        self._is_adopted = False

        self._captured_pid, is_alive, status, self._started_at_iso, self._started_at_monotonic, self._startup_latency_ms = hydrate_spooler_from_manifest(self.manifest_path)
        if is_alive:
            self._is_running = True
            self._status = status

        register_active_recorder(self)

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._is_running

    @property
    def status(self) -> SpoolStatus:
        with self._lock:
            return self._status

    @property
    def startup_latency_ms(self) -> float:
        return self._startup_latency_ms

    @property
    def is_adopted(self) -> bool:
        with self._lock:
            return self._is_adopted

    def mark_adopted(self) -> None:
        """Marks the spooler as adopted by LiveMeetingRecorder to maintain single capture authority."""
        with self._lock:
            self._is_adopted = True
            self._status = SpoolStatus.HANDOFF_COMPLETE

    @classmethod
    def spawn_instant(
        cls,
        spool_dir: Optional[Path] = None,
        segment_seconds: int = 15,
        device_index: str = ":0",
        mock_mode: bool = False,
        session_id: Optional[str] = None,
        source_mode: str = "mixed",
    ) -> InstantAudioSpooler:
        """Factory method: creates and immediately initiates audio capture in <200ms."""
        spooler = cls(
            spool_dir=spool_dir,
            segment_seconds=segment_seconds,
            device_index=device_index,
            mock_mode=mock_mode,
            session_id=session_id,
            source_mode=source_mode,
        )
        spooler.start_eager()
        return spooler

    def _setup_signal_traps(self) -> None:
        if not self._registered_with_atexit:
            atexit.register(self._cleanup_on_exit)
            self._registered_with_atexit = True

    def _cleanup_on_exit(self) -> None:
        if self._is_running:
            try:
                self.stop()
            except Exception:
                pass

    def start_eager(self) -> InstantAudioSpooler:
        """Launches audio capture subprocess or mock thread with sub-200ms latency."""
        t0 = time.perf_counter()
        now = datetime.now()
        self._started_at_iso = now.isoformat()
        self._started_at_monotonic = time.monotonic()

        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self._setup_signal_traps()

        with self._lock:
            if self._is_running:
                return self
            self._is_running = True

            if self.mock_mode:
                self._start_mock_capture()
            elif self.source_mode in ("mic", "microphone", "iphone-speaker", "iphone_speaker"):
                self._start_ffmpeg_capture()
            else:
                self._start_multi_source_capture()

            self._status = SpoolStatus.ACTIVE_SPOOLING
            self._startup_latency_ms = (time.perf_counter() - t0) * 1000.0
            self._save_manifest()

        logger.info(
            "InstantAudioSpooler spawned session %s in %.2fms at %s",
            self.session_id,
            self._startup_latency_ms,
            self.spool_dir,
        )
        return self

    def _start_mock_capture(self) -> None:
        def _mock_loop():
            idx = self.start_chunk_idx
            while self._is_running:
                target_chunk = self.spool_dir / f"chunk_{idx:05d}.wav"
                if not target_chunk.exists():
                    generate_synthetic_speech_chunk(target_chunk, duration_sec=float(self.segment_seconds))
                idx += 1
                # Poll faster in mock mode for snappy test cycles
                time.sleep(float(self.segment_seconds) * 0.1)

        self._mock_thread = threading.Thread(target=_mock_loop, daemon=True, name=f"spool_mock_{self.session_id}")
        self._mock_thread.start()

    def _start_ffmpeg_capture(self) -> None:
        chunk_pat = str(self.spool_dir / "chunk_%05d.wav")

        dev_idx = self.device_index
        if self.source_mode in ("iphone-speaker", "iphone_speaker") and sys.platform == "darwin":
            with contextlib.suppress(Exception):
                builtin = CoreAudioDeviceManager.find_builtin_microphone()
                if builtin:
                    CoreAudioDeviceManager.set_default_input_device_id(builtin.device_id)
                dev_idx = CoreAudioDeviceManager.get_default_input_avfoundation_index()
        elif (not dev_idx or dev_idx == ":0") and sys.platform == "darwin":
            from dual_pipeline.live_meeting_recorder_core.audio_capture_strategy import AudioStrategyProfileCache
            cached = AudioStrategyProfileCache.lookup_fast(self.source_mode, self.device_index)
            dev_idx = cached[2] if (cached and cached[2]) else (dev_idx or ":0")
        dev_idx = dev_idx or ":0"

        ffmpeg_cmd = [
            FFMPEG_BIN, "-y", "-fflags", "nobuffer", "-flags", "low_delay",
            "-probesize", "32", "-analyzeduration", "0", "-f", "avfoundation", "-i", dev_idx,
            "-ar", "16000", "-ac", "1", "-flush_packets", "1", str(self.master_wav),
            "-ar", "16000", "-ac", "1", "-f", "segment", "-segment_time", str(self.segment_seconds),
            "-segment_start_number", str(self.start_chunk_idx), "-reset_timestamps", "1", "-flush_packets", "1", chunk_pat,
        ]

        try:
            self._ffmpeg_proc = subprocess.Popen(
                ffmpeg_cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            logger.error("Failed to start instant spooler FFmpeg capture: %s", e)
            self._is_running = False
            self._status = SpoolStatus.FAILED
            raise

    def _start_multi_source_capture(self) -> None:
        try:
            from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import (
                start_multi_source_spooler_capture,
            )
            self._capture_mgr, self._ffmpeg_proc, self._sck_proc = start_multi_source_spooler_capture(self)
        except Exception as e:
            logger.error("Spooler multi-source capture init failed (%s); failing closed per Decision D1.", e)
            self._is_running = False
            self._status = SpoolStatus.FAILED
            raise

    def poll_available_chunks(self) -> List[Path]:
        """Returns ready (completed) chunk WAV files that have stabilized on disk."""
        if not self.spool_dir.exists():
            return []

        chunks = sorted(self.spool_dir.glob("chunk_*.wav"))
        if not chunks:
            return []

        # If still actively recording, exclude the most recent chunk as it is still being written
        if self._is_running and len(chunks) > 1:
            return chunks[:-1]
        elif not self._is_running:
            return chunks
        return []

    def _maybe_migrate_master_wav(self, target_master_wav: Optional[Path]) -> None:
        if target_master_wav is None or not self.master_wav.exists():
            return
        target_master_path = Path(target_master_wav)
        target_master_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(self.master_wav, target_master_path)
        except Exception as e:
            logger.warning("Could not migrate master_spool.wav: %s", e)

    def handshake_and_splice(
        self,
        target_chunks_dir: Path,
        target_master_wav: Optional[Path] = None,
    ) -> SpoolHandoffReceipt:
        """
        Migrates all spooled chunks into target_chunks_dir without dropping frames.
        Returns a cryptographic SpoolHandoffReceipt.
        """
        t0 = time.perf_counter()
        target_chunks_dir = Path(target_chunks_dir)
        target_chunks_dir.mkdir(parents=True, exist_ok=True)

        with self._lock:
            self._status = SpoolStatus.SPLICING_HANDOFF
            migrated_indices: List[int] = []
            total_duration = 0.0

            # Only migrate sealed/available chunks to prevent copying open/growing chunk
            all_chunks = sorted(self.spool_dir.glob("chunk_*.wav"))
            eligible_chunks = self.poll_available_chunks() if self._is_running else all_chunks
            for cf in eligible_chunks:
                idx, dur = migrate_single_chunk(cf, target_chunks_dir)
                if idx is not None and idx not in migrated_indices:
                    migrated_indices.append(idx)
                total_duration += dur

            self._maybe_migrate_master_wav(target_master_wav)
            self._status = SpoolStatus.HANDOFF_COMPLETE
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            # Gapless is verified only if every chunk in the spool was sealed and migrated
            is_gapless = (len(migrated_indices) == len(all_chunks)) and (len(migrated_indices) > 0)

            receipt = SpoolHandoffReceipt(
                session_id=self.session_id,
                source_spool_dir=str(self.spool_dir),
                target_chunks_dir=str(target_chunks_dir),
                migrated_chunk_count=len(migrated_indices),
                migrated_chunk_indices=sorted(migrated_indices),
                total_audio_duration_seconds=round(total_duration, 3),
                handoff_at_iso=datetime.now().isoformat(),
                handoff_latency_ms=round(elapsed_ms, 2),
                is_gapless=is_gapless,
            )
            self._save_manifest(target_chunks_dir=str(target_chunks_dir))

            logger.info(
                "InstantAudioSpooler spliced %d chunks (%.1fs audio) to %s in %.2fms",
                len(migrated_indices),
                total_duration,
                target_chunks_dir,
                elapsed_ms,
            )
            return receipt

    def stop(self) -> None:
        """Gracefully halts audio capture process and updates manifest."""
        with self._lock:
            has_live_handles = bool(
                self._is_running
                or self._capture_mgr is not None
                or self._ffmpeg_proc is not None
                or getattr(self, "_captured_pid", None) is not None
                or (self._mock_thread is not None and self._mock_thread.is_alive())
            )
            if not has_live_handles:
                return
            if self._is_adopted and self._capture_mgr is None and getattr(self, "_captured_pid", None) is None:
                # Capture authority was adopted by LiveMeetingRecorder; do not terminate subprocess twice
                self._is_running = False
                return
            self._is_running = False

            if self._capture_mgr is not None:
                from dual_pipeline.live_meeting_recorder_core.instant_spooler_contracts import stop_spooler_capture_manager
                stop_spooler_capture_manager(self._capture_mgr)
                self._capture_mgr = self._ffmpeg_proc = self._sck_proc = None
            else:
                self._ffmpeg_proc, self._captured_pid = terminate_spooler_ffmpeg(
                    self._ffmpeg_proc, getattr(self, "_captured_pid", None)
                )

            if self._mock_thread is not None and self._mock_thread.is_alive():
                self._mock_thread.join(timeout=1.0)
                self._mock_thread = None

            self._status = SpoolStatus.STOPPED
            self._save_manifest()

    def discard(self) -> None:
        """Halts capture and cleans up all ephemeral staging files."""
        with self._lock:
            if self._is_adopted:
                return
        self.stop()
        with self._lock:
            self._status = SpoolStatus.DISCARDED
            if self.spool_dir.exists():
                with contextlib.suppress(Exception):
                    shutil.rmtree(self.spool_dir, ignore_errors=True)

    def _save_manifest(self, target_chunks_dir: Optional[str] = None) -> None:
        save_spooler_manifest(self, target_chunks_dir=target_chunks_dir)
