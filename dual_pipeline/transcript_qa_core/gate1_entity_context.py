"""
dual_pipeline/transcript_qa_core/gate1_entity_context.py

Gate 1.10: Referenced Entity Grounding Gate (Constraint C-27).
Enforces zero tolerance for ungrounded bare first names in Outputs 01 and 02.
Every third-party actor referenced in spoken dialogue must be grounded with
full name, organization, and role, or explicitly annotated as unconfirmed.

The grounding-check name universe is derived dynamically from the
IdentityResolver graph (base roster + JSON reconciliations + PM roster +
SQLite nodes) — never from a hardcoded constant.  This ensures C-27 works for
every meeting, not just the current engagement.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Lazy module-level singleton — populated on first call so import cost is zero.
_RESOLVER_SINGLETON: Any = None


def _get_resolver() -> Any:
    """Return a lazily-instantiated IdentityResolver singleton."""
    global _RESOLVER_SINGLETON
    if _RESOLVER_SINGLETON is None:
        try:
            from dual_pipeline.identity_resolver_core.resolver import IdentityResolver
            _RESOLVER_SINGLETON = IdentityResolver()
        except Exception as e:
            logger.error("C-27 gate1_entity_context: Failed initializing IdentityResolver: %s", e)
            _RESOLVER_SINGLETON = None
    return _RESOLVER_SINGLETON


def _is_skip_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith(("#", "|", "![")):
        return True
    # Header metadata lines (e.g. - **Date:**, **Attendees:**, Participants:, etc.)
    if re.match(
        r"^[-*]?\s*\*\*(?:Date|Time|Meeting|Attendees|Participants|Project|Location|Mode|Document|Owner|Reviewer|Source|Status|Classification|Horizon|Confidence|Version|Context)\b",
        stripped,
        re.IGNORECASE,
    ):
        return True
    # Roster organization group bullets (e.g. - **PartnerAdvisory:**, - **Dominion:**, etc.)
    if re.match(
        r"^[-*]?\s*\*\*(?:PartnerAdvisory|Apex Global|Dominion|Client|Internal|External|Vendor|Partner|Stakeholder)[^*]*:\*\*",
        stripped,
        re.IGNORECASE,
    ):
        return True
    # Roster attendee lines with role in parentheses (e.g. - Bryce Poeter (Tech Lead) or - Eduardo (Ed) Juan More (Role))
    if re.match(r"^[-*]?\s*[A-Z][a-z]+(?:\s+(?:\([^)]*\)|[A-Z][\x27\u2019A-Za-z-]+))+\s*\([^)]+\)", stripped):
        return True
    return False


_PRODUCT_HOMOPHONE_PATTERNS = [
    (
        "sarah",
        "Sherpa",
        re.compile(
            r"\bSarah\b(?=.*\b(?:architecture\s+review|SAA|SAGE|Digital\s+Sherpa|recommends\s+the)\b)",
            re.IGNORECASE,
        ),
    ),
]


def is_product_homophone_context(line: str, name: str) -> Optional[str]:
    """Return canonical product/system name if a bare name token is an ASR product homophone in context."""
    norm = name.strip().lower()
    for target_name, product_name, pattern in _PRODUCT_HOMOPHONE_PATTERNS:
        if norm == target_name and pattern.search(line):
            return product_name
    return None


_SPOKEN_NICKNAME_EQUIVALENTS: Dict[str, Set[str]] = {
    "amanda": {"mandy"},
    "mandy": {"amanda"},
    "eduardo": {"ed", "eddie"},
    "ed": {"eduardo"},
    "anthony": {"tony"},
    "tony": {"anthony"},
    "jennifer": {"jen", "jenn"},
    "jen": {"jennifer", "jenn"},
    "matthew": {"matt"},
    "matt": {"matthew"},
    "daniel": {"dan", "danny"},
    "dan": {"daniel", "danny"},
    "alexander": {"alex"},
    "alex": {"alexander"},
    "karen": {"karyn"},
    "karyn": {"karen"},
}


def _build_alias_sibling_map(resolver: Any) -> Dict[str, Set[str]]:
    """Build bidirectional first-name alias sibling map from resolver and standard spoken equivalents."""
    siblings: Dict[str, Set[str]] = {k: set(v) for k, v in _SPOKEN_NICKNAME_EQUIVALENTS.items()}
    first_name_index = getattr(resolver, "first_name_index", None)
    if not isinstance(first_name_index, dict):
        return siblings
    canonical_to_firsts: Dict[str, Set[str]] = {}
    for fname, entries in first_name_index.items():
        for entry in (entries or []):
            if cname := str(entry.get("canonical_name") or "").strip().lower():
                canonical_to_firsts.setdefault(cname, set()).add(fname.lower())
    for firsts in canonical_to_firsts.values():
        for f in firsts:
            siblings.setdefault(f, set()).update(firsts - {f})
    return siblings


def _has_adjacent_nickname_self_correction(
    line: str,
    start: int,
    end: int,
    name: str,
    exempt_tokens: Set[str],
    alias_siblings: Optional[Dict[str, Set[str]]] = None,
) -> bool:
    """Return True if the bare name at line[start:end] is immediately preceded or followed by a spoken self-correction to a sibling alias or exempted token."""
    norm = name.lower()
    valid_siblings = set((alias_siblings or _SPOKEN_NICKNAME_EQUIVALENTS).get(norm, set()))
    # Check following token: e.g. "Amanda, Mandy", "Amanda — Mandy", "Amanda or Mandy", "Amanda, I mean Mandy"
    suffix = line[end:]
    m_after = re.match(
        r"^\s*(?:[,;/\-\u2013\u2014]+\s*)?(?:(?:or|i\s+mean|sorry)\b[,;\s]*)?([A-Z][a-z]{1,20})\b",
        suffix,
        re.IGNORECASE,
    )
    if m_after and suffix != m_after.group(1):
        adj_tok = m_after.group(1).lower()
        if adj_tok in valid_siblings:
            return True
    # Check preceding token: e.g. "Mandy, Amanda"
    prefix = line[:start]
    m_before = re.search(
        r"\b([A-Z][a-z]{1,20})\s*(?:[,;/\-\u2013\u2014]+\s*)?(?:(?:or|i\s+mean|sorry)\b[,;\s]*)?$",
        prefix,
        re.IGNORECASE,
    )
    if m_before:
        adj_tok = m_before.group(1).lower()
        if adj_tok in valid_siblings:
            return True
    return False


def _scan_line_for_ungrounded(
    idx: int,
    line: str,
    stakeholder_first_names: Set[str],
    exempt_tokens: Set[str],
    doc_label: str,
    alias_siblings: Optional[Dict[str, Set[str]]] = None,
) -> List[str]:
    line_failures = [
        f"C27_ASR_PRODUCT_HOMOPHONE_IN_{doc_label}: Line {idx} contains ASR product homophone "
        f"'{homophone_tok.capitalize()}' for system/product '{product_name}'. "
        f"Normalize to '{product_name}' rather than grounding to an off-roster person."
        for homophone_tok, product_name, pat in _PRODUCT_HOMOPHONE_PATTERNS
        if pat.search(line)
    ]
    for name in stakeholder_first_names - exempt_tokens:
        if not is_product_homophone_context(line, name) and _is_bare_name_ungrounded(
            line, name, exempt_tokens=exempt_tokens, alias_siblings=alias_siblings
        ):
            line_failures.append(
                f"C27_UNGROUNDED_STAKEHOLDER_IN_{doc_label}: Line {idx} references '{name.capitalize()}' without "
                "full name, organization, or title context. Enforce C-27 grounding."
            )
    return line_failures


def _scan_text_for_ungrounded_names(
    text: str,
    stakeholder_first_names: Set[str],
    exempt_tokens: Set[str],
    doc_label: str = "RECAP",
    alias_siblings: Optional[Dict[str, Set[str]]] = None,
) -> List[str]:
    if not text:
        return []
    failures: List[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if not _is_skip_line(line):
            failures.extend(
                _scan_line_for_ungrounded(idx, line, stakeholder_first_names, exempt_tokens, doc_label, alias_siblings)
            )
    return failures


def _scan_f1_for_ungrounded_names(
    f1_text: str,
    stakeholder_first_names: Set[str],
    exempt_tokens: Set[str],
    alias_siblings: Optional[Dict[str, Set[str]]] = None,
) -> List[str]:
    return _scan_text_for_ungrounded_names(
        f1_text, stakeholder_first_names, exempt_tokens, doc_label="TRANSCRIPT", alias_siblings=alias_siblings
    )


def _scan_f2_for_ungrounded_names(
    f2_text: str,
    stakeholder_first_names: Set[str],
    exempt_tokens: Set[str],
    alias_siblings: Optional[Dict[str, Set[str]]] = None,
) -> List[str]:
    return _scan_text_for_ungrounded_names(
        f2_text, stakeholder_first_names, exempt_tokens, doc_label="RECAP", alias_siblings=alias_siblings
    )


def _is_authenticated_clarification(c: Dict[str, Any], roster_norm: Set[str]) -> tuple[Optional[str], Optional[str]]:
    tok = str(c.get("token") or "").strip().lower()
    if not tok:
        return None, None
    c_name = str(c.get("canonical_name") or "").strip()
    rationale = str(c.get("rationale") or "")
    receipt_id = c.get("hitl_receipt_id") or c.get("decision_id")
    is_on_roster = tok in roster_norm or any(part.lower() in roster_norm for part in c_name.split() if len(part) > 2)
    if not is_on_roster and "Explicit operator entity resolution" in rationale and not receipt_id:
        return None, (
            f"C27_UNAUTHENTICATED_OFF_ROSTER_ENTITY_MAPPING: Token '{tok}' mapped to off-roster identity "
            f"'{c_name}' without an authenticated HITL decision receipt."
        )
    return tok, None


def _filter_authenticated_clarifications(
    entity_clarifications: Optional[List[Dict[str, Any]]],
    roster_norm: Set[str],
) -> tuple[Set[str], List[str]]:
    """Return exempt clarified tokens and any violations for unauthenticated off-roster global mappings."""
    clarified_norm: Set[str] = set()
    violations: List[str] = []
    for c in (entity_clarifications or []):
        tok, err = _is_authenticated_clarification(c, roster_norm)
        if err:
            violations.append(err)
        elif tok:
            clarified_norm.add(tok)
    return clarified_norm, violations


def _extract_f1_documented_grounding_tokens(f1_text: str, candidate_first_names: Set[str]) -> Set[str]:
    """Extract first-name tokens explicitly grounded or locked in Output 01's Referenced-person grounding, Ambiguity Lock Register, or dialogue full names."""
    if not f1_text or not candidate_first_names:
        return set()
    documented: Set[str] = set()
    ref_match = re.search(
        r"(?:^|\n)#{1,4}\s*Referenced-person grounding\b(.*?)(?=\n#{1,3}\s+|\n---|\Z)",
        f1_text,
        re.IGNORECASE | re.DOTALL,
    )
    alr_match = re.search(
        r"(?:^|\n)#{1,4}\s*Ambiguity Lock Register\b(.*?)(?=\n#{1,3}\s+|\Z)",
        f1_text,
        re.IGNORECASE | re.DOTALL,
    )
    sections_text = "\n".join(
        part for part in (
            ref_match.group(1) if ref_match else "",
            alr_match.group(1) if alr_match else "",
        )
        if part
    )
    if sections_text:
        words = {w.lower() for w in re.findall(r"\b[A-Za-z]{2,}\b", sections_text)}
        documented.update(words & candidate_first_names)
    for fname in candidate_first_names - documented:
        if re.search(rf"\b{re.escape(fname.capitalize())}\s+[A-Z][a-z]{{2,}}\b", f1_text):
            documented.add(fname)
    return documented


