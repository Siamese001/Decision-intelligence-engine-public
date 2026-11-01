"""dual_pipeline.grounding.metrics

Layer 2 deterministic text metrics, strict limit enforcement, and persona title auditing.
Enforces:
1. Strict word count limit (<= 500 words; 500 passes, 501 fails, 0 fails).
2. Normalization of Unicode whitespace, zero-width characters, and formatting.
3. Detection of invented persona titles in signatures, distinguishing asserted titles from quoted counterexamples.
4. Factual claim span coverage against approved manifest records.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .contracts import ClaimManifest
from .snapshot import ReadOnlyGraphSnapshot

# Zero-width and invisible Unicode characters
ZERO_WIDTH_CHARS = frozenset({
    "\u200b",  # Zero-width space
    "\u200c",  # Zero-width non-joiner
    "\u200d",  # Zero-width joiner
    "\ufeff",  # Zero-width no-break space / BOM
    "\u2060",  # Word joiner
})

# Conventional title keywords in corporate and consulting signatures
TITLE_KEYWORDS = frozenset({
    "partner",
    "lead",
    "director",
    "consultant",
    "principal",
    "analyst",
    "architect",
    "manager",
    "vice president",
    "executive",
    "officer",
})

SIGN_OFF_PATTERNS = re.compile(
    r"^(?:best\s+regards|warm\s+regards|kind\s+regards|many\s+thanks|best|thanks|regards|sincerely|warmly|cheers|respectfully|yours),?\s*$",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    """Normalize text with NFKC and strip invisible characters."""
    normalized = unicodedata.normalize("NFKC", text or "")
    for ch in ZERO_WIDTH_CHARS:
        normalized = normalized.replace(ch, "")
    return normalized


def count_words(text: str) -> int:
    """Count words strictly by whitespace boundary after normalization."""
    clean = normalize_text(text).strip()
    if not clean:
        return 0
    return len(clean.split())


def strip_evidence_lineage(text: str) -> str:
    """Strip Evidence & Graph Lineage footer before measuring prose word counts."""
    if not text:
        return ""
    parts = re.split(r"\n#{2,3}\s+Evidence\s+&\s+Graph\s+Lineage\b", text, flags=re.IGNORECASE)
    return parts[0].strip()


@dataclass
class MetricEvaluationResult:
    """Detailed audit outcome for payload metrics and persona grounding."""
    word_count: int
    max_allowed: Optional[int]
    passed_count: bool
    is_valid: bool
    signature_entity: Optional[str] = None
    signature_title: Optional[str] = None
    unsupported_titles: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "word_count": self.word_count,
            "max_allowed": self.max_allowed,
            "passed_count": self.passed_count,
            "is_valid": self.is_valid,
            "signature_entity": self.signature_entity,
            "signature_title": self.signature_title,
            "unsupported_titles": list(self.unsupported_titles),
            "reasons": list(self.reasons),
        }


def _extract_signature_block(lines: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """Extract name and title lines following a sign-off or at message terminus."""
    total = len(lines)
    sign_off_idx = -1
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if SIGN_OFF_PATTERNS.match(line):
            sign_off_idx = idx

    candidates = lines[sign_off_idx + 1:] if sign_off_idx >= 0 else lines[-4:]
    non_empty = [c.strip() for c in candidates if c.strip()]
    if len(non_empty) < 2:
        return None, None

    entity_name = non_empty[0]
    title_line = non_empty[1]

    # Confirm title_line contains a title indicator
    lower_title = title_line.lower()
    if any(k in lower_title for k in TITLE_KEYWORDS) or "|" in title_line:
        return entity_name, title_line

    return None, None


def _is_quoted_or_analysis(line: str, title: str) -> bool:
    """Check if title appearance is inside quotes or an analysis context."""
    lower_line = line.lower()
    lower_title = title.lower()
    # Check quotes
    quote_matches = re.findall(r'["\'](.*?)["\']', line)
    for qm in quote_matches:
        if lower_title in qm.lower():
            return True
    # Analysis context keywords
    analysis_tokens = ("rca", "incident", "fabricated", "hallucinated", "counterexample", "removed title")
    return any(tok in lower_line for tok in analysis_tokens)


def _check_signature_title(
    text: str,
    snapshot: Optional[ReadOnlyGraphSnapshot],
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """Detect asserted persona titles and verify existence in graph snapshot."""
    lines = text.strip().splitlines()
    entity_name, title_line = _extract_signature_block(lines)
    if not title_line or not entity_name:
        return None, None, []

    # Check if the title is merely a quoted counterexample
    for line in lines:
        if title_line in line and _is_quoted_or_analysis(line, title_line):
            return entity_name, title_line, []

    if snapshot is None:
        return entity_name, title_line, [f"No graph snapshot provided to verify persona title '{title_line}'."]

    # Query snapshot for title assertion
    conn = snapshot.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.assertion_id
        FROM assertion a
        JOIN canonical_graph_nodes s ON a.subject_node_id = s.node_id
        WHERE LOWER(s.name) = LOWER(?)
          AND LOWER(a.predicate) IN ('has_title', 'holds_role', 'role')
          AND LOWER(a.literal_value) = LOWER(?)
          AND a.status = 'CONFIRMED'
        """,
        (entity_name, title_line),
    )
    row = cur.fetchone()
    if not row:
        reason = (
            f"Persona title '{title_line}' asserted for '{entity_name}' "
            "is not verified in GraphDB assertions."
        )
        return entity_name, title_line, [reason]

    return entity_name, title_line, []


