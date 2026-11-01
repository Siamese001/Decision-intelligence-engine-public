"""
dual_pipeline/query_core/anti_conflation.py

Anti-conflation and entity-workstream validation for governed query answers.
Enforces Master Constraint C-28 and org-scope boundaries across query synthesis outputs.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ERR_COMPOUND_WORKSTREAM_CONFLATION = "ERR_COMPOUND_WORKSTREAM_CONFLATION"


class CompoundWorkstreamConflationError(ValueError):
    """Raised when an entity, table header, or assertion conflates distinct workstreams."""
    pass


_WS_TOKENS = r"(?:lens|sherpa|jarvis|dociq|nexus|conduit|regai|chakra|swarm|flexai|insights[\s_\-]?hub)"

_SINGLE_WS_PATTERN = re.compile(rf"\b(?P<ws>{_WS_TOKENS})\b", re.IGNORECASE)

# Detect org-boundary conflation (e.g., attributing 106 total NEE engineers directly to NEA or citing a 66-NEA edge for 106)
_ORG_SCOPE_CONFLATION_PATTERN = re.compile(
    r"\b106\s+(?:software\s+)?engineers?\s+(?:at|in|allocated\s+to|assigned\s+to)\s+(?:NEA|Apex Global\s+Analytics)\b",
    re.IGNORECASE,
)

# Detect cross-workstream personnel leakage: attributing Project LENS-only members to Project Beacon
_LENS_ONLY_PERSONNEL_ON_SHERPA_PATTERN = re.compile(
    r"\b(?:Alex\s+Sanchez|Jennifer\s+Pan|Jake\s+Stark|Morgan Vance\s+Krutilla)\b[^.!\n]{0,120}\b(?:Digital\s+Sherpa|Sherpa)\b"
    r"|\b(?:Digital\s+Sherpa|Sherpa)\b[^.!\n]{0,120}\b(?:Alex\s+Sanchez|Jennifer\s+Pan|Jake\s+Stark|Morgan Vance\s+Krutilla)\b",
    re.IGNORECASE,
)

# Detect ORG_ROLE_OPTICS_COLLISION: pitching a vendor/PartnerAdvisory candidate (e.g. Michael Romagnolo) as "Tech Lead" on Sherpa where client Tech Lead (Egle Tamasevice) exists
_VENDOR_TECH_LEAD_OPTICS_COLLISION_PATTERN = re.compile(
    r"\b(?:Michael(?:\s+Romagnolo)?|Angela(?:\s+Flowers)?|Aruna(?:\s+Chordia)?|PartnerAdvisory\s+resource)\b[^.!\n]{0,90}\b(?:as|be|take\s+over\s+as|serve\s+as|new)\s+(?:the\s+)?(?:Sherpa\s+|Digital\s+Sherpa\s+)?(?:Tech(?:nical)?\s+Lead)\b",
    re.IGNORECASE,
)

# Regex matching compound workstream slugs (e.g. lens/sherpa, lens_sherpa, jarvis/lens, dociq_sherpa)
_COMPOUND_SLUG_PATTERN = re.compile(
    rf"(?:^|[\w:_\-/])(?P<ws1>{_WS_TOKENS})(?:[/&_]|(?<!-)-(?!-))+(?P<ws2>{_WS_TOKENS})(?:$|[\w:_\-/])",
    re.IGNORECASE,
)

# Regex matching compound workstream names / headers in text (e.g. 'Project LENS / Sherpa', 'Jarvis / DocIQ', 'Sherpa & LENS')
_COMPOUND_HEADER_PATTERN = re.compile(
    rf"\b(?:project\s+|digital\s+)?(?P<h1>{_WS_TOKENS})\s*(?:/|&|\band\b)\s*(?:project\s+|digital\s+)?(?P<h2>{_WS_TOKENS})\b",
    re.IGNORECASE,
)


def is_compound_workstream_reference(text: str) -> bool:
    """Return True if text contains a compound multi-workstream conflation between two distinct workstreams."""
    if not text:
        return False
    for m in _COMPOUND_SLUG_PATTERN.finditer(text):
        if m.group("ws1").lower().replace("-", "_") != m.group("ws2").lower().replace("-", "_"):
            return True
    for m in _COMPOUND_HEADER_PATTERN.finditer(text):
        if m.group("h1").lower().replace("-", "_") != m.group("h2").lower().replace("-", "_"):
            return True
    return False


def _check_statement_anti_conflation(
    stmt: str,
    field_name: str,
    idx: int,
    failures: List[str],
    combined_cited_text: str = "",
) -> None:
    if is_compound_workstream_reference(stmt):
        has_slash_or_amp = bool(_COMPOUND_SLUG_PATTERN.search(stmt)) or bool(
            re.search(rf"\b{_WS_TOKENS}\s*(?:/|&)\s*(?:project\s+|digital\s+)?{_WS_TOKENS}\b", stmt, re.I)
        )
        mentioned = {
            m.group("ws").lower().replace("-", "_")
            for m in _SINGLE_WS_PATTERN.finditer(stmt)
        }
        corroborated = bool(combined_cited_text) and all(
            ws in combined_cited_text.replace("-", "_") for ws in mentioned
        )
        if has_slash_or_amp or not corroborated:
            failures.append(f"{field_name}[{idx}]: compound_workstream_conflation")
    if _ORG_SCOPE_CONFLATION_PATTERN.search(stmt):
        failures.append(f"{field_name}[{idx}]: org_scope_conflation_106_vs_66_nea")
    if _LENS_ONLY_PERSONNEL_ON_SHERPA_PATTERN.search(stmt):
        failures.append(f"{field_name}[{idx}]: cross_workstream_personnel_leakage_lens_to_sherpa")
    if _VENDOR_TECH_LEAD_OPTICS_COLLISION_PATTERN.search(stmt):
        failures.append(f"{field_name}[{idx}]: ORG_ROLE_OPTICS_COLLISION(client_tech_lead=Egle Tamasevice; use PartnerAdvisory Lead Engineer)")


def _build_record_text_index(packet: Optional[Dict[str, Any]]) -> Dict[str, str]:
    record_text_by_id: Dict[str, str] = {}
    if not isinstance(packet, dict):
        return record_text_by_id
    for lane in ("approved", "candidate", "external_public"):
        for item in packet.get(lane, []):
            if isinstance(item, dict) and isinstance(item.get("record"), dict):
                rec = item["record"]
                rid = str(rec.get("record_id", ""))
                if rid:
                    record_text_by_id[rid] = f"{rec.get('title', '')} {rec.get('retrieval_text', '')}".lower()
    return record_text_by_id


def validate_answer_anti_conflation(
    answer: Dict[str, Any],
    packet: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Validate an L2 governed answer dictionary against cross-workstream splicing."""
    failures: List[str] = []
    record_text_by_id = _build_record_text_index(packet)

    row_fields = (
        ("current_state", "statement"),
        ("pain_points", "pain_point"),
        ("opportunities", "why_prioritize"),
        ("guidance", "guidance"),
        ("hypotheses", "hypothesis"),
    )
    for field_name, text_key in row_fields:
        for idx, row in enumerate(answer.get(field_name, [])):
            if not isinstance(row, dict):
                continue
            stmt = str(row.get(text_key, ""))
            citations = [str(c) for c in row.get("citations", []) if c]
            combined_cited_text = (
                " ".join(record_text_by_id.get(c, "") for c in citations)
                if (record_text_by_id and citations)
                else ""
            )
            _check_statement_anti_conflation(stmt, field_name, idx, failures, combined_cited_text)
            if combined_cited_text:
                mentioned_ws = {
                    m.group("ws").lower().replace("-", "_")
                    for m in _SINGLE_WS_PATTERN.finditer(stmt)
                }
                for ws_tok in mentioned_ws:
                    if ws_tok not in combined_cited_text.replace("-", "_"):
                        failures.append(f"{field_name}[{idx}]: uncorroborated_workstream_splicing({ws_tok})")
                if re.search(r"\b106\b", stmt) and "106" not in combined_cited_text and "66" in combined_cited_text:
                    failures.append(f"{field_name}[{idx}]: numeric_scope_conflation_106_on_66_record")
    return failures
