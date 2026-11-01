"""
dual_pipeline/canonical_pipeline/u0_time_hydration.py

Time metadata extraction, session tag formatting, and container time hydration.
"""
from __future__ import annotations

import contextlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _format_session_tag_time(session_tag: str) -> Optional[str]:
    """Converts 6-digit HHMMSS timestamp (e.g. '163149') into human-readable 12h time."""
    if not session_tag or len(session_tag) < 4 or not session_tag[:4].isdigit():
        return None
    try:
        h24 = int(session_tag[:2])
        m = int(session_tag[2:4])
        period = "PM" if h24 >= 12 else "AM"
        h12 = h24 - 12 if h24 > 12 else (12 if h24 == 0 else h24)
        end_m = (m + 30) % 60
        end_h24 = h24 + (1 if m + 30 >= 60 else 0)
        end_period = "PM" if end_h24 >= 12 else "AM"
        end_h12 = end_h24 - 12 if end_h24 > 12 else (12 if end_h24 == 0 else end_h24)
        return f"{h12:02d}:{m:02d} {period} - {end_h12:02d}:{end_m:02d} {end_period} ET"
    except Exception:
        return None


def _format_iso_window(started_at: str, stopped_at: str) -> Optional[Tuple[str, str]]:
    try:
        dt_start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        dt_stop = datetime.fromisoformat(stopped_at.replace("Z", "+00:00"))
        try:
            from zoneinfo import ZoneInfo
            et_tz = ZoneInfo("America/New_York")
            dt_start = dt_start.astimezone(et_tz)
            dt_stop = dt_stop.astimezone(et_tz)
        except Exception:
            pass
        disp = f"{dt_start.strftime('%I:%M %p')} - {dt_stop.strftime('%I:%M %p')} ET"
        fn = f"{dt_start.strftime('%I%M%p')}_{dt_stop.strftime('%I%M%p')}_ET"
        return disp, fn
    except Exception:
        return None


