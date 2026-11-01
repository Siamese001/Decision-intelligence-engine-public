"""Audio measurement, session manifest, and chunk quarantine utilities for live recording."""
from __future__ import annotations

import contextlib
import json
import logging
import math
import re
import struct
import wave
from pathlib import Path
from typing import Any, Dict, Optional

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import EmptyAudioChunkError

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.audio_utils")


def get_audio_rms(wav_path: Path) -> float:
    """Calculates RMS amplitude for silence gating. Raises EmptyAudioChunkError on 0 frames."""
    p = Path(wav_path)
    if not p.exists() or p.stat().st_size < 100:
        raise EmptyAudioChunkError(f"WAV chunk {p.name} missing or below minimum header threshold.")
    with wave.open(str(p), "rb") as w:
        n = w.getnframes()
        if n == 0:
            raise EmptyAudioChunkError(f"WAV chunk {p.name} contains 0 audio frames.")
        data = w.readframes(n)
        samples = struct.unpack(f"{n}h", data)
        return math.sqrt(sum(s**2 for s in samples) / n)


def format_hh_mm_ss(sec: int | float) -> str:
    """Formats seconds into standard HH:MM:SS timestamp string."""
    sec_int = int(round(max(0, sec)))
    m, s = divmod(sec_int, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_chunk_time(sec: int | float) -> str:
    """Formats seconds into [HH:MM:SS] or [MM:SS] timestamp."""
    sec_int = int(round(max(0, sec)))
    m, s = divmod(sec_int, 60)
    h, m = divmod(m, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]" if h > 0 else f"[{m:02d}:{s:02d}]"



def _clean_resume_quarantine_markers(chunks_dir: Path) -> None:
    for candidate in (chunks_dir.parent / ".quarantine", chunks_dir / ".quarantine"):
        try:
            if candidate.is_file():
                candidate.unlink()
        except Exception:
            pass


def resolve_chunk_startup_state(
    chunks_dir: Path,
    session_tag: str,
    should_resume: bool,
) -> int:
    """Resolves chunk indexing and quarantines prior session chunks if not resuming."""
    if should_resume:
        _clean_resume_quarantine_markers(chunks_dir)
    existing_chunks = sorted(chunks_dir.glob("chunk_*.wav"))
    if not existing_chunks:
        return 0

    if should_resume:
        last_name = existing_chunks[-1].name
        m = re.search(r"chunk_(\d+)\.wav", last_name)
        if m:
            return int(m.group(1)) + 1
        return 0

    # Fresh meeting session: quarantine stale chunks from prior meetings
    quarantine_dir = chunks_dir / f".quarantine_{session_tag}"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    for cf in existing_chunks:
        try:
            cf.rename(quarantine_dir / cf.name)
        except Exception as e:
            logger.error("Failed to quarantine stale chunk %s: %s", cf.name, e)
            raise RuntimeError(f"Fail-Fast: Failed to quarantine stale chunk {cf.name}: {e}") from e
    return 0


def write_atomic_session_manifest(
    manifest_path: Path,
    payload: Dict[str, Any],
) -> None:
    """Writes session manifest atomically via temporary file replacement."""
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)


def init_live_transcript_file(
    transcript_md: Path,
    output_dir: Path,
    cache_dir: Path,
    meeting_name: str,
    meeting_date_display: str,
    project_name: str,
    attendees: list[dict[str, str]],
) -> None:
    """Initializes live markdown transcript file with metadata header if not present."""
    if transcript_md.exists():
        return
    import os
    from datetime import datetime

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    start_time_str = datetime.now().strftime("%I:%M %p ET")
    attendees_str = "\n".join(f"- **{a['name']}** ({a['role']}, {a['org']})" for a in attendees)
    effective_name = meeting_name or "Live Meeting"
    header = (
        f"# Live Meeting Transcript: {effective_name} | {meeting_date_display}\n\n"
        f"**Date:** {meeting_date_display}  \n"
        f"**Start Time:** {start_time_str}  \n"
        f"**Project:** `{project_name}`  \n"
        f"**Provenance:** `LIVE_PREVIEW (non-authoritative)`  \n"
        f"**Status:** `RECORDING_IN_PROGRESS`  \n\n"
        f"### Attendees\n{attendees_str}\n\n---\n\n## Live Feed\n\n"
    )
    with open(transcript_md, "w", encoding="utf-8") as f:
        f.write(header)
        f.flush()
        os.fsync(f.fileno())