def _extract_parenthetical_tokens(effective_roster: List[str]) -> Set[str]:
    tokens: Set[str] = set()
    for r in effective_roster:
        for m in re.finditer(r"\(([^)]+)\)", r):
            tokens.update(clean for part in m.group(1).split() if (clean := re.sub(r"[^\w]", "", part).lower()))
    return tokens


def _extract_resolver_roster_first_names(effective_roster: List[str], resolver: Any) -> Set[str]:
    if not hasattr(resolver, "first_name_index"):
        return set()
    lower_roster = [r.lower() for r in effective_roster]
    matched: Set[str] = set()
    for fname, candidates in resolver.first_name_index.items():
        if any(
            (cn := c.get("canonical_name", "").lower()) and (cn in rl or rl in cn)
            for c in candidates
            for rl in lower_roster
        ):
            matched.add(fname.lower())
    return matched


def _normalize_roster_tokens(effective_roster: List[str], resolver: Any) -> Set[str]:
    roster_norm = {
        clean
        for r in effective_roster
        for tok in r.split()
        if (clean := re.sub(r"[^\w]", "", tok).lower())
    }
    return roster_norm | _extract_parenthetical_tokens(effective_roster) | _extract_resolver_roster_first_names(effective_roster, resolver)


def evaluate_referenced_entity_context_gate(
    f1_text: str,
    f2_text: str,
    roster_names: Optional[List[str]] = None,
    entity_clarifications: Optional[List[Dict[str, Any]]] = None,
    identity_resolver: Any = None,
    project: Optional[str] = None,
) -> tuple[bool, List[str]]:
    """Evaluates Constraint C-27 compliance across Outputs 01 and 02."""
    effective_roster = list(roster_names or [])
    if not effective_roster and f1_text:
        from .gate1_helpers import _extract_roster_names
        effective_roster = _extract_roster_names({}, f1_text)

    resolver = identity_resolver or _get_resolver()
    if resolver is None:
        logger.error("C-27 gate1_entity_context: IdentityResolver unavailable; grounding check cannot be performed.")
        return False, ["C27_RESOLVER_UNAVAILABLE: IdentityResolver unavailable; entity grounding check cannot be evaluated."]

    roster_norm = _normalize_roster_tokens(effective_roster, resolver)
    clarified_norm, auth_failures = _filter_authenticated_clarifications(entity_clarifications, roster_norm)
    exempt_tokens = roster_norm | clarified_norm
    alias_siblings = _build_alias_sibling_map(resolver)
    for tok in list(exempt_tokens):
        exempt_tokens.update(alias_siblings.get(tok, set()))

    try:
        stakeholder_first_names: Set[str] = resolver.get_first_names_requiring_grounding(
            attendees=effective_roster, project=project
        )
    except TypeError:
        stakeholder_first_names = resolver.get_first_names_requiring_grounding(attendees=effective_roster)

    documented_f1_tokens = _extract_f1_documented_grounding_tokens(f1_text, stakeholder_first_names)
    exempt_tokens |= documented_f1_tokens
    for tok in list(documented_f1_tokens):
        exempt_tokens.update(alias_siblings.get(tok, set()))

    failures: List[str] = list(auth_failures)
    failures.extend(_scan_f1_for_ungrounded_names(f1_text, stakeholder_first_names, exempt_tokens, alias_siblings=alias_siblings))
    failures.extend(_scan_f2_for_ungrounded_names(f2_text, stakeholder_first_names, exempt_tokens, alias_siblings=alias_siblings))
    return len(failures) == 0, failures


