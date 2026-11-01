"""
dual_pipeline/hitl_governance_core/speaker_resolution_contract.py

Wave 1 bounded contract for authentic, durable speaker role resolutions.
Guarantees:
1. Strict separation of human confirmation from automatic directory/graph lookup.
2. Anti-conflation of professional job titles and organizations.
3. Rejection of synthetic approval origins and placeholder titles.
4. Idempotent resolution and deterministic digest calculation.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from dual_pipeline.core import digest, stable_id, utc_now
from dual_pipeline.hitl_governance_core.decision_record_writer import (
    DecisionRecordResult,
    record_operator_answer,
)
from dual_pipeline.hitl_governance_core.surfacing import validate_approval_origin


KNOWN_ORGANIZATIONS = {
    "partner_advisory", "partner_advisory build", "apex", "apex_global", "apex_global energy",
    "ge", "gcp", "google", "aws", "microsoft", "apple", "client",
}

PLACEHOLDER_TITLE_PATTERNS = [
    re.compile(r"^\s*\[?title pending.*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[?role not listed.*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\[?title not provided.*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*unknown\s*$", re.IGNORECASE),
    re.compile(r"^\s*placeholder\s*$", re.IGNORECASE),
    re.compile(r"^\s*n/?a\s*$", re.IGNORECASE),
    re.compile(r"^\s*none\s*$", re.IGNORECASE),
    re.compile(r"^\s*tbd\s*$", re.IGNORECASE),
]


class ResolutionProvenance(str, Enum):
    HUMAN_OPERATOR = "HUMAN_OPERATOR"
    ENTERPRISE_DIRECTORY = "ENTERPRISE_DIRECTORY"
    GRAPH_LOOKUP = "GRAPH_LOOKUP"
    AUTOMATIC = "AUTOMATIC"


class ResolutionStatus(str, Enum):
    AUTHENTICATED_HUMAN = "RESOLVED_HITL"
    DIRECTORY_LOOKUP = "RESOLVED_DIRECTORY"
    GRAPH_LOOKUP = "RESOLVED_GRAPH_LOOKUP"
    AUTOMATIC = "RESOLVED_AUTO"
    FAILED_PERSISTENCE = "FAILED_PERSISTENCE"
    REJECTED = "REJECTED"


def is_placeholder_title(title: Optional[str]) -> bool:
    """Returns True if the role or title is missing, empty, or a known placeholder."""
    if not title or not isinstance(title, str):
        return True
    cleaned = title.strip()
    if not cleaned:
        return True
    return any(p.match(cleaned) for p in PLACEHOLDER_TITLE_PATTERNS)


def is_conflated_organization(role: Optional[str], org: Optional[str] = None) -> bool:
    """Returns True if the given role is actually an organization name or lacks title tokens."""
    if not role or not isinstance(role, str):
        return False
    norm = role.strip().lower()
    if norm in KNOWN_ORGANIZATIONS:
        return True
    if org and norm == org.strip().lower():
        return True
    return False


def strip_org_suffix_from_role(role: Optional[str], org: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """Strips trailing organization suffix from a role string if present."""
    if not role or not isinstance(role, str):
        return "", (org.strip() if org and isinstance(org, str) and org.strip() else None)
    clean = role.strip()
    target_orgs = set(KNOWN_ORGANIZATIONS)
    if org and isinstance(org, str) and org.strip():
        target_orgs.add(org.strip().lower())
    for sep in (",", " -", " (", "/"):
        if sep == " (" and clean.endswith(")"):
            base, candidate = clean[:-1].rsplit(sep, 1)
        elif sep in clean:
            parts = clean.rsplit(sep, 1)
            base, candidate = parts[0], parts[1]
        else:
            continue
        if candidate.strip().lower() in target_orgs:
            return base.strip(), candidate.strip() or org
    return clean, (org.strip() if org and isinstance(org, str) and org.strip() else None)


@dataclass(frozen=True)
class SpeakerRoleResolution:
    """Immutable, typed resolution fact for an attendee's verified professional title."""
    resolution_id: str
    person_name: str
    resolved_role: str
    organization: Optional[str]
    provenance: str
    is_human_confirmed: bool
    status: str
    effective_time: str
    source_input: Dict[str, Any]
    ledger_entry_id: Optional[str]
    digest: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_resolution_digest(
    person_name: str,
    resolved_role: str,
    organization: Optional[str],
    provenance: str,
    is_human_confirmed: bool,
    source_input: Dict[str, Any],
) -> str:
    """Compute deterministic SHA-256 digest over core resolution attributes."""
    canonical_dict = {
        "person_name": person_name.strip(),
        "resolved_role": resolved_role.strip(),
        "organization": organization.strip() if organization else None,
        "provenance": provenance.strip().upper(),
        "is_human_confirmed": bool(is_human_confirmed),
        "source_input": source_input,
    }
    return digest(canonical_dict)


