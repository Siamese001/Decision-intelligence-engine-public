"""Fail-closed strategic evidence packets and candidate synthesis projection."""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Iterable, Mapping

from ._validators import format_time_text, parse_iso_time
from .authorization import AuthorizationContext
from .core import digest, stable_id
from .exceptions import DualPipelineValueError
from .reference_monitor import ReferenceMonitor, ReferenceMonitorError

EVIDENCE_ROLES = frozenset({"supporting", "counterevidence", "context"})
REVIEW_STATES = frozenset(
    {"SOURCE_BOUND", "UNREVIEWED", "REVIEWED", "REJECTED", "EXPIRED", "CONFLICTED"}
)
PROJECTION_STATES = frozenset({"READY_WITH_COUNTEREVIDENCE", "READY_SUPPORT_ONLY", "HITL_REQUIRED", "STALE_OR_UNKNOWN"})
REQUIRED_FIELDS = frozenset(
    {
        "evidence_id",
        "claim_id",
        "evidence_role",
        "content",
        "source_assertion_ids",
        "source_as_of",
        "recorded_at",
        "expires_at",
        "classification",
        "audiences",
        "review_state",
        "reviewed_by",
        "reviewed_at",
        "falsification_tests",
    }
)
CITATION_FIELDS = frozenset(
    {
        "source_assertion_id",
        "snapshot_digest",
        "content_digest",
        "locator",
        "claim_digest",
    }
)
CLIENT_AUDIENCES = frozenset({"nee-engineering", "client_approved_external"})
RESTRICTED_AUDIENCES = frozenset({"internal_working_team", "partner_advisory_account_leadership", "private_personal"})


class StrategicEvidenceError(DualPipelineValueError):
    """Raised when a W6 evidence packet violates the contract."""


def _expected_claim_digest(*, claim_id: str, evidence_role: str, content: Any) -> str:
    """Bind packet content to its claim without making a semantic claim.

    This is deliberately a deterministic integrity check.  It proves that a
    citation was made against the exact packet content and role; it does not
    pretend to establish natural-language entailment.
    """

    return digest({"claim_id": claim_id, "evidence_role": evidence_role, "content": content})


