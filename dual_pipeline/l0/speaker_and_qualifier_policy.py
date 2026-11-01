"""Layer 0 policy for speaker attribution hygiene, synthetic markers, and qualifier eligibility."""
from __future__ import annotations

import json
import re
from typing import Any

PLACEHOLDER_SPEAKER_LABELS: frozenset[str] = frozenset({
    "not specified", "participants", "forensic context", "forensic intake", "transcript header",
    "transcript participant roster", "forensic transcript participant roster", "meeting roster",
    "attribution summary", "unknown", "unresolved", "unattributed", "speaker", "multiple speakers",
    "narrator", "system", "summary", "status", "audiences", "document", "user", "client observer",
    "session context", "document context", "thread overview", "meeting header", "meeting transcript",
    "unattributed participant", "unattributed speaker", "unattributed workbook", "not identified in source",
    "partner_advisory speaker", "[partner_advisory speaker]", "partner_advisory team", "partner_advisory team (room)", "nea/apex (room)", "nea/apex",
})
_PLACEHOLDER_SPEAKER_SUBSTRINGS: tuple[str, ...] = (
    "participant roster", "participants section", "participant header", "participant list",
    "attendees roster", "meeting roster", "transcript roster", "session roster", "portfolio roster",
    "transcript header", "meeting header", "actual speaker unresolved", "recording channel label",
    "author not specified", "author not provided", "(room)",
)
ORG_OR_OPERATOR_SPEAKER_LABELS: frozenset[str] = frozenset({
    "partner_advisory", "apex_global", "apex_global energy", "apex_global analytics", "operator", "google", "ibm", "apex", "nea", "apex_trans",
    "nea executive engineering leadership", "apex development & partner_advisory", "partner_advisory & apex development",
    "apex_trans technology leadership", "nea product management & engineering leadership", "apex development",
})
SINGLE_TOKEN_SPEAKER_EXPANSIONS: dict[str, str] = {
    "dan": "Drew Oakley",
    "ben": "Ben Subinari",
    "jake": "Jake Stark",
    "mihir": "Mihir Sanghavi",
}

_GENERIC_SPEAKER_NUM_RE = re.compile(r"^speaker\s*\d+$", re.IGNORECASE)
_MACHINE_PERSON_SLUG_RE = re.compile(r"^person:[a-z0-9_\-]+$", re.IGNORECASE)
_INLINE_BOLD_PREFIX_RE = re.compile(
    r"^(?:\*{2,}([A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){1,3})\s*:?\*{2,}:?|([A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){1,3}):\*{2,})\s+"
)
_MID_LINE_SPEAKER_PREFIX_RE = re.compile(
    r"^(?:\[\d{1,2}:\d{2}(?::\d{2})?\]\s*|So,\s+Amit,\s+I\s+)\*{2}([A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){1,3}):?\*{2}:?(?:\s*\[\d{1,2}:\d{2}(?::\d{2})?\]:?)?\s*"
)
_LEAD_IN_STATEMENT_VERB_RE = re.compile(
    r"^(?:\[remediated_full_scope\]\s*)?([A-Z][a-z]+(?:\s+\([A-Za-z]+\))?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+"
    r"(?:stated|noted|explained|asked|confirmed|raised|highlighted|emphasized|proposed|shared|reported|mentioned|"
    r"recommended|clarified|added|outlined|described|pointed\s+out|warned|agreed|expressed|requested|challenged|"
    r"required|flagged|identified|observed|questioned|stressed|suggested|advised|acknowledged|communicated|"
    r"instructed|directed|committed|presented|walked\s+through|pushed\s+back|argued|voiced|told)\b"
)
_CONCEPT_HEADING_TOKENS_A: frozenset[str] = frozenset({
    "active", "action", "ai", "architecture", "atlas", "background", "blocker",
    "business", "context", "conduit", "data", "decision", "delivery", "dictionary",
    "dociq", "domain", "edo", "engine", "energy", "apex", "governance", "hub",
    "images", "intake", "jarvis", "key", "learning", "lens", "loop", "maxcore",
    "maximo", "metric", "metrics", "ml", "model", "nea", "apex_trans", "apex_global",
})
_CONCEPT_HEADING_TOKENS_B: frozenset[str] = frozenset({
    "nexus", "note", "objective", "ocr", "outcome", "overview", "persistent",
    "pgd", "phase", "pipeline", "platform", "program", "project", "queue",
    "reference", "review", "rewire", "risk", "role", "rules", "scope", "section",
    "sherpa", "partner_advisory", "sow", "sprint", "status", "summary", "system",
    "takeaway", "technical", "vanguard", "uat", "vision", "workstream",
})
_CONCEPT_HEADING_TOKENS: frozenset[str] = _CONCEPT_HEADING_TOKENS_A | _CONCEPT_HEADING_TOKENS_B