def _is_will_auxiliary(line: str, name: str) -> bool:
    if name.lower() != "will":
        return False
    return bool(
        re.search(r":\*\*\s*Will(?:\.{1,3}|[?!]|\s+(?:we|you|they|it|that|this|there|the|be|have|do|get|go|take|make|need|want|work)\b)\s*$", line, re.IGNORECASE)
        or re.search(r"\bWill\s+(?:we|you|they|it|that|this|there|the|be|have|do|get|go|take|make|need|want|work)\b", line)
    )


def _is_match_grounded(
    line: str,
    start: int,
    end: int,
    name: str,
    exempt_tokens: Optional[Set[str]],
    alias_siblings: Optional[Dict[str, Set[str]]],
) -> bool:
    if start > 0 and line[start - 1] in "([/" and end < len(line) and line[end] in ")]/":
        return True
    if _has_adjacent_nickname_self_correction(line, start, end, name, exempt_tokens or set(), alias_siblings=alias_siblings):
        return True
    prefix = line[:start].rstrip()
    return bool(
        prefix
        and (
            re.search(r"\b[A-Z][\x27\u2019A-Za-z-]+$", prefix)
            or re.search(r"\b(?:St|Saint|Mt|Dr|Mr|Mrs|Ms)\.$", prefix, re.IGNORECASE)
        )
    )