def _validate_citation_bindings(
    result: dict[str, Any], *, reference_monitor: ReferenceMonitor | None
) -> list[dict[str, Any]] | None:
    """Validate or derive immutable source citation bindings.

    Draft packets may be built without a monitor for compatibility with
    upstream candidate builders.  They are not admissible for projection
    until a monitor resolves the source envelope and derives these bindings.
    """

    raw = result.get("citation_bindings")
    if raw is not None and (
        not isinstance(raw, list)
        or not raw
        or any(not isinstance(item, Mapping) or set(item) != CITATION_FIELDS for item in raw)
    ):
        raise StrategicEvidenceError(
            "citation_bindings must be a nonempty list with exact source integrity fields"
        )
    source_ids = result["source_assertion_ids"]
    if raw is not None:
        bindings = [copy.deepcopy(dict(item)) for item in raw]
        binding_ids = [item["source_assertion_id"] for item in bindings]
        if sorted(binding_ids) != sorted(source_ids) or len(set(binding_ids)) != len(binding_ids):
            raise StrategicEvidenceError(
                "citation_bindings must cover exactly source_assertion_ids"
            )
    elif reference_monitor is None:
        return None
    else:
        try:
            rows = reference_monitor.registry.resolve(source_ids)
        except ReferenceMonitorError as exc:
            raise StrategicEvidenceError(str(exc)) from exc
        expected_claim_digest = _expected_claim_digest(
            claim_id=result["claim_id"],
            evidence_role=result["evidence_role"],
            content=result["content"],
        )
        bindings = [
            {
                "source_assertion_id": row["source_assertion_id"],
                "snapshot_digest": row["snapshot_digest"],
                "content_digest": row["content_digest"],
                "locator": copy.deepcopy(row["locator"]),
                "claim_digest": expected_claim_digest,
            }
            for row in rows
        ]
    expected_claim_digest = _expected_claim_digest(
        claim_id=result["claim_id"],
        evidence_role=result["evidence_role"],
        content=result["content"],
    )
    if reference_monitor is not None:
        try:
            source_rows = {
                row["source_assertion_id"]: row
                for row in reference_monitor.registry.resolve(source_ids)
            }
        except ReferenceMonitorError as exc:
            raise StrategicEvidenceError(str(exc)) from exc
    else:
        source_rows = {}
    for binding in bindings:
        source_id = binding["source_assertion_id"]
        if not isinstance(source_id, str) or not source_id.strip():
            raise StrategicEvidenceError("citation source_assertion_id must be nonblank text")
        if binding["claim_digest"] != expected_claim_digest:
            raise StrategicEvidenceError("citation claim_digest does not match packet content")
        try:
            _validate_digest(binding["snapshot_digest"], field="citation snapshot_digest")
            _validate_digest(binding["content_digest"], field="citation content_digest")
            locator = binding["locator"]
            if not isinstance(locator, Mapping) or set(locator) != {"kind", "value", "quote_digest"}:
                raise StrategicEvidenceError("citation locator is invalid")
            _validate_digest(locator["quote_digest"], field="citation locator.quote_digest")
        except (TypeError, KeyError) as exc:
            raise StrategicEvidenceError("citation binding integrity fields are invalid") from exc
        if source_rows:
            source = source_rows.get(source_id)
            if source is None:
                raise StrategicEvidenceError(f"citation source lineage is unknown: {source_id}")
            if binding["snapshot_digest"] != source["snapshot_digest"] or binding["content_digest"] != source["content_digest"]:
                raise StrategicEvidenceError("citation digest does not match immutable source envelope")
            if dict(binding["locator"]) != dict(source["locator"]):
                raise StrategicEvidenceError("citation locator does not match immutable source envelope")
    return sorted(bindings, key=lambda item: item["source_assertion_id"])