_NICKNAME_EQUIVALENTS: dict[str, str] = {
    "ed": "eduardo", "eduardo": "eduardo", "jen": "jennifer", "jennifer": "jennifer",
    "rob": "robert", "robert": "robert", "bob": "robert", "mike": "michael", "michael": "michael",
    "dan": "daniel", "daniel": "daniel", "tony": "anthony", "anthony": "anthony",
    "alex": "alex", "alexander": "alex", "stepfan": "stefanos", "steph": "stefanos", "stefanos": "stefanos",
    "mandy": "amanda", "amanda": "amanda", "chris": "christopher", "christopher": "christopher",
    "matt": "matthew", "matthew": "matthew",
}

SYNTHETIC_FIXTURE_MARKERS: tuple[str, ...] = (
    "Thirty legacy platforms identified for consolidation",
    "candidate-unverified-speculation",
    "Elena Rostova",
    "Alex Mercer",
    "Sarah Connor",
    "Frank Miller",
    "Carlos Mendez",
    "Marcus Vance",
    "Diana Prince",
    "Dr. Evelyn Reed",
    "David Chen",
    "Novel Contributor",
    "John Doe",
    "Jane Smith",
)
GARBLED_ASR_PROPER_NOUN_MARKERS: tuple[str, ...] = ("zimran", "priswas", "for spawn")
_NEET_ONLY_PERSONAS: frozenset[str] = frozenset({"Alex Rivera", "Jordan Taylor"})
_SPOKEN_DIALOGUE_MARKER_RE = re.compile(
    r"(?:^\s*(?:yeah|yep|uh|um|well,|so,|all right,|let's\s+see)\b|"
    r"\b(?:i'm|i'd|i'll|i've|you're|you've|you'll|we're|we've|they're|that's|something's|don't|can't|won't|didn't|"
    r"you\s+know|i\s+mean|i\s+think|i\s+guess|i\s+feel|right\?)\b|"
    r"\b(?:i|you|we|us|our)\b)",
    re.IGNORECASE,
)
_STRUCTURED_CARD_RE = re.compile(
    r"^(?:\[remediated_full_scope\]|[A-Z][A-Za-z0-9\s().\'\-&/]{1,65}:\s+(?:Role:|Atlas\b|PartnerAdvisory\b|Apex Global\b|"
    r"Engineering\b|Product\b|Strategic\b|Delivery\b|VP\b|Director\b|Lead\b|Manager\b|Specialist\b|Review\b|"
    r"Focuses\b|LENS\b|Managing\b|Executive\b|Client\b|Software\b|Senior\b|Principal\b|Chief\b|Head\b|"
    r"Partner\b|Consultant\b|Architect\b|Analyst\b|Developer\b|Data\b|AI\b|ML\b|Cloud\b|Google\b|IBM\b|"
    r"APEX\b|NEA\b|APEX_TRANS\b|PGD\b|EDO\b|IT\b|HR\b|Finance\b|Operations\b|Program\b|Project\b|Workstream\b|"
    r"Participant\b|Attendee\b|Member\b|Owner\b|Sponsor\b|Stakeholder\b|Technical\b|Business\b|Enterprise\b|"
    r"Global\b|Regional\b|Practice\b|Account\b|Engagement\b|fellow\b|Fellow\b))"
)
_PAREN_ROLE_CARD_RE = re.compile(
    r"^[A-Z][A-Za-z.\'\-]+(?:\s+[A-Z][A-Za-z.\'\-]+){1,3}\s*\([^)]{4,80}\)\.?$"
)
_ENTITY_ANCHOR_CARD_RE = re.compile(
    r"^[^:]{2,80}:\s+.+\((?:technical_anchor|workstream|program|system|platform|tool|metric|organization|stakeholder|person|artifact|deliverable|milestone|risk|dependency)\)\s+in\s+",
    re.IGNORECASE,
)
_NON_SPOKEN_DOC_SUBSTRINGS: tuple[str, ...] = (
    "document ", "charter ", "sow ", "sow:", "sow 1:", "statement of work", "partner_advisory sow", "po 2000",
    "deck ", "slide ", "org chart", "org_chart", "alignment & product team support roster",
    "offboarding", "roll-off", "bonus plan", "employment", "16-program portfolio",
    "headcount allocation export", "resource mapping", "investor conference", "policy framework",
    "ratification",
)
_NON_PROMOTABLE_ANSWER_USES: frozenset[str] = frozenset({
    "citation_only", "navigation_only", "source_only", "evidence_only",
})
_NON_PROMOTABLE_RETRIEVAL_KINDS: frozenset[str] = frozenset({"evidence_span", "entity_facet"})
_NON_PROMOTABLE_CANDIDATE_TYPES: frozenset[str] = frozenset({"source_chunk", "architecture_component"})
_STRUCTURED_NON_DIALOGUE_CANDIDATE_TYPES: frozenset[str] = frozenset({
    "entity_mention", "architecture_relationship", "action",
})
_SECTION_HEADER_PREFIX_RE = re.compile(
    r"^\*{2,}(?:Downstream Production Integration|Client Production Milestone):?\*{2,}:?\s*",
    re.IGNORECASE,
)
_EXTRA_BOLD_SPEAKER_RE = re.compile(r"^(\*{2}[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}:?\*{2})\*{2,}\s*")
_TS_LOCATOR_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def _strip_verified_inline_speaker_prefix(txt: str) -> str:
    m = _INLINE_BOLD_PREFIX_RE.match(txt)
    if m and _validate_extracted_person_name(m.group(1) or m.group(2) or ""):
        return txt[m.end() :].lstrip()
    m_mid = _MID_LINE_SPEAKER_PREFIX_RE.match(txt)
    if m_mid and _validate_extracted_person_name(m_mid.group(1) or ""):
        return txt[m_mid.end() :].lstrip()
    return txt