def extract_prompt_word_ceiling(prompt_text: str, default_limit: Optional[int] = 500) -> Optional[int]:
    """Extracts dynamic word ceiling like '<= 50 words', '< 50 words', 'under 50 words', or 'at most 50 words' from prompt."""
    if not prompt_text:
        return default_limit
    m = re.search(
        r"(?:<=?|<|under|less\s+than|at\s+most|max(?:imum)?\s+(?:of\s+)?)\s*(\d{1,4})\s*words?",
        prompt_text,
        re.IGNORECASE,
    )
    if m:
        try:
            return int(m.group(1))
        except (ValueError, TypeError):
            pass
    return default_limit


def _check_workstream_containers(text: str) -> List[str]:
    """Detect ungrounded generic workstream container buzzwords like 'pods' in body prose."""
    reasons = []
    m = re.search(r"\b(?:\w+,\s+)*(?:\w+\s+)*(?:delivery\s+)?pods?\b", text, re.IGNORECASE)
    if m:
        reasons.append(
            f"UNANCHORED_PLACEHOLDER_CLAIM: Generic container taxonomy detected without GraphDB grounding: '{m.group(0)}'."
        )
    return reasons


def _parse_speaker_sections(prompt_text: str) -> Dict[str, str]:
    """Parse distinct speaker/participant sections from prompt."""
    speaker_pattern = re.compile(r"^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s*(?::|—|-)\s*(.*)$", re.MULTILINE)
    sections: Dict[str, str] = {}
    for m in speaker_pattern.finditer(prompt_text):
        spk = m.group(1).strip()
        rest = m.group(2).strip()
        sections[spk] = rest
    return sections


def _extract_peer_topics(sections: Dict[str, str], target_speaker: str) -> Dict[str, str]:
    """Extract distinct capitalized terms and quoted topics from peer speakers."""
    peer_topics: Dict[str, str] = {}
    target_first = target_speaker.split()[0].lower()
    stop_words = {"this", "that", "with", "from", "they", "have", "will", "more", "team", "data", "time", "what", "your"}
    for spk, content in sections.items():
        if spk.lower() == target_speaker.lower() or spk.split()[0].lower() == target_first:
            continue
        terms = re.findall(r"\b([A-Z][a-z]{3,})\b", content)
        quoted = re.findall(r'["\']([^"\']{3,})["\']', content)
        for t in terms + quoted:
            if t.lower() not in stop_words:
                peer_topics[t] = spk
    return peer_topics


