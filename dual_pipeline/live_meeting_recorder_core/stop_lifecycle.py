"""
dual_pipeline/live_meeting_recorder_core/stop_lifecycle.py

Lifecycle stages for meeting stop: hint reconciliation, post-recording diarization, and sealing.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from pathlib import Path
import os
import shutil
import time
from typing import Any, List, Optional, Set
import wave

from dual_pipeline.live_meeting_recorder_core.worktree_manager import migrate_staged_files
from dual_pipeline.live_meeting_recorder_core.worktree_repointer import (
    _is_durable_master_or_cache_artifact,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.stop_lifecycle")


def _read_disk_attendees(recorder: Any) -> Optional[List[Any]]:
    manifest_p = getattr(recorder, "session_manifest", None)
    if manifest_p and Path(manifest_p).is_file():
        with contextlib.suppress(Exception):
            m_data = json.loads(Path(manifest_p).read_text(encoding="utf-8"))
            if isinstance(m_data.get("attendees"), list):
                return m_data["attendees"]

    if hasattr(recorder, "session_registry") and hasattr(recorder, "session_id"):
        with contextlib.suppress(Exception):
            m = recorder.session_registry.inspect(recorder.session_id)
            if m and m.attendees:
                return m.attendees
    return None


def _extract_hint_speaker(h: Any) -> Optional[str]:
    return h.get("speaker_name") if isinstance(h, dict) else getattr(h, "speaker_name", None)


def _collect_map_speakers(shm: Any) -> Set[str]:
    if not shm:
        return set()
    vr = getattr(shm, "voice_registry", None)
    candidates = (
        list((getattr(shm, "speaker_map", None) or {}).values())
        + [_extract_hint_speaker(h) for h in (getattr(shm, "hints", None) or [])]
        + [getattr(cl, "resolved_speaker", None) for cl in (getattr(vr, "voice_clusters", None) or {}).values()]
    )
    return {str(s) for s in candidates if s and s not in ("Speaker", "Unknown")}


def _collect_queue_speakers(shq: Any) -> Set[str]:
    confirmed: Set[str] = set()
    if not shq or not getattr(shq, "queue_path", None) or not Path(shq.queue_path).is_file():
        return confirmed
    with contextlib.suppress(Exception):
        q_data = json.loads(Path(shq.queue_path).read_text(encoding="utf-8"))
        for item in q_data:
            spk = item.get("speaker_name")
            if spk and spk not in ("Speaker", "Unknown"):
                confirmed.add(spk)
    return confirmed


def _collect_confirmed_speakers(recorder: Any) -> Set[str]:
    shm = getattr(recorder, "speaker_hint_manager", None)
    shq = getattr(recorder, "speaker_hint_queue", None)
    return _collect_map_speakers(shm) | _collect_queue_speakers(shq)


def sync_roster_from_disk_and_hints(recorder: Any) -> List[Any]:
    """Reloads roster from session_manifest / .session_registry and merges confirmed hint speakers."""
    disk_attendees = _read_disk_attendees(recorder)
    fallback = getattr(recorder, "attendees", []) or []
    current_attendees = list(disk_attendees if disk_attendees is not None else fallback)

    confirmed = _collect_confirmed_speakers(recorder)
    existing_names = {
        (a.get("name", "") if isinstance(a, dict) else str(a)).strip().lower()
        for a in current_attendees
    }

    for spk in confirmed:
        if spk.strip().lower() not in existing_names:
            current_attendees.append(spk)
            existing_names.add(spk.strip().lower())

    recorder.attendees = current_attendees
    return current_attendees

def halt_capture_before_drain(recorder: Any) -> None:
    """Stops capture subprocesses and spooler threads cleanly before draining chunks."""
    recorder.running = False
    cm = getattr(recorder, "capture_manager", None)
    if cm is not None:
        with contextlib.suppress(Exception):
            cm.graceful_stop()
        time.sleep(0.5 if not getattr(recorder, "mock_mode", False) else 0.05)
        with contextlib.suppress(Exception):
            cm.stop()
    recorder.ffmpeg_proc = None

    spooler = getattr(recorder, "spooler", None)
    if spooler is not None:
        with contextlib.suppress(Exception):
            spooler._is_adopted = False
            spooler.stop()
            spooler._is_running = False
            mock_t = getattr(spooler, "_mock_thread", None)
            if mock_t is not None and mock_t.is_alive():
                mock_t.join(timeout=1.0)
                spooler._mock_thread = None


def migrate_all_chunk_sources_on_drain(recorder: Any) -> None:
    """Migrates all remaining chunks from staging, capture_manager, and spooler directories."""
    target_dir = getattr(recorder, "chunks_dir", None)
    if not target_dir:
        return
    sources = [
        getattr(recorder, "_staging_chunks_dir", None),
        getattr(getattr(recorder, "capture_manager", None), "chunks_dir", None),
        getattr(getattr(recorder, "spooler", None), "spool_dir", None),
    ]
    seen = set()
    for src in sources:
        if not src:
            continue
        src_path = Path(src)
        if not src_path.exists():
            continue
        resolved = src_path.resolve()
        if resolved == target_dir.resolve() or resolved in seen:
            continue
        seen.add(resolved)
        try:
            migrate_staged_files(src_path, target_dir, [], is_final=True)
        except Exception as mig_err:
            logger.warning("Failed migrating staged chunks from %s to %s: %s", src_path, target_dir, mig_err)
            errs = getattr(recorder, "stop_lifecycle_errors", None)
            if not isinstance(errs, list):
                errs = []
                recorder.stop_lifecycle_errors = errs
            errs.append(f"migrate_staged_files:{mig_err}")


def _compute_wav_sha256_and_duration(wav_path: Path) -> tuple[str, float]:
    h = hashlib.sha256()
    with open(wav_path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    dur_s = 0.0
    with contextlib.suppress(Exception):
        with wave.open(str(wav_path), "rb") as w:
            rate = w.getframerate()
            if rate > 0:
                dur_s = round(w.getnframes() / float(rate), 3)
    return h.hexdigest(), dur_s


def _resolve_primary_root_cache_dir(recorder: Any, master_path: Path) -> Optional[Path]:
    """Resolves the corresponding output/transcript_cache directory in the primary repository root."""
    resolved_master = master_path.resolve()
    staging_cache = getattr(recorder, "_staging_cache_dir", None)
    if staging_cache:
        st_dir = Path(staging_cache).resolve()
        if st_dir != resolved_master.parent and ".worktrees" not in st_dir.parts:
            return st_dir

    parts = resolved_master.parts
    if ".worktrees" in parts:
        wt_idx = parts.index(".worktrees")
        if len(parts) > wt_idx + 2:
            primary_root = Path(*parts[:wt_idx])
            rel_parent = Path(*parts[wt_idx + 2 : -1])
            return primary_root / rel_parent

    repo_root = getattr(recorder, "repo_root", None)
    wt_dir = getattr(recorder, "worktree_dir", None)
    if repo_root and wt_dir:
        wt_resolved = Path(wt_dir).resolve()
        root_resolved = Path(repo_root).resolve()
        if wt_resolved != root_resolved and resolved_master.is_relative_to(wt_resolved):
            return root_resolved / resolved_master.parent.relative_to(wt_resolved)
    return None


def _atomic_copy_and_verify(
    src: Path, dst: Path, expected_sha256: Optional[str] = None
) -> bool:
    """Atomically copies src to dst via .tmp + fsync + os.replace and verifies SHA-256."""
    if not src.is_file():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if expected_sha256 and dst.is_file() and dst.stat().st_size == src.stat().st_size:
        existing_sha, _ = _compute_wav_sha256_and_duration(dst)
        if existing_sha == expected_sha256:
            return True

    tmp_dst = dst.with_name(f".{dst.name}.tmp.{os.getpid()}")
    try:
        shutil.copy2(src, tmp_dst)
        with open(tmp_dst, "rb") as f:
            os.fsync(f.fileno())
        os.replace(tmp_dst, dst)
        if expected_sha256:
            actual_sha, _ = _compute_wav_sha256_and_duration(dst)
            return actual_sha == expected_sha256
        return dst.is_file()
    except Exception as exc:
        logger.warning("Failed atomic mirror from %s to %s: %s", src, dst, exc)
        with contextlib.suppress(Exception):
            tmp_dst.unlink(missing_ok=True)
        return False


def mirror_master_wav_to_primary_root(
    recorder: Any, master_path: Path, expected_sha256: Optional[str] = None
) -> Optional[Path]:
    """Mirrors stitched master WAV and durable ASR sidecars to primary repository root."""
    primary_cache_dir = _resolve_primary_root_cache_dir(recorder, master_path)
    if primary_cache_dir is None:
        return None
    primary_wav = primary_cache_dir / master_path.name
    if not _atomic_copy_and_verify(master_path, primary_wav, expected_sha256=expected_sha256):
        return None

    for item in list(master_path.parent.iterdir()):
        if item.is_file() and item.name != master_path.name and _is_durable_master_or_cache_artifact(item):
            _atomic_copy_and_verify(item, primary_cache_dir / item.name)
    return primary_wav


def reconcile_master_wav_and_receipt(recorder: Any, receipt: Optional[Any]) -> Optional[Any]:
    """Promotes larger spooler master WAV if needed, repairs RIFF headers, and syncs receipt SHA256."""
    master_wav = getattr(recorder, "master_wav", None)
    if not master_wav:
        return receipt
    master_path = Path(master_wav)
    spool_master = getattr(getattr(recorder, "spooler", None), "master_wav", None)
    if spool_master and Path(spool_master).is_file():
        sp_path = Path(spool_master)
        cur_size = master_path.stat().st_size if master_path.is_file() else 0
        if sp_path.resolve() != master_path.resolve() and sp_path.stat().st_size > max(44, cur_size):
            master_path.parent.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(Exception):
                shutil.copy2(sp_path, master_path)

    if not master_path.is_file() or master_path.stat().st_size <= 44:
        return receipt

    with contextlib.suppress(Exception):
        from dual_pipeline.audio_ingestion_core.container_validator import repair_wav_riff_header

        repair_wav_riff_header(master_path)

    sha256_hex, dur_s = _compute_wav_sha256_and_duration(master_path)
    mirror_master_wav_to_primary_root(recorder, master_path, expected_sha256=sha256_hex)

    if receipt is not None:
        receipt.master_wav_sha256 = sha256_hex
        if dur_s > float(getattr(receipt, "duration_s", 0.0) or 0.0):
            receipt.duration_s = dur_s
        return receipt

    from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import StitchReceipt

    n_chunks = len(getattr(recorder, "processed_chunks", ()) or ())
    return StitchReceipt(
        success=True,
        valid_chunks=n_chunks,
        duration_s=dur_s,
        status="SUCCESS",
        total_chunks=n_chunks,
        padded_chunks=0,
        master_wav_sha256=sha256_hex,
        master_wav_path=str(master_path),
    )


def _reconcile_stop_hints(recorder: Any) -> None:
    if not (getattr(recorder, "speaker_hint_queue", None) and getattr(recorder, "speaker_hint_manager", None)):
        return
    try:
        recorder.speaker_hint_queue.load()
        recorder.speaker_hint_queue.reconcile_pending_hints(
            recorder.speaker_hint_manager,
            recorder.segments_jsonl,
            recorder.transcript_md,
            recorder.clean_slug,
            voice_registry=recorder.voice_registry,
        )
    except Exception as rec_err:
        logger.warning("Could not reconcile pending speaker hints: %s", rec_err)
        errs = getattr(recorder, "stop_lifecycle_errors", None)
        if not isinstance(errs, list):
            errs = []
            recorder.stop_lifecycle_errors = errs
        errs.append(f"reconcile_pending_hints:{rec_err}")


def _append_stop_error(recorder: Any, label: str, err: Exception) -> None:
    errs = getattr(recorder, "stop_lifecycle_errors", None)
    if not isinstance(errs, list):
        errs = []
        recorder.stop_lifecycle_errors = errs
    errs.append(f"{label}:{err}")


def finalize_hints_and_attributions(recorder: Any) -> None:
    """Reconciles tail hints, exports colocated speaker maps, and saves chat attributions."""
    try:
        sync_roster_from_disk_and_hints(recorder)
    except Exception as sync_err:
        logger.warning("Could not sync roster from disk and hints: %s", sync_err)
        _append_stop_error(recorder, "sync_roster_from_disk_and_hints", sync_err)
    _reconcile_stop_hints(recorder)
    if hasattr(recorder, "chat_attributions") and recorder.chat_attributions.attributions:
        try:
            recorder.chat_attributions.save(recorder.chat_attributions_path)
        except Exception as e:
            logger.warning(f"Could not save inline chat attributions: {e}")
            _append_stop_error(recorder, "chat_attributions", e)
    if hasattr(recorder, "speaker_hint_manager") and recorder.speaker_hint_manager:
        try:
            recorder.speaker_hint_manager.export_colocated_speaker_map(
                recorder.output_dir, recorder.clean_slug
            )
        except Exception as e:
            logger.warning(f"Could not export speaker map from hints: {e}")
            _append_stop_error(recorder, "export_colocated_speaker_map", e)


def finalize_diarization_and_seal(recorder: Any) -> None:
    """Runs optional post-recording acoustic diarization, executes seal, and releases slug lock."""
    try:
        recorder._run_post_recording_diarization()
        status = (
            "COMPLETED"
            if getattr(recorder, "_acoustic_diarization_complete", False)
            else (
                "FAILED"
                if getattr(recorder, "_acoustic_diarization_error", None)
                else "SKIPPED_OR_WARNING"
            )
        )
        recorder.enrichment_receipt = {
            "stage": "enrichment",
            "status": status,
            "error": getattr(recorder, "_acoustic_diarization_error", None),
        }
    except Exception as ee:
        logger.warning(
            "[ENRICHMENT-ERROR] Stage 3 optional enrichment encountered an error: %s (session remains sealed safely)",
            ee,
        )
        recorder.enrichment_receipt = {
            "stage": "enrichment",
            "status": "FAILED",
            "error": str(ee),
        }

    try:
        sync_roster_from_disk_and_hints(recorder)
    except Exception as sync_err:
        logger.warning("Failed syncing roster during final seal: %s", sync_err)
        errs = getattr(recorder, "stop_lifecycle_errors", None)
        if not isinstance(errs, list):
            errs = []
            recorder.stop_lifecycle_errors = errs
        errs.append(f"sync_roster_from_disk_and_hints:{sync_err}")
    recorder._cleanup_empty_session_or_seal()
    master_wav = getattr(recorder, "master_wav", None)
    if master_wav and Path(master_wav).is_file():
        try:
            mirror_master_wav_to_primary_root(recorder, Path(master_wav))
        except Exception as mir_err:
            logger.warning("Failed mirroring master wav during final seal: %s", mir_err)
            errs = getattr(recorder, "stop_lifecycle_errors", None)
            if not isinstance(errs, list):
                errs = []
                recorder.stop_lifecycle_errors = errs
            errs.append(f"mirror_master_wav_to_primary_root:{mir_err}")
    stop_errs = list(getattr(recorder, "stop_lifecycle_errors", None) or [])
    recorder.seal_receipt = {
        "stage": "seal",
        "status": "SEALED",
        "degraded": bool(stop_errs),
        "stop_lifecycle_errors": stop_errs,
        "transcript_md": str(recorder.transcript_md),
        "session_manifest": str(recorder.session_manifest),
        "acoustic_evidence_receipt": str(recorder.acoustic_evidence_receipt_path),
    }
    recorder._release_slug_lock()