def normalize_candidate_claim_text(scope_text: str | None) -> str:
    """Normalize leading stripped colon (': '), section header, or inline speaker prefixes."""
    if not scope_text:
        return ""
    txt = str(scope_text).strip()
    if txt.startswith(": "):
        txt = txt[2:].lstrip()
    txt = _SECTION_HEADER_PREFIX_RE.sub("", txt)
    txt = _EXTRA_BOLD_SPEAKER_RE.sub(r"\1 ", txt)
    return _strip_verified_inline_speaker_prefix(txt)


def is_degenerate_single_token_claim(scope_text: str | None) -> bool:
    """Return True when claim text is empty or a single isolated word token such as 'ESTABLISHED'."""
    norm = normalize_candidate_claim_text(scope_text)
    if not norm:
        return True
    tokens = re.findall(r"[A-Za-z0-9]+", norm)
    return len(tokens) <= 1 and len(norm) < 20


def is_placeholder_speaker(speaker: str | None) -> bool:
    """Return True when speaker_or_author is a placeholder header, roster label, or raw machine slug."""
    if speaker is None:
        return False
    clean = " ".join(str(speaker).strip().split())
    if not clean:
        return True
    low = clean.lower()
    norm = re.sub(r"[_\-]+", " ", low).strip()
    if low in PLACEHOLDER_SPEAKER_LABELS or norm in PLACEHOLDER_SPEAKER_LABELS:
        return True
    if any(sub in norm for sub in _PLACEHOLDER_SPEAKER_SUBSTRINGS):
        return True
    return bool(_GENERIC_SPEAKER_NUM_RE.match(clean) or _MACHINE_PERSON_SLUG_RE.match(clean))