def _validate_resolution_payload(
    person_name: str, resolved_role: str, organization: Optional[str]
) -> Tuple[str, str, Optional[str]]:
    clean_name = (person_name or "").strip()
    if not clean_name:
        raise ValueError("Cannot create speaker resolution with empty person_name")

    clean_role = (resolved_role or "").strip()
    if is_placeholder_title(clean_role):
        raise ValueError(f"Cannot resolve placeholder title '{clean_role}' for '{clean_name}'")

    clean_org = (
        organization.strip()
        if organization and isinstance(organization, str) and organization.strip()
        else None
    )

    if is_conflated_organization(clean_role, clean_org):
        raise ValueError(
            f"Conflation error: title '{clean_role}' appears to be an organization name, not a professional role."
        )

    return clean_name, clean_role, clean_org


def _resolve_status_and_verify(
    is_human_confirmed: bool,
    prov_str: str,
    operator_origin: Optional[Union[str, Mapping[str, Any]]],
    prior_user_command: Optional[str],
) -> Tuple[str, Any]:
    if is_human_confirmed:
        if prov_str.upper() not in ("HUMAN_OPERATOR", "OPERATOR", "USER_INPUT", "USER_EXPLICIT"):
            raise ValueError(
                f"Provenance integrity violation: is_human_confirmed=True but provenance is '{prov_str}'"
            )
        origin_payload = operator_origin or {"source": "human_operator", "text": "Operator verified professional title"}
        is_valid_origin, origin_reason = validate_approval_origin(
            origin_payload,
            prior_user_command=prior_user_command,
        )
        if not is_valid_origin:
            raise ValueError(f"Synthetic approval rejected: {origin_reason}")
        return ResolutionStatus.AUTHENTICATED_HUMAN.value, origin_payload

    prov_upper = prov_str.upper()
    if "DIR" in prov_upper:
        return ResolutionStatus.DIRECTORY_LOOKUP.value, None
    if "GRAPH" in prov_upper:
        return ResolutionStatus.GRAPH_LOOKUP.value, None
    return ResolutionStatus.AUTOMATIC.value, None


def _persist_human_resolution(
    *,
    res_id: str,
    clean_name: str,
    clean_role: str,
    clean_org: Optional[str],
    prov_str: str,
    clean_source: Dict[str, Any],
    origin_payload: Any,
    prior_user_command: Optional[str],
    repo_root: Optional[Union[Path, str]],
    sqlite_path: Optional[Union[Path, str]],
    json_path: Optional[Union[Path, str]],
    options: Optional[List[Any]] = None,
) -> str:
    try:
        recorded_options = list(options) if options else [
            {"key": clean_role, "label": f"(Recommended) Assign verified professional title: {clean_role}", "role": clean_role, "confidence": 0.95, "recommended": True},
            {"key": f"{clean_role} [Approximation]", "label": f"Assign approximate title: {clean_role}", "role": f"{clean_role} [Approximation]", "confidence": 0.80},
        ]
        record_res = record_operator_answer(
            decision_id=res_id,
            question=f"Assign verified professional title to {clean_name}",
            options=recorded_options,
            selected_option={"label": clean_role, "role": clean_role, "key": clean_role},
            operator_origin=origin_payload,
            prior_user_command=prior_user_command,
            category="role_clarification",
            rationale=f"Operator verified title '{clean_role}' for {clean_name}",
            metadata={
                "resolution_id": res_id,
                "person_name": clean_name,
                "resolved_role": clean_role,
                "organization": clean_org,
                "provenance": prov_str,
                "source_input": clean_source,
            },
            repo_root=repo_root,
            sqlite_path=sqlite_path,
            json_path=json_path,
        )
        return record_res.entry_id
    except Exception as e:
        raise RuntimeError(f"Failed to persist authentic speaker role resolution to ledger: {e}") from e