def _is_bare_name_ungrounded(
    line: str,
    name: str,
    exempt_tokens: Optional[Set[str]] = None,
    alias_siblings: Optional[Dict[str, Set[str]]] = None,
) -> bool:
    if _is_will_auxiliary(line, name):
        return False
    bare_pattern = re.compile(rf"\b{re.escape(name.capitalize())}\b(?!\s+[A-Z][\x27\u2019A-Za-z-]+)(?!\s*\(.*?\))(?!\s*\[.*?\])")
    return any(
        not _is_match_grounded(line, match.start(), match.end(), name, exempt_tokens, alias_siblings)
        for match in bare_pattern.finditer(line)
    )


def _flatten_dict_roster(roster: dict) -> List[str]:
    """Flatten a company-grouped dict roster into a flat list of attendee name strings."""
    return [
        m.group(1).strip()
        for att_list in roster.values()
        for att in (att_list if isinstance(att_list, list) else [])
        if (m := re.match(r"^([^(]+)", str(att)))
    ]


def _extract_speaker_map_names(speaker_map: Any) -> List[str]:
    if not isinstance(speaker_map, dict):
        return []
    names: List[str] = []
    for v in speaker_map.values():
        if isinstance(v, str) and v.lower() not in ("unknown", "unattributed"):
            clean = re.sub(r"\(.*?\)", "", v).strip()
            if clean and clean not in names:
                names.append(clean)
    return names