def is_unresolved_channel_or_room_speaker(speaker: str | None) -> bool:
    """Return True when speaker_or_author is an unresolved recording channel, room mic, or unattributed tag."""
    if speaker is None:
        return False
    low = " ".join(str(speaker).strip().split()).lower()
    return any(
        sub in low
        for sub in (
            "actual speaker unresolved",
            "recording channel label",
            "[partner_advisory speaker]",
            "partner_advisory speaker",
            "unattributed participant",
            "unattributed speaker",
            "(room)",
            "partner_advisory team",
            "nea/apex",
        )
    )


def is_organization_or_unresolved_speaker(speaker: str | None) -> bool:
    """Return True when speaker_or_author is an organization or generic operator label rather than a person."""
    if speaker is None:
        return False
    clean = " ".join(str(speaker).strip().split()).lower()
    return clean in ORG_OR_OPERATOR_SPEAKER_LABELS or is_unresolved_channel_or_room_speaker(speaker)


def clean_non_placeholder_speaker(speaker: str | None) -> str | None:
    """Return trimmed speaker name when valid and non-placeholder, else None."""
    if speaker is None:
        return None
    clean = " ".join(str(speaker).strip().split())
    if not clean or is_placeholder_speaker(clean):
        return None
    return clean


def resolve_deterministic_speaker_or_author(
    *,
    raw_speaker: str | None = None,
    scope_text: str | None = None,
    subject_canonical_name: str | None = None,
    subject_node_type: str | None = None,
    fallback_speaker: str | None = None,
) -> str | None:
    """Resolve deterministic non-placeholder speaker from explicit, inline, or stakeholder provenance."""
    inline_spk = extract_inline_turn_speaker(scope_text)
    explicit_spk = clean_non_placeholder_speaker(raw_speaker)
    if inline_spk and explicit_spk and not speakers_match_identity(inline_spk, explicit_spk):
        return explicit_spk
    if explicit_spk:
        return explicit_spk
    if inline_spk:
        return inline_spk
    ntype = str(subject_node_type or "").strip().lower()
    if ntype in {"stakeholder", "person", "internal_person", "person_profile"}:
        subj_spk = clean_non_placeholder_speaker(subject_canonical_name)
        if subj_spk and not is_structured_profile_card_claim(scope_text):
            return subj_spk
    return clean_non_placeholder_speaker(fallback_speaker)


def _validate_extracted_person_name(candidate_name: str) -> str | None:
    cleaned = re.sub(r"\s*\([^)]*\)", "", candidate_name).strip()
    tokens = [t.lower() for t in cleaned.split()]
    if len(tokens) < 2 or len(tokens) > 4:
        return None
    if any(tok in _CONCEPT_HEADING_TOKENS for tok in tokens):
        return None
    if is_placeholder_speaker(cleaned) or is_organization_or_unresolved_speaker(cleaned):
        return None
    return cleaned


def extract_inline_turn_speaker(scope_text: str | None) -> str | None:
    """Extract leading '**First Last:**' or 'First Last stated/noted...' human speaker prefix."""
    if not scope_text:
        return None
    txt = str(scope_text).lstrip()
    m = _INLINE_BOLD_PREFIX_RE.match(txt)
    if m:
        res = _validate_extracted_person_name(m.group(1) or m.group(2) or "")
        if res:
            return res
    m_mid = _MID_LINE_SPEAKER_PREFIX_RE.match(txt)
    if m_mid:
        res = _validate_extracted_person_name(m_mid.group(1) or "")
        if res:
            return res
    m_lead = _LEAD_IN_STATEMENT_VERB_RE.match(txt)
    if m_lead:
        return _validate_extracted_person_name(m_lead.group(1))
    return None


def _canonical_person_tokens(name: str) -> list[str]:
    cleaned = re.sub(r"\([^)]*\)", " ", name.lower())
    raw_toks = [t for t in re.findall(r"[a-z]+", cleaned) if len(t) > 1]
    return [_NICKNAME_EQUIVALENTS.get(t, t) for t in raw_toks]


