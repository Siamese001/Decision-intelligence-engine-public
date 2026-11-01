"""
dual_pipeline/transcript_metadata_core/transcript_placeholder_detector.py

Layer 1 foundational module for detecting placeholder job titles and generic attendee roles.
Can be safely imported by L4 (QA Gates) and L5 (Pipeline Stages) without downward flow inversion.
"""

from __future__ import annotations

import re

PLACEHOLDER_NOUNS: set[str] = {
    "participant", "speaker", "presenter", "facilitator", "guest",
    "unknown", "partner_advisory team contributor", "contributor", "colleague",
    "tbd", "tbc", "attendee", "team member", "team contributor", "member",
    "stakeholder", "leadership", "management", "representative", "client partner",
}
PLACEHOLDER_ROLES = PLACEHOLDER_NOUNS

ORGANIZATIONAL_TOKENS: set[str] = {
    "nea", "apex", "partner_advisory", "client", "partner", "apex_global", "apex_global energy",
    "florida power", "florida power & light", "vendor", "consulting",
    "pgd", "fpls", "partner_advisory consulting", "apex group", "apex_global inc",
}

INVALIDATOR_SUBSTRINGS: tuple[str, ...] = (
    "tbd", "tbc", "pending", "not provided", "not specified",
    "unknown", "unassigned", "to be confirmed", "to be determined",
    "not listed", "role not listed", "title not provided",
)


def normalize_role(s: Any) -> str:
    if isinstance(s, dict):
        s = s.get("role") or s.get("resolved_role") or ""
    elif not isinstance(s, str):
        s = str(s or "")
    s = re.sub(r"[\u200b\u200c\u200d\ufeff\u00a0]", "", s)
    return re.sub(r"[.!?,;:]+$", "", s).strip()


_normalize_role = normalize_role


def _matches_placeholder_tokens(words: list[str]) -> bool:
    generic = {"participant", "attendee", "tbd", "tbc", "unknown", "unassigned", "representative", "contributor", "member", "stakeholder"}
    if any(w in generic for w in words):
        return True
    corp_suffs = {"group", "inc", "llc", "ltd", "corp", "corporation", "co", "company"}
    base_org = " ".join(w for w in words if w not in corp_suffs).strip()
    if base_org in ORGANIZATIONAL_TOKENS:
        return True
    stop = {"of", "the", "and", "&", "/", "-", "for", "in", "at", "to", "team"} | corp_suffs
    core = [t for t in words if t not in stop]
    if core and all(t in PLACEHOLDER_NOUNS or t in ORGANIZATIONAL_TOKENS for t in core):
        return True
    return False


def is_placeholder_role(role_cand: Any) -> bool:
    if isinstance(role_cand, dict):
        role_cand = role_cand.get("role") or role_cand.get("resolved_role") or ""
    if not isinstance(role_cand, str) or not role_cand.strip():
        return True
    norm = normalize_role(role_cand)
    cl = norm.lower()
    if not cl or cl in PLACEHOLDER_NOUNS or cl in ORGANIZATIONAL_TOKENS:
        return True
    if len(norm) <= 4 and norm.isupper() and norm.isalpha():
        return True
    if any(inv in cl for inv in INVALIDATOR_SUBSTRINGS):
        return True
    words = re.sub(r"[^\w\s]", " ", cl).split()
    if _matches_placeholder_tokens(words):
        return True
    parts = [p.strip() for p in re.split(r"[,/]", cl) if p.strip()]
    if len(parts) > 1:
        return all(is_placeholder_role(p) for p in parts)
    return False


_is_placeholder = is_placeholder_role