def append_live_transcript_line(
    transcript_md: Path,
    time_str: str,
    spk: str,
    text: str,
) -> None:
    """Appends speaker turn to live transcript markdown file with fsync."""
    import os
    with open(transcript_md, "a", encoding="utf-8") as f:
        f.write(f"**{time_str}** {spk}: {text}\n\n")
        f.flush()
        os.fsync(f.fileno())


def append_live_transcript_pause(
    transcript_md: Path,
    start_time_str: str,
    end_time_str: str,
) -> None:
    """Appends explicit natural pause annotation to live transcript markdown file with fsync."""
    import os
    clean_start = start_time_str.strip("[]")
    clean_end = end_time_str.strip("[]")
    with open(transcript_md, "a", encoding="utf-8") as f:
        f.write(f"[Natural pause: {clean_start} – {clean_end} (no speech)]\n\n")
        f.flush()
        os.fsync(f.fileno())



def load_processed_chunk_files(segments_jsonl: Path) -> set[str]:
    """Extracts previously processed chunk filenames from segments.jsonl."""
    processed: set[str] = set()
    if not segments_jsonl.exists():
        return processed
    try:
        with open(segments_jsonl, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                if line.strip():
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError as je:
                        logger.error("Corrupted JSON line %d in %s: %s", line_no, segments_jsonl, je)
                        raise RuntimeError(f"Corrupted line {line_no} in segments JSONL '{segments_jsonl}': {je}") from je
                    cf = data.get("chunk_file")
                    if cf:
                        processed.add(cf)
    except OSError as e:
        logger.error("Failed to read segments JSONL %s: %s", segments_jsonl, e)
        raise RuntimeError(f"Failed to read segments JSONL '{segments_jsonl}': {e}") from e
    return processed


def _update_session_manifest(
    session_manifest: Optional[Path],
    captured_files: list[str],
    stopped_at: Optional[str] = None,
    wall_clock_duration_seconds: Optional[float] = None,
    session_end_time_display: Optional[str] = None,
    audio_starvation_events: Optional[list[dict]] = None,
) -> None:
    if not (session_manifest and session_manifest.is_file()):
        return
    import json

    try:
        manifest_data = json.loads(session_manifest.read_text(encoding="utf-8"))
        if captured_files:
            manifest_data["referenced_artifacts"] = captured_files
        if stopped_at:
            manifest_data["stopped_at"] = stopped_at
        if wall_clock_duration_seconds is not None:
            manifest_data["wall_clock_duration_seconds"] = wall_clock_duration_seconds
        if session_end_time_display:
            manifest_data["session_end_time_display"] = session_end_time_display
        if audio_starvation_events is not None:
            manifest_data["audio_starvation_events"] = audio_starvation_events
        write_atomic_session_manifest(session_manifest, manifest_data)
    except Exception:
        pass


def _write_fallback_collateral_drop_log(drop_log_path: Path, ref_dir: Path, captured_files: list[str]) -> None:
    if not (captured_files and not drop_log_path.exists()):
        return
    import hashlib
    import json
    drops = []
    for fname in captured_files:
        fpath = ref_dir / fname
        try:
            sha = hashlib.sha256(fpath.read_bytes()).hexdigest()
        except OSError:
            sha = ""
        drops.append({
            "filename": fname,
            "dropped_at_elapsed": "00:00:00",
            "sha256": sha,
            "attribution_mode": "COLLATERAL_REVIEWED",
        })
    with contextlib.suppress(OSError):
        drop_log_path.write_text(json.dumps(drops, indent=2, ensure_ascii=False), encoding="utf-8")


def _reconcile_manifest_duration(session_manifest: Optional[Path], metadata: Optional[Dict[str, Any]]) -> None:
    if not metadata or "wall_clock_duration_seconds" not in metadata:
        return
    if not session_manifest or not session_manifest.parent:
        return
    segments_jsonl = session_manifest.parent / "segments.jsonl"
    if not segments_jsonl.exists():
        return
    import json
    with open(segments_jsonl, "r", encoding="utf-8") as f:
        transcript_segments = [json.loads(line) for line in f if line.strip()]
    from dual_pipeline.audio_duration_gate import reconcile_transcript_coverage
    reconcile_transcript_coverage(metadata["wall_clock_duration_seconds"], transcript_segments)


def _get_captured_collateral_files(ref_dir: Path) -> list[str]:
    if not ref_dir.is_dir():
        return []
    return [
        f.name for f in sorted(ref_dir.glob("*"))
        if f.is_file() and not f.name.startswith(".")
    ]


def seal_live_transcript_footer(
    transcript_md: Path,
    total_chunks: int,
    session_manifest: Optional[Path] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Appends session paused timestamp and chunk total to transcript markdown, noting any captured artifacts."""
    if not transcript_md.exists():
        return
    import os
    from datetime import datetime

    end_time = (
        metadata.get("session_end_time_display")
        if metadata and metadata.get("session_end_time_display")
        else datetime.now().strftime("%I:%M %p ET")
    )

    _reconcile_manifest_duration(session_manifest, metadata)

    lines = [
        f"\n---\n*Session paused at {end_time}. Total chunks processed: {total_chunks}*",
        "[SESSION_SEALED]",
    ]

    ref_dir = transcript_md.parent / "referenced_artifacts"
    captured_files = _get_captured_collateral_files(ref_dir)
    if captured_files:
        lines.append(f"*Uploaded meeting collateral captured ({len(captured_files)} item{'s' if len(captured_files) > 1 else ''}): {', '.join(captured_files)}*")

    with open(transcript_md, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
        f.flush()
        os.fsync(f.fileno())

    stopped_at = metadata.get("stopped_at") if metadata else None
    wall_clock_dur = metadata.get("wall_clock_duration_seconds") if metadata else None
    _update_session_manifest(
        session_manifest,
        captured_files,
        stopped_at=stopped_at,
        wall_clock_duration_seconds=wall_clock_dur,
        session_end_time_display=end_time,
    )
    # Live transcript markdown is never autonomously rewritten at seal time (anti-autonomous attribution invariant)


def reconcile_post_session_attribution(transcript_md: Path, metadata: Dict[str, Any]) -> None:
    """Tombstone / No-op: Live transcript is preserved as non-authoritative preview without autonomous rewrites."""
    logger.debug("[ANTI-AUTONOMOUS-ATTRIBUTION] Skipping post-session live markdown rewrite; preserved as non-authoritative preview.")
    return


CONVERSATIONAL_BACKCHANNELS = {
    "yeah", "yes", "right", "okay", "ok", "sure", "uh-huh", "got it", "exactly", "cool", "fine", "no", "yep",
}


def _is_backchannel_repeat(norm: str, words: list[str], norm_history: list[str]) -> Optional[bool]:
    if norm in CONVERSATIONAL_BACKCHANNELS or (len(words) <= 2 and all(w in CONVERSATIONAL_BACKCHANNELS for w in words)):
        return len(norm_history) >= 2 and norm == norm_history[-1] == norm_history[-2]
    return None


def _is_jaccard_drift(words: list[str], last_history: str) -> bool:
    if len(words) < 5 or not last_history:
        return False
    prev_words = last_history.split()
    if len(prev_words) < 5:
        return False
    w_set, prev_set = set(words), set(prev_words)
    union = len(w_set | prev_set)
    return union > 0 and (len(w_set & prev_set) / union) >= 0.85


def is_cross_chunk_repetition(text: str, history: list[str]) -> bool:
    """Detects repetitive cross-chunk hallucination loops across recent utterances."""
    if not text or not history:
        return False
    norm = re.sub(r"[^\w\s]", "", text).lower().strip()
    words = norm.split()
    if not words:
        return False

    norm_history = [re.sub(r"[^\w\s]", "", h).lower().strip() for h in history[-6:]]
    if not norm_history:
        return False

    bc = _is_backchannel_repeat(norm, words, norm_history)
    if bc is not None:
        return bc

    if norm == norm_history[-1]:
        return True

    if len(words) <= 20 and norm_history[-4:].count(norm) >= 2:
        return True

    if ("discussing" in norm or "presentation" in norm) and any(
        ("discussing" in prev or "presentation" in prev) for prev in norm_history[-2:]
    ):
        return True

    return _is_jaccard_drift(words, norm_history[-1])


def generate_synthetic_speech_chunk(wav_path: Path, duration_sec: float = 15.0, sample_rate: int = 16000) -> None:
    """Generates a synthetic speech-simulated WAV chunk for mock and offline testing."""
    import math
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    n_samples = int(sample_rate * duration_sec)
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = bytearray()
        for i in range(n_samples):
            t = float(i) / sample_rate
            sample_val = int(4000.0 * math.sin(2.0 * math.pi * 220.0 * t))
            frames.extend(struct.pack("<h", sample_val))
        w.writeframes(frames)

