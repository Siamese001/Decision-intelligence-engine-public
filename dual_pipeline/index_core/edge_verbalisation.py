"""Registry-predicate verbalisation, text variants (V0/V1/V3), and lexical field representations."""
from __future__ import annotations

import difflib

from dual_pipeline.core import normalize_space

# Closed registry of 18 canonical edge predicates and their active-voice verbalisations
REGISTRY_PREDICATES: tuple[str, ...] = (
    "REPORTS_TO",
    "OWNS_OR_LEADS",
    "ROUTES_TO",
    "AFFILIATED_WITH",
    "SUPPORTED_BY",
    "CONTRIBUTES_TO",
    "CONTEXT_FOR",
    "CONTAINS",
    "GOVERNS",
    "DEPENDS_ON",
    "DELIVERS",
    "HAS_RISK",
    "CANDIDATE_FOR",
    "CONSTRAINS",
    "TRANSITIONS_TO",
    "HAS_ROLE_IN",
    "RELATED_TO",
    "USES_PATTERN",
)

PREDICATE_VERBALISATIONS: dict[str, str] = {
    "REPORTS_TO": "reports to",
    "OWNS_OR_LEADS": "owns or leads",
    "ROUTES_TO": "routes to canonical card",
    "AFFILIATED_WITH": "is affiliated with",
    "SUPPORTED_BY": "is supported by source",
    "CONTRIBUTES_TO": "contributes to",
    "CONTEXT_FOR": "is context for",
    "CONTAINS": "contains",
    "GOVERNS": "governs",
    "DEPENDS_ON": "depends on",
    "DELIVERS": "delivers",
    "HAS_RISK": "has risk",
    "CANDIDATE_FOR": "is a candidate for",
    "CONSTRAINS": "constrains",
    "TRANSITIONS_TO": "transitions to",
    "HAS_ROLE_IN": "has a role in",
    "RELATED_TO": "is related to",
    "USES_PATTERN": "uses the pattern",
}

# Reverse question phrases for bidirectional retrieval
REVERSE_PREDICATE_PHRASES: dict[str, str] = {
    "REPORTS_TO": "reporting manager of",
    "OWNS_OR_LEADS": "leadership of",
    "ROUTES_TO": "routes to card",
    "AFFILIATED_WITH": "affiliation of",
    "SUPPORTED_BY": "support provider for",
    "CONTRIBUTES_TO": "contribution to",
    "CONTEXT_FOR": "context for",
    "CONTAINS": "components of",
    "GOVERNS": "governance over",
    "DEPENDS_ON": "prerequisite for",
    "DELIVERS": "deliverable of",
    "HAS_RISK": "risk associated with",
    "CANDIDATE_FOR": "candidate proposal for",
    "CONSTRAINS": "constraint on",
    "TRANSITIONS_TO": "target transition of",
    "HAS_ROLE_IN": "role assignment in",
    "RELATED_TO": "relationship to",
    "USES_PATTERN": "pattern used by",
}

_LEGACY_KEYWORD_MAP: tuple[tuple[str, str], ...] = (
    ("ROUTES_TO", "ROUTES_TO"),
    ("CARD", "ROUTES_TO"),
    ("SOURCED_FROM", "SUPPORTED_BY"),
    ("SUPPORT", "SUPPORTED_BY"),
    ("LEAD", "OWNS_OR_LEADS"),
    ("OWNS", "OWNS_OR_LEADS"),
    ("AFFILIAT", "AFFILIATED_WITH"),
    ("REPORT", "REPORTS_TO"),
    ("CONTEXT", "CONTEXT_FOR"),
    ("MANDATE", "CONTEXT_FOR"),
    ("PATTERN", "USES_PATTERN"),
    ("ARCHITECT", "USES_PATTERN"),
    ("CONTAIN", "CONTAINS"),
    ("GOVERN", "GOVERNS"),
    ("DEPEND", "DEPENDS_ON"),
    ("DELIVER", "DELIVERS"),
    ("RISK", "HAS_RISK"),
    ("TRANSITION", "TRANSITIONS_TO"),
    ("ROLE", "HAS_ROLE_IN"),
)


def canonicalize_predicate(predicate: str | None) -> str:
    """Map legacy or raw predicate string to canonical registry predicate."""
    pred = str(predicate or "").strip().upper()
    if pred in PREDICATE_VERBALISATIONS:
        return pred
    for kw, target in _LEGACY_KEYWORD_MAP:
        if kw in pred:
            return target
    return "RELATED_TO"


