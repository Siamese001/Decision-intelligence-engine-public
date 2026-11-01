from __future__ import annotations

import atexit
import fcntl
import json
import logging
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logger = logging.getLogger("dual_pipeline.live_meeting_recorder")

from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import (
    GlobalMLXSessionLock, LiveSessionCollisionError, acquire_global_mlx_lock
)
from dual_pipeline.live_meeting_recorder_core.alignment_sync import (
    AlignmentDesynchronizationError,
    extract_initial_speaker_map as _extract_initial_speaker_map,
    verify_temporal_sync as _verify_temporal_sync,
    check_cluster_collapse as _check_cluster_collapse,
)
from dual_pipeline.live_meeting_recorder_core.acoustic_features import write_acoustic_evidence_receipt
from dual_pipeline.live_meeting_recorder_core.recording_host_integration import (
    clean_session_quarantine,
    enforce_initiation_budget,
    quarantine_recording_session,
    verify_adopted_spooler_proof,
)

import contextlib

from dual_pipeline.audio_ingestion import ensure_ffmpeg
from dual_pipeline.live_meeting_recorder_core.attendees import parse_attendees
from dual_pipeline.live_meeting_recorder_core.audio_capture import (
    AudioCaptureUnprovenError, AudioReasonCode, AudioStarvationDetector, LiveAudioCaptureManager,
)
from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    MIN_VALID_CHUNK_BYTES, register_active_recorder, validate_final_chunk,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import AudioMasterStitcher
from dual_pipeline.live_meeting_recorder_core.audio_utils import (
    _update_session_manifest, append_live_transcript_line, append_live_transcript_pause,
    format_chunk_time, format_hh_mm_ss, get_audio_rms, init_live_transcript_file,  # noqa: F401
    is_cross_chunk_repetition, load_processed_chunk_files, resolve_chunk_startup_state,
    seal_live_transcript_footer, write_atomic_session_manifest,
)
from dual_pipeline.live_meeting_recorder_core.audio_watchdog import AudioEnergyWatchdog
from dual_pipeline.live_meeting_recorder_core.chat_attribution_collector import (
    ChatAttributionCollector, InlineChatAttribution,
)
from dual_pipeline.live_meeting_recorder_core.chunk_health import ChunkHealth
from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import (
    extract_chunk_telemetry, extract_chunk_text_and_words, transcribe_meeting_chunk,
)
from dual_pipeline.live_meeting_recorder_core.copilot import LiveCopilotAdvisor
from dual_pipeline.live_meeting_recorder_core.hitl_manager import assert_no_hitl_bypass
from dual_pipeline.live_meeting_recorder_core.inflection_detector import InflectionDetector
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    AudioAnalysisError, IsolatedMLXTranscriberClient, MLXInitError, MLXReasonCode, MLXRuntimeLost,
)
from dual_pipeline.live_meeting_recorder_core.lexical_priming import LexicalPrimer
from dual_pipeline.live_meeting_recorder_core.live_voice_registry import LiveVoiceRegistry
from dual_pipeline.live_meeting_recorder_core.speaker_hints import (
    HintResult, SpeakerHintManager,
)
from dual_pipeline.live_meeting_recorder_core.stream_aggregator import (
    ConversationalSpeakerTracker, ConversationalTurnAggregator,
)
from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
    is_authentic_git_worktree, is_inside_isolated_worktree, migrate_staged_files,
    provision_live_transcription_worktree, repoint_recorder_paths,
    resolve_recorder_paths, teardown_empty_live_worktree,
)
from dual_pipeline.meeting_paths import normalize_meeting_date, standardize_meeting_slug
from dual_pipeline.live_meeting_recorder_core.fast_bootstrap import (
    resolve_bootstrap_spooler,
    resolve_session_staging_paths as _resolve_session_staging_paths,
)

def _archive_stale_residual_chunks(chunks_dir: Path, chunks: list[Path], timestamp: str) -> None:
    stale_archive = chunks_dir / f".stale_{timestamp}"
    stale_archive.mkdir(parents=True, exist_ok=True)
    for chunk_f in chunks:
        with contextlib.suppress(Exception):
            shutil.move(str(chunk_f), str(stale_archive / chunk_f.name))