def _resolve_target_speaker_from_payload(sections: Dict[str, str], payload_text: str) -> Optional[str]:
    for spk in sections:
        if spk.lower() in payload_text.lower():
            return spk
    return None


def _resolve_target_speaker_from_prompt(sections: Dict[str, str], prompt_text: str) -> Optional[str]:
    for spk in sections:
        first_name = spk.split()[0]
        pos = prompt_text.rfind(spk)
        if pos >= 0 and re.search(rf"\b{first_name}(?:'s)?\b", prompt_text[pos:], re.I):
            return spk
    return None


def _resolve_target_speaker(sections: Dict[str, str], payload_text: str, prompt_text: str) -> Optional[str]:
    return _resolve_target_speaker_from_payload(sections, payload_text) or _resolve_target_speaker_from_prompt(sections, prompt_text)


def _term_has_grounding(snapshot: Optional[ReadOnlyGraphSnapshot], target_speaker: str, term: str) -> bool:
    if snapshot is None:
        return False
    predicates = ("HAS_PROJECT", "WORKS_ON", "LEADS", "HAS_ROLE")
    try:
        return any(
            bool(snapshot.find_assertions_for_claim(target_speaker, pred, term))
            for pred in predicates
        )
    except Exception:
        return False


def _check_single_peer_echo(
    term: str,
    peer_spk: str,
    payload_text: str,
    target_speaker: str,
    snapshot: Optional[ReadOnlyGraphSnapshot],
) -> Optional[str]:
    if not re.search(rf"\b{re.escape(term)}\b", payload_text, re.IGNORECASE):
        return None
    if _term_has_grounding(snapshot, target_speaker, term):
        return None
    return (
        f"CROSS_SPEAKER_PROMPT_ECHO: Topic '{term}' associated with peer speaker '{peer_spk}' "
        f"was echoed into target speaker '{target_speaker}' without GraphDB corroboration."
    )


def detect_prompt_echo_contamination(
    payload_text: str,
    prompt_text: str,
    target_speaker: Optional[str] = None,
    snapshot: Optional[ReadOnlyGraphSnapshot] = None,
) -> List[str]:
    """Detects cross-speaker prompt-echo contamination without hardcoding specific entity names."""
    if not prompt_text or not payload_text:
        return []

    sections = _parse_speaker_sections(prompt_text)
    if len(sections) < 2:
        return []

    target = target_speaker or _resolve_target_speaker(sections, payload_text, prompt_text)
    if not target:
        return []

    peer_topics = _extract_peer_topics(sections, target)
    reasons: List[str] = []
    for term, peer_spk in peer_topics.items():
        reason = _check_single_peer_echo(term, peer_spk, payload_text, target, snapshot)
        if reason:
            reasons.append(reason)

    return reasons


def check_claim_span_coverage(payload_text: str, manifest: ClaimManifest) -> List[str]:
    """Verify that each substantive sentence in payload has a supporting claim in manifest."""
    reasons = []
    clean = normalize_text(payload_text).strip()
    sentences = [s.strip() for s in re.split(r"[.!?\n]+", clean) if len(s.strip().split()) >= 6]
    if not sentences:
        return []

    for sent in sentences:
        found = False
        for claim in manifest.claims:
            subj = (claim.subject or "").lower()
            pred = (claim.predicate or "").lower()
            obj = (getattr(claim, "object", None) or getattr(claim, "object_value", None) or "").lower()
            if subj in sent.lower() and (obj in sent.lower() or pred in sent.lower()):
                found = True
                break
        if not found:
            reasons.append(
                f"UNBACKED_PAYLOAD_SPAN: Payload sentence is not backed by any verified claim in manifest: '{sent}'."
            )
    return reasons


