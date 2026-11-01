"""
dual_pipeline/live_meeting_recorder_core/fast_bootstrap.py

Milestone 2: Pre-CLI fast bootstrap.
Executes lightweight preflight and spawns eager audio capture before
the 1.8s+ Click/CLI import cascade occurs, reaching first PCM byte in <= 0.5s.
Manages in-memory process ownership to prevent duplicate capture or orphaned children.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.fast_bootstrap")

_ACTIVE_BOOTSTRAP_SPOOLER: Optional[Any] = None


def is_bootstrap_eligible(argv: List[str]) -> bool:
    """Verifies that the command is genuinely eligible for eager audio capture."""
    if not argv:
        return False

    if os.environ.get("NEE_DISABLE_FAST_BOOTSTRAP") == "1":
        return False

    disqualifying_flags = {
        "-h",
        "--help",
        "--dry-run",
        "--no-instant",
        "--disable-fast-bootstrap",
        "--detach",
    }
    if any(arg in disqualifying_flags for arg in argv):
        return False

    return True


def get_bootstrap_spooler() -> Optional[Any]:
    """Returns the active in-memory bootstrap spooler instance, if any."""
    global _ACTIVE_BOOTSTRAP_SPOOLER
    return _ACTIVE_BOOTSTRAP_SPOOLER


def clear_bootstrap_spooler(stop: bool = False) -> Optional[Any]:
    """
    Clears the active bootstrap spooler reference.
    If stop=True, halts any running capture process to eliminate orphans.
    """
    global _ACTIVE_BOOTSTRAP_SPOOLER
    spooler = _ACTIVE_BOOTSTRAP_SPOOLER
    _ACTIVE_BOOTSTRAP_SPOOLER = None

    if stop and spooler is not None:
        try:
            if hasattr(spooler, "stop"):
                spooler.stop()
        except Exception as e:
            logger.debug("[FAST-BOOTSTRAP] Error stopping cleared spooler: %s", e)

    os.environ.pop("NEE_BOOTSTRAP_SESSION_ID", None)
    os.environ.pop("NEE_BOOTSTRAP_SPOOL_DIR", None)
    return spooler


def _parse_fast_bootstrap_args(argv: List[str]) -> Tuple[bool, str, int]:
    mock_mode = "--mock" in argv or "--mock-mode" in argv or os.environ.get("NEE_MOCK_RECORDER") == "1"
    source_mode = "iphone-speaker" if "--iphone-speaker" in argv else "mixed"
    if source_mode == "mixed":
        for i, a in enumerate(argv[:-1]):
            if a in ("--audio-source", "-s"):
                source_mode = argv[i + 1].lower().strip()
                break
    seg_sec = 15
    for i, a in enumerate(argv[:-1]):
        if a in ("--segment-seconds", "--segment-time"):
            try:
                seg_sec = max(1, int(argv[i + 1]))
            except ValueError:
                pass
            break
    return mock_mode, source_mode, seg_sec


def _is_mlx_session_already_open() -> bool:
    try:
        from dual_pipeline.live_meeting_recorder_core.mlx_session_lock import check_active_mlx_session
        is_active, holder = check_active_mlx_session()
        if is_active:
            logger.info(
                "[FAST-BOOTSTRAP] Active MLX session already open (holder PID: %s); skipping eager audio spooling.",
                getattr(holder, "pid", "unknown"),
            )
            return True
    except Exception as e:
        logger.debug("[FAST-BOOTSTRAP] Error checking global MLX session lock: %s", e)
    return False


def run_fast_bootstrap(argv: List[str]) -> Optional[str]:
    """Runs fast pre-CLI bootstrap for live recording commands."""
    global _ACTIVE_BOOTSTRAP_SPOOLER

    if not is_bootstrap_eligible(argv) or _is_mlx_session_already_open():
        return None

    mock_mode, source_mode, seg_sec = _parse_fast_bootstrap_args(argv)
    session_id = f"fastboot_{int(time.time())}_{os.getpid()}"
    spool_dir = Path(tempfile.gettempdir()) / f"nee_instant_spool_{session_id}"
    spool_dir.mkdir(parents=True, exist_ok=True)

    try:
        from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler

        spooler = InstantAudioSpooler(
            spool_dir=spool_dir,
            segment_seconds=seg_sec,
            mock_mode=mock_mode,
            session_id=session_id,
            source_mode=source_mode,
        )
        spooler.start_eager()
        _ACTIVE_BOOTSTRAP_SPOOLER = spooler

        os.environ["NEE_BOOTSTRAP_SESSION_ID"] = session_id
        os.environ["NEE_BOOTSTRAP_SPOOL_DIR"] = str(spool_dir)
        logger.info(
            "[FAST-BOOTSTRAP] Spooled eager capture (PID %s) into %s prior to CLI import.",
            getattr(spooler._ffmpeg_proc, "pid", None),
            spool_dir,
        )
        return str(spool_dir)
    except Exception as e:
        logger.debug("[FAST-BOOTSTRAP] Eager spooling skipped or failed: %s", e)
        _ACTIVE_BOOTSTRAP_SPOOLER = None
        return None


def resolve_session_staging_paths(
    worktree_materialized: bool,
    resolved_out: Path,
    resolved_cache: Path,
    resolved_chunks: Path,
    project_name: Optional[str],
    date_dirname: str,
    clean_slug: str,
    session_tag: str,
    mock_mode: bool,
    root_path: Optional[Path] = None,
) -> Tuple[Path, Path, Path]:
    """Resolves output, cache, and chunk staging directories for a live meeting session."""
    if worktree_materialized:
        if not mock_mode:
            for d in (resolved_out, resolved_cache, resolved_chunks):
                d.mkdir(parents=True, exist_ok=True)
        return resolved_out, resolved_cache, resolved_chunks

    proj = project_name or "UNRESOLVED"
    root = root_path or Path.cwd()
    staging_cache = root / "output" / "transcript_cache" / proj / date_dirname
    out_dir = root / "output" / proj / date_dirname / clean_slug
    cache_dir = staging_cache
    chunks_dir = cache_dir / f".staging_{clean_slug}_{session_tag}"
    if not mock_mode:
        for d in (cache_dir, chunks_dir):
            d.mkdir(parents=True, exist_ok=True)
    return out_dir, cache_dir, chunks_dir


def resolve_bootstrap_spooler(
    explicit_spooler: Optional[Any],
    dry_run: bool,
    mock_mode: bool,
) -> Optional[Any]:
    """Resolves existing in-memory or env-declared bootstrap spooler for the recorder."""
    if explicit_spooler is not None:
        return explicit_spooler

    in_mem_spooler = get_bootstrap_spooler()
    if in_mem_spooler is not None:
        if dry_run:
            clear_bootstrap_spooler(stop=True)
            return None
        return clear_bootstrap_spooler(stop=False)

    spool_dir_str = os.environ.get("NEE_BOOTSTRAP_SPOOL_DIR")
    if not spool_dir_str:
        return None

    spool_path = Path(spool_dir_str)
    session_id = os.environ.get("NEE_BOOTSTRAP_SESSION_ID")
    if not spool_path.exists():
        return None

    from dual_pipeline.live_meeting_recorder_core.instant_spooler import InstantAudioSpooler

    if dry_run:
        try:
            InstantAudioSpooler(spool_dir=spool_path, session_id=session_id).stop()
        except Exception:
            pass
        return None

    seg_sec = 15
    manifest_file = spool_path / "spool_manifest.json"
    if manifest_file.is_file():
        try:
            import json

            m_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            if int(m_data.get("segment_seconds", 0)) > 0:
                seg_sec = int(m_data["segment_seconds"])
        except Exception:
            pass

    return InstantAudioSpooler(
        spool_dir=spool_path,
        segment_seconds=seg_sec,
        session_id=session_id,
        mock_mode=mock_mode,
    )


