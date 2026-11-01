"""Deterministic, source-bound classification for governed intake directives.

The classifier is deliberately conservative: it never lowers an operator- or
source-declared classification, never broadens an audience, and never decides
to activate a release.  It supplies a replayable classification briefing that
routes low-risk candidate material to a signed batch policy when one is bound,
and routes elevated material to one human classification decision.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .contracts import validate_artifact
from .core import digest, stable_id

CLASSIFIER_NAME = "nee-deterministic-intake-classifier"
CLASSIFIER_VERSION = "1.1.0"
AUDIENCE_PLACEHOLDER = "OPERATOR_SET_AUDIENCE"
CLASSIFICATION_RANK = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}

_SENSITIVE_SIGNALS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "private_key_material",
        re.compile(r"-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----"),
        "restricted",
    ),
    (
        "cloud_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "restricted",
    ),
    (
        "us_social_security_number",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "confidential",
    ),
)


def _build_fusion_coordination(package_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Identifies the temporal anchor and semantic enhancers for Multimodal Fusion."""
    temporal_anchor = None
    semantic_enhancers = []

    # Priority: Audio/Video -> VTT/Transcript (never static HTML/Markdown/Docx documents)
    for row in package_rows:
        cid = str(row.get("contract_id", ""))
        if cid.endswith(":audio-source-intake:1.0.0") or cid.endswith(":video-source-intake:1.0.0"):
            temporal_anchor = row["path"]
            break

    if not temporal_anchor:
        for row in package_rows:
            cid = str(row.get("contract_id", ""))
            cid_lower = cid.lower()
            if "vtt" in cid_lower or "transcript-source-intake" in cid_lower:
                temporal_anchor = row["path"]
                break

    for row in package_rows:
        if row["path"] != temporal_anchor:
            semantic_enhancers.append(row["path"])

    return {
        "temporal_anchor": temporal_anchor,
        "semantic_enhancers": semantic_enhancers
    }


def _source_metadata(package: dict[str, Any]) -> dict[str, Any]:
    """Return only safe metadata; never include raw source content in the report."""

    source = package.get("source")
    if isinstance(source, dict):
        return {
            "source_ref": str(
                source.get("source_version_id") or source.get("source_id") or package["artifact_id"]
            ),
            "title": str(source.get("title") or source.get("source_id") or package["artifact_id"]),
            "classification": source["classification"],
            "audiences": list(source["audiences"]),
            "source_posture": source.get("source_posture"),
            "authority_rank": source.get("authority_rank"),
            "source_location": source.get("source_location"),
        }
    meeting = package.get("meeting")
    if isinstance(meeting, dict):
        return {
            "source_ref": str(meeting.get("meeting_id") or package["artifact_id"]),
            "title": str(meeting.get("title") or package["artifact_id"]),
            "classification": meeting["classification"],
            "audiences": list(meeting["audiences"]),
            "source_posture": "meeting_source",
            "authority_rank": None,
            "source_location": meeting.get("source_location"),
        }
    raise ValueError("intake package has no supported source or meeting classification metadata")


def sensitive_signal_types(
    package: dict[str, Any],
    source_text: str | None = None,
) -> list[tuple[str, str]]:
    """Detect only high-confidence secret/PII shapes without emitting matches."""

    rendered = json.dumps(package, ensure_ascii=False, sort_keys=True)
    if source_text:
        rendered = rendered + "\n" + source_text
    return [
        (name, classification)
        for name, pattern, classification in _SENSITIVE_SIGNALS
        if pattern.search(rendered)
    ]


def _resolve_embedding_disposition(
    high_risk: bool,
    auto_candidate_admission_eligible: bool,
    signed_policy_available: bool,
) -> tuple[str, str, bool, bool]:
    if high_risk:
        return "HOLD_FOR_REMEDIATION", "high", True, False
    if auto_candidate_admission_eligible and signed_policy_available:
        return "CANDIDATE_AUTO_ADMISSION_ELIGIBLE", "low", False, True
    if auto_candidate_admission_eligible:
        return "CANDIDATE_POLICY_OR_HITL_REQUIRED", "low", True, True
    return "CANDIDATE_ONLY_AFTER_HITL", "medium", True, False


