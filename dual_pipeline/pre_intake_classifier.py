"""Provider-free classification proposals before an intake package is finalized."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import digest, sha256_bytes, sha256_file, stable_id
from .exceptions import DualPipelineValueError
from .intake_classifier import CLASSIFICATION_RANK

CLASSIFIER_NAME = "nee-deterministic-pre-intake-classifier"
CLASSIFIER_VERSION = "2.0.0"
DEFAULT_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "policy" / "pre-intake-classification-policy.json"
)
CLASSIFICATIONS = frozenset(CLASSIFICATION_RANK)
CLASSIFICATION_PLACEHOLDER = "__OPERATOR_SET_CLASSIFICATION__"
AUDIENCE_PLACEHOLDER = "OPERATOR_SET_AUDIENCE"


class PreIntakePolicyError(DualPipelineValueError):
    """The checked-in classification policy is missing or unsafe."""


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PreIntakePolicyError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_policy_domains(domain_rules: Any) -> None:
    if not isinstance(domain_rules, dict):
        raise PreIntakePolicyError("domain_rules must be an object")
    for raw_domain, rule in domain_rules.items():
        domain = _require_text(raw_domain, "domain_rules key").lower()
        if domain != raw_domain or not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
            raise PreIntakePolicyError(f"domain rule is not normalized: {raw_domain!r}")
        if not isinstance(rule, dict):
            raise PreIntakePolicyError(f"domain rule must be an object: {domain}")
        _require_text(rule.get("organization"), f"domain_rules.{domain}.organization")
        _require_text(rule.get("audience"), f"domain_rules.{domain}.audience")


def _validate_single_signal_rule(index: int, rule: Any, observed_signal_types: set[str]) -> None:
    if not isinstance(rule, dict):
        raise PreIntakePolicyError(f"signal_rules[{index}] must be an object")
    signal_type = _require_text(rule.get("signal_type"), f"signal_rules[{index}].signal_type")
    if signal_type in observed_signal_types:
        raise PreIntakePolicyError(f"duplicate signal_type: {signal_type}")
    observed_signal_types.add(signal_type)
    if rule.get("classification") not in CLASSIFICATIONS:
        raise PreIntakePolicyError(f"signal_rules[{index}].classification is invalid")
    pattern_text = _require_text(rule.get("pattern"), f"signal_rules[{index}].pattern")
    if not isinstance(rule.get("ignore_case"), bool):
        raise PreIntakePolicyError(f"signal_rules[{index}].ignore_case must be boolean")
    _require_text(rule.get("reason"), f"signal_rules[{index}].reason")
    try:
        pattern = re.compile(pattern_text, re.IGNORECASE if rule["ignore_case"] else 0)
    except re.error as exc:
        raise PreIntakePolicyError(f"signal_rules[{index}].pattern is invalid: {exc}") from exc
    if pattern.search(""):
        raise PreIntakePolicyError(f"signal_rules[{index}].pattern must not match empty text")


def _validate_policy_signals(signal_rules: Any) -> None:
    if not isinstance(signal_rules, list) or not signal_rules:
        raise PreIntakePolicyError("signal_rules must be a non-empty array")
    observed_signal_types: set[str] = set()
    for index, rule in enumerate(signal_rules):
        _validate_single_signal_rule(index, rule, observed_signal_types)


def _validate_cross_org_rule(cross_rule: Any) -> None:
    if not isinstance(cross_rule, dict):
        raise PreIntakePolicyError("cross_organization_rule must be an object")
    _require_text(cross_rule.get("signal_type"), "cross_organization_rule.signal_type")
    if cross_rule.get("classification") not in CLASSIFICATIONS:
        raise PreIntakePolicyError("cross_organization_rule.classification is invalid")
    minimum_count = cross_rule.get("minimum_organization_count")
    if not isinstance(minimum_count, int) or minimum_count < 2:
        raise PreIntakePolicyError("cross_organization_rule.minimum_organization_count must be at least 2")
    _require_text(cross_rule.get("reason"), "cross_organization_rule.reason")


def load_pre_intake_policy(
    policy_path: Path | None = None,
) -> tuple[dict[str, Any], str, Path]:
    """Load and fail-closed validate the versioned local classifier policy."""

    path = (policy_path or DEFAULT_POLICY_PATH).expanduser().resolve()
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PreIntakePolicyError(f"pre-intake classification policy is missing: {path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PreIntakePolicyError(f"pre-intake classification policy is not canonical UTF-8 JSON: {path}") from exc
    if not isinstance(policy, dict) or policy.get("schema_version") != "1.0.0":
        raise PreIntakePolicyError("unsupported pre-intake classification policy schema")
    _require_text(policy.get("policy_id"), "policy_id")
    _require_text(policy.get("policy_version"), "policy_version")
    default_classification = policy.get("default_classification")
    if default_classification not in CLASSIFICATIONS:
        raise PreIntakePolicyError("default_classification is invalid")
    if default_classification == "public":
        raise PreIntakePolicyError("default_classification must not be public without an explicit operator decision")
    if policy.get("public_requires_explicit_operator_classification") is not True:
        raise PreIntakePolicyError("public classification must require an explicit operator classification")
    _validate_policy_domains(policy.get("domain_rules"))
    _validate_cross_org_rule(policy.get("cross_organization_rule"))
    _validate_policy_signals(policy.get("signal_rules"))
    return policy, sha256_file(path), path


def _collect_source_identity_values(source: dict[str, Any]) -> list[str]:
    values = [str(source[f]) for f in ("author", "owner", "owner_email", "organizer", "created_by", "publisher") if source.get(f)]
    for list_field in ("authors", "contributors", "participants", "attendees", "stakeholders", "reviewers"):
        items = source.get(list_field)
        if isinstance(items, list):
            values.extend(
                str(item.get("email") or item.get("name") or "") if isinstance(item, dict) else str(item)
                for item in items
            )
    return values


def _meeting_email_domains(package: dict[str, Any]) -> set[str]:
    """Extract domains from explicit meeting and document source identity metadata."""

    values: list[str] = []
    meeting = package.get("meeting")
    if isinstance(meeting, dict):
        values.append(str(meeting.get("organizer") or ""))
        attendees = meeting.get("attendees")
        if isinstance(attendees, list):
            values.extend(str(value) for value in attendees)
    source = package.get("source")
    if isinstance(source, dict):
        values.extend(_collect_source_identity_values(source))
    speakers = package.get("speakers")
    if isinstance(speakers, list):
        values.extend(
            str(speaker.get("email") or "") for speaker in speakers if isinstance(speaker, dict)
        )
    return {
        match.group(1).lower()
        for value in values
        for match in re.finditer(r"@([A-Z0-9.-]+\.[A-Z]{2,})\b", value, re.IGNORECASE)
    }


def _extract_fallback_package_text(package: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("source", "meeting"):
        container = package.get(key)
        if isinstance(container, dict):
            parts.extend(
                val.strip()
                for field in ("title", "summary", "description", "raw_text", "text", "source_id")
                if isinstance((val := container.get(field)), str) and val.strip()
            )
    return "\n".join(parts)


def _extract_archive_or_pdf_text(source_bytes: bytes) -> str:
    if source_bytes.startswith(b"PK\x03\x04"):
        try:
            from .docx_source import extract_docx_markdown
            return extract_docx_markdown(source_bytes)
        except Exception:
            return _extract_zip_xml_fallback(source_bytes)
    if source_bytes.startswith(b"%PDF-"):
        try:
            from .pdf_source import inspect_pdf_pages
            return inspect_pdf_pages(source_bytes).extracted_markdown.strip()
        except Exception:
            return ""
    return ""


def _extract_zip_xml_fallback(source_bytes: bytes) -> str:
    try:
        import io
        import zipfile
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(source_bytes)) as zf:
            xml_names = [n for n in sorted(zf.namelist()) if n.endswith(".xml") and any(k in n for k in ("slide", "document", "sharedStrings"))]
            xml_texts = [
                t.strip()
                for name in xml_names
                for t in ET.fromstring(zf.read(name)).itertext()
                if t and t.strip()
            ]
            return "\n".join(xml_texts)
    except Exception:
        return ""


def _extract_source_text(source_bytes: bytes, package: dict[str, Any]) -> str:
    """Decode UTF-8 text or extract decompressed/printable strings from binary document payloads."""
    try:
        decoded = source_bytes.decode("utf-8-sig")
        if decoded.strip():
            return decoded
    except UnicodeDecodeError:
        pass
    structured_text = _extract_archive_or_pdf_text(source_bytes)
    if structured_text:
        return structured_text
    printable_chunks = [
        chunk.decode("ascii", errors="ignore").strip()
        for chunk in re.findall(rb"[ -~\t\r\n]{4,}", source_bytes)
    ]
    extracted = "\n".join(c for c in printable_chunks if c)
    if extracted.strip():
        return extracted
    fallback = _extract_fallback_package_text(package)
    if fallback:
        return fallback
    if source_bytes.strip():
        return f"binary-payload-sha256:{sha256_bytes(source_bytes)}"
    raise ValueError("pre-intake classification requires non-empty source text")


def _declared_controls(
    package: dict[str, Any],
) -> tuple[str | None, list[str]]:
    container = package.get("source")
    if not isinstance(container, dict):
        container = package.get("meeting")
    if not isinstance(container, dict):
        return None, []
    raw_classification = container.get("classification")
    declared_classification = raw_classification if raw_classification in CLASSIFICATIONS else None
    raw_audiences = container.get("audiences")
    if not isinstance(raw_audiences, list):
        return declared_classification, []
    audiences = list(
        dict.fromkeys(
            value.strip()
            for value in raw_audiences
            if isinstance(value, str) and value.strip() and value.strip() != AUDIENCE_PLACEHOLDER
        )
    )
    return declared_classification, audiences


def _build_proposal_reasons(
    declared_classification: str | None,
    matched_rules: list[dict[str, Any]],
    cross_organization: bool,
    cross_rule: dict[str, Any],
    declared_audiences: list[str],
    proposed_audiences: list[str],
) -> tuple[set[str], list[str]]:
    signal_types = {rule["signal_type"] for rule in matched_rules}
    reasons = [
        "The proposal starts at the policy default classification; public requires an explicit operator decision."
        if declared_classification is None
        else "The explicit declared classification is the floor and was not lowered."
    ]
    reasons.extend(rule["reason"] for rule in matched_rules)
    if cross_organization:
        signal_types.add(cross_rule["signal_type"])
        reasons.append(cross_rule["reason"])
    if declared_audiences:
        reasons.append("Declared audiences were preserved exactly; domain rules did not broaden access.")
    elif proposed_audiences:
        reasons.append("Audience values were proposed only from checked-in meeting-domain mappings.")
    else:
        reasons.append("No governed audience mapping resolved; explicit operator input remains required.")
    reasons.append(
        "The proposal is advisory and requires operator approval or override before package finalization."
    )
    return signal_types, reasons


def propose_pre_intake_classification(
    *,
    package: dict[str, Any],
    source_bytes: bytes,
    policy_path: Path | None = None,
) -> dict[str, Any]:
    """Return a bounded classification proposal, never an authorization."""
    if not isinstance(package, dict):
        raise ValueError("pre-intake draft must be a JSON object")
    if not isinstance(source_bytes, bytes) or not source_bytes:
        raise ValueError("pre-intake classification requires non-empty source bytes")
    source_text = _extract_source_text(source_bytes, package)
    policy, policy_file_sha256, _ = load_pre_intake_policy(policy_path)
    source_hash, draft_digest = sha256_bytes(source_bytes), digest(package)
    classified_text = json.dumps(package, ensure_ascii=False, sort_keys=True) + "\n" + source_text
    matched_rules = [
        rule for rule in policy["signal_rules"]
        if re.search(rule["pattern"], classified_text, re.IGNORECASE if rule["ignore_case"] else 0)
    ]
    domains = _meeting_email_domains(package)
    matched_domain_rules = [rule for domain, rule in policy["domain_rules"].items() if domain in domains]
    organizations = {rule["organization"] for rule in matched_domain_rules}
    cross_rule = policy["cross_organization_rule"]
    cross_organization = len(organizations) >= cross_rule["minimum_organization_count"]
    declared_classification, declared_audiences = _declared_controls(package)
    classification_candidates = [declared_classification or policy["default_classification"], *(r["classification"] for r in matched_rules)]
    if cross_organization:
        classification_candidates.append(cross_rule["classification"])
    proposed_classification = max(classification_candidates, key=CLASSIFICATION_RANK.__getitem__)
    proposed_audiences = declared_audiences or list(dict.fromkeys(rule["audience"] for rule in matched_domain_rules))
    audience_basis = "declared_audience_floor" if declared_audiences else ("policy_mapped_meeting_domains" if proposed_audiences else "operator_input_required")
    signal_types, reasons = _build_proposal_reasons(
        declared_classification, matched_rules, cross_organization, cross_rule, declared_audiences, proposed_audiences
    )
    report = {
        "proposal_id": stable_id(
            "pre-intake-classification", package.get("artifact_id"), source_hash,
            draft_digest, policy_file_sha256, CLASSIFIER_NAME, CLASSIFIER_VERSION, length=28,
        ),
        "source_hash": source_hash, "draft_digest": draft_digest,
        "policy": {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"], "policy_file_sha256": policy_file_sha256},
        "classifier": {"name": CLASSIFIER_NAME, "version": CLASSIFIER_VERSION, "provider_call_count": 0},
        "declared_classification": declared_classification, "proposed_classification": proposed_classification,
        "proposed_audiences": proposed_audiences, "audience_basis": audience_basis,
        "risk_level": {"public": "low", "internal": "low", "confidential": "medium", "restricted": "high"}[proposed_classification],
        "signal_types": sorted(signal_types), "reasons": reasons,
        "operator_approval_required": True, "package_created": False, "directive_opened": False, "graph_mutation_authorized": False,
    }
    report["proposal_digest"] = digest(report)
    return report
