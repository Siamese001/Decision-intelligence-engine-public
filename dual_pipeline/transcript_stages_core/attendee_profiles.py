"""
dual_pipeline/transcript_stages_core/attendee_profiles.py

Layer 1: Canonical Structured Attendee Profile extraction and verification.
Binds confirmed name, role, organization, and scope with explicit field-level tracking.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union


def _extract_name_and_role_from_string(clean_str: str) -> tuple[str, Optional[str]]:
    m_role = re.search(r"\(([^)]+)\)", clean_str)
    raw_role = m_role.group(1).strip() if m_role else None
    m_name = re.match(r"^([^(\[]+)", clean_str)
    name = re.sub(r"[*_#:`]", "", m_name.group(1) if m_name else clean_str).strip()
    role = raw_role if (raw_role and raw_role != "Participant") else None
    return name, role


def _parse_string_item(
    clean_str: str,
    default_org: Optional[str],
    default_scope: Optional[str],
) -> Optional[Dict[str, Any]]:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        COMPANY_OR_GENERIC_TOKENS,
        is_blacklisted_attendee,
        normalize_company_name,
    )
    if not clean_str or is_blacklisted_attendee(clean_str):
        return None

    name, role = _extract_name_and_role_from_string(clean_str)
    if not name or is_blacklisted_attendee(name):
        return None

    org = default_org if (default_org and default_org not in ("Client / Partner", "Unknown")) else None
    if role and (role.upper() in ("NEA", "APEX", "partner_advisory", "CLIENT", "PARTNER") or role.lower() in COMPANY_OR_GENERIC_TOKENS):
        if not org:
            org = normalize_company_name(role)
        role = None

    c_fields = ["name"]
    if role is not None:
        c_fields.append("role")
    if org is not None:
        c_fields.append("organization")

    return {
        "name": name,
        "role": role,
        "organization": org,
        "scope": default_scope,
        "confirmed_fields": sorted(c_fields),
    }


def _clean_str_field(val: Any, ignored: tuple[str, ...]) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    return None if s in ignored else s


def _resolve_dict_confirmed_fields(
    item: Dict[str, Any],
    role: Optional[str],
    org: Optional[str],
    scope: Optional[str],
) -> List[str]:
    c_fields = list(item.get("confirmed_fields") or [])
    if c_fields:
        return sorted(list(set(c_fields)))
    fields = ["name"]
    if role is not None:
        fields.append("role")
    if org is not None:
        fields.append("organization")
    if scope is not None:
        fields.append("scope")
    return sorted(fields)


def _parse_dict_item(
    item: Dict[str, Any],
    default_org: Optional[str],
    default_scope: Optional[str],
) -> Optional[Dict[str, Any]]:
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    role = _clean_str_field(item.get("role"), ("", "Participant", "Unknown"))
    raw_org = item.get("organization") or item.get("org") or default_org
    org = _clean_str_field(raw_org, ("", "Client / Partner", "Unknown"))
    scope = _clean_str_field(item.get("scope") or default_scope, ("",))
    confirmed_fields = _resolve_dict_confirmed_fields(item, role, org, scope)
    return {
        "name": name,
        "role": role,
        "organization": org,
        "scope": scope,
        "confirmed_fields": confirmed_fields,
    }


def _extract_from_dict_input(
    confirmed_dict: Dict[str, Any],
    default_scope: Optional[str],
    add_fn: Any,
) -> None:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        is_metadata_header_label,
        normalize_company_name,
    )
    for org_key, val in confirmed_dict.items():
        eff_org = normalize_company_name(org_key) if not is_metadata_header_label(org_key) else None
        items = val if isinstance(val, list) else [val]
        for it in items:
            if isinstance(it, dict):
                add_fn(_parse_dict_item(it, eff_org, default_scope))
            elif isinstance(it, str):
                add_fn(_parse_string_item(it.strip(), eff_org, default_scope))


def _extract_from_sequence_input(
    confirmed_seq: Union[List[Any], tuple[Any, ...]],
    default_scope: Optional[str],
    add_fn: Any,
) -> None:
    for it in confirmed_seq:
        if isinstance(it, dict):
            add_fn(_parse_dict_item(it, None, default_scope))
        elif isinstance(it, str):
            add_fn(_parse_string_item(it.strip(), None, default_scope))


def _extract_from_str_input(
    confirmed_str: str,
    default_scope: Optional[str],
    add_fn: Any,
) -> None:
    from dual_pipeline.transcript_metadata_core.transcript_attendee_parser import (
        split_outside_parens,
    )
    for part in split_outside_parens(confirmed_str):
        add_fn(_parse_string_item(part.strip(), None, default_scope))


def extract_structured_confirmed_attendees(
    confirmed_attendees: Union[List[Any], Dict[str, Any], str],
    default_scope: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Extracts structured attendee profiles from confirmation input.

    Captures confirmed name, role, organization, and scope with explicit field-level tracking.
    """
    profiles: List[Dict[str, Any]] = []
    seen_names = set()

    def _add_profile(prof: Optional[Dict[str, Any]]) -> None:
        if prof and prof["name"] not in seen_names:
            seen_names.add(prof["name"])
            profiles.append(prof)

    if isinstance(confirmed_attendees, dict):
        _extract_from_dict_input(confirmed_attendees, default_scope, _add_profile)
    elif isinstance(confirmed_attendees, (list, tuple)):
        _extract_from_sequence_input(confirmed_attendees, default_scope, _add_profile)
    elif isinstance(confirmed_attendees, str):
        _extract_from_str_input(confirmed_attendees, default_scope, _add_profile)

    return profiles


def is_attendee_field_confirmed(
    metadata: Dict[str, Any],
    attendee_name: str,
    field_name: str,
) -> bool:
    """Verifies whether a specific attendee field (name, role, organization, scope) was explicitly confirmed.

    Returns False if only names were confirmed or if the field was unconfirmed or inferred.
    """
    receipt = metadata.get("attendee_confirmation_receipt")
    if not isinstance(receipt, dict) or receipt.get("status") != "RESOLVED_HITL":
        return False

    structured = receipt.get("structured_attendees") or metadata.get("structured_attendees") or []
    for p in structured:
        if isinstance(p, dict) and p.get("name") == attendee_name:
            confirmed_fields = p.get("confirmed_fields") or []
            return field_name in confirmed_fields

    if field_name == "name":
        from .attendee_gate import _extract_flat_confirmed_attendees
        flat_names = _extract_flat_confirmed_attendees(receipt.get("confirmed_attendees", []))
        return attendee_name in flat_names

    return False
