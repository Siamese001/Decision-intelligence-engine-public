"""
dual_pipeline/live_meeting_recorder_core/disaster_recovery.py

Layer 1: Live Recording Disaster Recovery and Reconstruction Engine.
Reconstructs unfinalized live recording sessions (interrupted by SIGKILL, power loss,
or container termination) into a validated master WAV, continuous transcript,
collateral drop log, and contract-valid session manifest.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from dual_pipeline.live_meeting_recorder_core.audio_chunk_validator import (
    AudioChunkValidator,
)
from dual_pipeline.live_meeting_recorder_core.audio_master_stitcher import (
    AudioMasterStitcher,
)
from dual_pipeline.live_meeting_recorder_core.audio_utils import format_chunk_time
from dual_pipeline.meeting_paths import (
    normalize_meeting_date,
    standardize_meeting_slug,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.disaster_recovery")


@dataclass
class RecoveryResult:
    """Outcome payload of an unfinalized session disaster recovery operation."""
    success: bool
    session_dir: Path
    master_wav: Optional[Path] = None
    manifest_path: Optional[Path] = None
    transcript_path: Optional[Path] = None
    collateral_log_path: Optional[Path] = None
    total_chunks: int = 0
    recovered_turns: int = 0
    recovery_rate: float = 0.0
    duration_seconds: float = 0.0
    error: Optional[str] = None


def _compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


class SessionRecoveryManager:
    """Reconstructs interrupted recording sessions from chunk artifacts and journals."""

    @classmethod
    def discover_chunks(cls, session_path: Path) -> List[Path]:
        """Finds all unique chunk_*.wav files sorted sequentially by index."""
        raw_chunks = list(session_path.glob("**/chunk_*.wav"))
        by_idx: Dict[int, Path] = {}
        for p in raw_chunks:
            idx = AudioMasterStitcher.extract_chunk_index(p)
            if idx >= 0 and (idx not in by_idx or p.stat().st_size > by_idx[idx].stat().st_size):
                by_idx[idx] = p

        valid_chunks: List[Path] = []
        for idx in sorted(by_idx.keys()):
            p = by_idx[idx]
            val = AudioChunkValidator.validate_chunk(p, min_bytes=44, allow_digital_zero=True)
            if val.is_valid:
                valid_chunks.append(p)
            else:
                logger.warning("[RECOVERY-SKIP] Skipping invalid chunk: %s (%s)", p.name, val.reason)
        return valid_chunks

    @classmethod
    def load_journal_events(cls, session_path: Path) -> List[Dict[str, Any]]:
        """Parses all journal events across any session_journal.jsonl files found."""
        events: List[Dict[str, Any]] = []
        seen_lines: Set[str] = set()
        for jp in session_path.glob("**/*journal.jsonl"):
            if not jp.is_file():
                continue
            with contextlib.suppress(Exception):
                for line in jp.read_text(encoding="utf-8").splitlines():
                    clean_line = line.strip()
                    if clean_line and clean_line not in seen_lines:
                        seen_lines.add(clean_line)
                        with contextlib.suppress(Exception):
                            events.append(json.loads(clean_line))
        return events

    @classmethod
    def load_existing_manifest(cls, session_path: Path) -> Dict[str, Any]:
        """Loads metadata from existing manifest JSON files if present."""
        for mp in session_path.glob("**/*manifest.json"):
            if mp.is_file():
                with contextlib.suppress(Exception):
                    data = json.loads(mp.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        return data
        return {}

    @classmethod
    def reconstruct_collateral(
        cls, session_path: Path, target_dir: Path, journal_events: List[Dict[str, Any]],
    ) -> Optional[Path]:
        """Reconstructs collateral_drop_log.json from referenced_artifacts/ and journal."""
        ref_dirs = list(session_path.glob("**/referenced_artifacts")) or [target_dir / "referenced_artifacts"]
        collateral_files: List[Path] = []
        for rd in ref_dirs:
            if rd.is_dir():
                collateral_files.extend([f for f in rd.iterdir() if f.is_file() and not f.name.startswith(".")])

        if not collateral_files:
            return None

        journal_drops = {
            ev.get("target_filename") or ev.get("filename"): ev
            for ev in journal_events if ev.get("event") == "collateral_dropped"
        }
        drops_list: List[Dict[str, Any]] = []
        seen_names: Set[str] = set()
        for f in sorted(collateral_files, key=lambda x: x.name):
            if f.name in seen_names:
                continue
            seen_names.add(f.name)
            j_entry = journal_drops.get(f.name, {})
            drops_list.append({
                "filename": f.name,
                "dropped_at_elapsed": j_entry.get("dropped_at_elapsed", "00:00:00"),
                "sha256": _compute_file_sha256(f),
                "attribution_mode": "COLLATERAL_REVIEWED",
            })

        log_path = target_dir / "collateral_drop_log.json"
        tmp_log = log_path.with_suffix(".tmp")
        tmp_log.write_text(json.dumps(drops_list, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_log, log_path)
        return log_path

    @classmethod
    def _read_segments_file(cls, sp: Path, segments: Dict[int, Dict[str, Any]]) -> None:
        if not sp.is_file():
            return
        with contextlib.suppress(Exception):
            for line in sp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    seg = json.loads(line)
                    if (c_idx := seg.get("chunk_idx")) is not None:
                        segments[int(c_idx)] = seg

    @classmethod
    def _collect_existing_segments(
        cls,
        session_path: Path,
        journal_events: List[Dict[str, Any]],
    ) -> Dict[int, Dict[str, Any]]:
        existing_segments: Dict[int, Dict[str, Any]] = {}
        for sp in session_path.glob("**/*segments.jsonl"):
            cls._read_segments_file(sp, existing_segments)

        for ev in journal_events:
            if (
                ev.get("event") == "chunk_transcribed"
                and (c_idx := ev.get("chunk_idx")) is not None
                and int(c_idx) not in existing_segments
            ):
                existing_segments[int(c_idx)] = ev
        return existing_segments

    @classmethod
    def _build_recovered_segments(
        cls,
        valid_chunks: List[Path],
        existing_segments: Dict[int, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        cumulative_sec = 0.0
        recovered_list: List[Dict[str, Any]] = []
        for chunk_p in valid_chunks:
            c_idx = AudioMasterStitcher.extract_chunk_index(chunk_p)
            dur = 5.0
            with contextlib.suppress(Exception), wave.open(str(chunk_p), "rb") as w:
                dur = w.getnframes() / float(w.getframerate())

            existing = existing_segments.get(c_idx)
            if existing:
                seg = existing
            else:
                seg = {
                    "chunk_idx": c_idx,
                    "timestamp": format_chunk_time(int(round(cumulative_sec))),
                    "seconds": int(round(cumulative_sec)),
                    "duration": round(dur, 2),
                    "text": "[UNTRANSCRIBED]",
                    "chunk_file": chunk_p.name,
                    "wav_file": chunk_p.name,
                    "speaker": "Speaker",
                    "is_final": True,
                    "status": "UNTRANSCRIBED",
                }
            recovered_list.append(seg)
            cumulative_sec += dur

        recovered_list.sort(key=lambda s: int(s.get("chunk_idx", 0)))
        return recovered_list

    @classmethod
    def reconstruct_segments(
        cls,
        valid_chunks: List[Path],
        session_path: Path,
        journal_events: List[Dict[str, Any]],
        target_segments_path: Path,
    ) -> Tuple[List[Dict[str, Any]], float]:
        """Reconstructs complete segments JSONL and calculates turn recovery rate."""
        existing_segments = cls._collect_existing_segments(session_path, journal_events)
        recovered_list = cls._build_recovered_segments(valid_chunks, existing_segments)

        target_segments_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_seg = target_segments_path.with_suffix(".tmp")
        with open(tmp_seg, "w", encoding="utf-8") as f_out:
            for s in recovered_list:
                f_out.write(json.dumps(s) + "\n")
        os.replace(tmp_seg, target_segments_path)

        total_captured = len(valid_chunks)
        transcribed_count = sum(
            1 for s in recovered_list
            if s.get("status") != "UNTRANSCRIBED"
            and s.get("text")
            and s.get("text") != "[UNTRANSCRIBED]"
            and not s.get("text", "").startswith("[Recovered audio dialogue")
        )
        recovery_rate = 1.0 if total_captured == 0 else min(1.0, transcribed_count / float(total_captured))
        return recovered_list, recovery_rate

    @classmethod
    def reconstruct_transcript(
        cls,
        segments: List[Dict[str, Any]],
        meeting: str,
        date: str,
        project: str,
        valid_chunks_count: int,
        duration_sec: float,
        recovery_rate: float,
        master_wav_name: str,
        target_transcript_path: Path,
    ) -> Path:
        lines = [
            f"# Live Meeting Transcript: {meeting or 'Live Meeting'} | {date}", "",
            f"- **Date:** {date}", f"- **Project:** {project}",
            "- **Status:** RECOVERED (Post-Crash Reconstruction)", "",
            "## Forensic Dialogue Feed", "",
        ]
        for s in segments:
            t_str = s.get("timestamp") or format_chunk_time(s.get("seconds", 0))
            if not t_str.startswith("["):
                t_str = f"[{t_str}]"
            text = s.get("text", "")
            if text == "[UNTRANSCRIBED]" or s.get("status") == "UNTRANSCRIBED":
                lines.append(f"{t_str} [UNTRANSCRIBED CHUNK {s.get('chunk_idx')}]\n")
            else:
                lines.append(f"{t_str} {s.get('speaker', 'Speaker')}: {text}\n")

        status_str = "DISASTER_RECOVERY_SUCCESSFUL" if recovery_rate >= 1.0 else "DISASTER_RECOVERY_PARTIAL"
        lines.extend([
            "---", "", "## Disaster Recovery Seal", "",
            f"- **Recovery Status:** {status_str}",
            f"- **Total Chunks Recovered:** {valid_chunks_count} / {valid_chunks_count}",
            f"- **Audio Duration:** {duration_sec:.2f}s",
            f"- **Dialogue Turn Recovery Rate:** {recovery_rate * 100:.1f}%",
            f"- **Master WAV Stitched:** {master_wav_name}",
            f"- **Reconstructed At:** {datetime.now().isoformat()}", "",
        ])
        target_transcript_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_tr = target_transcript_path.with_suffix(".tmp")
        tmp_tr.write_text("\n".join(lines), encoding="utf-8")
        os.replace(tmp_tr, target_transcript_path)
        return target_transcript_path

    @classmethod
    def recover_session(
        cls,
        session_dir: Path | str,
        output_dir: Optional[Path | str] = None,
        project: Optional[str] = None,
        meeting: Optional[str] = None,
        date: Optional[str] = None,
    ) -> RecoveryResult:
        """Executes disaster recovery on an unfinalized session directory."""
        s_path = Path(session_dir).resolve()
        if not s_path.is_dir():
            return RecoveryResult(success=False, session_dir=s_path, error=f"Directory does not exist: {s_path}")

        valid_chunks = cls.discover_chunks(s_path)
        if not valid_chunks:
            return RecoveryResult(success=False, session_dir=s_path, error=f"No valid audio chunks found in {s_path}")

        journal_events = cls.load_journal_events(s_path)
        manifest_meta = cls.load_existing_manifest(s_path)
        start_meta = next((ev for ev in journal_events if ev.get("event") == "session_start"), {})

        resolved_proj = project or manifest_meta.get("project") or start_meta.get("project") or "live_recovery"
        resolved_meet = meeting or manifest_meta.get("meeting") or start_meta.get("meeting") or s_path.name
        raw_date = date or manifest_meta.get("date") or start_meta.get("date") or datetime.now().strftime("%m/%d/%Y")

        try:
            date_dirname, resolved_date = normalize_meeting_date(raw_date)
        except Exception:
            now = datetime.now()
            date_dirname, resolved_date = now.strftime("%m-%d-%Y"), now.strftime("%m/%d/%Y")

        clean_slug = standardize_meeting_slug(resolved_meet)

        t_dir = Path(output_dir).resolve() if output_dir else s_path
        t_dir.mkdir(parents=True, exist_ok=True)

        master_wav_path = t_dir / f"00_{date_dirname}_{clean_slug}_master_recovered.wav"
        manifest_path = t_dir / f"00_{date_dirname}_{clean_slug}_manifest.json"
        segments_path = t_dir / f"01_{date_dirname}_{clean_slug}_segments.jsonl"
        transcript_path = t_dir / f"01_{date_dirname}_{clean_slug}_live_transcript.md"

        stitch_ok, stitched_count, total_dur = AudioMasterStitcher.stitch_chunk_files(valid_chunks, master_wav_path)
        if not stitch_ok:
            return RecoveryResult(success=False, session_dir=s_path, error="AudioMasterStitcher failed to stitch chunks.")

        collateral_log = cls.reconstruct_collateral(s_path, t_dir, journal_events)
        segments, recovery_rate = cls.reconstruct_segments(valid_chunks, s_path, journal_events, segments_path)

        cls.reconstruct_transcript(
            segments=segments, meeting=resolved_meet, date=resolved_date, project=resolved_proj,
            valid_chunks_count=len(valid_chunks), duration_sec=total_dur, recovery_rate=recovery_rate,
            master_wav_name=master_wav_path.name, target_transcript_path=transcript_path,
        )

        transcribed_turns = sum(
            1 for s in segments
            if s.get("status") != "UNTRANSCRIBED"
            and s.get("text")
            and s.get("text") != "[UNTRANSCRIBED]"
            and not s.get("text", "").startswith("[Recovered audio dialogue")
        )

        raw_att = manifest_meta.get("attendees") or start_meta.get("attendees")
        from dual_pipeline.live_meeting_recorder_core.attendees import parse_attendees
        resolved_attendees = parse_attendees(raw_att, project_name=resolved_proj) if raw_att else []

        recovery_status = "RECOVERED" if recovery_rate >= 1.0 else "RECOVERED_PARTIAL"
        manifest_data = {
            **manifest_meta, "project": resolved_proj, "meeting": resolved_meet, "date": resolved_date,
            "date_dirname": date_dirname, "clean_slug": clean_slug, "status": recovery_status,
            "is_recovered": True, "recovered_at": datetime.now().isoformat(), "total_chunks": len(valid_chunks),
            "valid_chunks_count": len(valid_chunks), "stitched_chunks_count": stitched_count,
            "total_duration_seconds": round(total_dur, 2), "recovery_rate": round(recovery_rate, 4),
            "master_wav": str(master_wav_path), "segments_jsonl": str(segments_path),
            "transcript_md": str(transcript_path), "collateral_drop_log": str(collateral_log) if collateral_log else None,
            "captured_files": [c.name for c in valid_chunks],
            "attendees": resolved_attendees,
        }
        tmp_mf = manifest_path.with_suffix(".tmp")
        tmp_mf.write_text(json.dumps(manifest_data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_mf, manifest_path)

        logger.info(
            "Disaster recovery successful for %s: %d chunks (%0.2fs), recovery_rate=%0.2f%%",
            clean_slug, len(valid_chunks), total_dur, recovery_rate * 100.0,
        )
        return RecoveryResult(
            success=True, session_dir=s_path, master_wav=master_wav_path, manifest_path=manifest_path,
            transcript_path=transcript_path, collateral_log_path=collateral_log, total_chunks=len(valid_chunks),
            recovered_turns=transcribed_turns, recovery_rate=recovery_rate, duration_seconds=total_dur,
        )


def recover_live_session(
    session_dir: Path | str,
    output_dir: Optional[Path | str] = None,
    project: Optional[str] = None,
    meeting: Optional[str] = None,
    date: Optional[str] = None,
) -> RecoveryResult:
    """Convenience functional wrapper around SessionRecoveryManager.recover_session."""
    return SessionRecoveryManager.recover_session(
        session_dir=session_dir, output_dir=output_dir, project=project, meeting=meeting, date=date,
    )