def _extract_header_attendees(f1_text: Optional[str]) -> List[str]:
    if not f1_text:
        return []
    att_match = re.search(r"\*\*(?:Attendees|Participants):\*\*\s*([^\n]+)", f1_text)
    if not att_match:
        return []
    names: List[str] = []
    for p in re.split(r"[,;]|\band\b", att_match.group(1)):
        clean = re.sub(r"[\-\*#]", "", re.sub(r"\(.*?\)", "", p)).strip()
        if clean and clean not in names:
            names.append(clean)
    return names


def _extract_roster_names_g18(meta: Dict[str, Any], f1_text: Optional[str] = None) -> List[str]:
    """Extract flat list of attendee names from meta roster/attendees/speaker_map for Gate 1.8 & 1.10."""
    meta_dict = meta if isinstance(meta, dict) else {}
    roster = meta_dict.get("roster") or meta_dict.get("attendees") or []
    if isinstance(roster, list):
        names = [r.get("name", "") if isinstance(r, dict) else str(r) for r in roster]
    elif isinstance(roster, dict):
        names = _flatten_dict_roster(roster)
    elif isinstance(roster, str):
        names = [cleaned for part in re.split(r"[;,]", roster) if (cleaned := re.sub(r"\(.*?\)", "", part).strip())]
    else:
        names = []
    return names or _extract_speaker_map_names(meta_dict.get("speaker_map")) or _extract_header_attendees(f1_text)


def _check_attribution_summary_table(
    f1_text: str, meta: Dict[str, Any]
) -> tuple[bool, List[str], int]:
    """Gate 1.8: Verify '## Attribution Summary' table is present for multi-party meetings."""
    roster_names = _extract_roster_names_g18(meta)
    if len(roster_names) < 2:
        return True, [], len(roster_names)
    has_summary = bool(re.search(r"(?:^|\n)#{1,3}\s+Attribution Summary\b", f1_text, re.IGNORECASE))
    if has_summary:
        return True, [], len(roster_names)
    return False, [
        f"ATTRIBUTION_SUMMARY_MISSING: Output 1 is missing required '## Attribution Summary' table "
        f"for a multi-party meeting with {len(roster_names)} attendees. "
        f"Each attendee must have a row showing turns attributed, confidence tier, and anchor cue."
    ], len(roster_names)