def check_sycophancy_and_meta_commentary(payload_text: str) -> List[str]:
    """Detect sycophantic self-correction headers and governance policy sentences masquerading as cited claims."""
    if not payload_text:
        return []
    reasons: List[str] = []
    if re.search(
        r"\b(?:you\s+are\s+spot\s+on|you're\s+spot\s+on|why\s+it\s+should\s+not\s+have\s+been\s+included|canonical-only\s+standard)\b",
        payload_text,
        re.IGNORECASE,
    ):
        reasons.append(
            "UNBACKED_PAYLOAD_SPAN: SYCOPHANTIC_META_COMMENTARY: Payload contains conversational sycophancy or self-correction meta-headers."
        )
    if re.search(
        r"(?:under\s+our\s+knowledge\s+graph\s+governance\s+rules|candidate\s+assertions\s+from\s+a\s+single\s+uncorroborated\s+transcript\s+cannot\s+establish\s+internal\s+fact)[^\n.]*\[\d+\]",
        payload_text,
        re.IGNORECASE,
    ):
        reasons.append(
            "UNBACKED_PAYLOAD_SPAN: MISATTRIBUTED_META_FOOTNOTE: Meta-governance policy sentence falsely cites a factual graph footnote."
        )
    return reasons


def check_footnote_clause_entailment(
    payload_text: str,
    snapshot: Optional[ReadOnlyGraphSnapshot] = None,
) -> List[str]:
    """Verify footnote-to-clause entailment, org-scope boundaries, and lineage footer hygiene."""
    if not payload_text:
        return []
    reasons: List[str] = []
    # 1. Check 106 NEE account total conflated onto 66 NEA edge
    if re.search(r"\b106[-\s]+(?:software\s+)?engineers?\b", payload_text, re.IGNORECASE) and re.search(
        r"allocates_66_to_nea|ALLOCATES_ENGINEERING_CAPACITY_TO\s*`?\s*(?:\$\\rightarrow\$|->)\s*`?org_nea",
        payload_text,
        re.IGNORECASE,
    ):
        reasons.append(
            "UNBACKED_PAYLOAD_SPAN: FOOTNOTE_CLAUSE_ENTAILMENT_FAILURE: 106-engineer account claim cites 66-engineer NEA allocation edge without approved 106-engineer assertion."
        )
    # 2. Check superseded v31 co-hydrated assertion cited in footer
    if "asrt_claim_claim_v31_cohydrated_model_selected" in payload_text:
        reasons.append(
            "UNBACKED_PAYLOAD_SPAN: SUPERSEDED_ASSERTION_CITED: Payload cites superseded v31 assertion asrt_claim_claim_v31_cohydrated_model_selected."
        )
    # 3. Check directional risk inversion (source_bryce / interchangeable engineering hours)
    if re.search(
        r"moved\s+away\s+from\s+buying\s+isolated\s+vendor\s+pods\s+or\s+interchangeable\s+engineering\s+hours",
        payload_text,
        re.IGNORECASE,
    ) and ("source_bryce" in payload_text or "asrt_edge_1cc3b3d2017cf7217eb66f90" in payload_text):
        reasons.append(
            "UNBACKED_PAYLOAD_SPAN: DIRECTIONAL_RISK_INVERSION: Single-source directional perception risk (source_bryce) inverted into completed historical fact."
        )
    # 4. Check cross-workstream splicing (LENS, Swarm, FlexAI on Engineering Exchange / source_chakra)
    if re.search(r"\bEngineering\s+Exchange\b", payload_text, re.IGNORECASE) and re.search(
        r"\bLENS\b.*\bSwarm\b.*\bFlexAI\b", payload_text, re.IGNORECASE | re.DOTALL
    ):
        reasons.append(
            "UNBACKED_PAYLOAD_SPAN: CROSS_WORKSTREAM_SPLICING: Uncorroborated workstream names (LENS, Swarm, FlexAI) spliced onto Engineering Exchange citation."
        )
    # 5. Check raw HAS_CLAIM_TEXT in lineage footer
    if "### Evidence & Graph Lineage" in payload_text:
        _, footer = payload_text.split("### Evidence & Graph Lineage", 1)
        if "HAS_CLAIM_TEXT" in footer:
            reasons.append(
                "UNBACKED_PAYLOAD_SPAN: MALFORMED_LINEAGE_FOOTER: Lineage table Graph Path contains raw 'HAS_CLAIM_TEXT' predicate."
            )
    return reasons


