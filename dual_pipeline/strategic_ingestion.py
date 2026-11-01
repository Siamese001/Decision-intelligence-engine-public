"""Deterministic intake controls for source-bound strategic assertions.

The source adapter, not an LLM, binds an exact selected chunk to its strategic
role.  The profile survives normalization and graph-operation planning as an
orthogonal qualifier; it never changes disposition, identity, or approval.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .strategic_reasoning import validate_strategic_profile

STRATEGIC_PROFILE_CONTROLS_KEY = "com.partner_advisory.nee.strategic-profile-controls"
STRATEGIC_CANDIDATE_PROFILES_KEY = "com.partner_advisory.nee.strategic-candidate-profiles"


def source_chunk_profiles(
    package: Mapping[str, Any],
    *,
    chunk_ids: Sequence[str],
) -> dict[str, dict[str, str]]:
    """Validate optional exact-chunk profile controls from a source package."""

    extensions = package.get("extensions", {})
    raw = (
        extensions.get(STRATEGIC_PROFILE_CONTROLS_KEY)
        if isinstance(extensions, Mapping)
        else None
    )
    if raw is None:
        return {}
    if not isinstance(raw, Mapping) or not raw:
        raise ValueError("strategic profile controls must be a nonempty object")
    known = set(chunk_ids)
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(
            f"strategic profile controls reference unknown chunks: {unknown}"
        )
    result: dict[str, dict[str, str]] = {}
    for chunk_id, profile in raw.items():
        if not isinstance(chunk_id, str) or not isinstance(profile, Mapping):
            raise ValueError("strategic profile controls have an invalid shape")
        result[chunk_id] = validate_strategic_profile(profile)
    return result


def candidate_strategic_profile(
    bundle: Mapping[str, Any],
    candidate_id: str,
) -> dict[str, str] | None:
    """Read one normalized candidate profile with closed-world validation."""

    extensions = bundle.get("extensions", {})
    raw = (
        extensions.get(STRATEGIC_CANDIDATE_PROFILES_KEY)
        if isinstance(extensions, Mapping)
        else None
    )
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("strategic candidate profiles must be an object")
    profile = raw.get(candidate_id)
    if profile is None:
        return None
    if not isinstance(profile, Mapping):
        raise ValueError("strategic candidate profile must be an object")
    return validate_strategic_profile(profile)


__all__ = [
    "STRATEGIC_CANDIDATE_PROFILES_KEY",
    "STRATEGIC_PROFILE_CONTROLS_KEY",
    "candidate_strategic_profile",
    "source_chunk_profiles",
]