def speakers_match_identity(inline_speaker: str, recorded_speaker: str) -> bool:
    """Return True when inline turn speaker and recorded speaker refer to the same person."""
    rec_low = recorded_speaker.strip().lower()
    inl_low = inline_speaker.strip().lower()
    if rec_low == inl_low:
        return True
    if rec_low == "operator" and inl_low in {"amit ayer", "amit"}:
        return True
    toks_a = _canonical_person_tokens(inline_speaker)
    toks_b = _canonical_person_tokens(recorded_speaker)
    if not toks_a or not toks_b:
        return False
    return toks_a[0] == toks_b[0] and toks_a[-1] == toks_b[-1]


def _extract_leading_bold_or_mid_speaker(scope_text: str | None) -> str | None:
    if not scope_text:
        return None
    txt = str(scope_text).lstrip()
    m = _INLINE_BOLD_PREFIX_RE.match(txt)
    if m:
        return _validate_extracted_person_name(m.group(1) or m.group(2) or "")
    m_mid = _MID_LINE_SPEAKER_PREFIX_RE.match(txt)
    return _validate_extracted_person_name(m_mid.group(1) or "") if m_mid else None


def has_inline_speaker_contradiction(scope_text: str | None, speaker_or_author: str | None) -> bool:
    """Return True when scope_text opens with '**Person A:**' contradicting speaker_or_author."""
    inline_spk = _extract_leading_bold_or_mid_speaker(scope_text)
    if not inline_spk:
        return False
    if not speaker_or_author or not str(speaker_or_author).strip() or is_placeholder_speaker(speaker_or_author):
        return True
    return not speakers_match_identity(inline_spk, str(speaker_or_author))


def is_spoken_meeting_source(
    source_type: str | None, source_name: str | None, cold_archive_ref: str | None = None
) -> bool:
    """Return True when a source represents a spoken intake session rather than a document."""
    cref = str(cold_archive_ref or "").strip().lower()
    if "01_" in cref and "transcript" in cref:
        return True
    if str(source_type or "").strip().lower() != "dual_pipeline_intake":
        return False
    sname = str(source_name or "").strip().lower()
    if any(sub in sname for sub in _NON_SPOKEN_DOC_SUBSTRINGS):
        return False
    return True


def is_structured_profile_card_claim(scope_text: str | None) -> bool:
    """Return True when scope_text is a structured entity summary card rather than dialogue."""
    if not scope_text:
        return False
    s = str(scope_text).strip()
    if s.startswith(("- **", "| ")) or "(source-confirmed speaker" in s.lower():
        return True
    return bool(_STRUCTURED_CARD_RE.match(s) or _PAREN_ROLE_CARD_RE.match(s) or _ENTITY_ANCHOR_CARD_RE.match(s))


def _has_cnode_prefix(record_id: str | None, subject_node_id: str | None) -> bool:
    return bool(
        (record_id and str(record_id).startswith("cnode_"))
        or (subject_node_id and str(subject_node_id).startswith("cnode_"))
    )


def _has_cross_project_persona_leak(
    subject_node_id: str | None, source_name: str | None, blob_low: str
) -> bool:
    is_nexus = "nexus" in str(subject_node_id or "").lower() or "project_nexus" in str(source_name or "").lower()
    return is_nexus and any(p in blob_low for p in _NEET_ONLY_PERSONAS)


def has_synthetic_or_cross_project_pollution(
    *,
    record_id: str | None = None,
    subject_node_id: str | None = None,
    scope_text: str | None = None,
    speaker_or_author: str | None = None,
    source_name: str | None = None,
) -> str | None:
    """Detect banned synthetic test markers or APEX_TRANS persona contamination in Nexus."""
    if _has_cnode_prefix(record_id, subject_node_id):
        return "synthetic_cnode_prefix"
    blob_low = f"{scope_text or ''} {speaker_or_author or ''}".lower()
    if any(m.lower() in blob_low for m in SYNTHETIC_FIXTURE_MARKERS):
        return "synthetic_fixture_marker"
    if any(m in blob_low for m in GARBLED_ASR_PROPER_NOUN_MARKERS):
        return "garbled_asr_proper_noun"
    if _has_cross_project_persona_leak(subject_node_id, source_name, blob_low):
        return "cross_project_persona_contamination"
    return None


