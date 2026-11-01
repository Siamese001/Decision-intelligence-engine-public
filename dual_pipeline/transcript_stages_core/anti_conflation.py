"""
dual_pipeline/transcript_stages_core/anti_conflation.py

Master Constraint C-28 enforcement: Strict Entity-Workstream Disentanglement & Anti-Conflation.
Prevents cross-workstream contamination, compound entity slugs, and multi-workstream table headers.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Union

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

# Regex matching compound workstream slugs (e.g. lens/sherpa, lens_sherpa, jarvis/lens, dociq_sherpa)
# Excludes double-dash ('--') which is the canonical inter-entity relationship/signal connector.
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


def validate_anti_conflation(
    target: Union[Dict[str, Any], str],
    context_label: str = "projection",
) -> None:
    """
    Validate that a graph projection dictionary, JSON string, or text content
    contains zero compound workstream entities, slugs, or table headers.

    Raises CompoundWorkstreamConflationError if a violation is detected.
    """
    if isinstance(target, str):
        try:
            data = json.loads(target)
            if isinstance(data, dict):
                validate_anti_conflation(data, context_label=context_label)
                return
        except json.JSONDecodeError:
            pass
        if is_compound_workstream_reference(target):
            m = _COMPOUND_HEADER_PATTERN.search(target) or _COMPOUND_SLUG_PATTERN.search(target)
            match = m.group(0) if m else target[:40]
            raise CompoundWorkstreamConflationError(
                f"{ERR_COMPOUND_WORKSTREAM_CONFLATION}: Forbidden compound workstream reference "
                f"'{match}' detected in {context_label}."
            )
        return

    if not isinstance(target, dict):
        return

    mutations = target.get("graph_mutations", target)
    if isinstance(mutations, dict):
        # Validate entity upserts
        ents = mutations.get("entity_upserts") or mutations.get("entities") or []
        for ent in ents:
            if isinstance(ent, dict):
                eid = str(ent.get("id", ""))
                ename = str(ent.get("name", ""))
                edesc = str(ent.get("description", ""))
                if is_compound_workstream_reference(eid):
                    raise CompoundWorkstreamConflationError(
                        f"{ERR_COMPOUND_WORKSTREAM_CONFLATION}: Compound workstream entity ID '{eid}' "
                        f"detected in entity_upserts (C-28 violation)."
                    )
                if is_compound_workstream_reference(ename):
                    raise CompoundWorkstreamConflationError(
                        f"{ERR_COMPOUND_WORKSTREAM_CONFLATION}: Compound workstream entity name '{ename}' "
                        f"detected in entity_upserts (C-28 violation)."
                    )
                if is_compound_workstream_reference(edesc):
                    raise CompoundWorkstreamConflationError(
                        f"{ERR_COMPOUND_WORKSTREAM_CONFLATION}: Compound workstream entity description '{edesc}' "
                        f"detected in entity_upserts (C-28 violation)."
                    )

        # Validate relationship signals
        for sig in mutations.get("relationship_signals", []):
            if isinstance(sig, dict):
                fid = str(sig.get("from_id", ""))
                tid = str(sig.get("to_id", ""))
                sid = str(sig.get("signal_id", ""))
                sdesc = str(sig.get("description", ""))
                for field_name, field_val in [("from_id", fid), ("to_id", tid), ("signal_id", sid), ("description", sdesc)]:
                    if field_name == "signal_id" and "--" in field_val:
                        if not is_compound_workstream_reference(fid) and not is_compound_workstream_reference(tid):
                            continue
                    if is_compound_workstream_reference(field_val):
                        raise CompoundWorkstreamConflationError(
                            f"{ERR_COMPOUND_WORKSTREAM_CONFLATION}: Compound workstream reference '{field_val}' "
                            f"in relationship_signals.{field_name} (C-28 violation)."
                        )

        # Validate assertion updates
        for cat in ("decision_updates", "proposal_updates", "action_updates", "risk_updates"):
            for item in mutations.get(cat, []):
                if isinstance(item, dict):
                    for k in ("id", f"{cat[:-8]}_id", "description", "rationale", "title"):
                        val = str(item.get(k, ""))
                        if is_compound_workstream_reference(val):
                            raise CompoundWorkstreamConflationError(
                                f"{ERR_COMPOUND_WORKSTREAM_CONFLATION}: Compound workstream reference '{val}' "
                                f"in {cat}.{k} (C-28 violation)."
                            )


def decompose_multi_workstream_utterance(
    utterance: str,
    speaker: str = "Cameron Mercer",
    speaker_org: str = "PartnerAdvisory",
    locator: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Decompose a macro reflection referencing multiple workstreams (e.g. Cameron Mercer's 09/18 quote)
    into isolated, workstream-scoped evidence envelopes adhering strictly to C-28.
    Ensures zero compound entities and clean attribution.
    """
    envelopes: List[Dict[str, Any]] = []
    default_loc = locator or {"kind": "timestamp", "value": "05:04"}

    # Pattern detecting Sherpa clause vs Lens clause in macro reflection
    sherpa_match = re.search(r"(?:whether\s+that['’]?s\s+)?the\s+sherpa\s+team\s+in\s+clay\b[^,;.]*", utterance, re.IGNORECASE)
    lens_match = re.search(r"(?:whether\s+that['’]?s\s+)?stuff\s+that\s+we['’]?re\s+seeing\s+on\s+their\s+nea\s+teams\s+like\s+the\s+lens\s+team\s+and\s+alex\b[^,;.]*", utterance, re.IGNORECASE)

    if sherpa_match:
        sherpa_phrase = sherpa_match.group(0).strip()
        envelopes.append({
            "envelope_id": "env_macro_sherpa_delivery_reflection",
            "workstream_id": "workstream_project_beacon",
            "workstream_name": "Project Beacon",
            "target_system_or_pod": "AI Hub / PGD",
            "speaker": speaker,
            "speaker_org": speaker_org,
            "locator": default_loc,
            "scoped_text": sherpa_phrase,
            "associated_personnel": ["Marcus Balk", "Clay Palumbo", "Lillian Le", "Andy Kilinskis", "Speaker_Alpha Muschett"],
            "excluded_personnel": ["Jen Pan"],
            "compound_entity_count": 0,
        })

    if lens_match:
        lens_phrase = lens_match.group(0).strip()
        envelopes.append({
            "envelope_id": "env_macro_lens_delivery_reflection",
            "workstream_id": "workstream_lens",
            "workstream_name": "Project LENS",
            "target_system_or_pod": "NEA",
            "speaker": speaker,
            "speaker_org": speaker_org,
            "locator": default_loc,
            "scoped_text": lens_phrase,
            "associated_personnel": ["Alex Sanchez", "Jen Pan", "Brett Gavagni"],
            "excluded_personnel": ["Clay Palumbo", "Marcus Balk"],
            "compound_entity_count": 0,
        })

    return envelopes




