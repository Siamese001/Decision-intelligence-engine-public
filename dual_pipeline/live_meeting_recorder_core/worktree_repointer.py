"""
dual_pipeline/live_meeting_recorder_core/worktree_repointer.py

Recorder path synchronization and runtime repointing for isolated worktrees.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.worktree_repointer")


def _is_spooler_active(spooler: Any) -> bool:
    if spooler is None:
        return False
    return bool(
        getattr(spooler, "_is_adopted", False)
        or getattr(spooler, "_is_running", False)
        or (callable(getattr(spooler, "is_adopted", None)) and spooler.is_adopted())
        or (callable(getattr(spooler, "is_running", None)) and spooler.is_running())
    )


def _repoint_capture_manager(cm: Any, spooler: Any, master_wav: Path, chunks_dir: Path) -> None:
    if cm is None:
        return
    has_bound_capture = bool(
        _is_spooler_active(spooler)
        or getattr(cm, "_active_strategy", None) == "spooler_adopted"
        or getattr(cm, "_ffmpeg_proc", None) is not None
    )
    if not has_bound_capture:
        cm.master_wav = master_wav
        cm.chunks_dir = chunks_dir


def _update_session_manifest(manifest_path: Optional[Path], master_wav: Path, segments_jsonl: Path) -> None:
    if manifest_path is None or not manifest_path.exists():
        return
    try:
        m_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        m_data["master_wav"] = str(master_wav)
        m_data["segments_jsonl"] = str(segments_jsonl)
        manifest_path.write_text(json.dumps(m_data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug("Failed updating manifest paths on repoint: %s", e)


def repoint_recorder_paths(recorder: Any, target_worktree_dir: Optional[Path] = None) -> None:
    """Repoints recorder runtime paths to the isolated worktree root."""
    if target_worktree_dir is not None:
        recorder.worktree_dir = Path(target_worktree_dir)
    recorder.output_dir, recorder.cache_dir, recorder.chunks_dir = (
        recorder._target_output_dir, recorder._target_cache_dir, recorder._target_chunks_dir
    )
    pfx = f"00_{recorder.date_dirname}_{recorder.clean_slug}"
    recorder.master_wav = recorder.cache_dir / f"{pfx}_master_{recorder.session_tag}.wav"
    recorder.session_manifest = recorder.cache_dir / f"{pfx}_{recorder.session_tag}_manifest.json"
    recorder.acoustic_evidence_receipt_path = recorder.cache_dir / f"{pfx}_{recorder.session_tag}_acoustic_evidence_receipt.json"
    recorder.transcript_md = recorder.output_dir / f"01_{recorder.date_dirname}_{recorder.clean_slug}_live_transcript.md"
    recorder.segments_jsonl = recorder.cache_dir / f"01_{recorder.date_dirname}_{recorder.clean_slug}_segments.jsonl"
    recorder.copilot_jsonl = recorder.cache_dir / f"01_{recorder.date_dirname}_{recorder.clean_slug}_copilot_feedback.jsonl"
    recorder.asr_filter_log = recorder.cache_dir / "asr_filter_log.jsonl"
    recorder.chat_attributions_path = recorder.output_dir / "inline_chat_attributions.json"
    if getattr(recorder, "chat_attributions", None):
        recorder.chat_attributions.output_path = recorder.chat_attributions_path
    if getattr(recorder, "speaker_hint_manager", None):
        recorder.speaker_hint_manager.output_dir = recorder.output_dir
        recorder.speaker_hint_manager.cache_dir = recorder.cache_dir
    if getattr(recorder, "speaker_hint_queue", None):
        recorder.speaker_hint_queue.output_dir = recorder.output_dir
        recorder.speaker_hint_queue.cache_dir = recorder.cache_dir
        recorder.speaker_hint_queue.queue_path = recorder.cache_dir / "speaker_hints_queue.json"

    _repoint_capture_manager(
        getattr(recorder, "capture_manager", None),
        getattr(recorder, "spooler", None),
        recorder.master_wav,
        recorder.chunks_dir,
    )

    recorder.collateral_ref_dir = recorder.output_dir / "referenced_artifacts"
    recorder.referenced_artifacts_dir = recorder.collateral_ref_dir
    recorder.ref_dir = recorder.collateral_ref_dir
    recorder.collateral_drop_log = recorder.output_dir / "collateral_drop_log.json"
    recorder.collateral_drop_log_path = recorder.collateral_drop_log
    if getattr(recorder, "collateral_watcher", None):
        recorder.collateral_watcher.repoint(
            new_ref_dir=recorder.collateral_ref_dir,
            new_log_path=recorder.collateral_drop_log,
        )
    if getattr(recorder, "copilot_advisor", None):
        recorder.copilot_advisor._session_output_dir = recorder.output_dir

    _update_session_manifest(
        getattr(recorder, "session_manifest", None),
        recorder.master_wav,
        recorder.segments_jsonl,
    )
    if hasattr(recorder, "_sync_session_manifest_paths") and getattr(recorder, "session_manifest_record", None) is not None:
        import contextlib
        with contextlib.suppress(Exception):
            recorder._sync_session_manifest_paths()


def _prune_empty_ancestors(path: Optional[Path], stop_at: Optional[Path] = None) -> None:
    """Removes empty directories upward from path until non-empty or stop_at is reached."""
    if path is None:
        return
    curr = Path(path)
    stop_resolved = Path(stop_at).resolve() if stop_at else None
    for _ in range(5):
        if not curr.exists() or not curr.is_dir():
            curr = curr.parent
            continue
        try:
            if stop_resolved and curr.resolve() == stop_resolved:
                break
            if any(curr.iterdir()):
                break
            curr.rmdir()
            curr = curr.parent
        except Exception:
            break


DURABLE_CACHE_SUFFIXES = (
    ".wav",
    ".raw_asr.json",
    ".segments.json",
    ".utterances.json",
    ".transcript_cache.json",
    ".cache_seal.json",
    "_manifest.json",
    "_acoustic_evidence_receipt.json",
)


def _is_durable_master_or_cache_artifact(path: Path) -> bool:
    """Returns True for master WAVs and sealed ASR cache artifacts that must survive in primary root."""
    name = path.name.lower()
    if name.startswith("chunk_") and name.endswith(".wav"):
        return False
    return any(name.endswith(sfx) for sfx in DURABLE_CACHE_SUFFIXES)


def _unlink_migrated_files(
    src_dir: Path,
    dst_dir: Path,
    pattern: str = "*",
    preserve_durable_audio: bool = False,
) -> None:
    import contextlib

    for item in list(src_dir.glob(pattern)):
        if item.is_file() and (dst_dir / item.name).exists():
            if preserve_durable_audio and _is_durable_master_or_cache_artifact(item):
                continue
            with contextlib.suppress(Exception):
                item.unlink()


def _migrate_referenced_artifacts(staging_out: Path, target_out: Path) -> None:
    import contextlib
    import shutil

    ref_src = staging_out / "referenced_artifacts"
    ref_dst = target_out / "referenced_artifacts"
    if not (ref_src.exists() and ref_src.is_dir()):
        return
    for item in list(ref_src.iterdir()):
        if item.is_file():
            ref_dst.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(Exception):
                shutil.copy2(item, ref_dst / item.name)
                item.unlink()
    with contextlib.suppress(Exception):
        if not any(ref_src.iterdir()):
            ref_src.rmdir()


def cleanup_staging_directories(
    staging_out: Optional[Path],
    staging_cache: Optional[Path],
    staging_chunks: Optional[Path],
    target_out: Optional[Path],
    target_cache: Optional[Path],
    target_chunks: Optional[Path],
    root_dir: Optional[Path] = None,
    chunks_final: bool = True,
) -> None:
    """Cleans up pre-materialization staging artifacts and empty directories in primary repo."""
    stop_root = (Path(root_dir) / "output") if root_dir else None
    if staging_out and target_out and staging_out.exists() and staging_out.resolve() != target_out.resolve():
        _migrate_referenced_artifacts(staging_out, target_out)
        _unlink_migrated_files(staging_out, target_out)
        _prune_empty_ancestors(staging_out, stop_at=stop_root)

    if chunks_final and staging_chunks and target_chunks and staging_chunks.exists() and staging_chunks.resolve() != target_chunks.resolve():
        _unlink_migrated_files(staging_chunks, target_chunks, pattern="chunk_*.wav")
        _prune_empty_ancestors(staging_chunks, stop_at=stop_root)

    if chunks_final and staging_cache and target_cache and staging_cache.exists() and staging_cache.resolve() != target_cache.resolve():
        _unlink_migrated_files(staging_cache, target_cache, preserve_durable_audio=True)
        _prune_empty_ancestors(staging_cache, stop_at=stop_root)


def build_staged_metadata_pairs(recorder: Any) -> list[tuple[Path, Path]]:
    """Builds source-to-target metadata file pairs for worktree migration."""
    return [
        (recorder.session_manifest, recorder._target_cache_dir),
        (recorder.segments_jsonl, recorder._target_cache_dir),
        (recorder.copilot_jsonl, recorder._target_cache_dir),
        (recorder.asr_filter_log, recorder._target_cache_dir),
        (recorder.acoustic_evidence_receipt_path, recorder._target_cache_dir),
        (recorder.cache_dir / "record_start_receipt.json", recorder._target_cache_dir),
        (recorder.cache_dir / "record_start_failure.json", recorder._target_cache_dir),
        (recorder.output_dir / "record_start_receipt.json", recorder._target_output_dir),
        (recorder.transcript_md, recorder._target_output_dir),
        (recorder.output_dir / "speaker_hints.json", recorder._target_output_dir),
        (recorder.output_dir / "speaker_hints_queue.json", recorder._target_output_dir),
        (recorder.cache_dir / "speaker_hints_queue.json", recorder._target_cache_dir),
        (recorder.output_dir / "inline_chat_attributions.json", recorder._target_output_dir),
        (recorder.output_dir / "speaker_hint_conflicts.jsonl", recorder._target_output_dir),
        (recorder.output_dir / f"{recorder.clean_slug}_speaker_map.json", recorder._target_output_dir),
        (recorder.collateral_drop_log_path, recorder._target_output_dir),
    ]


def finalize_worktree_materialization(
    recorder: Any, is_final: bool = False, root_dir: Optional[Path] = None
) -> None:
    """Migrates staged chunks and metadata, repoints recorder paths, and cleans staging directories."""
    for d in (recorder._target_output_dir, recorder._target_cache_dir, recorder._target_chunks_dir):
        d.mkdir(parents=True, exist_ok=True)
    staging_out, staging_cache, staging_chunks = (
        recorder.output_dir,
        recorder.cache_dir,
        recorder.chunks_dir,
    )
    recorder._staging_chunks_dir = staging_chunks
    recorder._staging_cache_dir = staging_cache
    cm = getattr(recorder, "capture_manager", None)
    spooler = getattr(recorder, "spooler", None)
    is_live_capturing = bool(
        getattr(recorder, "running", False)
        or _is_spooler_active(spooler)
        or (cm is not None and (getattr(cm, "_ffmpeg_proc", None) is not None or getattr(cm, "_is_running", False)))
    )
    chunks_final = bool(is_final and not is_live_capturing)
    recorder._migrate_staged_chunks(is_final=chunks_final)
    recorder._migrate_staged_metadata(is_final=chunks_final)
    recorder._repoint_recorder_paths_to_worktree()
    cleanup_staging_directories(
        staging_out,
        staging_cache,
        staging_chunks,
        recorder.output_dir,
        recorder.cache_dir,
        recorder.chunks_dir,
        root_dir=root_dir,
        chunks_final=chunks_final,
    )