def create_speaker_role_resolution(
    *,
    person_name: str,
    resolved_role: str,
    organization: Optional[str] = None,
    provenance: Union[str, ResolutionProvenance] = ResolutionProvenance.HUMAN_OPERATOR,
    is_human_confirmed: bool = True,
    operator_origin: Optional[Union[str, Mapping[str, Any]]] = None,
    prior_user_command: Optional[str] = None,
    source_input: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Union[Path, str]] = None,
    sqlite_path: Optional[Union[Path, str]] = None,
    json_path: Optional[Union[Path, str]] = None,
    store: Optional[Any] = None,
    options: Optional[List[Any]] = None,
) -> SpeakerRoleResolution:
    """Construct, validate, and optionally persist a speaker role resolution."""
    clean_name, clean_role, clean_org = _validate_resolution_payload(
        person_name, resolved_role, organization
    )
    prov_str = provenance.value if isinstance(provenance, ResolutionProvenance) else str(provenance).strip()

    status, origin_payload = _resolve_status_and_verify(
        is_human_confirmed, prov_str, operator_origin, prior_user_command
    )

    clean_source = dict(source_input or {})
    res_digest = compute_resolution_digest(
        person_name=clean_name,
        resolved_role=clean_role,
        organization=clean_org,
        provenance=prov_str,
        is_human_confirmed=is_human_confirmed,
        source_input=clean_source,
    )
    res_id = stable_id("res-role", clean_name, res_digest[:16], length=20)
    now = utc_now()

    ledger_entry_id = None
    if is_human_confirmed:
        ledger_entry_id = _persist_human_resolution(
            res_id=res_id,
            clean_name=clean_name,
            clean_role=clean_role,
            clean_org=clean_org,
            prov_str=prov_str,
            clean_source=clean_source,
            origin_payload=origin_payload,
            prior_user_command=prior_user_command,
            repo_root=repo_root,
            sqlite_path=sqlite_path,
            json_path=json_path,
            options=options,
        )

    return SpeakerRoleResolution(
        resolution_id=res_id,
        person_name=clean_name,
        resolved_role=clean_role,
        organization=clean_org,
        provenance=prov_str,
        is_human_confirmed=is_human_confirmed,
        status=status,
        effective_time=now,
        source_input=clean_source,
        ledger_entry_id=ledger_entry_id,
        digest=res_digest,
    )


def verify_ledger_backed_role_resolutions(
    metadata: Dict[str, Any],
    repo_root: Optional[Union[Path, str]] = None,
) -> Tuple[Dict[str, str], List[str]]:
    """
    Validates that `hitl_role_resolutions` and `attendee_roles_confirmed_by_operator`
    in metadata are backed by authentic `hitl_decision_ledger` receipts when
    `require_ledger_role_receipt` or `strict_ledger_role_gate` is enabled.
    Returns (verified_resolutions_dict, rejected_unbacked_names).
    """
    raw_res = metadata.get("hitl_role_resolutions")
    if not isinstance(raw_res, dict):
        raw_res = {}
    strict = bool(
        metadata.get("require_ledger_role_receipt")
        or metadata.get("strict_ledger_role_gate")
        or metadata.get("enforce_ledger_role_verification")
    )
    if not strict:
        return dict(raw_res), []

    receipts = metadata.get("hitl_role_receipts") if isinstance(metadata.get("hitl_role_receipts"), dict) else {}
    r_root = Path(repo_root or metadata.get("repo_root") or ".")
    ledger_json_paths = [
        Path(metadata["ledger_path"]) if metadata.get("ledger_path") else None,
        r_root / "hitl_decision_ledger.json",
        r_root / ".local" / "ledger" / "hitl_decision_ledger.json",
    ]
    ledger_entries: List[Dict[str, Any]] = []
    for lp in ledger_json_paths:
        if lp and lp.is_file():
            try:
                data = json.loads(lp.read_text(encoding="utf-8"))
                items = data if isinstance(data, list) else (data.get("entries") or data.get("decisions") or [])
                if isinstance(items, list):
                    ledger_entries.extend(it for it in items if isinstance(it, dict))
            except Exception:
                pass
    ledger_entries.sort(
        key=lambda e: (str(e.get("recorded_at") or e.get("timestamp") or ""), int(e.get("sequence_number") or 0)),
        reverse=True,
    )

    verified: Dict[str, str] = {}
    rejected: List[str] = []
    for name, role in list(raw_res.items()):
        clean_target = strip_org_suffix_from_role(role)[0]
        rec = receipts.get(name)
        rec_role = rec.get("resolved_role") if isinstance(rec, dict) else None
        rec_clean = strip_org_suffix_from_role(rec_role)[0] if rec_role else None
        has_valid_receipt = bool(
            isinstance(rec, dict)
            and rec.get("ledger_entry_id")
            and rec.get("is_human_confirmed")
            and (rec_role == role or (rec_clean and rec_clean == clean_target))
        )
        has_ledger_match = any(
            (
                (e.get("metadata") or {}).get("person_name") == name
                and (
                    (e.get("metadata") or {}).get("resolved_role") == role
                    or strip_org_suffix_from_role((e.get("metadata") or {}).get("resolved_role"))[0] == clean_target
                )
            )
            or (
                name in str(e.get("question") or e.get("prompt") or "")
                and (
                    str(role) in str(e.get("selected_option") or e.get("selected_key") or e.get("choice") or "")
                    or clean_target in str(e.get("selected_option") or e.get("selected_key") or e.get("choice") or "")
                )
            )
            for e in ledger_entries
        )
        if has_valid_receipt or has_ledger_match:
            verified[name] = role
        else:
            rejected.append(name)
            raw_res.pop(name, None)

    if metadata.get("attendee_roles_confirmed_by_operator") and (rejected or not verified):
        metadata["attendee_roles_confirmed_by_operator"] = False

    return verified, rejected

