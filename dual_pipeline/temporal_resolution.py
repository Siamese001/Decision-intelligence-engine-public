"""
dual_pipeline/temporal_resolution.py

Temporal resolution and multi-meeting boundary detection engine:
1. Regex filename timestamp parsing (_120516.wav -> 12:05:16 PM)
2. Adjacent session manifest discovery (*_manifest.json)
3. Audio sample-accurate duration derivation (T_end = T_start + duration)
4. Multi-meeting boundary detection on silence gaps and header restarts
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilenameTimestamp:
    hours: int
    minutes: int
    seconds: int
    time_str: str
    period: str
    token: str

    @property
    def hour(self) -> int: return self.hours
    @property
    def minute(self) -> int: return self.minutes
    @property
    def second(self) -> int: return self.seconds
    def __getitem__(self, key: str) -> Any: return getattr(self, key)
    def get(self, key: str, default: Any = None) -> Any: return getattr(self, key, default)


@dataclass(frozen=True)
class ResolvedTime:
    hour: int
    minute: int
    second: int

    @property
    def hours(self) -> int: return self.hour
    @property
    def minutes(self) -> int: return self.minute
    @property
    def seconds(self) -> int: return self.second
    def __getitem__(self, idx: int) -> int: return (self.hour, self.minute, self.second)[idx]
    def __iter__(self) -> Iterator[int]: return iter((self.hour, self.minute, self.second))


class TemporalResolutionResult(tuple):
    """
    Two-tuple (display_window, time_filename) with rich metadata attributes.
    Supports unpacking: display, time_fn = resolved
    and attribute access: resolved.start_time, resolved.end_time, resolved.display_window
    """

    def __new__(
        cls,
        display_window: str,
        time_filename: str,
        start_time: Optional[Any] = None,
        end_time: Optional[Any] = None,
        duration_seconds: Optional[float] = None,
        resolution_source: str = "UNRESOLVED",
    ):
        instance = super().__new__(cls, (display_window, time_filename))
        instance.display_window = display_window
        instance.time_filename = time_filename
        instance.start_time = start_time
        instance.end_time = end_time
        instance.duration_seconds = duration_seconds
        instance.resolution_source = resolution_source
        return instance


def parse_filename_timestamp(filename_or_path: Union[str, Path]) -> Optional[FilenameTimestamp]:
    """
    Parses timestamp tokens from filename or path stem.
    Matches:
      - _120516.wav -> 12:05:16
      - _12_05_16.wav -> 12:05:16
      - _0915AM or _0915PM
    """
    name = Path(str(filename_or_path)).name

    # 1. Match _(\d{2})(_)?(\d{2})(_)?(\d{2}) e.g. master_120516.wav or session_12_05_16
    m = re.search(r"_(?:master_)?([01]\d|2[0-3])_?([0-5]\d)_?([0-5]\d)(?:\.|\b|_)", name)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        period = "AM" if h < 12 else "PM"
        h12 = 12 if (h % 12) == 0 else (h % 12)
        return FilenameTimestamp(
            hours=h,
            minutes=mn,
            seconds=s,
            time_str=f"{h12:02d}:{mn:02d} {period}",
            period=period,
            token=m.group(0),
        )

    # 2. Match _(\d{1,2})(\d{2})\s*([AP]M)
    m = re.search(r"_([01]?\d)([0-5]\d)\s*([AP]M)", name, re.IGNORECASE)
    if m:
        h12, mn, period = int(m.group(1)), int(m.group(2)), m.group(3).upper()
        if h12 > 12:
            return None
        h24 = (0 if h12 == 12 else h12) if period == "AM" else (12 if h12 == 12 else h12 + 12)
        return FilenameTimestamp(
            hours=h24,
            minutes=mn,
            seconds=0,
            time_str=f"{h12:02d}:{mn:02d} {period}",
            period=period,
            token=m.group(0),
        )

    return None


def read_adjacent_session_manifest(audio_or_transcript_path: Union[str, Path]) -> Optional[Dict[str, Any]]:
    """
    Scans directory adjacent to input file for session manifest (*_manifest.json).
    Matches based on filename tokens, exact path, or session metadata.
    """
    p = Path(audio_or_transcript_path).resolve()
    dirs_to_search = [p, p / ".cache", p.parent, p.parent / ".cache"] if p.is_dir() else [p.parent, p.parent / ".cache"]

    for parent in dirs_to_search:
        if not parent.is_dir():
            continue

        c1 = parent / f"{p.stem}_manifest.json"
        if c1.is_file():
            try:
                return json.loads(c1.read_text(encoding="utf-8"))
            except Exception:
                pass

        m_ts = re.search(r"(\d{6})", p.stem)
        ts_token = m_ts.group(1) if m_ts else None

        for mf in sorted(parent.glob("*_manifest.json")):
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                if (data.get("master_wav") and Path(data["master_wav"]).name == p.name) or (ts_token and ts_token in mf.name):
                    return data
                clean_audio, clean_mf = re.sub(r"^(?:00_)?(?:master_)?", "", p.stem), re.sub(r"^(?:00_)?(?:master_)?|_manifest$", "", mf.stem)
                if clean_mf in clean_audio or clean_audio in clean_mf:
                    return data
                return data
            except Exception:
                continue

        for generic in ("session_manifest.json", "metadata.json"):
            c_gen = parent / generic
            if c_gen.is_file():
                try:
                    data = json.loads(c_gen.read_text(encoding="utf-8"))
                    if isinstance(data, dict) and (data.get("started_at") or data.get("time") or data.get("session_time")):
                        return data
                except Exception:
                    pass

    return None


def calculate_end_time(
    start_or_h: Union[int, FilenameTimestamp, dt_time, datetime, Dict[str, Any]],
    m_or_duration: Union[int, float],
    start_s: Optional[int] = None,
    duration_seconds: Optional[float] = None,
) -> ResolvedTime:
    """Calculates T_end = T_start + duration_seconds in 24-hour clock."""
    if isinstance(start_or_h, int) and start_s is not None and duration_seconds is not None:
        start_h, start_m, dur, s = start_or_h, int(m_or_duration), float(duration_seconds), int(start_s)
    elif hasattr(start_or_h, "hours") and hasattr(start_or_h, "minutes"):
        start_h, start_m, s, dur = start_or_h.hours, start_or_h.minutes, getattr(start_or_h, "seconds", 0), float(m_or_duration)
    elif hasattr(start_or_h, "hour") and hasattr(start_or_h, "minute"):
        start_h, start_m, s, dur = start_or_h.hour, start_or_h.minute, getattr(start_or_h, "second", 0), float(m_or_duration)
    elif isinstance(start_or_h, dict):
        start_h = start_or_h.get("hours", start_or_h.get("hour", 0))
        start_m = start_or_h.get("minutes", start_or_h.get("minute", 0))
        s, dur = start_or_h.get("seconds", start_or_h.get("second", 0)), float(m_or_duration)
    else:
        raise ValueError(f"Unsupported start time input: {start_or_h}")

    total_seconds = start_h * 3600 + start_m * 60 + s + int(round(dur))
    return ResolvedTime(hour=(total_seconds // 3600) % 24, minute=(total_seconds % 3600) // 60, second=total_seconds % 60)


def format_time_window(
    start_or_h: Union[int, FilenameTimestamp, dt_time, datetime, ResolvedTime],
    end_or_m: Union[int, FilenameTimestamp, dt_time, datetime, ResolvedTime],
    end_h: Optional[int] = None,
    end_m: Optional[int] = None,
    tz: str = "ET",
    tz_name: Optional[str] = None,
) -> Tuple[str, str]:
    """Formats 24h start/end into (display_time, time_filename)."""
    zone = tz_name or tz or "ET"
    if isinstance(start_or_h, int) and isinstance(end_or_m, int) and end_h is not None and end_m is not None:
        sh, sm, eh, em = start_or_h, end_or_m, end_h, end_m
    else:
        sh = getattr(start_or_h, "hour", getattr(start_or_h, "hours", 0))
        sm = getattr(start_or_h, "minute", getattr(start_or_h, "minutes", 0))
        eh = getattr(end_or_m, "hour", getattr(end_or_m, "hours", 0))
        em = getattr(end_or_m, "minute", getattr(end_or_m, "minutes", 0))

    p_start, h12_start = ("AM" if sh < 12 else "PM"), (12 if (sh % 12) == 0 else (sh % 12))
    p_end, h12_end = ("AM" if eh < 12 else "PM"), (12 if (eh % 12) == 0 else (eh % 12))
    return f"{h12_start:02d}:{sm:02d} {p_start} - {h12_end:02d}:{em:02d} {p_end} {zone}", f"{h12_start:02d}{sm:02d}{p_start}_{h12_end:02d}{em:02d}{p_end}_{zone}"


def resolve_temporal_window(  # noqa: C901
    input_path: Optional[Union[str, Path]] = None,
    meta: Optional[Dict[str, Any]] = None,
    raw_text: str = "",
) -> TemporalResolutionResult:
    """
    Deterministically resolves temporal window from:
    1. Explicit metadata / CLI override
    2. Adjacent session manifest started_at / ended_at / duration
    3. Filename timestamp token + container duration derivation
    4. Text-embedded timestamps
    Returns TemporalResolutionResult, or unresolvable sentinel if no evidence found.
    """
    meta_dict = meta if meta is not None else {}
    tz = meta_dict.get("timezone") or "ET"

    # 1. Explicit override
    if meta_dict.get("time"):
        t_val = str(meta_dict["time"])
        t_fn = meta_dict.get("time_filename") or re.sub(r"[:\s\-\u2013]+", "", t_val)
        return TemporalResolutionResult(
            display_window=t_val,
            time_filename=t_fn,
            resolution_source="EXPLICIT_OVERRIDE",
        )

    # 2. Adjacent session manifest
    manifest_data = None
    if input_path:
        manifest_data = read_adjacent_session_manifest(input_path)
    if manifest_data:
        started_at = manifest_data.get("started_at")
        ended_at = manifest_data.get("ended_at")
        duration_sec = manifest_data.get("duration_seconds")
        if started_at:
            try:
                dt_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                try:
                    from zoneinfo import ZoneInfo
                    dt_start = dt_start.astimezone(ZoneInfo("America/New_York"))
                except Exception:
                    pass
                start_res = ResolvedTime(hour=dt_start.hour, minute=dt_start.minute, second=dt_start.second)

                if ended_at:
                    dt_end = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
                    try:
                        from zoneinfo import ZoneInfo
                        dt_end = dt_end.astimezone(ZoneInfo("America/New_York"))
                    except Exception:
                        pass
                    end_res = ResolvedTime(hour=dt_end.hour, minute=dt_end.minute, second=dt_end.second)
                    dur = (dt_end - dt_start).total_seconds()
                    disp, fn = format_time_window(start_res, end_res, tz=tz)
                    return TemporalResolutionResult(
                        display_window=disp,
                        time_filename=fn,
                        start_time=start_res,
                        end_time=end_res,
                        duration_seconds=dur,
                        resolution_source="SESSION_MANIFEST",
                    )
                else:
                    # If duration not declared in manifest, check audio container
                    dur = float(duration_sec) if duration_sec else None
                    if dur is None and input_path and Path(input_path).is_file():
                        try:
                            from dual_pipeline.audio_ingestion_core.container_validator import (
                                validate_audio_container_headers,
                            )
                            spec = validate_audio_container_headers(input_path)
                            dur = spec.declared_duration_seconds
                        except Exception as e:
                            logger.error("Failed to validate audio container headers for %s: %s", input_path, e)
                            raise RuntimeError(f"Fail-Fast: Failed to validate audio container headers for '{input_path}': {e}") from e

                    if dur and dur > 0:
                        end_res = calculate_end_time(start_res, dur)
                        disp, fn = format_time_window(start_res, end_res, tz=tz)
                        return TemporalResolutionResult(
                            display_window=disp,
                            time_filename=fn,
                            start_time=start_res,
                            end_time=end_res,
                            duration_seconds=dur,
                            resolution_source="SESSION_MANIFEST",
                        )
            except Exception as e:
                logger.error("Failed parsing adjacent session manifest: %s", e)
                raise RuntimeError(f"Fail-Fast: Corrupt session manifest: {e}") from e

    # 3. Filename timestamp token + Audio duration
    if input_path:
        p = Path(str(input_path))
        ts_info = parse_filename_timestamp(p)
        if ts_info:
            start_res = ResolvedTime(hour=ts_info.hours, minute=ts_info.minutes, second=ts_info.seconds)

            duration_sec = None
            if p.is_file() and p.suffix.lower() in (".wav", ".mp3", ".m4a", ".aac"):
                try:
                    from dual_pipeline.audio_ingestion_core.container_validator import (
                        validate_audio_container_headers,
                    )
                    spec = validate_audio_container_headers(p)
                    duration_sec = spec.declared_duration_seconds
                except Exception as e:
                    logger.error("Failed to validate audio container headers for %s: %s", p, e)
                    raise RuntimeError(f"Fail-Fast: Failed to validate audio container headers for '{p}': {e}") from e

            if not duration_sec or duration_sec <= 0:
                raise ValueError(
                    f"Fail-Fast: Cannot derive meeting duration for audio container '{p}'. "
                    f"Audio container duration derivation failed and no adjacent session manifest exists."
                )
            dur = duration_sec
            end_res = calculate_end_time(start_res, dur)
            disp, fn = format_time_window(start_res, end_res, tz=tz)
            return TemporalResolutionResult(
                display_window=disp,
                time_filename=fn,
                start_time=start_res,
                end_time=end_res,
                duration_seconds=duration_sec,
                resolution_source="FILENAME_TOKEN",
            )

    # 4. Search raw transcript text
    if raw_text:
        m_time = re.search(
            r"\b(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM)?(?:\s*(?:ET|EST|EDT|PT|CT|MT))?)\b",
            raw_text[:5000],
            re.IGNORECASE,
        )
        if m_time:
            found = m_time.group(1).strip()
            m_win = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)(?:\s*(ET|EST|EDT))?", found, re.I)
            if m_win:
                h1, m1, p1, h2, m2, p2, zone = m_win.groups()
                p1 = p1 or p2
                zone = zone or "ET"
                fn = f"{int(h1):02d}{m1}{p1.upper()}_{int(h2):02d}{m2}{p2.upper()}_{zone.upper()}"
            else:
                fn = re.sub(r"[:\s\-\u2013]+", "", found)
            return TemporalResolutionResult(
                display_window=found,
                time_filename=fn,
                resolution_source="RAW_TRANSCRIPT_TEXT",
            )
        m_start = re.search(r"\*\*Start Time:\*\*\s*(\d{1,2}):(\d{2})\s*(AM|PM)?(?:\s*(ET|EST|EDT))?", raw_text, re.I)
        m_end = re.search(r"\*Session (?:paused|ended|stopped) at\s*(\d{1,2}):(\d{2})\s*(AM|PM)?(?:\s*(ET|EST|EDT))?", raw_text, re.I)
        if m_start and m_end:
            h1, m1, p1, z1 = m_start.groups()
            h2, m2, p2, z2 = m_end.groups()
            p1 = p1 or p2 or "PM"
            p2 = p2 or p1
            zone = z1 or z2 or "ET"
            disp = f"{int(h1):02d}:{m1} {p1.upper()} - {int(h2):02d}:{m2} {p2.upper()} {zone.upper()}"
            fn = f"{int(h1):02d}{m1}{p1.upper()}_{int(h2):02d}{m2}{p2.upper()}_{zone.upper()}"
            return TemporalResolutionResult(
                display_window=disp,
                time_filename=fn,
                resolution_source="TRANSCRIPT_START_END_MARKERS",
            )

    return TemporalResolutionResult(
        display_window="TIME_UNRESOLVED",
        time_filename="TIME_UNRESOLVED",
        resolution_source="UNRESOLVED",
    )


def _parse_hh_mm_ss_to_seconds(ts: str) -> float:
    """Parses HH:MM:SS or MM:SS to seconds."""
    parts = ts.split(":")
    if len(parts) == 3:
        return float(parts[0]) * 3600.0 + float(parts[1]) * 60.0 + float(parts[2])
    elif len(parts) == 2:
        return float(parts[0]) * 60.0 + float(parts[1])
    return float(parts[0])


_INTRA_MEETING_SECTIONS = {
    "live feed", "transcript", "forensic transcript", "verbatim dialogue", "dialogue", "attendees", "attendee", "participants", "roster",
    "ambiguity lock", "verification register", "ambiguity lock register", "attribution summary", "overview", "notes", "context", "action items", "decisions",
    "collateral & context", "presented collateral", "artifacts", "recap", "discussion", "meeting notes", "key takeaways", "referenced-person grounding", "evidence locks", "semantic constraints",
}
_HEADER_PREFIXES = (
    "date", "time", "start time", "end time", "project", "status", "provenance", "platform",
    "attendees", "participants", "classification", "collateral", "reviewed", "meeting", "topic",
    "objective", "recorded", "source", "editorial", "additional",
)


def _is_boundary_marker(stripped: str) -> Tuple[bool, bool]:
    h2_cand = stripped[3:].strip().lower() if stripped.startswith("## ") else ""
    is_intra_h2 = any(s in h2_cand for s in _INTRA_MEETING_SECTIONS)
    is_header = stripped.startswith("# ") or (stripped.startswith("## ") and not is_intra_h2)
    is_dialogue = bool(
        re.match(r"^(?:\*\*)?\[\d{1,2}:\d{2}", stripped)
        or (
            not stripped.startswith(("- ", "* ", "+ ", "---", "#"))
            and not (stripped.startswith("**") and not re.match(r"^\*\*\[\d{1,2}:\d{2}", stripped))
            and not any(stripped.lower().startswith(f"{k}:") for k in _HEADER_PREFIXES)
            and ":" in stripped
            and not stripped.lower().startswith("date:")
        )
    )
    return is_header, is_dialogue


def detect_multi_meeting_boundaries(  # noqa: C901
    text: str, min_silence_gap_seconds: float = 300.0
) -> List[Dict[str, Any]]:
    """
    Detects boundaries of multiple merged meetings within a single transcript.
    Looks for:
    1. Silence pause markers: [PAUSE >= 300s] or [GAP >= 300s] or [Natural conversation pause: >= 300s]
    2. Interval silence markers: [HH:MM:SS - HH:MM:SS] with gap >= min_silence_gap_seconds
    3. Fresh meeting title headers (# Project Strategy Session) after dialogue turns
    Returns list of segment boundaries with start/end timestamps.
    """
    segments: List[Dict[str, Any]] = []
    lines = text.splitlines()

    current_segment_lines: List[str] = []
    segment_idx = 1
    dialogue_started = False
    pending_silence_boundary = False
    current_trigger = "INITIAL"

    def finalize_segment(next_trigger: str) -> None:
        nonlocal segment_idx, current_segment_lines, dialogue_started, pending_silence_boundary, current_trigger
        if not current_segment_lines:
            return

        seg_text = "\n".join(current_segment_lines)

        # Extract first and last turn timestamps
        timestamps = re.findall(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", seg_text)
        start_ts = timestamps[0] if timestamps else None
        end_ts = timestamps[-1] if timestamps else None

        segments.append({
            "segment_index": segment_idx,
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "text": seg_text,
            "line_count": len(current_segment_lines),
            "boundary_trigger": current_trigger,
        })
        segment_idx += 1
        current_segment_lines = []
        dialogue_started = False
        pending_silence_boundary = False
        current_trigger = next_trigger

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_segment_lines:
                current_segment_lines.append(line)
            continue

        # Check for explicit silence gap e.g. [Natural conversation pause: 333s]
        m_pause = re.search(
            r"\[(?:Natural conversation )?(?:PAUSE|SILENCE|GAP)(?::|\s+)?\s*(\d+(?:\.\d+)?)\s*s?\]",
            stripped,
            re.IGNORECASE,
        )
        is_silence_line = False
        if m_pause:
            pause_sec = float(m_pause.group(1))
            if pause_sec >= min_silence_gap_seconds:
                pending_silence_boundary = True
                is_silence_line = True

        # Check for interval pause e.g. [00:23:27 - 00:29:00]
        if not stripped.startswith("|"):
            m_interval = re.search(
                r"\[(\d{1,2}:\d{2}(?::\d{2})?)\s*[-–]\s*(\d{1,2}:\d{2}(?::\d{2})?)\]",
                stripped,
            )
            if m_interval:
                try:
                    t1 = _parse_hh_mm_ss_to_seconds(m_interval.group(1))
                    t2 = _parse_hh_mm_ss_to_seconds(m_interval.group(2))
                    if (t2 - t1) >= min_silence_gap_seconds:
                        pending_silence_boundary = True
                        is_silence_line = True
                except Exception:
                    pass

        if is_silence_line:
            current_segment_lines.append(line)
            continue

        is_header, is_dialogue = _is_boundary_marker(stripped)

        if dialogue_started and is_header:
            trigger = (
                "SILENCE_GAP_AND_TITLE_RESTART"
                if pending_silence_boundary
                else "HEADER_RESTART"
            )
            finalize_segment(trigger)
        elif dialogue_started and pending_silence_boundary and is_dialogue:
            finalize_segment("SILENCE_GAP")

        if is_dialogue:
            dialogue_started = True

        current_segment_lines.append(line)

    if current_segment_lines:
        finalize_segment("TERMINAL_END")

    return segments
