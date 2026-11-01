"""Shared fail-closed reference monitor for Decision Intelligence reads.

W5.0 deliberately keeps this control outside the graph write path.  It is the
single policy implementation used to admit source-bound material into the
derived Decision Intelligence surface.  No caller-provided classification,
audience, or source identifier is trusted: every derived record is resolved
against an immutable source-assertion registry before it can be projected.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable, Mapping

from ._validators import format_time_text, parse_iso_time, require_sha256, require_text
from .authorization import CLASSIFICATIONS, AuthorizationContext
from .exceptions import DualPipelineValueError

CLASSIFICATION_RANK = {value: index for index, value in enumerate(CLASSIFICATIONS)}
AUDIENCE_REGISTRY = frozenset(
    {
        "nee-engineering",
        "client_approved_external",
        "internal_consulting",
        "internal_working_team",
        "partner_advisory_account_leadership",
        "private_personal",
    }
)
SOURCE_ASSERTION_FIELDS = frozenset(
    {
        "source_assertion_id",
        "source_id",
        "source_version_id",
        "snapshot_digest",
        "content_digest",
        "locator",
        "extraction_run_id",
        "classification",
        "audiences",
        "source_as_of",
        "captured_at",
        "revoked_at",
    }
)

# ``client_approved_external`` intentionally has the narrowest ceiling.  A
# source that needs a broader client delivery audience belongs in
# ``nee-engineering`` and still requires an explicit source ACL.
CLASSIFICATION_AUDIENCE_CEILINGS = {
    "public": frozenset(AUDIENCE_REGISTRY - {"private_personal"}),
    "internal": frozenset(
        {
            "nee-engineering",
            "internal_consulting",
            "internal_working_team",
            "partner_advisory_account_leadership",
        }
    ),
    "confidential": frozenset(
        {"nee-engineering", "internal_working_team", "partner_advisory_account_leadership"}
    ),
    "restricted": frozenset({"partner_advisory_account_leadership"}),
}


class ReferenceMonitorError(DualPipelineValueError):
    """Raised when evidence lacks verified lineage or access is not granted."""


def _parse_time(value: object, *, field: str, allow_none: bool = False) -> datetime | None:
    return parse_iso_time(value, field=field, error_type=ReferenceMonitorError, allow_none=allow_none)


def _time_text(value: datetime | None) -> str | None:
    return format_time_text(value)


def _text(value: object, *, field: str) -> str:
    return require_text(value, field=field, error_type=ReferenceMonitorError)


def _digest(value: object, *, field: str) -> str:
    return require_sha256(value, field=field, error_type=ReferenceMonitorError)


def _audiences(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReferenceMonitorError(f"{field} must be a nonempty list")
    normalized = [_text(item, field=field) for item in value]
    if len(set(normalized)) != len(normalized):
        raise ReferenceMonitorError(f"{field} must not contain duplicates")
    unknown = sorted(set(normalized) - AUDIENCE_REGISTRY)
    if unknown:
        raise ReferenceMonitorError(f"{field} contains unknown audiences: {unknown}")
    return sorted(normalized)


class SourceAssertionRegistry:
    """Immutable, closed-world lookup for source assertions used by DI."""

    def __init__(self, source_assertions: Iterable[Mapping[str, Any]]):
        rows = [self.validate_source_assertion(item) for item in source_assertions]
        by_id = {item["source_assertion_id"]: item for item in rows}
        if len(by_id) != len(rows):
            raise ReferenceMonitorError("source_assertion_id values must be unique")
        self._by_id = by_id

    @staticmethod
    def validate_source_assertion(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != SOURCE_ASSERTION_FIELDS:
            raise ReferenceMonitorError(
                "source assertion must contain exactly "
                f"{sorted(SOURCE_ASSERTION_FIELDS)}"
            )
        result = copy.deepcopy(dict(value))
        for field in (
            "source_assertion_id",
            "source_id",
            "source_version_id",
            "extraction_run_id",
        ):
            result[field] = _text(result[field], field=field)
        for field in ("snapshot_digest", "content_digest"):
            result[field] = _digest(result[field], field=field)
        locator = result["locator"]
        if (
            not isinstance(locator, Mapping)
            or set(locator) != {"kind", "value", "quote_digest"}
        ):
            raise ReferenceMonitorError(
                "locator must contain kind, value, and quote_digest"
            )
        result["locator"] = {
            "kind": _text(locator["kind"], field="locator.kind"),
            "value": _text(locator["value"], field="locator.value"),
            "quote_digest": _digest(locator["quote_digest"], field="locator.quote_digest"),
        }
        result["classification"] = _text(result["classification"], field="classification")
        if result["classification"] not in CLASSIFICATION_RANK:
            raise ReferenceMonitorError("classification is outside the closed vocabulary")
        result["audiences"] = _audiences(result["audiences"], field="audiences")
        illegal = set(result["audiences"]) - CLASSIFICATION_AUDIENCE_CEILINGS[
            result["classification"]
        ]
        if illegal:
            raise ReferenceMonitorError(
                "source assertion classification is not permitted for audiences: "
                f"{sorted(illegal)}"
            )
        source_as_of = _parse_time(result["source_as_of"], field="source_as_of")
        captured_at = _parse_time(result["captured_at"], field="captured_at")
        revoked_at = _parse_time(result["revoked_at"], field="revoked_at", allow_none=True)
        assert source_as_of is not None and captured_at is not None
        if captured_at < source_as_of:
            raise ReferenceMonitorError("captured_at must not precede source_as_of")
        if revoked_at is not None and revoked_at < captured_at:
            raise ReferenceMonitorError("revoked_at must not precede captured_at")
        result["source_as_of"] = _time_text(source_as_of)
        result["captured_at"] = _time_text(captured_at)
        result["revoked_at"] = _time_text(revoked_at)
        return result

    def resolve(self, source_assertion_ids: Iterable[str]) -> list[dict[str, Any]]:
        raw_ids = list(source_assertion_ids)
        if not raw_ids:
            raise ReferenceMonitorError("source_assertion_ids must be nonempty")
        ids = [_text(item, field="source_assertion_ids") for item in raw_ids]
        if len(set(ids)) != len(ids):
            raise ReferenceMonitorError("source_assertion_ids must not contain duplicates")
        missing = sorted(set(ids) - set(self._by_id))
        if missing:
            raise ReferenceMonitorError(
                f"source assertion lineage is unknown or unavailable: {missing}"
            )
        return [copy.deepcopy(self._by_id[item]) for item in sorted(ids)]


class ReferenceMonitor:
    """Authorize source-bound, audience-scoped, bitemporal DI projections."""

    def __init__(self, registry: SourceAssertionRegistry):
        if not isinstance(registry, SourceAssertionRegistry):
            raise TypeError("registry must be a SourceAssertionRegistry")
        self.registry = registry

    @staticmethod
    def _authorize_principal(
        authorization_context: AuthorizationContext, *, audience: str
    ) -> None:
        if not isinstance(authorization_context, AuthorizationContext):
            raise ReferenceMonitorError(
                "an authenticated AuthorizationContext is required"
            )
        authorization_context.assert_fresh()
        if audience not in AUDIENCE_REGISTRY:
            raise ReferenceMonitorError("requested audience is outside the closed registry")
        if audience not in authorization_context.audiences:
            raise ReferenceMonitorError("authenticated principal lacks requested audience")
        if audience == "private_personal":
            raise ReferenceMonitorError("private_personal is not a graph or DI audience")

    def derived_labels(self, source_assertion_ids: Iterable[str]) -> dict[str, Any]:
        """Return the strictest classification and exact ACL intersection."""

        rows = self.registry.resolve(source_assertion_ids)
        classification = max(
            (item["classification"] for item in rows), key=CLASSIFICATION_RANK.__getitem__
        )
        audiences = set(rows[0]["audiences"])
        for item in rows[1:]:
            audiences.intersection_update(item["audiences"])
        audiences.intersection_update(CLASSIFICATION_AUDIENCE_CEILINGS[classification])
        if not audiences:
            raise ReferenceMonitorError(
                "cited source assertions have no shared permitted audience"
            )
        return {"classification": classification, "audiences": sorted(audiences)}

    def verify_derived_labels(
        self,
        *,
        source_assertion_ids: Iterable[str],
        classification: object,
        audiences: object,
    ) -> dict[str, Any]:
        expected = self.derived_labels(source_assertion_ids)
        claimed_classification = _text(classification, field="classification")
        claimed_audiences = _audiences(audiences, field="audiences")
        if claimed_classification != expected["classification"]:
            raise ReferenceMonitorError(
                "derived classification must be exactly the strictest cited source classification"
            )
        if claimed_audiences != expected["audiences"]:
            raise ReferenceMonitorError(
                "derived audiences must exactly equal the cited-source ACL intersection"
            )
        return expected

    def visible(
        self,
        *,
        source_assertion_ids: Iterable[str],
        classification: object,
        audiences: object,
        audience: str,
        authorization_context: AuthorizationContext,
        known_at: str,
        recorded_at: str | None = None,
    ) -> bool:
        """Return visibility only after ACL, lineage, and time checks all pass.

        Unknown lineage and invalid labels raise rather than silently widening the
        read.  A source that did not exist at ``known_at`` is simply unavailable
        to a historical projection and is excluded without revealing its ID.
        """

        self._authorize_principal(authorization_context, audience=audience)
        labels = self.verify_derived_labels(
            source_assertion_ids=source_assertion_ids,
            classification=classification,
            audiences=audiences,
        )
        known_time = _parse_time(known_at, field="known_at")
        recorded_time = (
            _parse_time(recorded_at, field="recorded_at")
            if recorded_at is not None
            else None
        )
        assert known_time is not None
        if recorded_time is not None and recorded_time > known_time:
            return False
        rows = self.registry.resolve(source_assertion_ids)
        if any(
            _parse_time(item["captured_at"], field="captured_at") > known_time
            or _parse_time(item["source_as_of"], field="source_as_of") > known_time
            or (
                item["revoked_at"] is not None
                and _parse_time(item["revoked_at"], field="revoked_at") <= known_time
            )
            for item in rows
        ):
            return False
        clearance_rank = CLASSIFICATION_RANK[authorization_context.classification_clearance]
        required_rank = CLASSIFICATION_RANK[labels["classification"]]
        return (
            audience in labels["audiences"]
            and required_rank <= clearance_rank
            and audience in CLASSIFICATION_AUDIENCE_CEILINGS[labels["classification"]]
        )

    def filter_retrieval_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        audience: str,
        authorization_context: AuthorizationContext,
        known_at: str,
    ) -> list[dict[str, Any]]:
        """Filter a retrieval/sidecar projection before any content is exposed.

        This adapter is intentionally content-agnostic.  It supports graph,
        sidecar, and multi-hop records as long as each record carries the same
        source assertion envelope required by the DI projection functions.
        """

        visible: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ReferenceMonitorError("retrieval record must be an object")
            required = {"source_assertion_ids", "classification", "audiences"}
            if not required <= set(record):
                raise ReferenceMonitorError("retrieval record lacks source-bound visibility fields")
            if self.visible(
                source_assertion_ids=record["source_assertion_ids"],
                classification=record["classification"],
                audiences=record["audiences"],
                audience=audience,
                authorization_context=authorization_context,
                known_at=known_at,
                recorded_at=record.get("recorded_at"),
            ):
                visible.append(copy.deepcopy(dict(record)))
        return visible


__all__ = [
    "AUDIENCE_REGISTRY",
    "CLASSIFICATION_AUDIENCE_CEILINGS",
    "CLASSIFICATION_RANK",
    "ReferenceMonitor",
    "ReferenceMonitorError",
    "SourceAssertionRegistry",
]