def is_raw_spoken_dialogue(scope_text: str | None) -> bool:
    """Return True when text exhibits first/second-person spoken dialogue markers."""
    if not scope_text or is_structured_profile_card_claim(scope_text):
        return False
    return bool(_SPOKEN_DIALOGUE_MARKER_RE.search(str(scope_text)))


def is_clearable_structured_placeholder(
    scope_text: str | None,
    qualifiers_json: str | dict[str, Any] | None = None,
    locator_value: str | None = None,
    validation_state: str | None = None,
    speaker_or_author: str | None = None,
    source_type: str | None = None,
    source_name: str | None = None,
    cold_archive_ref: str | None = None,
) -> bool:
    """Return True when a claim is a structured non-dialogue card whose placeholder speaker can be cleared."""
    clean_txt = normalize_candidate_claim_text(scope_text)
    if is_degenerate_single_token_claim(clean_txt):
        return False
    if is_structured_profile_card_claim(clean_txt) or " is a reported speaker in " in clean_txt:
        return True
    if source_type and not is_spoken_meeting_source(source_type, source_name, cold_archive_ref):
        return True
    q = parse_qualifiers_dict(qualifiers_json)
    ctype = str(q.get("dual_pipeline_candidate_type") or q.get("candidate_type") or "").strip().lower()
    if ctype in _STRUCTURED_NON_DIALOGUE_CANDIDATE_TYPES:
        return True
    if q.get("action_status") or str(q.get("answer_use") or "").strip().lower() == "guidance":
        return True
    loc = str(locator_value or "").strip().lower()
    if ctype in {"risk", "dependency"} and not _TS_LOCATOR_RE.search(loc) and loc != "## transcript":
        return True
    if loc in {"hitl_decision_ledger", "executive summary", "overview & context"} or (
        loc.startswith(("attachment:", "assertion:", "claim:", "# ", "## ", "**participants", "section ", "executive summary", "ambiguity lock register"))
        and not _TS_LOCATOR_RE.search(loc)
        and loc != "## transcript"
    ):
        return True
    return False


def _classify_spoken_or_missing_speaker(
    txt: str,
    speaker_or_author: str | None,
    subject_node_id: str | None,
    source_type: str | None,
    source_name: str | None,
    qualifiers_json: str | dict[str, Any] | None,
    locator_value: str | None,
    cold_archive_ref: str | None = None,
) -> str | None:
    is_spoken = is_spoken_meeting_source(source_type, source_name, cold_archive_ref)
    clean_spk = str(speaker_or_author or "").strip()
    if clean_spk and clean_spk.lower() in SINGLE_TOKEN_SPEAKER_EXPANSIONS:
        return "single_token_speaker_name"
    if (is_spoken or str(source_type or "").strip().lower() == "dual_pipeline_intake") and is_organization_or_unresolved_speaker(speaker_or_author):
        return "organization_or_unresolved_speaker"
    if is_clearable_structured_placeholder(
        txt, qualifiers_json, locator_value, source_type=source_type, source_name=source_name, cold_archive_ref=cold_archive_ref
    ):
        return None
    if clean_spk:
        return None
    is_workstream_subj = str(subject_node_id or "").startswith(("workstream_", "program_"))
    if is_spoken or (is_raw_spoken_dialogue(txt) and is_workstream_subj):
        return "missing_meeting_speaker"
    return None