def _validate_digest(value: object, *, field: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise StrategicEvidenceError(f"{field} must be a SHA-256 digest")


def _parse_time(value: object, *, field: str, allow_none: bool = False) -> datetime | None:
    return parse_iso_time(value, field=field, error_type=StrategicEvidenceError, allow_none=allow_none)


def _time_text(value: datetime | None) -> str | None:
    return format_time_text(value)


def validate_evidence_packet(
    value: Mapping[str, Any], *, reference_monitor: ReferenceMonitor | None = None
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not set(value) >= REQUIRED_FIELDS or set(value) - (REQUIRED_FIELDS | {"citation_bindings"}):
        raise StrategicEvidenceError(f"evidence packet must contain exactly {sorted(REQUIRED_FIELDS)} or those fields plus citation_bindings")
    result = copy.deepcopy(dict(value))
    for field in ("evidence_id", "claim_id", "classification"):
        if not isinstance(result[field], str) or not result[field].strip():
            raise StrategicEvidenceError(f"{field} must be nonblank text")
        result[field] = result[field].strip()
    if result["evidence_role"] not in EVIDENCE_ROLES:
        raise StrategicEvidenceError("evidence_role is outside the W6 vocabulary")
    if result["review_state"] not in REVIEW_STATES:
        raise StrategicEvidenceError("review_state is outside the W6 vocabulary")
    if result["content"] is None:
        raise StrategicEvidenceError("content is required")
    source_ids = result["source_assertion_ids"]
    if not isinstance(source_ids, list) or not source_ids or any(not isinstance(item, str) or not item.strip() for item in source_ids) or len(set(source_ids)) != len(source_ids):
        raise StrategicEvidenceError("source_assertion_ids must be a unique nonempty list")
    audiences = result["audiences"]
    if not isinstance(audiences, list) or not audiences or any(not isinstance(item, str) or not item.strip() for item in audiences) or len(set(audiences)) != len(audiences):
        raise StrategicEvidenceError("audiences must be a unique nonempty list")
    source_as_of = _parse_time(result["source_as_of"], field="source_as_of")
    recorded_at = _parse_time(result["recorded_at"], field="recorded_at")
    expires_at = _parse_time(result["expires_at"], field="expires_at")
    reviewed_at = _parse_time(result["reviewed_at"], field="reviewed_at", allow_none=True)
    if recorded_at < source_as_of:
        raise StrategicEvidenceError("recorded_at must not precede source_as_of")
    if expires_at <= recorded_at:
        raise StrategicEvidenceError("expires_at must be after source_as_of")
    reviewed_by = result["reviewed_by"]
    if result["review_state"] == "REVIEWED":
        if not isinstance(reviewed_by, str) or not reviewed_by.strip() or reviewed_at is None:
            raise StrategicEvidenceError("reviewed evidence requires reviewer and reviewed_at")
    elif reviewed_by is not None and (not isinstance(reviewed_by, str) or not reviewed_by.strip()):
        raise StrategicEvidenceError("reviewed_by must be nonblank text or null")
    tests = result["falsification_tests"]
    if not isinstance(tests, list) or any(not isinstance(item, Mapping) or set(item) != {"test_id", "question"} or not isinstance(item["test_id"], str) or not item["test_id"].strip() or not isinstance(item["question"], str) or not item["question"].strip() for item in tests):
        raise StrategicEvidenceError("falsification_tests must be a list of {test_id, question}")
    result["source_assertion_ids"] = sorted(item.strip() for item in source_ids)
    result["audiences"] = sorted(item.strip() for item in audiences)
    result["source_as_of"] = _time_text(source_as_of)
    result["recorded_at"] = _time_text(recorded_at)
    result["expires_at"] = _time_text(expires_at)
    result["reviewed_at"] = _time_text(reviewed_at)
    if result["reviewed_by"] is not None:
        result["reviewed_by"] = result["reviewed_by"].strip()
    if reference_monitor is not None:
        try:
            reference_monitor.verify_derived_labels(
                source_assertion_ids=result["source_assertion_ids"],
                classification=result["classification"],
                audiences=result["audiences"],
            )
        except ReferenceMonitorError as exc:
            raise StrategicEvidenceError(str(exc)) from exc
    citation_bindings = _validate_citation_bindings(result, reference_monitor=reference_monitor)
    if citation_bindings is not None:
        result["citation_bindings"] = citation_bindings
    return result


def build_evidence_packet(
    *,
    claim_id: str,
    evidence_role: str,
    content: Any,
    source_assertion_ids: Iterable[str],
    source_as_of: str,
    recorded_at: str,
    expires_at: str,
    classification: str,
    audiences: Iterable[str],
    review_state: str = "UNREVIEWED",
    reviewed_by: str | None = None,
    reviewed_at: str | None = None,
    falsification_tests: Iterable[Mapping[str, str]] = (),
    evidence_id: str | None = None,
    reference_monitor: ReferenceMonitor | None = None,
    citation_bindings: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    source_ids = list(source_assertion_ids)
    raw = {
        "evidence_id": evidence_id
        or stable_id("evidence", claim_id, evidence_role, source_ids, source_as_of),
        "claim_id": claim_id,
        "evidence_role": evidence_role,
        "content": content,
        "source_assertion_ids": source_ids,
        "source_as_of": source_as_of,
        "recorded_at": recorded_at,
        "expires_at": expires_at,
        "classification": classification,
        "audiences": list(audiences),
        "review_state": review_state,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "falsification_tests": [dict(item) for item in falsification_tests],
    }
    if citation_bindings is not None:
        raw["citation_bindings"] = [dict(item) for item in citation_bindings]
    return validate_evidence_packet(raw, reference_monitor=reference_monitor)


def project_synthesis_workspace(
    packets: Iterable[Mapping[str, Any]],
    *,
    claim_id: str,
    audience: str,
    as_of: str,
    known_at: str,
    authorization_context: AuthorizationContext,
    reference_monitor: ReferenceMonitor,
) -> dict[str, Any]:
    query_time = _parse_time(as_of, field="as_of")
    assert query_time is not None
    if not isinstance(reference_monitor, ReferenceMonitor):
        raise StrategicEvidenceError("a ReferenceMonitor is required for projection")
    try:
        validated = [
            validate_evidence_packet(item, reference_monitor=reference_monitor)
            for item in packets
            if item.get("claim_id") == claim_id
        ]
        visible = [
            item
            for item in validated
            if reference_monitor.visible(
                source_assertion_ids=item["source_assertion_ids"],
                classification=item["classification"],
                audiences=item["audiences"],
                audience=audience,
                authorization_context=authorization_context,
                known_at=known_at,
                recorded_at=item["recorded_at"],
            )
        ]
    except ReferenceMonitorError as exc:
        raise StrategicEvidenceError(str(exc)) from exc
    if not visible:
        return {"claim_id": claim_id, "audience": audience, "as_of": as_of, "known_at": known_at, "state": "STALE_OR_UNKNOWN", "recommendation_posture": "CANDIDATE_ONLY", "evidence_ids": [], "gaps": ["missing_supporting_evidence"]}
    active = [item for item in visible if item["review_state"] not in {"REJECTED", "EXPIRED"} and query_time <= _parse_time(item["expires_at"], field="expires_at")]
    expired_ids = [item["evidence_id"] for item in visible if query_time > _parse_time(item["expires_at"], field="expires_at")]
    support = [item for item in active if item["evidence_role"] == "supporting"]
    counter = [item for item in active if item["evidence_role"] == "counterevidence"]
    # Context packets never establish support or counterevidence. Retain them
    # so callers can expose the exact source-bound material considered without
    # converting it into a candidate recommendation.
    context = [item for item in visible if item["evidence_role"] == "context"]
    blocked = [item for item in active if item["review_state"] in {"UNREVIEWED", "CONFLICTED"}]
    gaps: list[str] = []
    if expired_ids:
        gaps.append("expired_evidence")
    if blocked:
        gaps.append("unreviewed_or_conflicted_evidence")
    if support and any(not item["falsification_tests"] for item in support):
        gaps.append("missing_falsification_test")
    if not support:
        gaps.append("missing_supporting_evidence")
    elif not counter:
        gaps.append("counterevidence_not_found")
    if blocked or (support and any(not item["falsification_tests"] for item in support)):
        state = "HITL_REQUIRED"
    elif not support:
        state = "STALE_OR_UNKNOWN"
    elif counter:
        state = "READY_WITH_COUNTEREVIDENCE"
    else:
        state = "READY_SUPPORT_ONLY"
    return {
        "claim_id": claim_id,
        "audience": audience,
        "as_of": as_of,
        "known_at": known_at,
        "state": state,
        "recommendation_posture": "CANDIDATE_ONLY",
        "supporting_evidence_ids": sorted(item["evidence_id"] for item in support),
        "counterevidence_ids": sorted(item["evidence_id"] for item in counter),
        "context_evidence_ids": sorted(item["evidence_id"] for item in context),
        "expired_evidence_ids": sorted(expired_ids),
        "gaps": sorted(set(gaps)),
        "falsification_tests": [test for item in support for test in item["falsification_tests"]],
        "supporting_source_assertion_ids": sorted(
            source_id for item in support for source_id in item["source_assertion_ids"]
        ),
        "counterevidence_source_assertion_ids": sorted(
            source_id for item in counter for source_id in item["source_assertion_ids"]
        ),
        "context_source_assertion_ids": sorted(
            source_id for item in context for source_id in item["source_assertion_ids"]
        ),
        "citation_binding_digests": sorted(
            digest(binding) for item in (support + counter) for binding in item.get("citation_bindings", [])
        ),
        "context_citation_binding_digests": sorted(
            digest(binding) for item in context for binding in item.get("citation_bindings", [])
        ),
    }
