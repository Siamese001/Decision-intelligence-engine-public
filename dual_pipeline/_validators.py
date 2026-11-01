"""Reusable input-validation helpers for the NEE dual-pipeline runtime.

Every helper accepts an ``error_type`` keyword to match the caller's
domain-specific exception class.  This keeps validation DRY without
forcing a single exception hierarchy on all subsystems.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Text / string validators
# ---------------------------------------------------------------------------

def require_text(
    value: object,
    *,
    field: str,
    error_type: type[Exception] = ValueError,
    allow_empty: bool = False,
) -> str:
    """Validate *value* is a string and return it stripped.

    Raises *error_type* when validation fails.  With ``allow_empty=True``
    an empty (but still ``str``) value is accepted.
    """
    if not isinstance(value, str):
        raise error_type(f"{field} must be text")
    result = value.strip()
    if not result and not allow_empty:
        raise error_type(f"{field} must be nonblank text")
    return result


def require_sha256(
    value: object,
    *,
    field: str,
    error_type: type[Exception] = ValueError,
) -> str:
    """Validate *value* is a lowercase 64-char hex SHA-256 digest."""
    rendered = require_text(value, field=field, error_type=error_type)
    if len(rendered) != 64 or any(
        character not in "0123456789abcdef" for character in rendered
    ):
        raise error_type(f"{field} must be a lowercase SHA-256 digest")
    return rendered


def require_positive_int(
    value: Any,
    *,
    label: str,
    error_type: type[Exception] = ValueError,
    allow_zero: bool = False,
) -> int:
    """Validate *value* is an integer ≥ 1 (or ≥ 0 with ``allow_zero``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise error_type(f"{label} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise error_type(f"{label} must be at least {minimum}")
    return value


def require_strict_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    label: str,
    error_type: type[Exception] = ValueError,
) -> None:
    """Assert *value* contains exactly the *required* keys."""
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        raise error_type(
            f"{label} keys are invalid; missing={missing}, unknown={unknown}"
        )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse_iso_time(
    value: object,
    *,
    field: str,
    error_type: type[Exception] = ValueError,
    allow_none: bool = False,
) -> datetime | None:
    """Parse an ISO-8601 timestamp string to a UTC-aware ``datetime``."""
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise error_type(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise error_type(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time_text(value: datetime | None) -> str | None:
    """Render a ``datetime`` as a canonical Z-suffixed ISO-8601 string."""
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    """Check whether *table* exists in the SQLite database."""
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def sql_placeholders(values: list[str] | list[Any]) -> str:
    """Return a comma-separated ``?`` placeholder string for *values*."""
    if not values:
        raise ValueError("cannot build SQL placeholders for an empty list")
    return ",".join("?" for _ in values)


def row_hash(
    row: sqlite3.Row | dict[str, Any] | None,
    *,
    zero_hash: str,
    digest_fn: Any,
) -> str:
    """Return the canonical digest of *row*, or *zero_hash* for ``None``."""
    return zero_hash if row is None else digest_fn(dict(row))