def classify_speaker_attribution_defect(
    *,
    predicate: str | None,
    scope_text: str | None,
    speaker_or_author: str | None,
    subject_node_id: str | None = None,
    source_type: str | None = None,
    source_name: str | None = None,
    assertion_id: str | None = None,
    qualifiers_json: str | dict[str, Any] | None = None,
    locator_value: str | None = None,
    transcript_timestamp: str | None = None,
    validation_state: str | None = None,
    cold_archive_ref: str | None = None,
) -> str | None:
    """Return deterministic defect code if an assertion violates speaker or purity invariants."""
    syn = has_synthetic_or_cross_project_pollution(
        record_id=assertion_id, subject_node_id=subject_node_id,
        scope_text=scope_text, speaker_or_author=speaker_or_author, source_name=source_name,
    )
    if syn:
        return syn
    if str(predicate or "HAS_CLAIM_TEXT").strip().upper() != "HAS_CLAIM_TEXT":
        return None
    txt = str(scope_text or "").lstrip()
    if txt.startswith(": "):
        return "stripped_empty_speaker_colon"
    if has_inline_speaker_contradiction(txt, speaker_or_author):
        return "inline_speaker_contradiction"
    if is_placeholder_speaker(speaker_or_author):
        return "placeholder_speaker_label"
    eff_loc = locator_value if locator_value is not None else transcript_timestamp
    return _classify_spoken_or_missing_speaker(
        txt, speaker_or_author, subject_node_id, source_type, source_name, qualifiers_json, eff_loc, cold_archive_ref
    )


def parse_qualifiers_dict(qualifiers_json: str | dict[str, Any] | None) -> dict[str, Any]:
    """Parse qualifiers_json safely into a dictionary."""
    if isinstance(qualifiers_json, dict):
        return dict(qualifiers_json)
    if not qualifiers_json or not str(qualifiers_json).strip():
        return {}
    try:
        parsed = json.loads(str(qualifiers_json))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def has_non_promotable_qualifiers(qualifiers_json: str | dict[str, Any] | None) -> bool:
    """Return True when qualifiers explicitly forbid canonical fact promotion."""
    q = parse_qualifiers_dict(qualifiers_json)
    if not q:
        return False
    if str(q.get("answer_use") or "").lower() in _NON_PROMOTABLE_ANSWER_USES:
        return True
    if str(q.get("retrieval_kind") or "").lower() in _NON_PROMOTABLE_RETRIEVAL_KINDS:
        return True
    if str(q.get("dual_pipeline_candidate_type") or "").lower() in _NON_PROMOTABLE_CANDIDATE_TYPES:
        return True
    if q.get("project_status_snapshot") is not None:
        return True
    if "dual_pipeline_candidate_id" not in q and q.get("fact_promotion_eligible") is False:
        return True
    return str(q.get("source_kind") or "") == "a2a_fact_vector_packet" and q.get("fact_promotion_eligible") is False


def _is_non_factual_guidance_type(q: dict[str, Any]) -> bool:
    return (
        str(q.get("dual_pipeline_candidate_type") or "").lower() == "action"
        or str(q.get("retrieval_kind") or "").lower() in {"guidance", "evidence_span", "entity_facet"}
        or str(q.get("answer_use") or "").lower() in {"guidance", *_NON_PROMOTABLE_ANSWER_USES}
    )


def has_stale_candidate_qualifiers(qualifiers_json: str | dict[str, Any] | None) -> bool:
    """Return True when a canonical row still carries candidate disposition or ineligible flags."""
    q = parse_qualifiers_dict(qualifiers_json)
    if not q or has_non_promotable_qualifiers(q):
        return False
    if str(q.get("dual_pipeline_disposition") or "").upper() == "CANDIDATE":
        return True
    if _is_non_factual_guidance_type(q):
        return False
    return (
        q.get("fact_promotion_eligible") is False
        or str(q.get("answer_use") or "").lower() == "candidate_hypothesis"
    )


def sync_canonical_qualifiers_json(qualifiers_json: str | None) -> str:
    """Return canonicalized qualifiers_json for an approved, grounded canonical assertion."""
    q = parse_qualifiers_dict(qualifiers_json)
    if not q:
        return qualifiers_json or "{}"
    q.pop("speaker_hygiene_quarantined", None)
    if "dual_pipeline_disposition" in q:
        q["dual_pipeline_disposition"] = "APPROVED"
    if not _is_non_factual_guidance_type(q) and not has_non_promotable_qualifiers(q):
        if q.get("fact_promotion_eligible") is False:
            q["fact_promotion_eligible"] = True
        if str(q.get("answer_use") or "").lower() == "candidate_hypothesis":
            q["answer_use"] = "factual_answer"
    return json.dumps(q, sort_keys=True)
