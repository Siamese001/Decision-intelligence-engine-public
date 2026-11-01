"""
dual_pipeline/temporal_ssot.py

Canonical Single Source of Truth (SSOT) for temporal representation across all pipeline layers.

Invariants (Rule 5 & Master Constraint C-07):
- Tier 1 (Database & Contract SSOT): ISO-8601 (YYYY-MM-DDTHH:MM:SSZ or YYYY-MM-DD).
- Tier 2 (Human Artifact & Projection SSOT): MM/DD/YYYY (Headers, recaps, observed_at, stale_after).
- Tier 3 (Filesystem Path SSOT): MM-DD-YYYY (Directory paths under output/<project>/<meeting_date>/).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Union

# Regex matchers for strict validation
RE_ISO8601 = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$")
RE_HUMAN_DATE = re.compile(r"^(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(\d{4})$")
RE_PATH_DATE = re.compile(r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-(\d{4})$")


class TemporalSSOT:
    """Canonical temporal conversions and strict schema validation."""

    @staticmethod
    def parse_to_date(val: Union[str, date, datetime]) -> date:
        """Parse any valid representation into a datetime.date object."""
        if isinstance(val, datetime):
            return val.date()
        if isinstance(val, date):
            return val

        str_val = str(val).strip()
        # Try MM/DD/YYYY
        m_human = RE_HUMAN_DATE.match(str_val)
        if m_human:
            month, day, year = int(m_human.group(1)), int(m_human.group(2)), int(m_human.group(3))
            return date(year, month, day)

        # Try MM-DD-YYYY
        m_path = RE_PATH_DATE.match(str_val)
        if m_path:
            month, day, year = int(m_path.group(1)), int(m_path.group(2)), int(m_path.group(3))
            return date(year, month, day)

        # Try ISO-8601
        if "T" in str_val:
            try:
                clean_iso = str_val.replace("Z", "+00:00")
                return datetime.fromisoformat(clean_iso).date()
            except Exception:
                pass
        try:
            return date.fromisoformat(str_val)
        except Exception:
            pass

        raise ValueError(
            f"Invalid date format '{val}'. Must be ISO-8601 (YYYY-MM-DD), "
            f"Human (MM/DD/YYYY), or Filesystem (MM-DD-YYYY)."
        )

    @classmethod
    def to_iso8601(cls, val: Union[str, date, datetime], include_time: bool = False) -> str:
        """Tier 1: Convert to canonical ISO-8601 string for databases and contracts."""
        d = cls.parse_to_date(val)
        if include_time:
            dt = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
        return d.isoformat()

    @classmethod
    def to_human_date(cls, val: Union[str, date, datetime]) -> str:
        """Tier 2: Convert to MM/DD/YYYY for human Markdown artifacts and Output 03 observed_at."""
        d = cls.parse_to_date(val)
        return f"{d.month:02d}/{d.day:02d}/{d.year:04d}"

    @classmethod
    def to_path_date(cls, val: Union[str, date, datetime]) -> str:
        """Tier 3: Convert to MM-DD-YYYY for directory paths (output/<project>/<MM-DD-YYYY>/)."""
        d = cls.parse_to_date(val)
        return f"{d.month:02d}-{d.day:02d}-{d.year:04d}"

    @staticmethod
    def validate_human_date(val: str) -> bool:
        """Verify string strictly matches MM/DD/YYYY."""
        return bool(RE_HUMAN_DATE.match(str(val).strip()))

    @staticmethod
    def validate_path_date(val: str) -> bool:
        """Verify string strictly matches MM-DD-YYYY."""
        return bool(RE_PATH_DATE.match(str(val).strip()))

    @staticmethod
    def validate_iso8601(val: str) -> bool:
        """Verify string strictly matches ISO-8601."""
        return bool(RE_ISO8601.match(str(val).strip()))