def render_edge_text_v0(
    subject_name: str,
    raw_predicate: str,
    object_name: str,
    scope: str = "",
) -> str:
    """V0: Legacy baseline fallback edge text format."""
    s = normalize_space(subject_name)
    p = normalize_space(raw_predicate)
    o = normalize_space(object_name)
    sc = normalize_space(scope)
    base = f"{s} {p} {o}."
    return f"{base} {sc}." if sc else base


def render_edge_text_v1(
    subject_name: str,
    predicate: str,
    object_name: str,
) -> str:
    """V1: Registry-predicate verbalised sentence: '{S} {phrase(pred)} {O}.'"""
    canon_p = canonicalize_predicate(predicate)
    phrase = PREDICATE_VERBALISATIONS.get(canon_p, "is related to")
    s = normalize_space(subject_name)
    o = normalize_space(object_name)
    return f"{s} {phrase} {o}."


def render_edge_text_v2(
    subject_name: str,
    subject_type: str,
    predicate: str,
    object_name: str,
    object_type: str,
    quote: str = "",
) -> str:
    """V2: V1 + entity types + full evidence quote (max 220 chars)."""
    v1 = render_edge_text_v1(subject_name, predicate, object_name)
    st = normalize_space(subject_type or "entity")
    ot = normalize_space(object_type or "entity")
    q = normalize_space(quote)
    suffix = f" Evidence: {q[:220]}" if q else ""
    return f"{v1} Subject type: {st}. Object type: {ot}.{suffix}"


def render_edge_text_v3(
    subject_name: str,
    predicate: str,
    object_name: str,
    quote: str = "",
    is_distinguishing: bool = False,
) -> str:
    """V3: V1 verbalisation with distinguishing quote attached only if unique and >=40 chars."""
    v1 = render_edge_text_v1(subject_name, predicate, object_name)
    q = normalize_space(quote)
    if is_distinguishing and q and len(q) >= 40:
        return f"{v1} Evidence: {q[:220]}"
    return v1


def render_edge_lexical_fields(
    subject_name: str,
    predicate: str,
    object_name: str,
    quote: str = "",
    scope: str = "",
) -> dict[str, str]:
    """Structured lexical / BM25 field dictionary for hybrid retrieval channel."""
    canon_p = canonicalize_predicate(predicate)
    phrase = PREDICATE_VERBALISATIONS.get(canon_p, "is related to")
    rev_phrase = REVERSE_PREDICATE_PHRASES.get(canon_p, "relationship to")
    s = normalize_space(subject_name)
    o = normalize_space(object_name)
    return {
        "subject": s,
        "predicate": canon_p,
        "predicate_phrase": phrase,
        "reverse_predicate_phrase": rev_phrase,
        "object": o,
        "verbalised_forward": f"{s} {phrase} {o}.",
        "verbalised_reverse": f"{o} {rev_phrase} {s}.",
        "quote": normalize_space(quote),
        "scope": normalize_space(scope),
        "lexical_body": f"{s} {phrase} {rev_phrase} {o}. {normalize_space(quote)}",
    }


def compute_near_duplicate_share(
    texts: list[str],
    similarity_threshold: float = 0.95,
) -> tuple[float, int, int]:
    """Compute share of texts with lexical/token near-duplicates (SequenceMatcher >= threshold).

    Returns:
        (near_dup_share, near_dup_count, total_count)
    """
    if not texts:
        return 0.0, 0, 0
    total = len(texts)
    near_dups = 0
    for i in range(total):
        t1 = texts[i]
        is_dup = False
        for j in range(total):
            if i == j:
                continue
            t2 = texts[j]
            if abs(len(t1) - len(t2)) > 30:
                continue
            ratio = difflib.SequenceMatcher(None, t1, t2).quick_ratio()
            if ratio >= similarity_threshold:
                is_dup = True
                break
        if is_dup:
            near_dups += 1
    return round(near_dups / total, 4), near_dups, total


def verify_no_classification_widening(
    edge_classification: str,
    source_classification: str | None,
) -> bool:
    """Verifies that an edge does not inherit a weaker classification than its evidence source."""
    ranks = {"public": 0, "external_approved": 0, "internal": 1, "confidential": 2, "restricted": 3}
    e_rank = ranks.get(str(edge_classification or "").lower(), 1)
    s_rank = ranks.get(str(source_classification or "").lower(), 1) if source_classification else e_rank
    return e_rank >= s_rank
