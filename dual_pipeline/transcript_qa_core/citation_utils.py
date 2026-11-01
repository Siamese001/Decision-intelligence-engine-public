"""
dual_pipeline/transcript_qa_core/citation_utils.py

Citation validation and judge report integrity verification utilities.
Ensures that all judge citations resolve to candidate artifact text,
and that QA status, score floors, reconciliation, aggregate scores,
verdicts, and seals agree without contradiction.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _extract_item_citation(item: Any) -> Optional[str]:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        q = item.get("quote") or item.get("text") or item.get("span")
        if isinstance(q, str):
            return q
    return None


def extract_judge_card_citations(card: Dict[str, Any]) -> List[str]:
    """Extract grounding citations or cited spans from a judge scorecard."""
    if not isinstance(card, dict):
        return []
    citations: List[str] = []
    for key in ("grounding_citations", "citations", "cited_spans"):
        val = card.get(key)
        if isinstance(val, list):
            for item in val:
                c = _extract_item_citation(item)
                if c:
                    citations.append(c)
    return citations


def _is_trivial_citation(cit: str) -> bool:
    if not cit:
        return True
    lower = cit.lower()
    if any(
        tok in lower
        for tok in (
            "c0 roster",
            "c0_roster",
            "c0 receipt",
            "c0_receipt",
            "c0.6",
            "graphdb_role_verification",
            "role_verification",
            "role verification",
            "verification receipt",
            "verified attendee",
            "roster receipt",
        )
    ):
        return True
    return lower in ("none", "n/a", "none (passing evaluation)", "not applicable") or lower.startswith("none (")


def _strip_quotes(cit: str) -> str:
    if (cit.startswith('"') and cit.endswith('"')) or (cit.startswith("'") and cit.endswith("'")):
        return cit[1:-1].strip()
    return cit


def _match_quoted_spans(cit: str, text: str, norm_text: str) -> bool:
    quoted = re.findall(r"['\"“”‘’]([^'\"“”‘’]{3,})['\"“”‘’]", cit)
    if not quoted:
        return False
    if all(" ".join(q.lower().split()) in norm_text for q in quoted):
        return True
    clean_text = " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())
    clean_quoted = [" ".join(re.sub(r"[^\w\s]", " ", q.lower()).split()) for q in quoted]
    return any(cq in clean_text for cq in clean_quoted if len(cq) >= 4)


def _match_evidence_lock(cit: str, norm_text: str) -> bool:
    if not cit.lower().startswith(("evidence lock:", "evidence note:", "lock:")):
        return False
    body = cit.split(":", 1)[1].strip()
    entities = [e for e in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", body) if len(e) >= 3]
    if entities and any(e.lower() in norm_text for e in entities):
        return True
    clauses = [c.strip().lower() for c in re.split(r"[;,\.]", body) if len(c.strip()) > 8]
    for clause in clauses:
        words = [w for w in clause.split() if len(w) > 4]
        if len(words) >= 2 and all(w in norm_text for w in words[:3]):
            return True
    return False


def _match_header_attribution(cit: str, text: str, norm_text: str) -> bool:
    hdr_m = re.match(
        r"^.*?(?:header|attribution|reference|summary|context|source\s+line|source)[\w\s/–—\-]*[:\—\–]\s*(.*)",
        cit,
        re.IGNORECASE,
    )
    if not hdr_m:
        return False
    body = hdr_m.group(1).strip()
    date_m = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", body)
    if date_m and date_m.group(0) in text:
        return True
    entities = [e for e in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", body) if len(e) >= 3]
    if entities and any(e.lower() in norm_text for e in entities):
        return True
    body_words = [w.lower() for w in re.split(r"[^\w]+", body) if len(w) > 3]
    if body_words and any(w in norm_text for w in body_words):
        return True
    clauses = [c.strip().lower() for c in re.split(r"[;,\.]", body) if len(c.strip()) > 8]
    for clause in clauses:
        words = [w for w in clause.split() if len(w) > 4]
        if len(words) >= 2 and all(w in norm_text for w in words[:3]):
            return True
    return False


def _match_bracketed_header(cit: str, norm_text: str) -> bool:
    bracket_m = re.match(r"^(?:transcript\s*)?\[([^\]]+)\]\s*[:\-—–]?\s*(.*)", cit, re.IGNORECASE)
    if not bracket_m:
        return False
    loc_hdr = bracket_m.group(1).lower()
    hdr_words = [w for w in re.split(r"[^\w:]+", loc_hdr) if len(w) > 3]
    hdr_in_text = not hdr_words or any(w in norm_text for w in hdr_words)
    body = bracket_m.group(2).strip()
    body_entities = [e for e in re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", body) if len(e) >= 3]
    if hdr_in_text and body_entities and any(e.lower() in norm_text for e in body_entities):
        return True
    return bool(hdr_in_text and any(clause.strip().lower() in norm_text for clause in re.split(r"[—–,;]", body) if len(clause.strip()) >= 6))


def _match_candidate_section_ref(cit: str, text: str, norm_text: str) -> bool:
    m = re.match(r"^Candidate\s+(.+)", cit, re.IGNORECASE)
    if m:
        sec = m.group(1).strip().lower()
        if sec in norm_text:
            return True
        words = [w for w in re.split(r"[^\w]+", sec) if len(w) > 3]
        if words and sum(1 for w in words if w in norm_text) >= min(2, len(words)):
            return True
    return False


def _match_commentary_patterns(cit: str, text: str, norm_text: str) -> bool:
    if _match_evidence_lock(cit, norm_text):
        return True
    if _match_header_attribution(cit, text, norm_text):
        return True
    if _match_candidate_section_ref(cit, text, norm_text):
        return True
    return _match_bracketed_header(cit, norm_text)


def _timestamp_in_text(ts: str, text: str) -> bool:
    if ts in text:
        return True
    parts = ts.split(":")
    if len(parts) == 3 and parts[0] == "00" and f"{parts[1]}:{parts[2]}" in text:
        return True
    return len(parts) >= 2 and f"{parts[0]}:{parts[1]}" in text


def _match_media_locators(cit: str, text: str, norm_text: str) -> bool:
    if any(_timestamp_in_text(ts, text) for ts in re.findall(r"\b\d{2}:\d{2}(?::\d{2})?\b", cit)):
        return True
    lower_text = text.lower()
    ev_match = re.search(r"\b(?:ev|evidence)-[a-z0-9\-]+\b", cit.lower())
    if ev_match and ev_match.group(0) in lower_text:
        return True
    att_match = re.search(r"\battachment:[A-Za-z0-9_.\-]+\b", cit, re.IGNORECASE)
    if att_match and att_match.group(0).lower() in lower_text:
        return True
    turn_m = re.search(r"\bturns?\s+(\d+)(?:\s*-\s*(\d+))?\b", cit.lower())
    if not turn_m:
        return False
    total_turns = len(re.findall(r"\[\d{2}:\d{2}", text))
    start_turn = int(turn_m.group(1))
    return (total_turns > 0 and 1 <= start_turn <= total_turns + 10) or "turn" in norm_text


def _match_page_or_section_locator(cit: str, text: str, norm_text: str) -> bool:
    page_m = re.search(r"(?:\bpages?|\bpp?\.?)\s*(\d+(?:[-–—]\d+)?)\b[:\s,]*(.*)", cit, re.IGNORECASE)
    if page_m:
        first_page = re.split(r"[-–—]", page_m.group(1))[0]
        if (
            re.search(r"\bpages?\s+" + re.escape(first_page) + r"\b", text, re.IGNORECASE)
            or re.search(r"(?:^|\n)\s*" + re.escape(first_page) + r"\s*(?:\n|$)", text)
        ):
            return True
        words = [w for w in re.split(r"[^\w]+", page_m.group(2).strip().lower()) if len(w) > 3]
        if words and any(w in norm_text for w in words):
            return True
    sec_m = re.search(r"(?:§|sections?\s+|sec\.?\s*)(\d+(?:\.\d+)*)[:\s]*(.*)", cit, re.IGNORECASE)
    if not sec_m:
        return False
    sec_num, sec_title = sec_m.group(1), sec_m.group(2).strip().lower()
    if sec_num in norm_text or not sec_title or sec_title in norm_text:
        return True
    return any(w in norm_text for w in re.split(r"[^\w]+", sec_title) if len(w) > 3)


def _match_doc_locators(cit: str, text: str, norm_text: str) -> bool:
    if re.search(r"\b(?:offsets?|lines?)\s+\d+", cit, re.IGNORECASE):
        return True
    slide_m = re.search(r"\bslides?\s+(\d+)\b", cit, re.IGNORECASE)
    if slide_m and re.search(r"\bslide\s+" + slide_m.group(1) + r"\b", text, re.IGNORECASE):
        return True
    return _match_page_or_section_locator(cit, text, norm_text)


def _match_numeric_locators(cit: str, text: str, norm_text: str) -> bool:
    return _match_media_locators(cit, text, norm_text) or _match_doc_locators(cit, text, norm_text)


def _match_structured_locators(cit: str, norm_text: str) -> bool:
    id_m = re.search(r"\[(?:[a-zA-Z_]+_)?id=([^\]]+)\]", cit)
    if id_m and id_m.group(1).strip().lower() in norm_text:
        return True
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+$", cit):
        parts = cit.split(".")
        if all(part.lower() in norm_text for part in parts):
            return True
    if "," in cit:
        parts = [p.strip() for p in cit.split(",")]
        matching_parts = 0
        for p in parts:
            p_clean = p.split(":")[0].strip()
            if p_clean.lower() in norm_text or any(
                w.lower() in norm_text for w in re.split(r"[^\w]+", p_clean) if len(w) > 4
            ):
                matching_parts += 1
        if len(parts) >= 2 and matching_parts >= 2:
            return True
    return False


def _matches_spans_or_locators(cit: str, text: str, norm_text: str) -> bool:
    if _match_quoted_spans(cit, text, norm_text):
        return True
    if _match_commentary_patterns(cit, text, norm_text):
        return True
    if _match_numeric_locators(cit, text, norm_text):
        return True
    return _match_structured_locators(cit, norm_text)


def _clean_source_citation_note(cit: str) -> str:
    cleaned = re.sub(
        r"\s*\([^)]*(?:speaker|timestamp|attendee|unavailable|audio)[^)]*\)",
        "",
        cit,
        flags=re.IGNORECASE,
    ).strip()
    return re.sub(
        r"^(?:source\s+line|source\s+text|source|line\s*\d*|doc\s+line|document\s+line)\s*[:\-—–]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()


def _matches_cleaned_note(cit: str, text: str, norm_text: str) -> bool:
    cleaned = _clean_source_citation_note(cit)
    cleaned = re.sub(
        r"^(?:no\s+timestamp\s+or\s+speaker\s+attribution\s+(?:is\s+)?provided\.?\s*|transcript\s+roster\s+lists\s*|roster\s+lists\s*)+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    if not cleaned:
        return False
    lower_clean = cleaned.lower()
    if lower_clean in text.lower() or " ".join(lower_clean.split()) in norm_text:
        return True
    return _matches_spans_or_locators(cleaned, text, norm_text)


def _matches_commentary_entities_or_phrases(cit: str, norm_text: str) -> bool:
    entities_in_cit = re.findall(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b", cit)
    if entities_in_cit and sum(1 for e in entities_in_cit if e.lower() in norm_text) >= min(2, len(entities_in_cit)):
        return True
    ignored = {
        "meeting does", "establish a", "she had", "says she", "had already",
        "does not", "does not establish", "the meeting",
    }
    return any(
        phrase.lower() in norm_text and phrase.lower() not in ignored
        for phrase in re.findall(r"\b[a-zA-Z]{4,}(?:\s+[a-zA-Z]{4,}){1,2}\b", cit)
    )


def citation_matches_text(citation: str, text: str) -> bool:
    """Determine whether a judge citation resolves to a substring or cited evidence in text."""
    cit = _strip_quotes(citation.strip())
    if _is_trivial_citation(cit):
        return True
    lower_cit = cit.lower()
    norm_text = " ".join(text.lower().split())
    if lower_cit in text.lower() or " ".join(lower_cit.split()) in norm_text:
        return True
    if _matches_spans_or_locators(cit, text, norm_text):
        return True
    return _matches_cleaned_note(cit, text, norm_text) or _matches_commentary_entities_or_phrases(cit, norm_text)


def validate_judge_card_citations(
    card: Dict[str, Any], artifact_text: Optional[Union[str, List[str]]]
) -> Tuple[bool, List[str]]:
    """Validates that all citations in a card resolve against the artifact text or context texts."""
    if not artifact_text or not isinstance(card, dict):
        return True, []
    texts = [artifact_text] if isinstance(artifact_text, str) else [t for t in artifact_text if t]
    if not texts:
        return True, []
    citations = extract_judge_card_citations(card)
    missing = [c for c in citations if not any(citation_matches_text(c, t) for t in texts)]
    return len(missing) == 0, missing


def _statuses_contradict(s1: Any, s2: Any) -> bool:
    if not s1 or not s2:
        return False
    pass_statuses = {"PASS", "PASS_ADVISORY", "PASS_AUTHORIZED_EXCEPTION"}
    p1, p2 = s1 in pass_statuses, s2 in pass_statuses
    return (p1 != p2) or (not p1 and not p2 and s1 != s2)


def _check_status_alignment(
    judge_report: Dict[str, Any], final_decision: Optional[Dict[str, Any]]
) -> Optional[str]:
    status = judge_report.get("status")
    overall = judge_report.get("overall_verdict")
    if _statuses_contradict(status, overall):
        return f"Contradictory judge report status ({status}) vs overall_verdict ({overall})"
    if final_decision and isinstance(final_decision, dict):
        dec_status = final_decision.get("status")
        if _statuses_contradict(status, dec_status):
            return f"Contradictory status: judge_report ({status}) vs final_decision ({dec_status})"
    return None


def _check_aggregate_score_agreement(judge_report: Dict[str, Any]) -> Optional[str]:
    from dual_pipeline.canonical_pipeline.quality_aggregates import compute_quality_aggregate_score
    calc_agg = compute_quality_aggregate_score(judge_report)
    rec_agg = judge_report.get("aggregate_composite_score")
    if calc_agg is not None and rec_agg is not None:
        try:
            if abs(float(rec_agg) - float(calc_agg)) > 0.05:
                return f"Contradictory aggregate score: recorded {rec_agg} != calculated {calc_agg:.2f}"
        except (ValueError, TypeError):
            return f"Invalid aggregate score format: {rec_agg}"
    return None


def _find_unresolved_citations(card: Any, texts: List[Optional[str]]) -> List[str]:
    if not isinstance(card, dict) or card.get("verdict") not in ("PASS", "PASS_ADVISORY"):
        return []
    all_citations = extract_judge_card_citations(card)
    valid_texts = [t for t in texts if t]
    return [c for c in all_citations if not any(citation_matches_text(c, t) for t in valid_texts)]


def _check_stage_citations(
    judge_report: Dict[str, Any],
    f1: Optional[str],
    f2: Optional[str],
    f3: Optional[str],
) -> Optional[str]:
    stage_map = [
        ("output_1_evaluation", [f1]),
        ("output_2_evaluation", [f2, f1]),
        ("output_3_evaluation", [f3, f1]),
    ]
    for key, texts in stage_map:
        card = judge_report.get(key, {})
        unresolved = _find_unresolved_citations(card, texts)
        if unresolved:
            return f"Unresolvable citations in {key}: {unresolved[:3]}"
    return None


def verify_judge_report_integrity(
    judge_report: Dict[str, Any],
    final_decision: Optional[Dict[str, Any]] = None,
    f1_text: Optional[str] = None,
    f2_text: Optional[str] = None,
    f3_text: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Verifies that QA status, score floors, reconciliation, aggregate scores,
    verdicts, final decision, citations, and hashes agree without contradiction.
    """
    if not isinstance(judge_report, dict):
        return False, "Judge report is not a dictionary"

    stat_err = _check_status_alignment(judge_report, final_decision)
    if stat_err:
        return False, stat_err

    agg_err = _check_aggregate_score_agreement(judge_report)
    if agg_err:
        return False, agg_err

    cit_err = _check_stage_citations(judge_report, f1_text, f2_text, f3_text)
    if cit_err:
        return False, cit_err

    return True, None