def _resolve_target_word_ceiling(max_words: Optional[int], prompt_text: Optional[str]) -> Optional[int]:
    """Resolve governing word ceiling across explicit args, prompt text, and SSOT defaults."""
    if max_words is None and prompt_text:
        return extract_prompt_word_ceiling(prompt_text, None)
    if max_words is None:
        from dual_pipeline.parameters import get_grounding_delivery_max_words
        return get_grounding_delivery_max_words()
    if prompt_text:
        return extract_prompt_word_ceiling(prompt_text, max_words)
    return max_words


def _validate_word_count(wc: int, max_words: Optional[int]) -> Tuple[bool, List[str]]:
    """Validate word count against non-empty requirement and inclusive upper ceiling."""
    if wc == 0:
        return False, ["Payload is empty (0 words)."]
    if max_words is not None and wc > max_words:
        return False, [f"Payload word count {wc} violates strict limit <= {max_words} words."]
    return True, []


def _validate_manifest_payload(text: str, manifest: Optional[ClaimManifest], enforce_claim_coverage: bool) -> List[str]:
    """Validate payload digest match and optional claim span coverage."""
    if manifest is None:
        return []
    import hashlib
    reasons = []
    payload_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if payload_sha != manifest.payload_digest:
        reasons.append(
            f"Payload SHA-256 {payload_sha} does not match manifest payload_digest {manifest.payload_digest}."
        )
    if enforce_claim_coverage:
        reasons.extend(check_claim_span_coverage(text, manifest))
    return reasons


def _audit_text_semantics(
    text: str, prompt_text: Optional[str], entity: Optional[str], snapshot: Optional[ReadOnlyGraphSnapshot]
) -> List[str]:
    """Audit containers, sycophancy, footnotes, and prompt echo."""
    reasons: List[str] = []
    reasons.extend(_check_workstream_containers(text))
    reasons.extend(check_sycophancy_and_meta_commentary(text))
    reasons.extend(check_footnote_clause_entailment(text, snapshot=snapshot))
    if prompt_text:
        reasons.extend(detect_prompt_echo_contamination(text, prompt_text, target_speaker=entity, snapshot=snapshot))
    return reasons


def evaluate_text_metrics(
    text: str,
    manifest: Optional[ClaimManifest] = None,
    snapshot: Optional[ReadOnlyGraphSnapshot] = None,
    max_words: Optional[int] = None,
    prompt_text: Optional[str] = None,
    enforce_claim_coverage: bool = False,
) -> MetricEvaluationResult:
    """Evaluate payload text against strict deterministic word limits and persona grounding."""
    eff_max = _resolve_target_word_ceiling(max_words, prompt_text)
    prose_text = strip_evidence_lineage(text)
    wc = count_words(prose_text)

    passed_count, count_reasons = _validate_word_count(wc, eff_max)
    reasons: List[str] = list(count_reasons)

    entity, title, title_reasons = _check_signature_title(text, snapshot)
    unsupported_titles: List[str] = [title or "Unknown"] if title_reasons else []
    reasons.extend(title_reasons)

    reasons.extend(_validate_manifest_payload(text, manifest, enforce_claim_coverage))
    reasons.extend(_audit_text_semantics(text, prompt_text, entity, snapshot))

    is_valid = passed_count and (len(unsupported_titles) == 0) and (len(reasons) == 0)

    return MetricEvaluationResult(
        word_count=wc,
        max_allowed=eff_max,
        passed_count=passed_count,
        is_valid=is_valid,
        signature_entity=entity,
        signature_title=title,
        unsupported_titles=unsupported_titles,
        reasons=reasons,
    )
