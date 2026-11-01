"""Canonical RFC 8785 receipt generation and verification."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import uuid
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize value to deterministic UTF-8 bytes with sorted keys."""
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest_payload(payload: dict[str, Any]) -> str:
    """Compute sha256 hex digest over payload excluding canonical_digest."""
    clone = copy.deepcopy(payload)
    clone.pop("canonical_digest", None)
    return hashlib.sha256(canonical_json_bytes(clone)).hexdigest()


def verify_report_digest(report: dict[str, Any]) -> bool:
    """Verify that report canonical_digest matches its payload."""
    declared = report.get("canonical_digest")
    if not isinstance(declared, str) or not declared:
        return False
    expected = digest_payload(report)
    return declared == expected


def create_hygiene_report(
    *,
    mode: str,
    scheduled: bool,
    policy_digest: str,
    pins: dict[str, Any],
    summary: dict[str, Any],
    generations_analysis: dict[str, Any] | None = None,
    transcript_cache_analysis: dict[str, Any] | None = None,
    reports_clutter_analysis: dict[str, Any] | None = None,
    root_clutter_analysis: dict[str, Any] | None = None,
    actions: list[dict[str, Any]] | None = None,
    report_id: str | None = None,
    timestamp: str | None = None,
) -> dict[str, Any]:
    """Create a schema-compliant, sealed storage hygiene report."""
    if mode not in ("plan", "apply"):
        raise ValueError(f"mode must be 'plan' or 'apply', got {mode}")

    rid = report_id or f"hygiene-report-{uuid.uuid4().hex[:12]}"
    ts = timestamp or dt.datetime.now(dt.timezone.utc).isoformat()

    doc: dict[str, Any] = {
        "contract_id": "urn:nee:storage-hygiene-report:1.0.0",
        "schema_version": "1.0.0",
        "report_id": rid,
        "timestamp": ts,
        "mode": mode,
        "scheduled": scheduled,
        "policy_digest": policy_digest,
        "pins": pins,
        "summary": summary,
    }

    if generations_analysis is not None:
        doc["generations_analysis"] = generations_analysis
    if transcript_cache_analysis is not None:
        doc["transcript_cache_analysis"] = transcript_cache_analysis
    if reports_clutter_analysis is not None:
        doc["reports_clutter_analysis"] = reports_clutter_analysis
    if root_clutter_analysis is not None:
        doc["root_clutter_analysis"] = root_clutter_analysis
    if actions is not None:
        doc["actions"] = actions

    doc["canonical_digest"] = digest_payload(doc)
    return doc
