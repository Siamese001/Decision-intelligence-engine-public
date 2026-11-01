"""
dual_pipeline/integrations/calendar.py

Calendar integration module supporting:
- iCalendar (.ics / RFC 5545) parsing
- Google Calendar API event dictionary parsing
- Microsoft Graph /events dictionary parsing

Auto-populates meeting_name, date (MM/DD/YYYY), start/end times (ET), and attendee rosters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

EASTERN_TZ = ZoneInfo("America/New_York")


@dataclass
class CalendarMeetingMetadata:
    """Normalized metadata extracted from calendar events."""

    meeting_name: str
    date: str  # MM/DD/YYYY
    start_time_et: str  # e.g., "12:05 PM"
    end_time_et: str  # e.g., "01:05 PM"
    attendees: List[str] = field(default_factory=list)
    organizer: Optional[str] = None
    description: Optional[str] = None
    raw_location: Optional[str] = None

    @property
    def attendees_csv(self) -> str:
        return ", ".join(self.attendees)


def _iso_to_et(dt_str: str) -> tuple[str, str]:
    """Converts an ISO 8601 string to (MM/DD/YYYY, 'HH:MM AM/PM ET')."""
    clean_str = dt_str.replace("Z", "+00:00")
    dt = datetime.fromisoformat(clean_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    et_dt = dt.astimezone(EASTERN_TZ)
    return et_dt.strftime("%m/%d/%Y"), et_dt.strftime("%I:%M %p")


def parse_ics(content_or_path: str | Path) -> List[CalendarMeetingMetadata]:
    """Parses iCalendar content or file path into a list of CalendarMeetingMetadata."""
    if isinstance(content_or_path, Path) or (isinstance(content_or_path, str) and "\n" not in content_or_path and Path(content_or_path).exists()):
        text = Path(content_or_path).read_text(encoding="utf-8")
    else:
        text = str(content_or_path)

    events: List[CalendarMeetingMetadata] = []
    vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, flags=re.DOTALL)

    for block in vevent_blocks:
        summary_m = re.search(r"^SUMMARY:(.*?)$", block, flags=re.MULTILINE)
        summary = summary_m.group(1).strip() if summary_m else "Untitled Meeting"

        # Sanitize summary for meeting name standard (letters, digits, underscores)
        meeting_name = re.sub(r"[^\w\s-]", "", summary).strip().replace(" ", "_")

        dtstart_m = re.search(r"^DTSTART[^:]*:(.*?)$", block, flags=re.MULTILINE)
        dtend_m = re.search(r"^DTEND[^:]*:(.*?)$", block, flags=re.MULTILINE)

        date_str = datetime.now(EASTERN_TZ).strftime("%m/%d/%Y")
        start_time_et = "12:00 PM"
        end_time_et = "01:00 PM"

        if dtstart_m:
            raw_start = dtstart_m.group(1).strip()
            try:
                # Handle YYYYMMDDTHHMMSSZ or YYYYMMDDTHHMMSS
                if "T" in raw_start:
                    fmt = "%Y%m%dT%H%M%SZ" if raw_start.endswith("Z") else "%Y%m%dT%H%M%S"
                    dt_start = datetime.strptime(raw_start, fmt)
                    if not raw_start.endswith("Z"):
                        dt_start = dt_start.replace(tzinfo=EASTERN_TZ)
                    else:
                        dt_start = dt_start.replace(tzinfo=timezone.utc).astimezone(EASTERN_TZ)
                    date_str = dt_start.strftime("%m/%d/%Y")
                    start_time_et = dt_start.strftime("%I:%M %p")
            except Exception:
                pass

        if dtend_m:
            raw_end = dtend_m.group(1).strip()
            try:
                if "T" in raw_end:
                    fmt = "%Y%m%dT%H%M%SZ" if raw_end.endswith("Z") else "%Y%m%dT%H%M%S"
                    dt_end = datetime.strptime(raw_end, fmt)
                    if not raw_end.endswith("Z"):
                        dt_end = dt_end.replace(tzinfo=EASTERN_TZ)
                    else:
                        dt_end = dt_end.replace(tzinfo=timezone.utc).astimezone(EASTERN_TZ)
                    end_time_et = dt_end.strftime("%I:%M %p")
            except Exception:
                pass

        # Attendees
        attendees: List[str] = []
        for line in block.splitlines():
            if line.startswith("ATTENDEE"):
                cn_m = re.search(r'CN="?([^;":]+)"?', line, flags=re.IGNORECASE)
                mailto_m = re.search(r'mailto:([^\s;]+)', line, flags=re.IGNORECASE)
                name = cn_m.group(1).strip() if cn_m else (mailto_m.group(1).strip() if mailto_m else "")
                if name:
                    attendees.append(name)

        # Organizer
        organizer = None
        for line in block.splitlines():
            if line.startswith("ORGANIZER"):
                cn_m = re.search(r'CN="?([^;":]+)"?', line, flags=re.IGNORECASE)
                mailto_m = re.search(r'mailto:([^\s;]+)', line, flags=re.IGNORECASE)
                organizer = cn_m.group(1).strip() if cn_m else (mailto_m.group(1).strip() if mailto_m else None)
                break

        desc_m = re.search(r"^DESCRIPTION:(.*?)$", block, flags=re.MULTILINE)
        description = desc_m.group(1).strip() if desc_m else None

        events.append(
            CalendarMeetingMetadata(
                meeting_name=meeting_name,
                date=date_str,
                start_time_et=start_time_et,
                end_time_et=end_time_et,
                attendees=attendees,
                organizer=organizer,
                description=description,
            )
        )

    return events


def parse_google_calendar_event(event: Dict[str, Any]) -> CalendarMeetingMetadata:
    """Parses a Google Calendar API event resource."""
    summary = event.get("summary", "Untitled Meeting")
    meeting_name = re.sub(r"[^\w\s-]", "", summary).strip().replace(" ", "_")

    start_raw = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
    end_raw = event.get("end", {}).get("dateTime") or event.get("end", {}).get("date")

    date_str, start_time_et = _iso_to_et(start_raw) if start_raw and "T" in start_raw else (datetime.now(EASTERN_TZ).strftime("%m/%d/%Y"), "12:00 PM")
    _, end_time_et = _iso_to_et(end_raw) if end_raw and "T" in end_raw else (date_str, "01:00 PM")

    attendees = [
        att.get("displayName") or att.get("email", "")
        for att in event.get("attendees", [])
        if att.get("displayName") or att.get("email")
    ]

    organizer = event.get("organizer", {}).get("displayName") or event.get("organizer", {}).get("email")

    return CalendarMeetingMetadata(
        meeting_name=meeting_name,
        date=date_str,
        start_time_et=start_time_et,
        end_time_et=end_time_et,
        attendees=attendees,
        organizer=organizer,
        description=event.get("description"),
        raw_location=event.get("location"),
    )


def parse_ms_graph_event(event: Dict[str, Any]) -> CalendarMeetingMetadata:
    """Parses a Microsoft Graph /events resource."""
    subject = event.get("subject", "Untitled Meeting")
    meeting_name = re.sub(r"[^\w\s-]", "", subject).strip().replace(" ", "_")

    start_raw = event.get("start", {}).get("dateTime")
    end_raw = event.get("end", {}).get("dateTime")

    date_str, start_time_et = _iso_to_et(start_raw) if start_raw else (datetime.now(EASTERN_TZ).strftime("%m/%d/%Y"), "12:00 PM")
    _, end_time_et = _iso_to_et(end_raw) if end_raw else (date_str, "01:00 PM")

    attendees = [
        att.get("emailAddress", {}).get("name") or att.get("emailAddress", {}).get("address", "")
        for att in event.get("attendees", [])
        if att.get("emailAddress")
    ]

    organizer = event.get("organizer", {}).get("emailAddress", {}).get("name") or event.get("organizer", {}).get("emailAddress", {}).get("address")

    return CalendarMeetingMetadata(
        meeting_name=meeting_name,
        date=date_str,
        start_time_et=start_time_et,
        end_time_et=end_time_et,
        attendees=attendees,
        organizer=organizer,
        description=event.get("bodyPreview") or event.get("body", {}).get("content"),
        raw_location=event.get("location", {}).get("displayName"),
    )
