"""Graph assertion temporal freshness calculation and gating."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from storage.database import create_sqlite_conn, sqlite3

from ..temporal_ssot import TemporalSSOT


class GraphFreshnessError(RuntimeError):
    """Raised when knowledge graph freshness drops below strict operational threshold."""

    pass


def _is_assertion_stale(raw_stale: Any, as_of: date) -> bool:
    """Determine whether an assertion stale_after date has passed."""
    if raw_stale is None or str(raw_stale).strip() == "":
        return False
    try:
        stale_d = TemporalSSOT.parse_to_date(raw_stale)
        return stale_d < as_of
    except Exception:
        # Malformed stale_after treated as unverified/stale
        return True


def _resolve_target_connection(
    target: Path | str | sqlite3.Connection, fail_closed: bool
) -> Tuple[Optional[sqlite3.Connection], bool, Optional[dict[str, Any]]]:
    """Resolve database connection from target or path."""
    if isinstance(target, sqlite3.Connection):
        return target, False, None
    p = Path(target)
    if not p.is_file():
        res = {
            "status": "ERROR",
            "reason": f"Graph database file not found: {p}",
            "freshness_ratio": 0.0,
        }
        if fail_closed:
            raise GraphFreshnessError(res["reason"])
        return None, False, res
    conn = create_sqlite_conn(p, readonly=True)
    return conn, True, None


def _fetch_active_assertions(conn: sqlite3.Connection) -> List[sqlite3.Row]:
    """Fetch active assertion rows from sqlite database."""
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='assertion'"
    )
    if not cur.fetchone():
        return []

    cols_info = conn.execute("PRAGMA table_info(assertion)").fetchall()
    cols = {row["name"] for row in cols_info}

    query = "SELECT assertion_id, stale_after FROM assertion"
    where_clauses: list[str] = []
    if "currentness_state" in cols:
        where_clauses.append("currentness_state = 'current'")
    if "status" in cols:
        where_clauses.append("status IN ('CONFIRMED', 'PROPOSED')")
    if where_clauses:
        query += " WHERE " + " AND ".join(where_clauses)

    return conn.execute(query).fetchall()


def _build_empty_freshness_report(as_of: date) -> dict[str, Any]:
    """Build default report for empty or non-existent assertion table."""
    return {
        "status": "PASS",
        "freshness_ratio": 1.0,
        "total_active": 0,
        "active_non_stale": 0,
        "active_stale": 0,
        "alert_triggered": False,
        "fail_closed_triggered": False,
        "as_of_date": as_of.isoformat(),
    }


def calculate_graph_freshness(
    target: Path | str | sqlite3.Connection,
    *,
    as_of_date: date | None = None,
    alert_threshold: float = 0.90,
    fail_threshold: float = 0.70,
    fail_closed: bool = False,
) -> dict[str, Any]:
    """Calculate the ratio of active, non-stale assertions across the knowledge graph.

    Args:
        target: Database file path or sqlite3 connection.
        as_of_date: Cutoff date to evaluate staleness against (defaults to today UTC).
        alert_threshold: Ratio below which an ALERT is triggered (default: 0.90).
        fail_threshold: Ratio below which a FAILED status or exception occurs (default: 0.70).
        fail_closed: If True, raises GraphFreshnessError when freshness is below fail_threshold.

    Returns:
        Freshness report dictionary.
    """
    as_of = as_of_date or datetime.now(timezone.utc).date()
    conn, should_close, error_res = _resolve_target_connection(target, fail_closed)
    if error_res is not None or conn is None:
        return error_res or {}

    try:
        conn.row_factory = sqlite3.Row
        rows = _fetch_active_assertions(conn)
        total_active = len(rows)

        if total_active == 0:
            return _build_empty_freshness_report(as_of)

        stale_count = sum(1 for r in rows if _is_assertion_stale(r["stale_after"], as_of))
        active_non_stale = total_active - stale_count
        freshness_ratio = active_non_stale / total_active

        is_failed = freshness_ratio < fail_threshold
        is_alert = freshness_ratio < alert_threshold
        status = "FAILED" if is_failed else ("ALERT" if is_alert else "PASS")
        details = (
            f"Freshness ratio {freshness_ratio:.4f} ({active_non_stale}/{total_active}) "
            f"evaluated as of {as_of.isoformat()}."
        )

        report = {
            "status": status,
            "freshness_ratio": round(freshness_ratio, 4),
            "total_active": total_active,
            "active_non_stale": active_non_stale,
            "active_stale": stale_count,
            "alert_triggered": is_alert,
            "fail_closed_triggered": is_failed,
            "alert_threshold": alert_threshold,
            "fail_threshold": fail_threshold,
            "as_of_date": as_of.isoformat(),
            "details": details,
        }

        if fail_closed and is_failed:
            raise GraphFreshnessError(
                f"Graph freshness ratio {freshness_ratio:.4f} is below fail threshold {fail_threshold:.4f}."
            )

        return report

    finally:
        if should_close and conn is not None:
            conn.close()