def _build_intake_reasons(
    auto_candidate_admission_eligible: bool,
    human_approval_required: bool,
    policy_admission_required: bool,
    detected_signals: set[tuple[str, str]],
    shared_audiences: list[str],
) -> list[str]:
    reasons = [
        "The recommendation is the most restrictive declared package classification.",
        "Audience lists are preserved per package; the classifier does not broaden access.",
    ]
    if auto_candidate_admission_eligible:
        reasons.append(
            "The exact package set is low-risk candidate material eligible for "
            "one signed batch-policy admission when such a policy is bound."
        )
    if human_approval_required:
        reasons.append(
            "A single source-bound HITL classification decision is required before "
            "active graph/index embedding."
        )
    elif policy_admission_required:
        reasons.append(
            "The signed batch-policy admission replaces a repeated human approval; "
            "it remains bound to this exact package set."
        )
    if detected_signals:
        reasons.append(
            "High-confidence sensitive-content signals raise classification and "
            "require remediation review."
        )
    if not shared_audiences:
        reasons.append(
            "The package set has no common audience; release must retain each "
            "package's own audience boundary."
        )
    return reasons


def _load_package_rows(
    packages: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    package_rows: list[dict[str, Any]] = []
    detected_signals: set[tuple[str, str]] = set()
    for binding in packages:
        path = Path(binding["path"])
        package = json.loads(path.read_text(encoding="utf-8"))
        validate_artifact(package)
        metadata = _source_metadata(package)
        package_rows.append(
            {
                "path": str(path), "package_sha256": binding["sha256"],
                "artifact_id": package["artifact_id"], "contract_id": package["contract_id"],
                **metadata,
            }
        )
        detected_signals.update(
            sensitive_signal_types(
                package,
                source_text=str(binding.get("source_text") or binding.get("raw_text") or ""),
            )
        )
    if not package_rows:
        raise ValueError("classification requires at least one bound package")
    return package_rows, detected_signals


def classify_packages(
    *,
    directive_id: str,
    package_set_digest: str,
    packages: Iterable[dict[str, Any]],
    batch_admission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify exact directive package bytes with no provider calls."""
    package_rows, detected_signals = _load_package_rows(packages)
    declared_classification = max((row["classification"] for row in package_rows), key=CLASSIFICATION_RANK.__getitem__)
    signal_classification = max(
        (classification for _name, classification in detected_signals),
        key=CLASSIFICATION_RANK.__getitem__,
        default="public",
    )
    recommended_classification = max((declared_classification, signal_classification), key=CLASSIFICATION_RANK.__getitem__)
    audience_sets = [
        {aud.strip() for aud in row["audiences"] if isinstance(aud, str) and aud.strip() and aud.strip() != AUDIENCE_PLACEHOLDER}
        for row in package_rows
    ]
    shared_audiences = sorted(set.intersection(*audience_sets)) if audience_sets else []
    high_risk = any(
        CLASSIFICATION_RANK[classification] >= CLASSIFICATION_RANK["confidential"]
        for _name, classification in detected_signals
    )
    signed_policy_available = bool(isinstance(batch_admission, dict) and batch_admission.get("admission_mode") == "SIGNED_POLICY")
    auto_candidate_admission_eligible = bool(
        recommended_classification in {"public", "internal"} and not detected_signals and shared_audiences
    )
    embedding_recommendation, risk_level, human_approval_required, policy_admission_required = _resolve_embedding_disposition(
        high_risk, auto_candidate_admission_eligible, signed_policy_available
    )
    reasons = _build_intake_reasons(
        auto_candidate_admission_eligible, human_approval_required, policy_admission_required, detected_signals, shared_audiences
    )
    report = {
        "classification_id": stable_id(
            "intake-classification", directive_id, package_set_digest, CLASSIFIER_NAME, CLASSIFIER_VERSION, length=28
        ),
        "directive_id": directive_id, "package_set_digest": package_set_digest,
        "classifier": {"name": CLASSIFIER_NAME, "version": CLASSIFIER_VERSION, "provider_call_count": 0},
        "recommended_classification": recommended_classification, "declared_classification": declared_classification,
        "shared_audiences": shared_audiences, "embedding_recommendation": embedding_recommendation,
        "risk_level": risk_level, "human_approval_required": human_approval_required,
        "policy_admission_required": policy_admission_required,
        "auto_candidate_admission_eligible": auto_candidate_admission_eligible,
        "batch_admission_mode": batch_admission.get("admission_mode") if isinstance(batch_admission, dict) else None,
        "sensitive_signal_types": sorted(name for name, _classification in detected_signals),
        "reasons": reasons, "packages": package_rows,
        "extensions": {"com.nee.fusion_coordinator": _build_fusion_coordination(package_rows)},
    }
    report["classification_digest"] = digest(report)
    return report