class LiveMeetingRecorder:
    """Governed live meeting recorder and real-time transcription engine."""

    def __init__(
        self,
        project_name: Optional[str] = None,
        meeting_name: Optional[str] = None,
        meeting_date: Optional[str] = None,
        attendees: Union[str, List[Any], None] = None,
        device_index: str = ":0",
        segment_seconds: int = 15,
        whisper_prompt: Optional[str] = None,
        output_dir: Optional[Path] = None,
        mock_mode: bool = False,
        backend: str = "auto",
        audio_source: str = "auto",
        whisper_model: Optional[str] = None,
        enable_ws: bool = False,
        ws_port: int = 8765,
        enable_copilot: bool = False,
        copilot_prefetch: bool = True,
        resume: bool = False,
        isolated_process: bool = True,
        enforce_worktree: bool = True,
        enable_hitl: bool = True,
        inactivity_timeout_seconds: float = 0.0,
        max_duration_minutes: Optional[float] = None,
        initial_speaker_map: Optional[Dict[str, str]] = None,
        immediate_worktree: bool = False,
        dry_run: bool = False,
        fast_start: bool = True,
        spooler: Any = None,
        init_result: Any = None,
        **kwargs: Any,
    ):
        t_init_start = time.perf_counter()
        self.fast_start = fast_start
        if enable_hitl is False:
            raise ValueError("HITL cannot be disabled for live transcription. Human-In-The-Loop resolution is mandatory.")
        assert_no_hitl_bypass(kwargs)
        register_active_recorder(self)

        self.init_result = init_result or kwargs.get("init_result")
        if self.init_result:
            meeting_name = meeting_name or getattr(self.init_result, "meeting_name", None)
            project_name = project_name or getattr(self.init_result, "project_name", None)
            if getattr(self.init_result, "date_dirname", None):
                meeting_date = getattr(self.init_result, "meeting_date_display", None) or meeting_date
            if getattr(self.init_result, "attendees", None) and not attendees:
                attendees = self.init_result.attendees

        self.meeting_name = meeting_name
        from dual_pipeline.project_aliases import infer_project_from_title, resolve_project_name
        if not project_name or str(project_name).lower() == "auto":
            inferred = infer_project_from_title(meeting_name) if meeting_name else None
            self.project_name = resolve_project_name(inferred) if inferred else ("UNRESOLVED" if meeting_name else None)
        else:
            self.project_name = resolve_project_name(str(project_name).strip())

        self.is_anonymous = not self.meeting_name and not self.project_name

        self._anonymous_transcript_buffer = []
        self._anonymous_segments_buffer = []
        self.resume, self.isolated_process, self.dry_run = resume, isolated_process, dry_run
        self.session_start_time, self.session_start_monotonic = time.time(), time.monotonic()
        self.initiation_budget_seconds = float(kwargs.get("initiation_budget_seconds") or 30.0)
        self.initiation_deadline_monotonic = self.session_start_monotonic + self.initiation_budget_seconds
        val_inactivity = kwargs.get("inactivity_timeout_seconds")
        self.inactivity_timeout_seconds = float(val_inactivity if val_inactivity is not None else inactivity_timeout_seconds)
        self.max_duration_minutes = float(kwargs["max_duration_minutes"]) if "max_duration_minutes" in kwargs else (float(max_duration_minutes) if max_duration_minutes is not None else None)
        self.last_audible_monotonic = time.monotonic()
        self.total_audible_chunks = 0
        self.auto_stopped_reason: Optional[str] = None

        now = datetime.now()
        if meeting_date:
            try:
                self.date_dirname, self.meeting_date_display = normalize_meeting_date(meeting_date)
            except Exception:
                self.date_dirname, self.meeting_date_display = meeting_date.strip().replace("/", "-"), meeting_date
        else:
            self.date_dirname, self.meeting_date_display = normalize_meeting_date(now)

        self.attendees = parse_attendees(attendees, project_name=self.project_name)
        self.initial_speaker_map: Dict[str, str] = _extract_initial_speaker_map(initial_speaker_map, kwargs)
        self.device_index, self.segment_seconds, self.mock_mode = device_index, segment_seconds, mock_mode
        if backend and backend.lower() not in ("auto", "mlx"):
            raise ValueError(
                f"Transcription backend '{backend}' is prohibited. "
                "Only local offline MLX Metal GPU transcription is permitted for live meeting audio."
            )
        self.backend, self.whisper_model = backend, whisper_model
        self.audio_source = audio_source.lower().strip() if audio_source else "auto"
        self.enable_ws, self.ws_port, self.enable_copilot, self.enable_copilot_prefetch = enable_ws, ws_port, enable_copilot, bool(kwargs.get("enable_copilot_prefetch", copilot_prefetch))
        self.instant, self.probation_ms, self.host_chat_id, self.idempotency_key, self.is_cold_start = bool(kwargs.get("instant", True)), int(kwargs.get("probation_ms", 1000)), kwargs.get("host_chat_id"), kwargs.get("idempotency_key"), bool(kwargs.get("is_cold_start", False))
        self.session_id, self._copilot_staleness_warned = str(kwargs.get("session_id", uuid.uuid4())), False
        if self.init_result and getattr(self.init_result, "clean_slug", None):
            self.clean_slug = self.init_result.clean_slug
        elif self.is_anonymous:
            self.clean_slug = f"anon_session_{uuid.uuid4().hex[:8]}"
        else:
            try:
                self.clean_slug = standardize_meeting_slug(self.meeting_name, attendees=self.attendees, max_len=40, project_name=self.project_name)
            except Exception:
                clean = re.sub(r"[^\w\-]+", "_", self.meeting_name).strip("_")
                self.clean_slug = clean[:40] if clean else "session"
        self.output_dir = self._target_output_dir = output_dir or (ROOT / ".session_registry" / self.clean_slug)
        self._acquire_slug_advisory_lock()
        self.immediate_worktree = immediate_worktree
        try:
            self._complete_post_lock_init(
                now=now, t_init_start=t_init_start, output_dir=output_dir, enforce_worktree=enforce_worktree,
                spooler=spooler, whisper_prompt=whisper_prompt, copilot_prefetch=copilot_prefetch, kwargs=kwargs,
            )
        except BaseException:
            self._release_slug_lock()
            raise

    def _acquire_slug_advisory_lock(self) -> None:
        self._global_mlx_lock = acquire_global_mlx_lock(
            self.clean_slug, self.project_name, self.session_id, repo_root=(self.output_dir if (self.mock_mode and self.dry_run) else None),
        )
        self._slug_lock_path, self._slug_lock_fd = self._global_mlx_lock.lock_path.parent / f"{self.clean_slug}.flock", None
        try:
            self._slug_lock_fd = open(self._slug_lock_path, "a+")  # noqa: SIM115
            fcntl.flock(self._slug_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._slug_lock_fd.seek(0)
            self._slug_lock_fd.truncate()
            self._slug_lock_fd.write(f"{os.getpid()}\n")
            self._slug_lock_fd.flush()
        except (BlockingIOError, OSError) as e:
            holder_pid = None
            with contextlib.suppress(Exception):
                content = Path(self._slug_lock_path).read_text().strip()
                if content.isdigit():
                    holder_pid = int(content)
            self._release_slug_lock()
            raise LiveSessionCollisionError(self.clean_slug, holder_pid) from e

    def _init_target_and_staging_paths(self, now: datetime, output_dir: Optional[Path], enforce_worktree: bool) -> None:
        ensure_ffmpeg()
        if self.init_result:
            self.worktree_dir = getattr(self.init_result, "worktree_dir", None)
            self.worktree_branch = getattr(self.init_result, "worktree_branch", None)
            self._target_output_dir = getattr(self.init_result, "output_dir", output_dir or ROOT)
            self._target_cache_dir = getattr(self.init_result, "cache_dir", self._target_output_dir / ".cache")
            self._target_chunks_dir = getattr(self.init_result, "chunks_dir", self._target_output_dir / "chunks")
            self._is_inside_worktree = is_inside_isolated_worktree(ROOT)
            self._worktree_materialized = (self.worktree_dir is None) or self._is_inside_worktree
            resolved_out, resolved_cache, resolved_chunks = self._target_output_dir, self._target_cache_dir, self._target_chunks_dir
        elif self.is_anonymous:
            self.worktree_dir = self.worktree_branch = None
            self._target_output_dir = output_dir or (ROOT / ".session_registry" / self.clean_slug)
            self._target_cache_dir, self._target_chunks_dir = self._target_output_dir / ".cache", self._target_output_dir / "chunks"
            self._is_inside_worktree, self._worktree_materialized = False, True
            resolved_out, resolved_cache, resolved_chunks = self._target_output_dir, self._target_cache_dir, self._target_chunks_dir
        else:
            resolved_out, resolved_cache, resolved_chunks, self.worktree_dir, self.worktree_branch = resolve_recorder_paths(
                self.project_name, self.date_dirname, self.clean_slug, output_dir, ROOT,
                enforce_worktree=enforce_worktree, meeting_name=self.meeting_name, lazy=True,
            )
            self._target_output_dir, self._target_cache_dir, self._target_chunks_dir = resolved_out, resolved_cache, resolved_chunks
            self._is_inside_worktree = is_inside_isolated_worktree(ROOT)
            self._worktree_materialized = (self.worktree_dir is None) or self._is_inside_worktree

        self.session_tag = now.strftime("%H%M%S")
        self.output_dir, self.cache_dir, self.chunks_dir = _resolve_session_staging_paths(
            self._worktree_materialized, resolved_out, resolved_cache, resolved_chunks,
            self.project_name, self.date_dirname, self.clean_slug, self.session_tag, self.mock_mode,
        )
        self._staging_chunks_dir = self.chunks_dir
        pfx = f"00_{self.date_dirname}_{self.clean_slug}"
        self.master_wav = self.cache_dir / f"{pfx}_master_{self.session_tag}.wav"
        self.session_manifest = self.cache_dir / f"{pfx}_{self.session_tag}_manifest.json"
        self.acoustic_evidence_receipt_path = self.cache_dir / f"{pfx}_{self.session_tag}_acoustic_evidence_receipt.json"
        self.transcript_md = self.output_dir / f"01_{self.date_dirname}_{self.clean_slug}_live_transcript.md"
        self.segments_jsonl = self.cache_dir / f"01_{self.date_dirname}_{self.clean_slug}_segments.jsonl"
        self.copilot_jsonl = self.cache_dir / f"01_{self.date_dirname}_{self.clean_slug}_copilot_feedback.jsonl"
        self.asr_filter_log = self.cache_dir / "asr_filter_log.jsonl"
        self.referenced_artifacts_dir = self.ref_dir = self.output_dir / "referenced_artifacts"
        self.collateral_drop_log_path = self.output_dir / "collateral_drop_log.json"
        self._sync_session_manifest_paths()

    def _apply_spooler_handshake(self) -> None:
        if self.dry_run or not self.spooler or not hasattr(self.spooler, "handshake_and_splice"):
            return
        self.spooler_receipt = self.spooler.handshake_and_splice(target_chunks_dir=self.chunks_dir, target_master_wav=self.master_wav)
        migrated_indices = getattr(self.spooler_receipt, "migrated_chunk_indices", None)
        if migrated_indices:
            self.start_chunk_idx = max(migrated_indices) + 1
        elif getattr(self.spooler_receipt, "migrated_chunk_count", 0) > 0:
            self.start_chunk_idx = self.spooler_receipt.migrated_chunk_count

    def _init_spooler_and_worktree_state(self, now: datetime, should_resume: bool, spooler: Any, kwargs: Dict[str, Any]) -> None:
        if self.immediate_worktree and not self._worktree_materialized:
            from dual_pipeline.live_meeting_recorder_core.worktree_manager import run_git
            run_git(["worktree", "prune"], cwd=ROOT, check=False)
            target_existing = list(self._target_chunks_dir.glob("chunk_*.wav")) if self._target_chunks_dir.exists() else []
            if target_existing and not should_resume:
                _archive_stale_residual_chunks(self._target_chunks_dir, target_existing, now.strftime('%H%M%S'))
            self.materialize_worktree()

        self.check_initiation_budget("WORKTREE_SETUP")
        self.collateral_watcher, self.chat_collateral_bridge = None, None
        self.starvation_detector = AudioStarvationDetector(threshold_rms=0.001, starvation_seconds=15.0)
        self.starvation_alarms: list[Dict[str, Any]] = []
        self.stop_lifecycle_errors: list[str] = []
        self.processed_chunks: set[str] = set()
        self.start_chunk_idx = resolve_chunk_startup_state(self.chunks_dir, self.session_tag, should_resume)
        self.spooler = resolve_bootstrap_spooler(spooler or kwargs.get("spooler"), self.dry_run, self.mock_mode)
        if self.spooler is not None and int(getattr(self.spooler, "segment_seconds", 0) or 0) > 0:
            self.segment_seconds = int(self.spooler.segment_seconds)
        self.spooler_receipt = None
        self._apply_spooler_handshake()
        self.check_initiation_budget("SPOOLER_HANDSHAKE")

    def _complete_post_lock_init(
        self, *, now: datetime, t_init_start: float, output_dir: Optional[Path],
        enforce_worktree: bool, spooler: Any, whisper_prompt: Optional[str],
        copilot_prefetch: bool, kwargs: Dict[str, Any],
    ) -> None:
        self._setup_abort_and_signal_handlers()
        from dual_pipeline.live_meeting_recorder_core.session_registry import SessionRegistry
        self.session_registry = SessionRegistry(ROOT)
        self.session_registry.reap_stale_sessions()
        self.session_manifest_record = self.session_registry.register(
            session_id=self.session_id, parent_pid=os.getpid(), meeting_slug=self.clean_slug,
            ws_port=self.ws_port, engagement_name=self.project_name, attendees=self.attendees,
        )
        self.session_id = self.session_manifest_record.session_id
        self.check_initiation_budget("SESSION_REGISTRY")
        self._init_target_and_staging_paths(now, output_dir, enforce_worktree)
        should_resume = bool(self.resume or (self.meeting_name and "resume" in self.meeting_name.lower()))
        self._init_spooler_and_worktree_state(now, should_resume, spooler, kwargs)

        self.processed_chunks = load_processed_chunk_files(self.segments_jsonl)
        self.transcribed_chunks = set(self.processed_chunks)
        self._Queued_chunks, self._chunks_lock = set(), threading.Lock()
        self._chunk_failures, self._consecutive_asr_failures, self._asr_failed = {}, 0, False
        self.last_written_chunk_idx = (self.start_chunk_idx - 1) if self.start_chunk_idx > 0 else None
        self._cumulative_elapsed_seconds = 0.0
        self._chunk_timings: Dict[int, Tuple[float, float]] = {}
        self.running = False
        self._transcript_io_lock = threading.RLock()
        self._chunk_queue: queue.Queue[Optional[Tuple[Path, int]]] = queue.Queue(maxsize=64)
        self._asr_worker_thread: Optional[threading.Thread] = None
        self._max_backlog, self._poll_lag_sec = 0, 0.0
        self.capture_manager = LiveAudioCaptureManager(
            chunks_dir=self.chunks_dir, master_wav=self.master_wav, source_mode=self.audio_source,
            device_index=self.device_index, segment_seconds=self.segment_seconds, start_chunk_idx=self.start_chunk_idx,
            mock_mode=self.mock_mode, deadline_monotonic=self.initiation_deadline_monotonic,
            skip_device_discovery=bool(self.spooler and self.spooler.is_running),
        )
        self._ffmpeg_proc_override: Optional[subprocess.Popen] = None
        self.lexical_primer = LexicalPrimer(project_name=self.project_name)
        self.whisper_prompt = whisper_prompt or self.lexical_primer.build_whisper_prompt(meeting_name=self.meeting_name, attendees=self.attendees)
        self._init_collateral_subsystem()
        self.ws_server = None
        self.copilot_advisor = (
            LiveCopilotAdvisor(
                self.project_name, self.meeting_name, self.attendees, mock_mode=self.mock_mode,
                enable_prefetch=copilot_prefetch, session_output_dir=self.output_dir,
                collateral_watcher=self.collateral_watcher, get_elapsed_fn=lambda: self.elapsed_seconds,
            )
            if self.enable_copilot else None
        )
        self.inflection_detector = InflectionDetector() if self.enable_copilot else None
        from dual_pipeline.live_meeting_recorder_core.audio_vad import AudioVADSegmenter
        self.vad_segmenter = AudioVADSegmenter(energy_threshold=0.0037, min_speech_duration_ms=240, min_silence_duration_ms=400)
        self.isolated_transcriber = (
            IsolatedMLXTranscriberClient(mock_mode=self.mock_mode, default_model=self.whisper_model, backend=self.backend)
            if self.isolated_process and not self.dry_run else None
        )
        self._init_speaker_and_diarization_subsystems(should_resume, now, t_init_start)

    def _init_speaker_and_diarization_subsystems(self, should_resume: bool, now: datetime, t_init_start: float) -> None:
        self.enable_post_stop_diarization = False
        self.diarization_engine = self._diarization_thread = self._diarization_load_error = None
        if not self.mock_mode and not self.dry_run:
            from dual_pipeline.audio_ingestion_core.diarization_engine import check_diarization_preflight
            preflight_ok, preflight_reason = check_diarization_preflight()
            if not preflight_ok:
                logger.warning("[DIARIZATION-PREFLIGHT] Pinned diarization preflight check failed: %s", preflight_reason)
        self._cumulative_audio_sec = self._load_resumed_audio_sec(should_resume)
        self.turn_aggregator = ConversationalTurnAggregator()
        self.speaker_tracker = ConversationalSpeakerTracker(self.attendees)
        self.chat_attributions_path = self.output_dir / "inline_chat_attributions.json"
        self.chat_attributions = ChatAttributionCollector(attendees=self.attendees, output_path=self.chat_attributions_path)
        self.voice_registry = LiveVoiceRegistry(initial_speaker_map=self.initial_speaker_map, attendees=self.attendees)
        self.speaker_hint_manager = SpeakerHintManager(
            output_dir=self.output_dir, cache_dir=self.cache_dir, attendees=self.attendees, voice_registry=self.voice_registry,
        )
        from dual_pipeline.live_meeting_recorder_core.speaker_hint_queue import DurableSpeakerHintQueue
        self.speaker_hint_queue = DurableSpeakerHintQueue(self.output_dir, self.cache_dir)
        self.audio_watchdog = AudioEnergyWatchdog(
            flatline_threshold_rms=5.0, alert_after_chunks=2, segment_seconds=self.segment_seconds, on_alert_callback=self._on_audio_watchdog_alert,
        )
        self._silent_pause_start_sec, self._silent_pause_end_sec, self._silent_chunk_count = None, None, 0
        self.recent_transcripts: List[str] = []
        self._write_session_manifest(now)
        self.initialization_latency_ms = (time.perf_counter() - t_init_start) * 1000.0
        self._start_heartbeat_worker()

    def _load_resumed_audio_sec(self, should_resume: bool) -> float:
        if not (should_resume and self.segments_jsonl.exists()):
            return 0.0
        with contextlib.suppress(Exception):
            lines = [ln.strip() for ln in self.segments_jsonl.read_text(encoding="utf-8").splitlines() if ln.strip()]
            if lines:
                last = json.loads(lines[-1])
                return float(last.get("seconds", 0.0)) + float(last.get("duration", self.segment_seconds))
        return 0.0

    def _init_collateral_subsystem(self) -> None:
        """Initializes collateral referenced artifacts folder and watcher subsystem."""
        self.collateral_ref_dir = self.output_dir / "referenced_artifacts"
        if not self.mock_mode and not self.dry_run and self._worktree_materialized:
            self.collateral_ref_dir.mkdir(parents=True, exist_ok=True)
        self.collateral_drop_log = self.output_dir / "collateral_drop_log.json"
        from dual_pipeline.live_meeting_recorder_core.collateral_watcher import CollateralFsWatcher

        self.collateral_watcher = CollateralFsWatcher(ref_dir=self.collateral_ref_dir, get_elapsed_fn=lambda: getattr(self, "elapsed_seconds", 0.0), log_path=self.collateral_drop_log)
        if not self.mock_mode and not self.dry_run:
            self.collateral_watcher.start()

    @property
    def elapsed_seconds(self) -> float:
        """Returns current session elapsed seconds, prioritizing audio timeline over wall clock."""
        if getattr(self, "_cumulative_elapsed_seconds", 0.0) > 0.0:
            return self._cumulative_elapsed_seconds
        return max(
            0.0, time.monotonic() - getattr(self, "session_start_monotonic", time.monotonic())
        )

    def resolve_identity(
        self,
        project_name: str,
        meeting_name: str,
        attendees: Union[str, List[Any], None] = None,
        meeting_date: Optional[str] = None,
    ):
        """Wave 4: Atomic Migration & Resolution Phase"""
        if not self.is_anonymous:
            return

        from dual_pipeline.project_aliases import resolve_project_name
        self.project_name = resolve_project_name(project_name) if project_name else "UNRESOLVED"
        self.meeting_name = meeting_name
        if attendees:
            self.attendees = parse_attendees(attendees, project_name=self.project_name)

        now = datetime.now()
        if meeting_date:
            try:
                self.date_dirname, self.meeting_date_display = normalize_meeting_date(meeting_date)
            except Exception:
                self.date_dirname, self.meeting_date_display = (
                    meeting_date.strip().replace("/", "-"),
                    meeting_date,
                )
        else:
            self.date_dirname, self.meeting_date_display = normalize_meeting_date(now)

        try:
            self.clean_slug = standardize_meeting_slug(
                self.meeting_name,
                attendees=self.attendees,
                max_len=40,
                project_name=self.project_name,
            )
        except Exception:
            clean = re.sub(r"[^\w\-]+", "_", self.meeting_name).strip("_")
            self.clean_slug = clean[:40] if clean else "session"

        # Provision worktree
        from dual_pipeline.live_meeting_recorder_core.worktree_manager import (
            ensure_live_transcription_worktree,
        )

        old_chunks_dir = getattr(self, "chunks_dir", None)
        old_segments_jsonl = getattr(self, "segments_jsonl", None)
        old_transcript_md = getattr(self, "transcript_md", None)

        wt_dir, wt_branch = ensure_live_transcription_worktree(
            project_name=self.project_name,
            meeting_name=self.meeting_name,
            date_dirname=self.date_dirname,
            clean_slug=self.clean_slug,
            repo_root=ROOT,
            lazy=False,
        )
        self.worktree_dir = wt_dir
        self.worktree_branch = wt_branch
        self._worktree_materialized = is_authentic_git_worktree(wt_dir)

        resolved_out = wt_dir / "output" / self.project_name / self.date_dirname / self.clean_slug
        resolved_cache = resolved_out / ".cache"
        resolved_chunks = resolved_cache / "chunks"

        resolved_out.mkdir(parents=True, exist_ok=True)
        resolved_cache.mkdir(parents=True, exist_ok=True)
        resolved_chunks.mkdir(parents=True, exist_ok=True)

        # Move chunks
        if old_chunks_dir and old_chunks_dir.exists():
            for chunk_file in old_chunks_dir.glob("chunk_*.wav"):
                shutil.move(str(chunk_file), str(resolved_chunks / chunk_file.name))

        self._target_output_dir, self._target_cache_dir, self._target_chunks_dir = (
            resolved_out, resolved_cache, resolved_chunks
        )
        repoint_recorder_paths(self, target_worktree_dir=wt_dir)
        self._migrate_anonymous_session_files(old_segments_jsonl, old_transcript_md)
        self._flush_anonymous_buffers_and_reset()

    def _flush_anonymous_buffers_and_reset(self) -> None:
        if self._anonymous_segments_buffer:
            with open(self.segments_jsonl, "a") as f:
                for seg in self._anonymous_segments_buffer:
                    f.write(json.dumps(seg) + "\n")
        if self._anonymous_transcript_buffer:
            with open(self.transcript_md, "a") as f:
                for t in self._anonymous_transcript_buffer:
                    f.write(t + "\n")
        self.is_anonymous = False
        self._anonymous_transcript_buffer, self._anonymous_segments_buffer = [], []
        self.lexical_primer = LexicalPrimer(project_name=self.project_name)
        self.whisper_prompt = self.lexical_primer.build_whisper_prompt(meeting_name=self.meeting_name, attendees=self.attendees)
        self.speaker_tracker = ConversationalSpeakerTracker(self.attendees)
        for attr_name in ("chat_attributions", "speaker_hint_manager", "voice_registry"):
            obj = getattr(self, attr_name, None)
            if obj is not None:
                obj.attendees = self.attendees

    def _migrate_anonymous_session_files(self, old_segments_jsonl: Any, old_transcript_md: Any) -> None:
        for old_f, new_f in ((old_segments_jsonl, self.segments_jsonl), (old_transcript_md, self.transcript_md)):
            if old_f and Path(old_f).is_file() and Path(old_f).resolve() != Path(new_f).resolve():
                Path(new_f).parent.mkdir(parents=True, exist_ok=True)
                with open(old_f, "r", encoding="utf-8") as src_f, open(new_f, "a", encoding="utf-8") as dst_f:
                    dst_f.write(src_f.read())
                with contextlib.suppress(Exception):
                    Path(old_f).unlink()

    def adopt_spooler(self, spooler: Any) -> Any:
        """Adopts an existing InstantAudioSpooler, splicing its buffered chunks into the worktree."""
        receipt = spooler.handshake_and_splice(
            target_chunks_dir=self.chunks_dir,
            target_master_wav=self.master_wav,
        )
        self.spooler = spooler
        self.spooler_receipt = receipt
        if getattr(receipt, "migrated_chunk_indices", None):
            self.start_chunk_idx = max(receipt.migrated_chunk_indices) + 1
        elif getattr(receipt, "migrated_chunk_count", 0) > 0:
            self.start_chunk_idx = receipt.migrated_chunk_count
        if hasattr(self, "capture_manager") and self.capture_manager is not None:
            self.capture_manager.start_chunk_idx = self.start_chunk_idx
        return receipt

    @property
    def ffmpeg_proc(self) -> Optional[subprocess.Popen]:
        if hasattr(self, "capture_manager") and self.capture_manager:
            return getattr(self.capture_manager, "_ffmpeg_proc", None)
        return getattr(self, "_ffmpeg_proc_override", None)

    @ffmpeg_proc.setter
    def ffmpeg_proc(self, val: Optional[subprocess.Popen]) -> None:
        self._ffmpeg_proc_override = val

    def apply_speaker_hint(
        self,
        quote: str,
        speaker_name: str,
        force: bool = False,
        operator_id: str = "operator",
    ) -> HintResult:
        """Applies an operator speaker hint to the active recording session."""
        if not hasattr(self, "speaker_hint_manager") or not self.speaker_hint_manager:
            return HintResult(
                status="FAIL",
                error="HINT_MANAGER_UNAVAILABLE",
                details="SpeakerHintManager not initialized.",
            )
        with getattr(self, "_transcript_io_lock", contextlib.nullcontext()):
            res = self.speaker_hint_manager.apply_hint(
                quote=quote,
                speaker_name=speaker_name,
                segments_jsonl_path=self.segments_jsonl,
                transcript_md_path=self.transcript_md,
                operator_id=operator_id,
                force=force,
                meeting_slug=self.clean_slug,
            )
            if (
                res.status == "PASS"
                and res.voice_id
                and hasattr(self, "voice_registry")
                and self.voice_registry
            ):
                self.voice_registry.bind_speaker_hint(res.voice_id, speaker_name)
            return res

    def record_chat_message(
        self, message: str, elapsed_seconds: Optional[float] = None
    ) -> List[InlineChatAttribution]:
        """Record and parse an inline operator chat message for speaker attributions."""
        sec = elapsed_seconds
        attributions = self.chat_attributions.record_attribution(message, elapsed_seconds=sec)
        for attr in attributions:
            q = getattr(attr, "quoted_text", None) or getattr(attr, "quote", None)
            s = getattr(attr, "resolved_speaker", None) or getattr(attr, "speaker_name", None) or getattr(attr, "speaker", None)
            if attr.attribution_type in {"quote_to_speaker", "speaker_to_quote"} and q and s:
                res = self.apply_speaker_hint(quote=q, speaker_name=s)
                if res.status != "PASS" and getattr(self, "speaker_hint_queue", None):
                    self.speaker_hint_queue.enqueue_hint(speaker=s, quote=q, raw_input=message, elapsed_seconds=sec)
        return attributions

    def _on_audio_watchdog_alert(self, msg: str, chunk_idx: int, rms: float) -> None:
        print(f"\n{msg}\n", flush=True)

    def _handle_ws_action(self, data: Dict[str, Any]) -> None:
        """Tombstone: WebSocket server decommissioned in Wave 6."""
        pass

    def _write_session_manifest(self, now: Any = None) -> None:
        if now is None:
            started_at = datetime.now().isoformat()
        elif hasattr(now, "isoformat"):
            started_at = now.isoformat()
        else:
            started_at = datetime.fromtimestamp(float(now)).isoformat()
        manifest_data = {
            "project": self.project_name,
            "meeting": self.meeting_name,
            "date": self.meeting_date_display,
            "date_dirname": self.date_dirname,
            "session_tag": self.session_tag,
            "started_at": started_at,
            "master_wav": str(self.master_wav),
            "segments_jsonl": str(self.segments_jsonl),
            "attendees": self.attendees,
        }
        if self.copilot_advisor and getattr(self.copilot_advisor, "session_context_packet", None):
            pkt = self.copilot_advisor.session_context_packet
            manifest_data["copilot_prefetch"] = {
                "enabled": True,
                "packet_hash": pkt.packet_hash,
                "total_items": pkt.total_items_count,
                "decisions_count": len(pkt.open_decisions),
                "risks_count": len(pkt.open_risks),
                "action_items_count": len(pkt.open_action_items),
                "latency_ms": pkt.retrieval_latency_ms,
            }
        else:
            manifest_data["copilot_prefetch"] = {"enabled": False}
        write_atomic_session_manifest(self.session_manifest, manifest_data)

    def _init_transcript_file(self) -> None:
        init_live_transcript_file(
            self.transcript_md,
            self.output_dir,
            self.cache_dir,
            self.meeting_name,
            self.meeting_date_display,
            self.project_name,
            self.attendees,
        )

    def check_initiation_budget(self, stage: str = "STARTUP") -> None:
        """Enforces the absolute initiation SLA deadline (30.0s)."""
        enforce_initiation_budget(self, stage)

    def _quarantine_session(self, reason: str, reason_code: str) -> None:
        """Quarantines the session directory, marks .quarantine, stops isolated worker, and releases locks."""
        self._terminal_quarantined = True
        self._asr_failed = True
        self.running = False
        if getattr(self, "isolated_transcriber", None) is not None:
            with contextlib.suppress(Exception):
                self.isolated_transcriber._terminal_failure = True
                self.isolated_transcriber.stop()
        quarantine_recording_session(self, reason, reason_code)
        self._release_slug_lock()
        if hasattr(self, "session_registry") and hasattr(self, "session_id"):
            with contextlib.suppress(Exception):
                self.session_registry.unregister(self.session_id)

    def _clean_stale_quarantine(self) -> None:
        """Removes stale .quarantine markers from active session directories."""
        clean_session_quarantine(self)

    def _start_background_diarization(self) -> None:
        """Starts background loading of pinned acoustic diarization engine after recording confirmed (F10)."""
        if not getattr(self, "enable_post_stop_diarization", False):
            return
        if self.mock_mode or self.dry_run or self.diarization_engine is not None:
            return
        if self._diarization_thread is not None and self._diarization_thread.is_alive():
            return

        def _bg_loader():
            try:
                from dual_pipeline.audio_ingestion_core.diarization_engine import (
                    get_diarization_engine,
                )

                self.diarization_engine = get_diarization_engine()
                logger.info(
                    "[BACKGROUND-DIARIZATION] Acoustic diarization engine successfully warmed up."
                )
            except Exception as e:
                self._diarization_load_error = e
                logger.warning(
                    "[BACKGROUND-DIARIZATION] Failed to warm up diarization engine in background: %s",
                    e,
                )

        self._diarization_thread = threading.Thread(
            target=_bg_loader, daemon=True, name="BackgroundDiarizationLoader"
        )
        self._diarization_thread.start()

    def _drain_bootstrap_spooler_before_capture(self) -> None:
        """Releases bootstrap spooler before dual-source capture to prevent hardware contention."""
        if not (self.spooler and self.spooler.is_running):
            return
        logger.info(
            "[LIFECYCLE] Releasing bootstrap spooler before dual-source capture to prevent AVFoundation contention."
        )
        self.spooler.stop()
        if hasattr(self.spooler, "handshake_and_splice"):
            self.spooler_receipt = self.spooler.handshake_and_splice(
                target_chunks_dir=self.chunks_dir, target_master_wav=self.master_wav
            )
            indices = getattr(self.spooler_receipt, "migrated_chunk_indices", None)
            count = getattr(self.spooler_receipt, "migrated_chunk_count", 0)
            if indices:
                self.start_chunk_idx = max(indices) + 1
            elif count > 0:
                self.start_chunk_idx = count
            self.capture_manager.start_chunk_idx = self.start_chunk_idx

    def start_ffmpeg(self) -> None:
        try:
            self.check_initiation_budget("PRE_CAPTURE_START")
            if self.isolated_transcriber and not self.isolated_transcriber.is_running:
                self.transcriber_future = self.isolated_transcriber.start_async()
            should_adopt_spooler = (
                self.spooler is not None
                and self.spooler.is_running
            )
            if should_adopt_spooler:
                logger.info(
                    "[SINGLE-CAPTURE] Adopting running spooler capture authority (session %s)",
                    getattr(self.spooler, "session_id", "unknown"),
                )
                self.capture_manager.adopt_spooler(self.spooler)
                self.ffmpeg_proc = self.capture_manager._ffmpeg_proc
                self.running = True
                self.start_receipt = verify_adopted_spooler_proof(self)
            else:
                self._drain_bootstrap_spooler_before_capture()
                self.start_receipt = self.capture_manager.start(run_proof=not self.mock_mode)
                self.ffmpeg_proc = self.capture_manager._ffmpeg_proc
                self.running = True
            self.check_initiation_budget("POST_CAPTURE_START")
            self._start_background_diarization()
            self._clean_stale_quarantine()
        except AudioCaptureUnprovenError as e:
            self._early_abort_cleanup()
            self._quarantine_session(reason=e.detail, reason_code=e.reason_code)
            raise
        except (MLXInitError, MLXRuntimeLost) as e:
            self._early_abort_cleanup()
            self._quarantine_session(
                reason=getattr(e, "detail", str(e)),
                reason_code=getattr(e, "reason_code", "MLX_INIT_ERROR"),
            )
            raise

    _SILENCE_RMS_THRESHOLD: float = 120.0

    def _transcribe_chunk(
        self, wav_path: Path, chunk_idx: Optional[int] = None, return_segments: bool = False
    ) -> Union[Optional[str], Tuple[Optional[str], List[Dict[str, Any]]]]:
        if hasattr(self, "transcriber_future") and self.transcriber_future is not None:
            try:
                self.transcriber_future.result(timeout=60.0)
            except Exception as e:
                self._quarantine_session(
                    reason=getattr(e, "detail", str(e)),
                    reason_code=getattr(
                        e,
                        "reason_code",
                        MLXReasonCode.MLX_READY_DEADLINE_EXCEEDED
                        if isinstance(e, TimeoutError)
                        else "MLX_INIT_ERROR",
                    ),
                )
                raise
            finally:
                self.transcriber_future = None
        try:
            health = ChunkHealth.evaluate(wav_path, allow_digital_zero=self.mock_mode)
            if health.error:
                raise AudioAnalysisError(f"Audio analysis failed on chunk {wav_path.name}: {health.error}")
            if health.duration_sec < 0.01:
                raise AudioAnalysisError(f"Audio analysis failed on chunk {wav_path.name}: duration {health.duration_sec:.6f}s below minimum threshold")
            if health.rms < self._SILENCE_RMS_THRESHOLD or not health.is_audible:
                return ("", []) if return_segments else ""
            if hasattr(self, "vad_segmenter") and not self.mock_mode:
                if not self.vad_segmenter.is_speech_chunk(wav_path) or not self.vad_segmenter.detect_speech_boundaries(wav_path):
                    return ("", []) if return_segments else ""
        except AudioAnalysisError:
            raise
        except Exception as e:
            logger.error("Audio analysis failed on chunk %s: %s", wav_path.name, e)
            raise AudioAnalysisError(f"Audio analysis failed on chunk {wav_path.name}: {e}") from e

        filter_log = getattr(self, "asr_filter_log", None)
        if self.mock_mode:
            res = f"Transcribed speech content for {wav_path.name}."
            return (res, []) if return_segments else res
        if self.isolated_transcriber:
            return self.isolated_transcriber.transcribe(
                wav_path, whisper_prompt=self.whisper_prompt, return_segments=return_segments
            )
        return transcribe_meeting_chunk(
            wav_path, whisper_prompt=self.whisper_prompt, mock_mode=self.mock_mode,
            model_name=self.whisper_model, backend=self.backend, filter_log_path=filter_log,
            chunk_idx=chunk_idx, return_segments=return_segments,
        )

    def _emit_heartbeat(self, rms: float = 0.0, chunk_idx: int = 0, time_str: str = "", force: bool = False) -> None:
        if hasattr(self, "session_registry") and hasattr(self, "session_id"):
            ff_pid = getattr(getattr(self.capture_manager, "_ffmpeg_proc", None), "pid", None)
            sck_pid = getattr(getattr(self.capture_manager, "_sck_proc", None), "pid", None)

            def _safe_rel(p: Optional[Path]) -> Optional[str]:
                try:
                    return str(p.resolve().relative_to(ROOT.resolve())) if p else None
                except Exception:
                    return str(p) if p else None

            t_md = _safe_rel(getattr(self, "transcript_md", None))
            c_jsonl = _safe_rel(getattr(self, "copilot_jsonl", None))
            out_dir = _safe_rel(getattr(self, "output_dir", None))

            g_sha, g_gen = None, None
            copilot_obj = getattr(self, "copilot_advisor", None) or getattr(self, "copilot_adviser", None)
            if copilot_obj and getattr(copilot_obj, "retriever", None):
                g_sha = getattr(copilot_obj.retriever, "graph_sha256", None)
                g_gen = getattr(copilot_obj.retriever, "generation", None)

            from dual_pipeline.live_meeting_recorder_core.heartbeat_worker import compute_recorder_lifecycle_states
            c_st, a_st, ctx_st, cop_st = compute_recorder_lifecycle_states(self)

            self.session_registry.heartbeat(
                self.session_id,
                ffmpeg_pid=ff_pid,
                sck_pid=sck_pid,
                transcript_md=t_md,
                copilot_jsonl=c_jsonl,
                output_dir=out_dir,
                cache_dir=str(self.cache_dir) if getattr(self, "cache_dir", None) else None,
                segments_jsonl=str(self.segments_jsonl) if getattr(self, "segments_jsonl", None) else None,
                graph_sha256=g_sha,
                generation=g_gen,
                capture_state=c_st,
                asr_state=a_st,
                context_state=ctx_st,
                copilot_state=cop_st,
                force=force,
            )

    def _start_heartbeat_worker(self) -> None:
        if getattr(self, "_heartbeat_worker", None) is None:
            from dual_pipeline.live_meeting_recorder_core.heartbeat_worker import HeartbeatWorker
            self._heartbeat_worker = HeartbeatWorker(self)
            self._heartbeat_worker.start()

    def _commit_segment_event(
        self, chunk_idx: int, time_str: str, sec: Union[int, float], text: str, wav_name: str, rms: float, spk: str,
        duration_sec: float = 0.0, start_sec: Optional[float] = None, end_sec: Optional[float] = None,
        chunk_dur: Optional[float] = None, verbatim: Optional[str] = None, normalized: Optional[str] = None,
        voice_id: Optional[str] = None, confidence: Optional[float] = None, words: Optional[List[Dict[str, Any]]] = None,
        asr_conf: Optional[float] = None, no_speech_prob: Optional[float] = None,
    ) -> None:
        s_sec = float(sec if start_sec is None else start_sec)
        d_sec = float(duration_sec or (chunk_dur if chunk_dur is not None else self.segment_seconds))
        e_sec = float(s_sec + d_sec if end_sec is None else end_sec)
        from dual_pipeline.sanitizer import LocalPreEgressSanitizer
        clean_text = LocalPreEgressSanitizer.scrub_text(text)
        verbatim_val = LocalPreEgressSanitizer.scrub_text(verbatim or clean_text)
        normalized_val = LocalPreEgressSanitizer.scrub_text(normalized or clean_text)
        payload: Dict[str, Any] = {
            "chunk_idx": chunk_idx, "timestamp": time_str, "seconds": int(round(s_sec)),
            "start_sec": round(s_sec, 3), "end_sec": round(e_sec, 3),
            "duration": round(d_sec, 2), "duration_sec": round(d_sec, 3),
            "text": verbatim_val, "verbatim": verbatim_val, "normalized": normalized_val,
            "chunk_file": wav_name, "wav_file": wav_name, "rms": round(rms, 4), "speaker": spk, "is_final": True,
            "asr_conf": round(float(1.0 if asr_conf is None else asr_conf), 3),
            "no_speech_prob": round(float(0.0 if no_speech_prob is None else no_speech_prob), 4),
        }
        if voice_id is not None:
            payload["voice_id"] = voice_id
        if confidence is not None:
            payload["confidence"] = round(confidence, 3)
        if words is not None:
            payload["words"] = words
        with getattr(self, "_transcript_io_lock", contextlib.nullcontext()):
            with open(self.segments_jsonl, "a", encoding="utf-8") as f_seg:
                f_seg.write(json.dumps(payload) + "\n")
            if not self.transcript_md.exists():
                self._init_transcript_file()
            append_live_transcript_line(self.transcript_md, time_str, spk, normalized_val)
            display_prefix = time_str if time_str.startswith("[") else f"[{time_str}]"
            print(f"{display_prefix} {spk}: {normalized_val}", flush=True)
            if getattr(self, "speaker_hint_queue", None) and getattr(self, "speaker_hint_manager", None):
                with contextlib.suppress(Exception):
                    self.speaker_hint_queue.reconcile_pending_hints(
                        self.speaker_hint_manager, self.segments_jsonl, self.transcript_md, self.clean_slug, voice_registry=self.voice_registry
                    )

    def _on_copilot_feedback(self, feedback: Dict[str, Any]) -> None:
        try:
            if self.inflection_detector:
                self.inflection_detector.reset_cadence(time.time())
            with open(self.copilot_jsonl, "a", encoding="utf-8") as f_cop:
                f_cop.write(json.dumps(feedback) + "\n")
                f_cop.flush()
                os.fsync(f_cop.fileno())
            cue = f" [{feedback['snap_cue']}]" if feedback.get("snap_cue") else ""
            trig = f" ({feedback['inflection_trigger']})" if feedback.get("inflection_trigger") else ""
            print(
                f"    ⚡ [COPILOT SNAP {feedback.get('category', 'STRATEGIC')}]{cue}{trig}: {feedback.get('suggested_script', '')}",
                flush=True,
            )
        except Exception:
            pass

    def _dispatch_copilot_feedback(
        self, spk: str, text: str, time_str: str, inflection_event: Optional[Any] = None
    ) -> None:
        if self.copilot_advisor:
            self.copilot_advisor.submit_turn_async(
                spk,
                text,
                time_str,
                time.time(),
                self._on_copilot_feedback,
                inflection_event=inflection_event,
            )

    @property
    def _default_speaker(self) -> str:
        return "Speaker"

    def _handle_empty_chunk_pause(
        self,
        chunk_idx: int,
        sec: int | float,
        time_str: str,
        rms: float,
        chunk_dur: Optional[float] = None,
    ) -> None:
        dur = float(chunk_dur) if chunk_dur is not None else float(self.segment_seconds)
        if self._silent_pause_start_sec is None:
            self._silent_pause_start_sec = sec
        if chunk_dur is not None:
            dur = float(chunk_dur)
        else:
            dur = self._chunk_timings.get(chunk_idx, (sec, self.segment_seconds))[1]
        self._silent_pause_end_sec = sec + dur
        self._silent_chunk_count += 1
        if self.inflection_detector and self.copilot_advisor:
            pause_event = self.inflection_detector.evaluate_chunk(
                chunk_idx, int(round(sec)), "", rms, time.time()
            )
            if pause_event:
                flushed = self.turn_aggregator.flush() if self.turn_aggregator else None
                spk = flushed.speaker if flushed else self._default_speaker
                self._dispatch_copilot_feedback(
                    spk, flushed.text if flushed else "", time_str, pause_event
                )

    def _flush_pending_pause(self) -> None:
        """Emits an explicit natural pause or recording gap annotation if >= 15s of silence accumulated."""
        if (
            self._silent_chunk_count >= 1
            and self._silent_pause_start_sec is not None
            and self._silent_pause_end_sec is not None
        ):
            pause_dur = self._silent_pause_end_sec - self._silent_pause_start_sec
            if pause_dur >= 15.0:
                start_fmt = format_hh_mm_ss(int(round(self._silent_pause_start_sec)))
                end_fmt = format_hh_mm_ss(int(round(self._silent_pause_end_sec)))
                is_starved = bool(
                    (
                        getattr(self, "starvation_detector", None)
                        and self.starvation_detector.is_starved
                    )
                    or any(
                        (self._silent_pause_start_sec - 15.0)
                        <= ev.get("start_sec", 0.0)
                        <= (self._silent_pause_end_sec + 15.0)
                        for ev in getattr(self, "starvation_alarms", [])
                    )
                )
                if is_starved:
                    line = f"[Recording gap: {start_fmt} – {end_fmt} (Audio starvation / dropped stream)]\n\n"
                    with open(self.transcript_md, "a", encoding="utf-8") as f:
                        f.write(line)
                else:
                    append_live_transcript_pause(self.transcript_md, start_fmt, end_fmt)
        self._silent_pause_start_sec, self._silent_pause_end_sec, self._silent_chunk_count = (
            None,
            None,
            0,
        )

    def _handle_speech_turn_copilot(
        self, chunk_idx: int, sec: int | float, time_str: str, text: str, rms: float, spk: str, chunk_dur: Optional[float] = None,
    ) -> None:
        try:
            dur = float(chunk_dur) if chunk_dur is not None else float(self.segment_seconds)
            if float(sec) > 45 * 60 and not getattr(self, "_copilot_staleness_warned", False):
                self._copilot_staleness_warned = True
                logger.warning("Live meeting session exceeded 45 minutes; pre-fetched graph context may be stale.")
            inflection = self.inflection_detector.evaluate_chunk(chunk_idx, int(round(sec)), text, rms, time.time()) if self.inflection_detector else None
            completed_turn = self.turn_aggregator.add_chunk(chunk_idx, sec, text, spk, duration=dur) if self.turn_aggregator else None
            if completed_turn and self.copilot_advisor:
                self._dispatch_copilot_feedback(completed_turn.speaker, completed_turn.text, completed_turn.display_time, inflection)
            elif inflection and self.copilot_advisor:
                utterance = getattr(self.turn_aggregator, "get_current_utterance", lambda: "")() if self.turn_aggregator else ""
                self._dispatch_copilot_feedback(spk, utterance or text, time_str, inflection)
        except Exception as exc:
            logger.warning("[COPILOT-TURN-WARN] Non-fatal copilot turn error on chunk %d: %s", chunk_idx, exc)


    def _get_chunk_duration(self, wav_path: Path) -> float:
        """Calculates exact chunk duration in seconds from WAV frame headers with single-pass caching."""
        health = ChunkHealth.evaluate(wav_path, allow_digital_zero=self.mock_mode)
        if health.duration_sec > 0.0:
            return health.duration_sec
        return float(self.segment_seconds)

    def _compute_chunk_timeline(self, chunk_idx: int, wav_path: Path) -> Tuple[float, float, float]:
        """Calculates drift-free sample-accurate start, duration, and end seconds for a chunk."""
        if chunk_idx in self._chunk_timings:
            start_sec, dur = self._chunk_timings[chunk_idx]
            return start_sec, dur, start_sec + dur

        dur = self._get_chunk_duration(wav_path)
        if dur <= 0.0:
            dur = float(self.segment_seconds)

        if chunk_idx <= 0:
            start_sec = 0.0
        elif (chunk_idx - 1) in self._chunk_timings:
            prev_start, prev_dur = self._chunk_timings[chunk_idx - 1]
            start_sec = prev_start + prev_dur
        else:
            accum_sec = 0.0
            for idx in range(chunk_idx):
                if idx in self._chunk_timings:
                    accum_sec += self._chunk_timings[idx][1]
                else:
                    p = self.chunks_dir / f"chunk_{idx:05d}.wav"
                    if not p.exists():
                        p = self.chunks_dir / f"chunk_{idx:04d}.wav"
                    if p.exists():
                        d = self._get_chunk_duration(p)
                        accum_sec += d if d > 0.0 else float(self.segment_seconds)
                    else:
                        accum_sec += float(self.segment_seconds)
            start_sec = accum_sec

        end_sec = start_sec + dur
        self._chunk_timings[chunk_idx] = (start_sec, dur)
        self._cumulative_elapsed_seconds = end_sec
        return start_sec, dur, end_sec

    def _check_starvation_alarm(self, chunk_idx: int, rms: float, chunk_dur: float, time_str: str, start_sec: float, end_sec: float) -> None:
        detector = getattr(self, "starvation_detector", None) or getattr(getattr(self, "capture_manager", None), "starvation_detector", None)
        if not detector:
            return
        # Calibrate int16 RMS [0, 32768] to normalized [0.0, 1.0] when detector threshold is <= 1.0
        thresh = float(getattr(detector, "threshold_rms", 0.005) or 0.005)
        eval_rms = (float(rms) / 32768.0) if (thresh <= 1.0 and float(rms) > 1.0) else float(rms)
        if detector.record_chunk(eval_rms, chunk_dur) != "AUDIO_STARVATION_PAUSE":
            return
        alarm_event = {
            "event": "AUDIO_STARVATION_ALARM", "chunk_idx": chunk_idx, "rms": rms, "rms_normalized": round(eval_rms, 6), "time_str": time_str,
            "start_sec": start_sec, "end_sec": end_sec, "silence_duration_seconds": detector.silence_duration, "timestamp": time.time(),
        }
        if not hasattr(self, "starvation_alarms"):
            self.starvation_alarms = []
        self.starvation_alarms.append(alarm_event)
        logger.warning("🚨 [AUDIO_STARVATION_ALARM] Audio RMS %.6f < 0.001 for >15s at %s. Hardware stream starved!", rms, time_str)
        print(f"\n🚨 [AUDIO_STARVATION_ALARM] Audio RMS {rms:.6f} < 0.001 for >15s at {time_str}. Stream starved!", flush=True)
        if self.ws_server:
            with contextlib.suppress(Exception):
                self.ws_server.broadcast_stream(
                    segment_id=f"starv_{chunk_idx:05d}", text=f"[AUDIO_STARVATION_ALARM: Sustained digital silence RMS={rms:.6f}]",
                    is_final=True, confidence=1.0, start_ts=round(start_sec, 3), end_ts=round(end_sec, 3), speaker="System",
                )
                self.ws_server.broadcast_json(json.dumps(alarm_event))

    def process_chunk(self, wav_path: Path, chunk_idx: int) -> Optional[str]:
        """Transcribes chunk, commits segment to JSONL, and updates markdown feed."""
        if not self._worktree_materialized and self.worktree_dir and self.worktree_branch:
            self.materialize_worktree()
            if not wav_path.exists() and (self.chunks_dir / wav_path.name).exists():
                wav_path = self.chunks_dir / wav_path.name
        rms = ChunkHealth.evaluate(wav_path, allow_digital_zero=self.mock_mode).rms
        start_sec, chunk_dur, end_sec = self._compute_chunk_timeline(chunk_idx, wav_path)
        self._cumulative_audio_sec = end_sec
        sec, time_str = int(round(start_sec)), format_chunk_time(int(round(start_sec)))
        self.audio_watchdog.record_chunk(chunk_idx, rms, time_str)
        self._check_starvation_alarm(chunk_idx, rms, chunk_dur, time_str, start_sec, end_sec)
        self._emit_heartbeat(rms, chunk_idx, time_str)
        try:
            trans_res = self._transcribe_chunk(wav_path, chunk_idx=chunk_idx, return_segments=True)
        except TypeError:
            trans_res = self._transcribe_chunk(wav_path, chunk_idx=chunk_idx)
        text, chunk_words = extract_chunk_text_and_words(trans_res, start_sec)
        asr_conf, no_speech_prob = extract_chunk_telemetry(trans_res)
        from dual_pipeline.audio_postprocess import is_hallucinated_or_prompt_echo_turn
        wp = getattr(self, "whisper_prompt", None)
        if not text or is_hallucinated_or_prompt_echo_turn(text, no_speech_prob, wp):
            self._handle_empty_chunk_pause(chunk_idx, sec, time_str, rms, chunk_dur=chunk_dur)
            return None
        primer = getattr(self, "lexical_primer", None)
        normalized_text, verbatim_text = (
            primer.post_process_transcript_with_verbatim(text) if hasattr(primer, "post_process_transcript_with_verbatim")
            else (primer.post_process_transcript(text) if primer else text, text)
        )
        if is_hallucinated_or_prompt_echo_turn(normalized_text, no_speech_prob, wp) or is_cross_chunk_repetition(normalized_text, getattr(self, "recent_transcripts", [])):
            self._handle_empty_chunk_pause(chunk_idx, sec, time_str, rms, chunk_dur=chunk_dur)
            return None
        self._flush_pending_pause()
        self.recent_transcripts = (getattr(self, "recent_transcripts", []) + [normalized_text])[-10:]
        spk, voice_id, confidence = self._resolve_chunk_speaker_and_tracker(wav_path, chunk_idx, normalized_text, start_sec, chunk_dur)
        self._commit_segment_event(
            chunk_idx=chunk_idx, time_str=time_str, sec=sec, text=normalized_text, wav_name=wav_path.name,
            rms=rms, spk=spk, duration_sec=chunk_dur, start_sec=start_sec, end_sec=end_sec, chunk_dur=chunk_dur,
            verbatim=verbatim_text, normalized=normalized_text, voice_id=voice_id, confidence=confidence,
            words=chunk_words, asr_conf=asr_conf, no_speech_prob=no_speech_prob,
        )
        self._handle_speech_turn_copilot(chunk_idx, sec, time_str, normalized_text, rms, spk, chunk_dur=chunk_dur)
        with contextlib.suppress(Exception):
            os.utime(self.transcript_md, None)
        self.last_written_chunk_idx = chunk_idx
        return normalized_text

    def _sync_session_manifest_paths(self) -> None:
        rec = self.session_manifest_record
        rec.output_dir, rec.cache_dir = str(self.output_dir), str(self.cache_dir)
        rec.transcript_md, rec.segments_jsonl, rec.copilot_jsonl = str(self.transcript_md), str(self.segments_jsonl), str(self.copilot_jsonl)
        self.session_registry.heartbeat(
            self.session_id, output_dir=rec.output_dir, cache_dir=rec.cache_dir,
            transcript_md=rec.transcript_md, segments_jsonl=rec.segments_jsonl, copilot_jsonl=rec.copilot_jsonl, force=True,
        )

    def _resolve_chunk_speaker_and_tracker(self, wav_path: Path, chunk_idx: int, text: str, start_sec: float, dur: float) -> Tuple[str, Optional[str], Optional[float]]:
        spk, voice_id, confidence = "Speaker", None, None
        if getattr(self, "voice_registry", None):
            try:
                spk, voice_id, confidence = self.voice_registry.resolve_chunk_speaker(
                    wav_path=wav_path, chunk_idx=chunk_idx, text=text, start_sec=start_sec, duration_sec=dur, fallback_speaker="Speaker"
                )
            except Exception as exc:
                logger.warning("[SPEAKER-RESOLVE-WARN] Voice registry error on chunk %d: %s", chunk_idx, exc)
        if (spk in ("Speaker", "Host", "Unknown", "Voice Host") or str(spk).startswith("Voice ")) and getattr(self, "speaker_tracker", None):
            try:
                tracked = self.speaker_tracker.attribute_chunk(text, current_sec=start_sec, default_speaker=spk)
                if tracked and tracked not in ("Speaker", "Host", "Unknown", "Voice Host") and not str(tracked).startswith("Voice "):
                    spk = tracked
            except Exception as exc:
                logger.warning("[SPEAKER-TRACKER-WARN] Speaker tracker error on chunk %d: %s", chunk_idx, exc)
        if spk in ("Host", "Voice Host") and getattr(self, "voice_registry", None):
            host_name = self.voice_registry.resolve_host_attendee_name()
            if host_name:
                spk = host_name
        return spk, voice_id, confidence

    def _migrate_staged_chunks(self, is_final: bool = False) -> None:
        migrate_staged_files(self.chunks_dir, self._target_chunks_dir, [], is_final=is_final)

    def _migrate_staged_metadata(self, is_final: bool = False) -> None:
        from dual_pipeline.live_meeting_recorder_core.worktree_repointer import build_staged_metadata_pairs
        migrate_staged_files(self.chunks_dir, self._target_chunks_dir, build_staged_metadata_pairs(self), is_final=is_final)

    def _repoint_recorder_paths_to_worktree(self) -> None:
        repoint_recorder_paths(self)

    def materialize_worktree(self, is_final: bool = False) -> None:
        """Provisions deferred git worktree on first valid chunk and migrates staging files."""
        if self._worktree_materialized or not self.worktree_dir or not self.worktree_branch:
            return
        try:
            from dual_pipeline.live_meeting_recorder_core.worktree_repointer import finalize_worktree_materialization
            provision_live_transcription_worktree(self.worktree_dir, self.worktree_branch, ROOT)
            self._worktree_materialized = True
            finalize_worktree_materialization(self, is_final=is_final, root_dir=ROOT)
            self._emit_heartbeat(force=True)
        except Exception as e:
            logger.warning("[WORKTREE-PROVISION-FAILED] Failed to provision %s at %s (%s)", self.worktree_branch, self.worktree_dir, e)
            if self.worktree_dir and self.worktree_dir.exists() and not is_authentic_git_worktree(self.worktree_dir):
                shutil.rmtree(self.worktree_dir, ignore_errors=True)
            self.worktree_dir = self.worktree_branch = None
            self._worktree_materialized = True

    def _is_chunk_audible(self, cf: Path) -> bool:
        if self.mock_mode:
            return True
        try:
            health = ChunkHealth.evaluate(cf, allow_digital_zero=self.mock_mode)
            if health.rms < self._SILENCE_RMS_THRESHOLD or not health.is_audible:
                return False
            if hasattr(self, "vad_segmenter"):
                boundaries = self.vad_segmenter.detect_speech_boundaries(cf)
                return len(boundaries) > 0
            return True
        except Exception:
            return False

    def _maybe_materialize_for_chunk(self, cf: Path) -> Path:
        """Never write into an unregistered .worktrees/ folder. Provisions git worktree before the first chunk."""
        if not self._worktree_materialized and self.worktree_dir and self.worktree_branch:
            self.materialize_worktree(is_final=not getattr(self, "running", False))
        return (self.chunks_dir / cf.name) if (not cf.exists() and (self.chunks_dir / cf.name).exists()) else cf

    def _start_asr_worker(self) -> None:
        """Starts background ASR worker thread for decoupled non-blocking queue processing."""
        if self._asr_worker_thread is None or not self._asr_worker_thread.is_alive():
            self._asr_worker_thread = threading.Thread(
                target=self._asr_worker_loop,
                name="live-asr-worker",
                daemon=True,
            )
            self._asr_worker_thread.start()

    def _record_chunk_failure(self, cf: Path) -> None:
        failures = getattr(self, "_chunk_failures", None)
        lock = getattr(self, "_chunks_lock", None)
        queued = getattr(self, "_Queued_chunks", None)
        if isinstance(failures, dict):
            failures[cf.name] = failures.get(cf.name, 0) + 1
            if failures[cf.name] < 2:
                if lock:
                    with lock:
                        self.processed_chunks.discard(cf.name)
                        if isinstance(queued, set):
                            queued.discard(cf.name)
                else:
                    self.processed_chunks.discard(cf.name)
        self._consecutive_asr_failures += 1
        if self._consecutive_asr_failures >= 3:
            self._asr_failed = True

    def _drain_remaining_queue_on_quarantine(self) -> None:
        self._chunk_queue.task_done()
        while not self._chunk_queue.empty():
            with contextlib.suppress(Exception):
                self._chunk_queue.get_nowait()
                self._chunk_queue.task_done()

    def _mark_chunk_completed(self, name: str) -> None:
        lock = getattr(self, "_chunks_lock", None)
        queued = getattr(self, "_Queued_chunks", None)
        if lock:
            with lock:
                self.processed_chunks.add(name)
                self.transcribed_chunks.add(name)
                if isinstance(queued, set):
                    queued.discard(name)
        else:
            self.processed_chunks.add(name)
            self.transcribed_chunks.add(name)
        self._consecutive_asr_failures = 0

    def _handle_asr_worker_quarantine(self, cf: Path, e: Exception) -> None:
        self._record_chunk_failure(cf)
        logger.error("[ASR-WORKER-QUARANTINE] Terminal MLX worker failure on %s: %s", cf.name, e)
        if not getattr(self, "_terminal_quarantined", False):
            self._quarantine_session(
                reason=getattr(e, "detail", str(e)),
                reason_code=getattr(e, "reason_code", MLXReasonCode.MLX_RUNTIME_LOST),
            )
        self._drain_remaining_queue_on_quarantine()

    def _asr_worker_loop(self) -> None:
        """Background worker consuming chunks from queue to isolate ASR inference from poll loop."""
        while True:
            item = self._chunk_queue.get()
            if item is None or getattr(self, "_terminal_quarantined", False):
                self._chunk_queue.task_done()
                break
            cf, chunk_idx = item
            try:
                self.process_chunk(cf, chunk_idx)
                self._mark_chunk_completed(cf.name)
            except (MLXInitError, MLXRuntimeLost) as e:
                self._handle_asr_worker_quarantine(cf, e)
                break
            except Exception as e:
                self._record_chunk_failure(cf)
                logger.error("[ASR-WORKER-ERROR] Error processing %s (consecutive failures: %d): %s", cf.name, self._consecutive_asr_failures, e)
                if getattr(self, "_terminal_quarantined", False):
                    self._drain_remaining_queue_on_quarantine()
                    break
            finally:
                if not getattr(self, "_terminal_quarantined", False):
                    self._chunk_queue.task_done()

    _chunk_worker_loop = _asr_worker_loop

    def _drain_asr_worker(self, timeout: float = 30.0) -> None:
        """Drains the in-flight chunk queue and shuts down the ASR worker thread."""
        if self._asr_worker_thread and self._asr_worker_thread.is_alive():
            t_end = time.monotonic() + max(1.0, float(timeout))
            while getattr(self._chunk_queue, "unfinished_tasks", 0) > 0 and time.monotonic() < t_end:
                time.sleep(0.05)
            with contextlib.suppress(Exception):
                self._chunk_queue.put(None, timeout=2.0)
            rem = max(0.1, t_end - time.monotonic())
            self._asr_worker_thread.join(timeout=rem)
            self._asr_worker_thread = None

    def _is_chunk_already_handled(self, name: str) -> bool:
        lock = getattr(self, "_chunks_lock", None)
        queued = getattr(self, "_Queued_chunks", None)
        if lock:
            with lock:
                return name in self.processed_chunks or (isinstance(queued, set) and name in queued)
        return name in self.processed_chunks

    def _run_chunk_sync(self, cf: Path, chunk_idx: int) -> None:
        lock = getattr(self, "_chunks_lock", None)
        try:
            self.process_chunk(cf, chunk_idx)
            if lock:
                with lock:
                    self.processed_chunks.add(cf.name)
                    self.transcribed_chunks.add(cf.name)
            else:
                self.processed_chunks.add(cf.name)
                self.transcribed_chunks.add(cf.name)
            self._consecutive_asr_failures = 0
        except Exception:
            self._record_chunk_failure(cf)
            raise

    def _enqueue_or_run_chunk(self, cf: Path, chunk_idx: int) -> None:
        if not (self._asr_worker_thread and self._asr_worker_thread.is_alive()):
            self._run_chunk_sync(cf, chunk_idx)
            return
        lock, queued = getattr(self, "_chunks_lock", None), getattr(self, "_Queued_chunks", None)
        backlog = self._chunk_queue.qsize()
        self._max_backlog = max(self._max_backlog, backlog)
        if backlog >= 3:
            logger.warning("[ASR-BACKLOG-WARNING] ASR worker backlog at %d chunks", backlog)
        try:
            if lock and isinstance(queued, set):
                with lock:
                    queued.add(cf.name)
            self._chunk_queue.put((cf, chunk_idx), timeout=5.0)
        except Exception as exc:
            if lock and isinstance(queued, set):
                with lock:
                    queued.discard(cf.name)
            logger.warning("[ASR-QUEUE-FULL] Deferred chunk %s for retry: %s", cf.name, exc)

    def _process_chunk_file(self, cf: Path) -> None:
        if self._is_chunk_already_handled(cf.name):
            return
        if not cf.exists() and (self.chunks_dir / cf.name).exists():
            cf = self.chunks_dir / cf.name
        m = re.search(r"chunk_(?:e\d+_)?(\d+)\.wav", cf.name)
        chunk_idx = int(m.group(1)) if m else 0
        health = ChunkHealth.evaluate(cf, allow_digital_zero=self.mock_mode)
        if not health.is_valid:
            dur = health.duration_sec if health.duration_sec > 0.0 else float(self.segment_seconds)
            start_sec = float(chunk_idx * self.segment_seconds)
            end_sec = start_sec + dur
            time_str = format_chunk_time(int(round(start_sec)))
            if getattr(self, "audio_watchdog", None):
                self.audio_watchdog.record_chunk(chunk_idx, health.rms, time_str)
            self._check_starvation_alarm(chunk_idx, health.rms, dur, time_str, start_sec, end_sec)
            self.processed_chunks.add(cf.name)
            self._record_chunk_failure(cf)
            return
        if self._is_chunk_audible(cf):
            self.last_audible_monotonic = time.monotonic()
            self.total_audible_chunks += 1
        cf = self._maybe_materialize_for_chunk(cf)
        if self.capture_manager and hasattr(self.capture_manager, "_watchdog"):
            self.capture_manager._watchdog.notify_chunk_written()
        if self.capture_manager and hasattr(self.capture_manager, "check_headphone_trap_sentinel"):
            self.capture_manager.check_headphone_trap_sentinel(chunk_idx + 1)
        self._enqueue_or_run_chunk(cf, chunk_idx)

    def _process_prior_chunks(self, wav_files: List[Path]) -> None:
        for cf in wav_files[:-1]:
            self._process_chunk_file(cf)

    def emit_speculative_preview(self, chunk_file: Path, chunk_idx: int) -> Optional[str]:
        """Tombstone: Speculative preview eliminated in Wave 6 ($M_1 = 1.0$)."""
        return None

    def _process_latest_chunk(self, latest: Path, last_file_sizes: Dict[str, int]) -> None:
        if latest.name in self.processed_chunks or not latest.exists():
            return
        curr_size = latest.stat().st_size
        prev_size = last_file_sizes.get(latest.name, -1)
        if curr_size == prev_size and curr_size > 0:
            if curr_size < MIN_VALID_CHUNK_BYTES and not self.mock_mode:
                err = f"Chunk {latest.name} stopped growing at {curr_size}B (< minimum {MIN_VALID_CHUNK_BYTES}B)."
                logger.error("🛑 [CHUNK-UNDERFLOW] %s Fail-closed without fallback.", err)
                self._quarantine_session(
                    reason=err, reason_code=AudioReasonCode.AUDIO_GROWTH_STALLED
                )
                raise AudioCaptureUnprovenError(
                    stage="CHUNK_UNDERFLOW",
                    reason_code=AudioReasonCode.AUDIO_GROWTH_STALLED,
                    detail=err,
                )
            self._process_chunk_file(latest)
        else:
            last_file_sizes[latest.name] = curr_size

    def _poll_and_process_chunks(self, last_file_sizes: Dict[str, int]) -> None:
        t0 = time.perf_counter()
        for src in (
            getattr(getattr(self, "capture_manager", None), "chunks_dir", None),
            getattr(getattr(self, "spooler", None), "spool_dir", None),
        ):
            if src and Path(src).exists() and Path(src).resolve() != self.chunks_dir.resolve():
                with contextlib.suppress(Exception):
                    migrate_staged_files(Path(src), self.chunks_dir, [])
        wav_files = sorted(self.chunks_dir.glob("chunk_*.wav"))
        if len(wav_files) >= 2:
            self._process_prior_chunks(wav_files)
        if wav_files:
            self._process_latest_chunk(wav_files[-1], last_file_sizes)
        self._poll_lag_sec = time.perf_counter() - t0


    def _check_startup_probation(self, started_at: float) -> bool:
        if self.mock_mode or (time.time() - started_at) <= 1.0:
            return False
        p = self.capture_manager._ffmpeg_proc
        if p and p.poll() is not None:
            err = f"Capture process terminated prematurely during startup probation (exit code {p.poll()})."
            logger.error("🛑 [STARTUP-HEALTH-FAIL] %s Fail-closed without fallback.", err)
            self._quarantine_session(reason=err, reason_code=AudioReasonCode.FFMPEG_EARLY_EXIT)
            raise AudioCaptureUnprovenError(
                stage="STARTUP_PROBATION",
                reason_code=AudioReasonCode.FFMPEG_EARLY_EXIT,
                detail=err,
            )
        return True

    def run(self, max_iterations: Optional[int] = None) -> None:
        """Main processing loop watching the rolling chunks directory."""
        self.running = True
        self._init_transcript_file()
        self._start_asr_worker()
        self.start_ffmpeg()
        if hasattr(self, "collateral_watcher") and self.collateral_watcher:
            self.collateral_watcher.start()
        if hasattr(self, "chat_collateral_bridge") and self.chat_collateral_bridge:
            self.chat_collateral_bridge.start()

        last_file_sizes: Dict[str, int] = {}
        iterations, started_at = 0, time.time()
        health_checked = False

        try:
            while self.running:
                if max_iterations is not None and iterations >= max_iterations:
                    break
                iterations += 1
                self._poll_and_process_chunks(last_file_sizes)
                if getattr(self.capture_manager, "watchdog_exception", None) is not None:
                    w_err = self.capture_manager.watchdog_exception
                    self._quarantine_session(reason=w_err.detail, reason_code=w_err.reason_code)
                    raise w_err
                if not health_checked:
                    health_checked = self._check_startup_probation(started_at)

                if self.max_duration_minutes is not None and self.max_duration_minutes > 0:
                    elapsed_min = (time.time() - started_at) / 60.0
                    if elapsed_min >= self.max_duration_minutes:
                        print(f"⏱️ [AUTO-STOP] Maximum duration ceiling reached ({self.max_duration_minutes} min). Stopping recording session.", flush=True)
                        self.auto_stopped_reason = f"MAX_DURATION_EXCEEDED ({self.max_duration_minutes}m)"
                        break

                if self.inactivity_timeout_seconds is not None and self.inactivity_timeout_seconds > 0:
                    silence_duration = time.monotonic() - self.last_audible_monotonic
                    if silence_duration >= self.inactivity_timeout_seconds:
                        logger.warning("⏱️ [AUTO-STOP] Inactivity timeout triggered (%.1fs >= %.1fs silence). Stopping recording session.", silence_duration, self.inactivity_timeout_seconds)
                        print(f"⏱️ [AUTO-STOP] Inactivity timeout triggered ({silence_duration:.1f}s >= {self.inactivity_timeout_seconds}s silence). Stopping recording session.", flush=True)
                        self.auto_stopped_reason = f"INACTIVITY_TIMEOUT ({silence_duration:.1f}s >= {self.inactivity_timeout_seconds}s)"
                        break

                time.sleep(1.0 if not self.mock_mode else 0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _drain_remaining_chunks(self) -> None:
        """Drains remaining audio chunks and flushes pending turns/pauses."""
        from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import migrate_all_chunk_sources_on_drain
        migrate_all_chunk_sources_on_drain(self)
        for cf in sorted(self.chunks_dir.glob("chunk_*.wav")):
            with contextlib.suppress(Exception):
                self._process_chunk_file(cf)
        self._drain_asr_worker()
        for cf in sorted(self.chunks_dir.glob("chunk_*.wav")):
            if cf.name not in self.processed_chunks:
                with contextlib.suppress(Exception):
                    self._process_chunk_file(cf)
        if self.turn_aggregator:
            flushed = self.turn_aggregator.flush()
            if flushed and self.copilot_advisor:
                self._dispatch_copilot_feedback(flushed.speaker, flushed.text, flushed.display_time)
        self._flush_pending_pause()

    def _shutdown_background_services(self) -> None:
        """Stops capture manager, transcribers, and background services."""
        self.capture_manager.stop()
        self.ffmpeg_proc = None
        self._drain_asr_worker()
        if hasattr(self, "collateral_watcher") and self.collateral_watcher:
            with contextlib.suppress(Exception):
                self.collateral_watcher.stop(log_path=self.collateral_drop_log_path)
        if self.ws_server and self.ws_server.is_running:
            with contextlib.suppress(Exception):
                self.ws_server.stop()
        if self.isolated_transcriber:
            with contextlib.suppress(Exception):
                self.isolated_transcriber.stop()
        if self.copilot_advisor:
            with contextlib.suppress(Exception):
                self.copilot_advisor.shutdown(wait=False, cancel_futures=True)

    def _stitch_master_audio(self) -> Optional[Any]:
        """Stitches rolling audio chunks into unified master WAV file with skip-and-pad resilience."""
        from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import reconcile_master_wav_and_receipt
        receipt = None
        if (len(self.processed_chunks) > 0 or any(self.chunks_dir.glob("chunk_*.wav"))) and any(self.chunks_dir.glob("chunk_*.wav")):
            try:
                receipt = AudioMasterStitcher.stitch_master_wav(
                    self.chunks_dir,
                    self.master_wav,
                    pad_missing_or_corrupt=True,
                )
            except Exception as e:
                logger.error("[STITCHER-NONFATAL] Master audio stitching encountered error: %s", e)
        receipt = reconcile_master_wav_and_receipt(self, receipt)
        self.stitch_receipt = receipt
        if receipt is not None:
            logger.info(
                "[MASTER-AUDIO-STITCHED] %d chunks stitched (%0.2fs, sha256=%s, status=%s)",
                receipt.total_chunks,
                receipt.duration_s,
                receipt.master_wav_sha256[:12] if receipt.master_wav_sha256 else "none",
                receipt.status,
            )
            from dual_pipeline.live_meeting_recorder_core.master_asr_seeder import seed_master_asr_from_recorder
            seed_master_asr_from_recorder(self, receipt)
        return receipt

    def _abort_and_rollback_empty_session(self) -> None:
        """Teardowns worktree and unlinks unneeded session files on early abort."""
        if not self.mock_mode and self.chunks_dir.exists():
            shutil.rmtree(self.chunks_dir, ignore_errors=True)
        active_roots = (Path.cwd().resolve(), ROOT.resolve())
        if not getattr(self, "_is_inside_worktree", False) and self.worktree_dir and self.worktree_dir.resolve() not in active_roots:
            teardown_empty_live_worktree(self.worktree_dir, self.worktree_branch, ROOT)
        for artifact in (self.session_manifest, self.transcript_md):
            with contextlib.suppress(Exception):
                if artifact.exists():
                    artifact.unlink()
        with contextlib.suppress(Exception):
            if hasattr(self, "collateral_ref_dir") and self.collateral_ref_dir.exists() and not any(self.collateral_ref_dir.iterdir()):
                self.collateral_ref_dir.rmdir()
            if hasattr(self, "output_dir") and self.output_dir.exists() and not any(self.output_dir.iterdir()):
                self.output_dir.rmdir()

    def _cleanup_empty_session_or_seal(self) -> None:
        """Seals transcript if chunks exist; otherwise executes early abort rollback guard."""
        if len(self.processed_chunks) == 0:
            self._abort_and_rollback_empty_session()
            raise RuntimeError(
                "Recording session aborted: 0 audio chunks were captured. "
                "Check audio input device permissions and source configuration."
            )

        has_acoustic = bool(getattr(self, "_acoustic_diarization_complete", False))
        is_resolved = bool(getattr(self, "_acoustic_roster_resolved", False))
        diar_status = "DIARIZED_ACOUSTIC_RESOLVED" if is_resolved else ("DIARIZED_ACOUSTIC" if has_acoustic else "UNDIARIZED")
        now_stop = getattr(self, "session_stop_datetime", None) or datetime.now()
        stop_time = getattr(self, "session_stop_time", None) or time.time()
        start_time = getattr(self, "session_start_time", None) or stop_time
        start_datetime = getattr(self, "session_start_datetime", None) or (datetime.fromtimestamp(start_time) if start_time else now_stop)
        wall_clock_dur = round(max(0.0, stop_time - start_time), 2)
        start_dt = datetime.fromtimestamp(start_time) if isinstance(start_time, (int, float)) else now_stop
        self.meeting_time_window = f"{start_dt.strftime('%I:%M %p')} - {now_stop.strftime('%I:%M %p')} ET"
        time_display = self.meeting_time_window
        from dual_pipeline.cli.canonical_invocation import build_canonical_ingest_command

        ref_dir = getattr(self, "collateral_ref_dir", None)
        att_dir_arg = ref_dir if (ref_dir and ref_dir.is_dir() and any(f.is_file() and not f.name.startswith(".") for f in ref_dir.iterdir())) else None
        canonical_cmd = build_canonical_ingest_command(
            input_path=self.master_wav,
            project=self.project_name,
            meeting=self.meeting_name,
            date=self.meeting_date_display,
            attendees=self.attendees,
            reviewer=os.environ.get("NEE_REVIEWER", "operator"),
            filename_slug=getattr(self, "clean_slug", None),
            worktree_dir=getattr(self, "worktree_dir", None),
            time=self.meeting_time_window,
            attachment_dir=att_dir_arg,
        )
        self.canonical_ingest_command = canonical_cmd

        collapse_info, acoustic_receipt_data = write_acoustic_evidence_receipt(
            voice_registry=getattr(self, "voice_registry", None),
            session_tag=self.session_tag,
            clean_slug=self.clean_slug,
            processed_chunks_count=len(self.processed_chunks),
            attendees=self.attendees,
            stop_timestamp=now_stop.isoformat(),
            session_manifest_path=self.session_manifest,
            receipt_path=self.acoustic_evidence_receipt_path,
            cache_dir=self.cache_dir,
        )

        stitch_data = (
            self.stitch_receipt.to_dict() if getattr(self, "stitch_receipt", None) else None
        )
        starv_alarms = getattr(self, "starvation_alarms", [])
        meta = {
            "attendees": self.attendees,
            "project": self.project_name,
            "source_type": "AUDIO_DIARIZED" if has_acoustic else "AUDIO_ONLY_UNDIARIZED",
            "has_acoustic_diarization": has_acoustic,
            "diarization_status": diar_status,
            "auto_stopped_reason": self.auto_stopped_reason,
            "stopped_at": now_stop.isoformat(),
            "wall_clock_duration_seconds": wall_clock_dur,
            "session_end_time_display": now_stop.strftime("%I:%M %p ET"),
            "provenance": "LIVE_PREVIEW (non-authoritative)",
            "canonical_ingest_command": canonical_cmd,
            "stitch_receipt": stitch_data,
            "audio_starvation_events": starv_alarms,
            "recording_completed": True,
            "attribution_completed": False,
            "acoustic_evidence_receipt": str(self.acoustic_evidence_receipt_path),
            "collapse_detection": collapse_info,
        }
        if self.session_manifest and self.session_manifest.exists():
            _update_session_manifest(
                self.session_manifest,
                captured_files=[], stopped_at=meta["stopped_at"],
                wall_clock_duration_seconds=meta["wall_clock_duration_seconds"],
                session_end_time_display=meta["session_end_time_display"],
                audio_starvation_events=starv_alarms,
            )
            try:
                m_data = json.loads(self.session_manifest.read_text(encoding="utf-8")) if self.session_manifest.exists() else {}
                m_data.update({
                    "time": time_display,
                    "session_time": time_display,
                    "ended_at": now_stop.isoformat(),
                    "canonical_ingest_command": canonical_cmd, "provenance": "LIVE_PREVIEW (non-authoritative)",
                    "recording_completed": True, "attribution_completed": False,
                    "acoustic_evidence_receipt": str(self.acoustic_evidence_receipt_path),
                    "acoustic_evidence_receipt_path": str(self.acoustic_evidence_receipt_path),
                    "collapse_detection": collapse_info,
                })
                if stitch_data:
                    m_data["stitch_receipt"] = stitch_data
                self.session_manifest.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning("Could not persist extra manifest metadata: %s", e)

        if not self.transcript_md.exists():
            self._init_transcript_file()
        seal_live_transcript_footer(
            self.transcript_md, len(self.processed_chunks), self.session_manifest, meta
        )

        print(
            f"\n[CANONICAL-INGESTION-INVOCATION] Master audio ready for governed ingestion:\n{canonical_cmd}\n",
            flush=True,
        )

    def _validate_final_chunk(self) -> None:
        if not self.chunks_dir.exists() or not any(self.chunks_dir.glob("*.wav")):
            return
        try:
            validate_final_chunk(self.chunks_dir, self.segment_seconds)
        except Exception as e:
            logger.warning("[FINAL-CHUNK-VALIDATION-NONFATAL] Error validating final chunk: %s", e)

    def _resolve_acoustic_to_roster(
        self,
        turns: List[Dict[str, Any]],
        segments: List[Dict[str, Any]],
    ) -> Dict[str, str]:
        """Tombstone / Deprecated: Roster resolution is handled downstream at Stage U0/L1."""
        return {t["speaker"]: "Speaker" for t in turns}

    def _align_transcript_with_acoustic_turns(self, turns: List[Dict[str, Any]]) -> None:
        """Tombstone: Live transcript markdown is never autonomously rewritten (anti-autonomous attribution invariant)."""
        logger.debug("[ANTI-AUTONOMOUS-ATTRIBUTION] Skipping live transcript alignment overwrite.")
        return

    def _run_post_recording_diarization(self) -> Optional[List[Dict[str, Any]]]:
        """Runs pinned acoustic diarization on master WAV as optional enrichment; never throws."""
        if not getattr(self, "enable_post_stop_diarization", False):
            logger.info("Skipping post-recording diarization by default (deferred to batch intake).")
            return None
        if (
            (self.mock_mode and self.diarization_engine is None)
            or not self.master_wav.exists()
            or self.master_wav.stat().st_size < 4096
        ):
            return None

        if self._diarization_thread is not None and self._diarization_thread.is_alive():
            logger.info("Waiting for background diarization engine initialization to complete...")
            self._diarization_thread.join(timeout=120)

        try:
            if self.diarization_engine is None:
                if getattr(self, "_diarization_load_error", None) is not None:
                    logger.error(
                        "Diarization background initialization failed: %s",
                        self._diarization_load_error,
                    )
                    raise RuntimeError(
                        f"Post-recording acoustic diarization failed: {self._diarization_load_error}"
                    ) from self._diarization_load_error
                from dual_pipeline.audio_ingestion_core.diarization_engine import (
                    get_diarization_engine,
                )

                self.diarization_engine = get_diarization_engine()

            logger.info(
                "Executing post-recording acoustic diarization on %s...", self.master_wav.name
            )
            expected_roster = len([a for a in self.attendees if a.get("name")])
            expected_roster = expected_roster if expected_roster > 1 else None
            turns = self.diarization_engine.extract_speaker_turns(
                self.master_wav,
                expected_roster_count=expected_roster,
            )
            if not turns:
                logger.warning("Acoustic diarization returned 0 turns on master WAV.")
                return None

            logger.info(
                "Post-recording acoustic diarization extracted %d turns across %d speakers.",
                len(turns),
                len(set(t["speaker"] for t in turns)),
            )

            pfx = f"00_{self.date_dirname}_{self.clean_slug}"
            tracks_file = self.cache_dir / f"{pfx}_{self.session_tag}_diarization_tracks.json"
            tracks_file.write_text(json.dumps(turns, indent=2), encoding="utf-8")
            self._acoustic_diarization_complete = True
            return turns
        except Exception as e:
            logger.warning(
                "[ENRICHMENT-WARNING] Post-recording acoustic diarization failed or skipped: %s (recording sealed safely)",
                e,
            )
            self._acoustic_diarization_error = str(e)
            return None

    def _release_slug_lock(self) -> None:
        if hasattr(self, "_heartbeat_worker") and self._heartbeat_worker:
            with contextlib.suppress(Exception):
                self._heartbeat_worker.stop()
        if hasattr(self, "session_registry") and hasattr(self, "session_id"):
            with contextlib.suppress(Exception):
                self.session_registry.unregister(self.session_id)
        if self._slug_lock_fd is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(self._slug_lock_fd, fcntl.LOCK_UN)
                self._slug_lock_fd.close()
            self._slug_lock_fd = None
        if getattr(self, "isolated_transcriber", None) is not None and self.isolated_transcriber.is_running:
            with contextlib.suppress(Exception):
                self.isolated_transcriber.stop()
        if hasattr(self, "_global_mlx_lock") and self._global_mlx_lock is not None:
            with contextlib.suppress(Exception):
                self._global_mlx_lock.release()
            self._global_mlx_lock = None

    def _early_abort_cleanup(self) -> None:
        if getattr(self, "_is_stopped", False):
            return
        if len(getattr(self, "processed_chunks", [])) > 0:
            self.stop()
            return
        self._is_stopped = True
        if hasattr(self, "spooler") and self.spooler is not None:
            with contextlib.suppress(Exception):
                self.spooler.stop()
        if hasattr(self, "chat_collateral_bridge") and self.chat_collateral_bridge is not None:
            with contextlib.suppress(Exception):
                self.chat_collateral_bridge.stop()
        if hasattr(self, "collateral_watcher") and self.collateral_watcher is not None:
            with contextlib.suppress(Exception):
                self.collateral_watcher.stop()
        if hasattr(self, "capture_manager") and self.capture_manager is not None:
            with contextlib.suppress(Exception):
                self.capture_manager.stop()
        active_roots = (Path.cwd().resolve(), ROOT.resolve())
        wt_dir = getattr(self, "worktree_dir", None)
        if not getattr(self, "_is_inside_worktree", False) and len(getattr(self, "processed_chunks", [])) == 0 and wt_dir and Path(wt_dir).resolve() not in active_roots:
            with contextlib.suppress(Exception):
                teardown_empty_live_worktree(self.worktree_dir, self.worktree_branch, ROOT)
        self._release_slug_lock()

    def _setup_abort_and_signal_handlers(self) -> None:
        atexit.register(self._early_abort_cleanup)
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            try:
                prev_h = signal.getsignal(sig)

                def _make_handler(ph=prev_h, signum_val=sig):
                    def _handler(signum, frame):
                        if getattr(self, "running", False):
                            self.running = False
                            self.auto_stopped_reason = f"SIGNAL_{signum}"
                            if signum == signal.SIGINT:
                                raise KeyboardInterrupt()
                            self.stop()
                        elif len(getattr(self, "processed_chunks", [])) > 0 and not getattr(self, "_is_stopped", False):
                            self.stop()
                        else:
                            self._early_abort_cleanup()

                        if callable(ph) and ph not in (signal.SIG_IGN, signal.SIG_DFL, _handler):
                            ph(signum, frame)
                        else:
                            sys.exit(128 + signum)

                    return _handler

                signal.signal(sig, _make_handler(prev_h, sig))
            except (ValueError, OSError):
                pass

    def stop(self) -> None:
        """Gracefully halts audio capture, executes ordered stages (drain -> stitch -> hint export -> seal), and releases locks."""
        if getattr(self, "_is_stopped", False):
            return
        self._is_stopped = True
        now_stop = datetime.now()
        self.session_stop_datetime = now_stop
        self.session_stop_time = time.time()
        self.session_stop_monotonic = time.monotonic()
        try:
            from dual_pipeline.live_meeting_recorder_core.stop_lifecycle import (
                finalize_diarization_and_seal,
                finalize_hints_and_attributions,
                halt_capture_before_drain,
            )
            # Stage 1: Halt capture cleanly and drain remaining chunks
            halt_capture_before_drain(self)
            self._drain_remaining_chunks()
            self._flush_pending_pause()
            if len(self.processed_chunks) > 0 and not self._worktree_materialized and self.worktree_dir and self.worktree_branch:
                self.materialize_worktree(is_final=True)
            self._shutdown_background_services()
            if len(self.processed_chunks) > 0:
                try:
                    self._validate_final_chunk()
                except Exception as e:
                    logger.warning("[FINAL-CHUNK-VALIDATION-NONFATAL] Error validating final chunk in stop(): %s", e)
            self.drain_receipt = {"stage": "drain", "status": "COMPLETED", "processed_chunks": len(self.processed_chunks)}

            # Stage 2: Reconcile tail hints, export colocated speaker maps & chat attributions
            finalize_hints_and_attributions(self)

            # Stage 3: Stitch (with skip-and-pad resilience) and seed ASR cache
            self._stitch_master_audio()

            # Stage 4: Diarization & Seal
            finalize_diarization_and_seal(self)

        finally:
            self._release_slug_lock()
            try:
                from dual_pipeline.live_meeting_recorder_core.chunk_transcriber import clear_mlx_transcriber_memory
                clear_mlx_transcriber_memory()
            except Exception as e:
                logger.debug("Failed clearing MLX transcriber memory on stop: %s", e)
