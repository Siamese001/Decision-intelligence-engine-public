"""Controls for time-bounded project-status source evidence."""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from .core import stable_id

PROJECT_STATUS_SNAPSHOT_KEY = "com.partner_advisory.nee.project-status-snapshot"
PROJECT_STATUS_CANDIDATE_PROFILES_KEY = "com.partner_advisory.nee.project-status-candidate-profiles"
SNAPSHOT_SCHEMA_VERSION = "1.0.0"


def validate_snapshot_profile(value: object) -> dict[str, Any]:
    """Validate a source-bound weekly snapshot that can never be a fact."""

    if not isinstance(value, Mapping):
        raise ValueError("project-status snapshot profile must be an object")
    expected = {
        "schema_version", "snapshot_id", "scope_id", "as_of_date",
        "source_posture", "fact_promotion_eligible",
    }
    if set(value) != expected:
        raise ValueError("project-status snapshot profile has an invalid shape")
    profile = {key: value[key] for key in expected}
    if (
        profile["schema_version"] != SNAPSHOT_SCHEMA_VERSION
        or not isinstance(profile["snapshot_id"], str) or not profile["snapshot_id"].strip()
        or not isinstance(profile["scope_id"], str) or not profile["scope_id"].strip()
        or not isinstance(profile["as_of_date"], str)
        or profile["source_posture"] != "citation_only_weekly_snapshot"
        or profile["fact_promotion_eligible"] is not False
    ):
        raise ValueError("project-status snapshot profile has invalid values")
    try:
        date.fromisoformat(profile["as_of_date"])
    except ValueError as exc:
        raise ValueError("project-status snapshot as_of_date must be ISO-8601") from exc
    return profile


def build_snapshot_profile(*, scope_id: str, as_of_date: str) -> dict[str, Any]:
    """Create one exact snapshot classification for a source package."""

    scope = scope_id.strip()
    if not scope:
        raise ValueError("project-status snapshot scope_id is required")
    date.fromisoformat(as_of_date)
    return validate_snapshot_profile(
        {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_id": stable_id("project-status-snapshot", scope, as_of_date, length=28),
            "scope_id": scope,
            "as_of_date": as_of_date,
            "source_posture": "citation_only_weekly_snapshot",
            "fact_promotion_eligible": False,
        }
    )


def candidate_snapshot_profiles(bundle: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Read snapshot bindings for candidates from a sealed normalized bundle."""

    extensions = bundle.get("extensions", {})
    raw = extensions.get(PROJECT_STATUS_CANDIDATE_PROFILES_KEY) if isinstance(extensions, Mapping) else None
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("project-status candidate profiles must be an object")
    candidate_ids = {
        candidate.get("candidate_id") for candidate in bundle.get("candidates", [])
        if isinstance(candidate, Mapping) and isinstance(candidate.get("candidate_id"), str)
    }
    if not set(raw) <= candidate_ids:
        raise ValueError("project-status candidate profile references an unknown candidate")
    return {str(candidate_id): validate_snapshot_profile(profile) for candidate_id, profile in raw.items()}