def _hydrate_time_from_container(
    metadata: Dict[str, Any], manifest_data: Dict[str, Any], search_dirs: List[Path]
) -> bool:
    if metadata.get("time"):
        if not metadata.get("time_filename"):
            m_win = re.search(
                r"(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)(?:\s*(ET|EST|EDT))?",
                str(metadata["time"]),
                re.I,
            )
            if m_win:
                h1, m1, p1, h2, m2, p2, zone = m_win.groups()
                metadata["time_filename"] = (
                    f"{int(h1):02d}{m1}{(p1 or p2).upper()}_{int(h2):02d}{m2}{p2.upper()}_{(zone or 'ET').upper()}"
                )
            else:
                metadata["time_filename"] = re.sub(r"[:\s\-\u2013]+", "", str(metadata["time"]))
        return False

    started_at = manifest_data.get("started_at")
    stopped_at = manifest_data.get("stopped_at") or manifest_data.get("ended_at")
    if started_at and stopped_at:
        win = _format_iso_window(str(started_at), str(stopped_at))
        if win:
            metadata["time"], tag = win
            metadata.setdefault("time_filename", tag)
            return True

    direct_time = (
        manifest_data.get("time")
        or manifest_data.get("session_time")
        or manifest_data.get("session_end_time_display")
    )
    if direct_time and isinstance(direct_time, str) and not (direct_time.isdigit() and len(direct_time) == 6):
        metadata["time"] = str(direct_time).strip()
        m_win = re.search(
            r"(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)(?:\s*(ET|EST|EDT))?",
            direct_time,
            re.I,
        )
        if m_win:
            h1, m1, p1, h2, m2, p2, zone = m_win.groups()
            p1 = p1 or p2
            zone = zone or "ET"
            metadata.setdefault(
                "time_filename",
                f"{int(h1):02d}{m1}{p1.upper()}_{int(h2):02d}{m2}{p2.upper()}_{zone.upper()}",
            )
        elif manifest_data.get("time_filename"):
            metadata.setdefault("time_filename", manifest_data["time_filename"])
        return True

    time_cand = direct_time or manifest_data.get("session_tag")
    if time_cand and str(time_cand).isdigit() and len(str(time_cand)) == 6:
        formatted = _format_session_tag_time(str(time_cand))
        if formatted:
            metadata["time"] = formatted
            metadata.setdefault("time_filename", str(time_cand))
            return True
    if started_at:
        with contextlib.suppress(Exception):
            dt_s = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            try:
                from zoneinfo import ZoneInfo
                dt_s = dt_s.astimezone(ZoneInfo("America/New_York"))
            except Exception:
                pass
            tag = dt_s.strftime("%H%M%S")
            formatted = _format_session_tag_time(tag)
            if formatted:
                metadata["time"] = formatted
                metadata.setdefault("time_filename", tag)
                return True
    for s_dir in search_dirs:
        for t_file in sorted(s_dir.glob("01_*.md")):
            with contextlib.suppress(Exception):
                txt = t_file.read_text(encoding="utf-8")
                m_t = re.search(
                    r"\*\*Time:\*\*\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?\s*[-–]\s*\d{1,2}:\d{2}\s*(?:AM|PM)(?:\s*ET)?)",
                    txt,
                    re.I,
                )
                if m_t:
                    t_val = m_t.group(1).strip()
                    metadata["time"] = t_val
                    m_win = re.search(
                        r"(\d{1,2}):(\d{2})\s*(AM|PM)?\s*[-–]\s*(\d{1,2}):(\d{2})\s*(AM|PM)",
                        t_val,
                        re.I,
                    )
                    if m_win:
                        h1, m1, p1, h2, m2, p2 = m_win.groups()
                        p1 = p1 or p2
                        metadata.setdefault(
                            "time_filename",
                            f"{int(h1):02d}{m1}{p1.upper()}_{int(h2):02d}{m2}{p2.upper()}_ET",
                        )
                    return True
                m_start = re.search(
                    r"\*\*Start Time:\*\*\s*(\d{1,2}):(\d{2})\s*(AM|PM)?(?:\s*ET)?",
                    txt,
                    re.I,
                )
                m_end = re.search(
                    r"\*Session (?:paused|ended|stopped) at\s*(\d{1,2}):(\d{2})\s*(AM|PM)?(?:\s*ET)?",
                    txt,
                    re.I,
                )
                if m_start and m_end:
                    h1, m1, p1 = m_start.groups()
                    h2, m2, p2 = m_end.groups()
                    p1 = p1 or p2 or "PM"
                    p2 = p2 or p1
                    metadata["time"] = f"{int(h1):02d}:{m1} {p1.upper()} - {int(h2):02d}:{m2} {p2.upper()} ET"
                    metadata.setdefault(
                        "time_filename",
                        f"{int(h1):02d}{m1}{p1.upper()}_{int(h2):02d}{m2}{p2.upper()}_ET",
                    )
                    return True
        for wav_file in s_dir.glob("00_*_master*.wav"):
            m_tag = re.search(r"_(\d{6})(?:_master)?\.wav$", wav_file.name)
            if m_tag and (formatted := _format_session_tag_time(m_tag.group(1))):
                metadata["time"] = formatted
                metadata.setdefault("time_filename", m_tag.group(1))
                return True
        m_slug = re.search(r"(\d{1,2})(\d{2})(am|pm)_et", s_dir.name.lower())
        if m_slug:
            h, m_val, ampm = int(m_slug.group(1)), int(m_slug.group(2)), m_slug.group(3)
            h24 = (0 if h == 12 else h) if ampm == "am" else (12 if h == 12 else h + 12)
            tag = f"{h24:02d}{m_val:02d}00"
            if formatted := _format_session_tag_time(tag):
                metadata["time"] = formatted
                metadata.setdefault("time_filename", tag)
                return True
    return False


__all__ = [
    "_format_session_tag_time",
    "_format_iso_window",
    "_hydrate_time_from_container",
]
