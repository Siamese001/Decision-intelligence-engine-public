"""W0 audit verification for assertion temporal grounding and intervals."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from storage.database import create_sqlite_conn, sqlite3

from .temporal_ssot import TemporalSSOT


class TemporalGroundingError(RuntimeError):
    """Raised when knowledge assertions violate temporal grounding or interval invariants."""

    pass


def _parse_temporal_date(
    val: Any,
    field_name: str,
    assertion_id: str,
    anomalies: list[dict[str, Any]],
) -> date | None:
    """Safely parse a temporal attribute using TemporalSSOT."""
    if val is None or str(val).strip() == "":
        return None
    try:
        return TemporalSSOT.parse_to_date(val)
    except Exception as exc:
        anomalies.append(
            {
                "assertion_id": assertion_id,
                "field": field_name,
                "value": str(val),
                "issue": f"MALFORMED_TIMESTAMP ({exc})",
            }
        )
        return None


def _check_assertion_dates(
    r: sqlite3.Row,
    reference_date: date,
    anomalies: list[dict[str, Any]],
) -> None:
    """Validate observed_at and stale_after for a single assertion row."""
    aid = str(r["assertion_id"])
    row_keys = set(r.keys())
    raw_obs = r["observed_at"] if "observed_at" in row_keys else None
    raw_stale = r["stale_after"] if "stale_after" in row_keys else None

    if raw_obs is None or str(raw_obs).strip() == "":
        anomalies.append(
            {
                "assertion_id": aid,
                "field": "observed_at",
                "value": None,
                "issue": "MISSING_OBSERVED_AT",
            }
        )
        return

    obs_d = _parse_temporal_date(raw_obs, "observed_at", aid, anomalies)
    if obs_d is None:
        return

    if obs_d > reference_date:
        anomalies.append(
            {
                "assertion_id": aid,
                "field": "observed_at",
                "value": str(raw_obs),
                "issue": f"FUTURE_OBSERVED_AT (observed {obs_d} > reference {reference_date})",
            }
        )

    if raw_stale is not None and str(raw_stale).strip() != "":
        stale_d = _parse_temporal_date(raw_stale, "stale_after", aid, anomalies)
        if stale_d is not None and stale_d < obs_d:
            anomalies.append(
                {
                    "assertion_id": aid,
                    "field": "stale_after",
                    "value": str(raw_stale),
                    "issue": f"INVERTED_TEMPORAL_INTERVAL (stale_after {stale_d} < observed_at {obs_d})",
                }
            )


def _resolve_audit_connection(
    target: Path | str | sqlite3.Connection,
    fail_closed: bool,
) -> tuple[sqlite3.Connection | None, bool, dict[str, Any] | None]:
    """Resolve database connection from target or file path."""
    if isinstance(target, sqlite3.Connection):
        return target, False, None
    p = Path(target)
    if not p.is_file():
        res = {
            "status": "ERROR",
            "reason": f"Graph database file not found: {p}",
            "anomalies": [],
        }
        if fail_closed:
            raise TemporalGroundingError(res["reason"])
        return None, False, res
    conn = create_sqlite_conn(p, readonly=True)
    return conn, True, None


def _collect_assertion_anomalies(
    conn: sqlite3.Connection,
    ref_d: date,
) -> tuple[int, list[dict[str, Any]]] | None:
    """Collect temporal anomalies across all assertions."""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='assertion'")
    if not cur.fetchone():
        return None

    rows = conn.execute("SELECT * FROM assertion").fetchall()
    anomalies: list[dict[str, Any]] = []
    for r in rows:
        _check_assertion_dates(r, ref_d, anomalies)
    return len(rows), anomalies


def _raise_temporal_grounding_error(anomalies: list[dict[str, Any]]) -> None:
    """Format and raise TemporalGroundingError for anomalies."""
    issues = "; ".join(f"{a['assertion_id']}: {a['issue']}" for a in anomalies[:5])
    if len(anomalies) > 5:
        issues += f" (+{len(anomalies) - 5} more)"
    raise TemporalGroundingError(
        f"W0 Temporal Grounding Audit failed closed with {len(anomalies)} violation(s): {issues}"
    )


def validate_assertion_temporal_grounding(
    target: Path | str | sqlite3.Connection,
    *,
    reference_date: date | None = None,
    fail_closed: bool = True,
) -> dict[str, Any]:
    """Audit all assertions in GraphDB for valid temporal grounding and non-inverted intervals.

    Args:
        target: Database file path or active sqlite3 connection.
        reference_date: Cutoff date for future timestamp checks. Defaults to today UTC.
        fail_closed: If True, raises TemporalGroundingError on any detected anomalies.

    Returns:
        Audit report containing status, counts, and itemized anomalies.
    """
    ref_d = reference_date or datetime.now(timezone.utc).date()
    conn, should_close, error_res = _resolve_audit_connection(target, fail_closed)
    if error_res is not None or conn is None:
        return error_res or {}

    try:
        conn.row_factory = sqlite3.Row
        collected = _collect_assertion_anomalies(conn, ref_d)
        if collected is None:
            return {
                "status": "PASS",
                "total_checked": 0,
                "anomalies": [],
                "details": "No assertion table present in database.",
            }

        total_checked, anomalies = collected
        is_valid = len(anomalies) == 0
        status = "PASS" if is_valid else "VIOLATION"

        report = {
            "status": status,
            "total_checked": total_checked,
            "valid_count": total_checked - len({a["assertion_id"] for a in anomalies}),
            "violation_count": len(anomalies),
            "anomalies": anomalies,
            "reference_date": ref_d.isoformat(),
        }

        if fail_closed and not is_valid:
            _raise_temporal_grounding_error(anomalies)

        return report

    finally:
        if should_close and conn is not None:
            conn.close()


class AudioCompletenessGroundingError(RuntimeError):
    """Raised when audio-derived assertions or evidence violate Gate F audio completeness invariants."""

    pass

def _parse_audio_certificate(cert: Any) -> tuple[bool, str, str]:
    if hasattr(cert, "verdict"):
        verdict = getattr(cert, "verdict", None)
        verdict_str = verdict.value if hasattr(verdict, "value") else str(verdict)
        cert_id = getattr(cert, "certificate_id", "unknown")
        hitl_approved = getattr(cert, "hitl_approved", False)
        if hasattr(cert, "is_certified"):
            is_certified = bool(cert.is_certified)
        else:
            is_certified = (verdict_str in ("PASS", "PASSED")) or (verdict_str in ("WARN", "WARNING") and hitl_approved)
        return is_certified, verdict_str, cert_id
    elif isinstance(cert, dict):
        verdict_str = str(cert.get("verdict", "UNKNOWN"))
        cert_id = cert.get("certificate_id", "unknown")
        hitl_approved = cert.get("hitl_approved", False)
        if "is_certified" in cert:
            is_certified = bool(cert["is_certified"])
        else:
            is_certified = (verdict_str in ("PASS", "PASSED")) or (verdict_str in ("WARN", "WARNING") and hitl_approved)
        return is_certified, verdict_str, cert_id
    return False, "INVALID_TYPE", "unknown"

def validate_release_audio_certificate(
    cert: Any,
    *,
    fail_closed: bool = True,
) -> dict[str, Any]:
    """Validate that an audio certificate is fully certified for release.

    Args:
        cert: AudioCaptureCertificate instance or dictionary.
        fail_closed: If True, raises AudioCompletenessGroundingError on failure.

    Returns:
        Audit report with certificate status and verification details.
    """
    if cert is None:
        return {
            "status": "PASS",
            "is_certified": True,
            "details": "No audio certificate provided (non-audio release).",
        }

    is_certified, verdict_str, cert_id = _parse_audio_certificate(cert)

    if not is_certified:
        msg = f"Audio certificate '{cert_id}' is not certified (verdict={verdict_str}). Audio completeness release gate failed."
        if fail_closed:
            raise AudioCompletenessGroundingError(msg)
        return {
            "status": "VIOLATION",
            "certificate_id": cert_id,
            "verdict": verdict_str,
            "is_certified": False,
            "reason": msg,
        }

    return {
        "status": "PASS",
        "certificate_id": cert_id,
        "verdict": verdict_str,
        "is_certified": True,
    }


def _count_audio_sources(rows: list[sqlite3.Row]) -> int:
    count = 0
    for r in rows:
        keys = r.keys() if hasattr(r, "keys") else ()
        stype = str(r["source_type"]).lower() if "source_type" in keys else ""
        if any(k in stype for k in ("audio", "meeting_recording", "recording", "voice")):
            count += 1
    return count

def _parse_payload(er: sqlite3.Row) -> dict[str, Any]:
    keys = er.keys() if hasattr(er, "keys") else ()
    if "payload_json" in keys and er["payload_json"]:
        import json
        try:
            return json.loads(er["payload_json"])
        except Exception:
            return {}
    return {}

def _process_single_audit_event(er: sqlite3.Row) -> tuple[int, dict[str, Any] | None]:
    keys = er.keys() if hasattr(er, "keys") else ()
    etype = str(er["event_type"]).lower() if "event_type" in keys else ""
    if "gate_f" not in etype and "audio_completeness" not in etype:
        return 0, None
    payload = _parse_payload(er)
    gate_status = payload.get("status") or payload.get("verdict") or "UNKNOWN"
    if str(gate_status).upper() in ("PASS", "PASSED", "WARNING_APPROVED"):
        return 1, None
    event_id = er["event_id"] if "event_id" in keys else "unknown"
    return 0, {
        "event_id": event_id,
        "issue": f"GATE_F_UNRESOLVED_OR_FAILED (verdict={gate_status})",
    }


def _scan_audit_events(event_rows: list[sqlite3.Row]) -> tuple[int, list[dict[str, Any]]]:
    gate_f_passes = 0
    anomalies: list[dict[str, Any]] = []
    for er in event_rows:
        passes, anom = _process_single_audit_event(er)
        gate_f_passes += passes
        if anom is not None:
            anomalies.append(anom)
    return gate_f_passes, anomalies

def validate_audio_completeness_grounding(
    target: Path | str | sqlite3.Connection,
    *,
    fail_closed: bool = True,
) -> dict[str, Any]:
    """Audit all audio-related sources and audit events in GraphDB for Gate F completeness compliance.

    Args:
        target: Database file path or active sqlite3 connection.
        fail_closed: If True, raises AudioCompletenessGroundingError on any detected anomalies.

    Returns:
        Audit report containing status, counts, and itemized anomalies.
    """
    conn, should_close, error_res = _resolve_audit_connection(target, fail_closed)
    if error_res is not None or conn is None:
        return error_res or {}

    try:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}

        anomalies: list[dict[str, Any]] = []
        audio_sources_count = 0
        gate_f_passes = 0

        # Check source table if present
        if "source" in tables:
            rows = conn.execute("SELECT * FROM source").fetchall()
            audio_sources_count = _count_audio_sources(rows)

        # Check audit_event table if present
        if "audit_event" in tables:
            event_rows = conn.execute("SELECT * FROM audit_event").fetchall()
            gate_f_passes, new_anoms = _scan_audit_events(event_rows)
            anomalies.extend(new_anoms)

        # If audio sources exist but no Gate F pass was recorded
        if audio_sources_count > 0 and gate_f_passes == 0:
            anomalies.append({
                "issue": f"MISSING_GATE_F_AUDIT: {audio_sources_count} audio source(s) detected without certified Gate F audit event.",
            })

        is_valid = len(anomalies) == 0
        status = "PASS" if is_valid else "VIOLATION"

        report = {
            "status": status,
            "audio_sources_count": audio_sources_count,
            "gate_f_passes": gate_f_passes,
            "anomalies": anomalies,
        }

        if fail_closed and not is_valid:
            issues = "; ".join(a.get("issue", str(a)) for a in anomalies[:5])
            raise AudioCompletenessGroundingError(
                f"W0 Audio Completeness Audit failed closed with {len(anomalies)} violation(s): {issues}"
            )

        return report

    finally:
        if should_close and conn is not None:
            conn.close()

