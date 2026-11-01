"""
dual_pipeline/live_meeting_recorder_core/copilot_retriever_ranking.py

Provenance scoring, multi-query candidate collection, and dense/lexical ranking
helpers for CopilotGraphRetriever.
Extracted to maintain module size budget (<= 1083 lines) and CC <= 15.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .copilot_connection_policy import (
    calculate_engagement_affinity,
    is_assertion_in_scope,
    is_candidate_control_checklist,
    is_enterprise_policy,
    match_engagement,
    normalize_engagement_id,
)
from .copilot_predicates import (
    STRUCTURAL_IDENTITY_PREDICATES,
    VOLATILE_OPERATIONAL_PREDICATES,
    normalize_copilot_predicate,
)
from .copilot_roster_bridge import (
    expand_role_query_tokens,
)
from .prefetch_query_builder import (
    DOMAIN_ACRONYM_MAP,
)

_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_\-]{3,}\b|\b[A-Z0-9_\-]{2}\b")
_STUB_CLAIM_RE = re.compile(r"\b(?:is an identified|is a reported speaker in)\b", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b")
_HISTORICAL_QUERY_RE = re.compile(
    r"\b(?:july|august|september|q1|q2|q3|historical|history|originally|previous|prior\s+quarter|archived|last\s+quarter)\b",
    re.IGNORECASE,
)


def parse_assertion_epoch(raw_date: Any) -> float:
    """Parses an assertion or evidence date string into a UTC epoch timestamp (0.0 if missing/invalid)."""
    if not raw_date:
        return 0.0
    if isinstance(raw_date, (int, float)):
        return float(raw_date) if raw_date > 0 else 0.0
    text = str(raw_date).strip()
    if not text:
        return 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    except Exception:
        pass
    if m_iso := _ISO_DATE_RE.search(text):
        try:
            return datetime(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)), tzinfo=timezone.utc).timestamp()
        except Exception:
            return 0.0
    if m_us := _US_DATE_RE.search(text):
        try:
            return datetime(int(m_us.group(3)), int(m_us.group(1)), int(m_us.group(2)), tzinfo=timezone.utc).timestamp()
        except Exception:
            return 0.0
    return 0.0


def _resolve_evidence_epoch(assertion: Dict[str, Any], topology: Any) -> float:
    ev_meta = getattr(topology, "evidence_meta", None)
    supp_ev = getattr(topology, "supporting_evidence", None)
    aid = str(assertion.get("assertion_id") or "")
    ev_ids = assertion.get("evidence_ids") or (supp_ev.get(aid, []) if isinstance(supp_ev, dict) else [])
    best_ep = 0.0
    if isinstance(ev_meta, dict) and isinstance(ev_ids, list):
        for eid in ev_ids:
            em = ev_meta.get(eid)
            if isinstance(em, dict):
                ep = parse_assertion_epoch(em.get("source_date_normalized") or em.get("observed_at") or em.get("source_date"))
                if ep > best_ep:
                    best_ep = ep
    return best_ep


def resolve_assertion_epoch(assertion: Dict[str, Any], topology: Any = None) -> float:
    """Resolves the latest observed UTC epoch for an assertion, falling back to supporting evidence metadata."""
    cached = assertion.get("_observed_epoch")
    if isinstance(cached, (int, float)) and cached > 0.0:
        return float(cached)
    for key in ("source_date_normalized", "observed_at", "source_date", "valid_from", "created_at"):
        if (ep := parse_assertion_epoch(assertion.get(key))) > 0.0:
            assertion["_observed_epoch"] = ep
            return ep
    best_ep = _resolve_evidence_epoch(assertion, topology) if topology is not None else 0.0
    assertion["_observed_epoch"] = best_ep
    return best_ep


def compute_temporal_decay(
    assertion: Dict[str, Any],
    reference_epoch: float,
    query_str: str = "",
) -> float:
    """Computes predicate-aware piecewise exponential temporal decay multiplier."""
    if not isinstance(assertion, dict) or assertion.get("provenance_scope") == "POLICY" or is_enterprise_policy(assertion):
        return 1.0
    if query_str and _HISTORICAL_QUERY_RE.search(query_str):
        return 1.0
    obs_epoch = float(assertion.get("_observed_epoch") or 0.0) or resolve_assertion_epoch(assertion)
    if obs_epoch <= 0.0 or reference_epoch <= 0.0:
        return 0.85
    age_days = max(0.0, (reference_epoch - obs_epoch) / 86400.0)
    pred = normalize_copilot_predicate(assertion.get("predicate"))
    is_structural = pred in STRUCTURAL_IDENTITY_PREDICATES and pred not in VOLATILE_OPERATIONAL_PREDICATES
    half_life, floor_val = (90.0, 0.55) if is_structural else (30.0, 0.25)
    decay = max(floor_val, math.pow(0.5, age_days / half_life))
    if age_days <= 7.0:
        decay *= 1.15 - (0.15 * (age_days / 7.0))
    elif age_days > 60.0 and not is_structural:
        decay = max(floor_val, decay * 0.65)
    return float(decay)

_RETRIEVER_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "have", "has", "had", "not", "but", "into", "over", "more", "some", "such",
    "than", "then", "them", "they", "their", "there", "what", "when", "where",
    "which", "while", "who", "whom", "whose", "why", "how", "all", "any", "both",
    "each", "few", "most", "other", "own", "same", "too", "very", "just", "now",
    "also", "only", "about", "after", "before", "between", "under", "again",
    "further", "once", "here", "can", "will", "would", "could", "should", "may",
    "might", "must", "shall", "been", "being", "does", "did", "doing", "our",
    "your", "his", "her", "its", "out", "off", "per", "via",
})

_QUERY_TOPIC_SYNONYMS: Dict[str, Tuple[str, ...]] = {
    "productivity": ("velocity", "throughput", "efficiency", "commits", "metrics", "kpi", "scorecard", "outcome"),
    "increase": ("increased", "increasing", "growth", "higher", "gain", "improvement", "accelerated"),
    "velocity": ("productivity", "throughput", "commits", "sprint", "metrics", "kpi", "scorecard", "outcome"),
    "metrics": ("velocity", "productivity", "commits", "kpi", "scorecard", "measurement", "telemetry", "outcome"),
}


def tokenize_text(text: str) -> Set[str]:
    """Extracts normalized alphanumeric query tokens excluding stopwords."""
    if not text:
        return set()
    raw = _TOKEN_PATTERN.findall(text)
    return {w.lower() for w in raw if not w.startswith("-") and w.lower() not in _RETRIEVER_STOPWORDS}


def determine_assertion_provenance(
    assertion: Dict[str, Any],
    engagement_id: Optional[str],
    topological_engagements: Set[str],
) -> Tuple[bool, bool, str, str]:
    """Determines (is_primary, is_predecessor, prov_scope, origin_eng) for an assertion."""
    origin_eng = normalize_engagement_id(
        assertion.get("engagement_id") or assertion.get("project_scope")
    )
    is_primary = bool(engagement_id and match_engagement(origin_eng, engagement_id))
    is_pred = bool(
        topological_engagements
        and any(match_engagement(origin_eng, te) for te in topological_engagements)
    )
    if is_primary:
        prov_scope = "PRIMARY"
    elif is_pred:
        prov_scope = "PREDECESSOR"
    elif is_enterprise_policy(assertion):
        prov_scope = "POLICY"
    else:
        prov_scope = "CROSS"
    return is_primary, is_pred, prov_scope, origin_eng


def _expand_query_synonyms(tokens: List[str]) -> Set[str]:
    """Expands raw query tokens with domain acronyms, topic synonyms, and role synonyms."""
    query_tokens = set(tokens)
    for t in tokens:
        query_tokens.update(DOMAIN_ACRONYM_MAP.get(t, ()))
        query_tokens.update(_QUERY_TOPIC_SYNONYMS.get(t, ()))
    return expand_role_query_tokens(query_tokens)


def extract_query_tokens_and_seeds(
    query: Any, allowed_nodes: Set[str]
) -> Tuple[Set[str], Set[str]]:
    """Extracts query tokens, direct seed nodes, and acronym/synonym expansions."""
    if not hasattr(query, "retrieval_seeds"):
        tokens = list(tokenize_text(str(query)))
        return _expand_query_synonyms(tokens), set()

    tokens: List[str] = []
    direct_seed_nodes: Set[str] = set()
    for s in query.retrieval_seeds():
        tokens.extend(tokenize_text(s))
        if s in allowed_nodes:
            direct_seed_nodes.add(s)
    if hasattr(query, "focus_turns"):
        for ft in query.focus_turns():
            tokens.extend(tokenize_text(ft.get("text", "")))
    return _expand_query_synonyms(tokens), direct_seed_nodes



def _extract_assertion_tokens(assertion: Dict[str, Any]) -> Set[str]:
    asrt_tokens = assertion.get("_tokens")
    if asrt_tokens is None:
        claim = str(assertion.get("claim_text") or assertion.get("object_literal") or "")
        subj = str(assertion.get("subject") or assertion.get("subject_node_id") or "")
        obj = str(assertion.get("object") or assertion.get("object_node_id") or "")
        pred = str(assertion.get("predicate") or "")
        asrt_tokens = tokenize_text(claim) | tokenize_text(f"{subj} {obj} {pred}")
        assertion["_tokens"] = asrt_tokens
    return asrt_tokens


def _compute_provenance_multiplier(
    assertion: Dict[str, Any], query_str: str, query_tokens: Set[str]
) -> float:
    prov = assertion.get("provenance_scope")
    if assertion.get("is_primary") or prov == "PRIMARY":
        return 1.60
    if prov == "POLICY" or is_enterprise_policy(assertion):
        return 0.20 if is_candidate_control_checklist(assertion) else 0.80
    if assertion.get("is_predecessor") or prov == "PREDECESSOR":
        mult = 0.75
        origin = str(assertion.get("origin_engagement_id") or "").lower().replace("project:", "").strip()
        q_lower = query_str.lower()
        if origin and (origin in q_lower or origin in query_tokens):
            mult *= 1.15
        return mult
    return 0.70


_ROLE_QUERY_TERMS: frozenset[str] = frozenset({
    "role", "owner", "lead", "swe", "engineer", "developer", "architect", "director", "sponsor", "who", "tech",
})
_GENERIC_ORG_TOKENS: frozenset[str] = frozenset({
    "apex_global", "energy", "partner_advisory", "apex", "nea", "apex_trans", "florida", "power", "light", "analytics", "external", "internal",
})
_QUERY_STOPWORDS: frozenset[str] = frozenset({
    "how", "does", "do", "did", "what", "where", "when", "why", "who", "which",
    "with", "for", "about", "from", "into", "onto", "tell", "show", "question",
    "say", "says", "said", "point", "regarding", "discuss", "integrate", "integrates",
})


def _is_human_speaker_claim(retriever: Any, assertion: Dict[str, Any], asrt_tokens: Set[str]) -> bool:
    subj = str(assertion.get("subject") or assertion.get("subject_node_id") or "")
    obj = str(assertion.get("object") or assertion.get("object_node_id") or "")
    att_nids = getattr(retriever, "_attendee_node_ids", set())
    if subj.startswith(("person:", "attendee:", "workstream:")) or obj.startswith(("person:", "attendee:", "workstream:")):
        return True
    if subj in att_nids or obj in att_nids:
        return True
    specific_att_tokens = getattr(retriever, "_attendee_tokens", set()) - _GENERIC_ORG_TOKENS
    return bool(asrt_tokens & specific_att_tokens)


def _extract_node_tokens(nid: str, nodes: Any) -> Set[str]:
    if not nid:
        return set()
    tokens = set(tokenize_text(nid))
    nd = nodes.get(nid, {}) if isinstance(nodes, dict) else {}
    if isinstance(nd, dict):
        tokens |= tokenize_text(str(nd.get("canonical_name") or ""))
        tokens |= tokenize_text(str(nd.get("display_name") or ""))
    return tokens


def _has_non_entity_topic_overlap(
    retriever: Any, assertion: Dict[str, Any], query_tokens: Set[str], asrt_tokens: Set[str]
) -> bool:
    subj = str(assertion.get("subject") or assertion.get("subject_node_id") or "")
    obj = str(assertion.get("object") or assertion.get("object_node_id") or "")
    nodes = getattr(getattr(retriever, "_topology", None), "nodes", {}) or {}
    entity_name_tokens = _extract_node_tokens(subj, nodes) | _extract_node_tokens(obj, nodes)
    ignored_tokens = (
        entity_name_tokens
        | getattr(retriever, "_attendee_tokens", set())
        | _ROLE_QUERY_TERMS
        | _QUERY_STOPWORDS
        | tokenize_text(getattr(retriever, "engagement_id", "") or "")
    )
    non_entity_query_tokens = query_tokens - ignored_tokens
    return not non_entity_query_tokens or bool(non_entity_query_tokens & asrt_tokens)


def _resolve_permitted_engagements(retriever: Any, permitted_engagements: Optional[Set[str]]) -> Any:
    return permitted_engagements if permitted_engagements is not None else retriever.permitted_engagements


def _is_roster_stub(aid: str, claim: str) -> bool:
    return aid.startswith("asrt_attendee_") or "is an active meeting participant with role" in claim.lower()


def _is_unmatched_or_irrelevant(
    retriever: Any,
    assertion: Dict[str, Any],
    query_tokens: Set[str],
    asrt_tokens: Set[str],
    is_roster: bool,
) -> bool:
    if not (query_tokens & asrt_tokens):
        return True
    if is_roster:
        return False
    return not _has_non_entity_topic_overlap(retriever, assertion, query_tokens, asrt_tokens)


def _adjust_type_score(
    retriever: Any,
    assertion: Dict[str, Any],
    asrt_tokens: Set[str],
    query_tokens: Set[str],
    is_roster: bool,
    base_score: float,
) -> float:
    if is_roster:
        return base_score * 0.35 if not (query_tokens & _ROLE_QUERY_TERMS) else base_score
    if _is_human_speaker_claim(retriever, assertion, asrt_tokens):
        return base_score * 2.0
    return base_score


def score_provenance_assertion(
    retriever: Any,
    assertion: Dict[str, Any],
    query_tokens: Set[str],
    query_str: str = "",
    permitted_engagements: Optional[Set[str]] = None,
) -> float:
    """Calculates provenance-weighted salience score for an assertion."""
    claim = str(assertion.get("claim_text") or assertion.get("object_literal") or "")
    if _STUB_CLAIM_RE.search(claim):
        return 0.0
    asrt_tokens = _extract_assertion_tokens(assertion)
    aid = str(assertion.get("assertion_id") or "")
    is_roster = _is_roster_stub(aid, claim)
    if _is_unmatched_or_irrelevant(retriever, assertion, query_tokens, asrt_tokens, is_roster):
        return 0.0

    overlap = query_tokens & asrt_tokens
    base_score = sum(retriever._token_idf.get(t, 1.0) for t in overlap)
    length_norm = 1.0 / (1.0 + 0.015 * max(0, len(asrt_tokens) - 15))
    base_score *= length_norm * (1.2 if assertion.get("is_priority") else 1.0)
    base_score = _adjust_type_score(retriever, assertion, asrt_tokens, query_tokens, is_roster, base_score)

    if aid.startswith("asrt_edge_") and " -> " in claim:
        base_score *= 0.15

    eff_permitted = _resolve_permitted_engagements(retriever, permitted_engagements)
    affinity = calculate_engagement_affinity(
        assertion, retriever.engagement_id, eff_permitted
    )
    if affinity <= 0.0:
        return 0.0

    prov_mult = _compute_provenance_multiplier(assertion, query_str, query_tokens)
    ref_epoch = float(getattr(retriever, "_reference_epoch", 0.0) or 0.0)
    decay_mult = compute_temporal_decay(assertion, ref_epoch, query_str)
    assertion["_temporal_decay_multiplier"] = decay_mult
    return base_score * prov_mult * affinity * decay_mult


def retrieve_lexical_ranking(
    retriever: Any,
    query_tokens: Set[str],
    direct_seed_nodes: Set[str],
    query_str: str,
    priority_only: bool,
    max_results: int,
    permitted_engagements: Optional[Set[str]] = None,
) -> List[Tuple[str, float]]:
    """Retrieves and scores lexical candidates, returning [(assertion_id, score), ...]."""
    matched_nodes = set(direct_seed_nodes)
    for t in query_tokens:
        matched_nodes.update(retriever._inverted_index.get(t, set()))

    seen_ids: Set[str] = set()
    candidate_pool = retriever._collect_node_assertions(
        matched_nodes, seen_ids, priority_only, max(50, max_results * 5), query_tokens=query_tokens
    )
    unmatched = retriever._scan_unmatched_claims(
        query_tokens, seen_ids, max(20, max_results * 2)
    )
    candidate_pool.extend(unmatched)

    scored: List[Tuple[str, float]] = []
    for a in candidate_pool:
        aid = a.get("assertion_id")
        if not aid:
            continue
        s = retriever._score_assertion(a, query_tokens, query_str, permitted_engagements=permitted_engagements)
        if s > 0.0:
            scored.append((aid, s))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def retrieve_dense_ranking(
    retriever: Any,
    query_vector: Any,
    top_k: int = 50,
    query_str: str = "",
    permitted_engagements: Optional[Set[str]] = None,
) -> List[Tuple[str, float]]:
    """Computes dense vector ranking mapped to valid in-scope assertion IDs."""
    dense_hits = retriever.score_dense_assertions(query_vector, top_k=top_k)
    if not dense_hits:
        return []

    eff_permitted = permitted_engagements if permitted_engagements is not None else retriever.permitted_engagements
    ref_epoch = float(getattr(retriever, "_reference_epoch", 0.0) or 0.0)
    ranked: List[Tuple[str, float]] = []
    for rid, sim in dense_hits:
        aid = retriever._dense_record_to_assertion_id.get(rid, rid)
        asrt = retriever._indexed_assertion_records.get(aid)
        if not asrt:
            continue
        if retriever.engagement_id:
            if not is_assertion_in_scope(asrt, retriever.engagement_id, eff_permitted):
                continue
        elif not is_enterprise_policy(asrt):
            continue
        decay = compute_temporal_decay(asrt, ref_epoch, query_str)
        adj_sim = float(sim) * (0.7 + 0.3 * decay)
        ranked.append((aid, adj_sim))
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked


def collect_multi_query_rankings(
    retriever: Any,
    multi_queries: Dict[str, str],
    query_tokens: Set[str],
    direct_seed_nodes: Set[str],
    priority_only: bool,
    max_results: int,
    permitted_engagements: Optional[Set[str]] = None,
) -> List[List[Tuple[str, float]]]:
    """Collects candidate rankings across multi-query expansion variants."""
    rankings: List[List[Tuple[str, float]]] = []
    for q_name, q_text in multi_queries.items():
        if q_name == "primary":
            q_toks = query_tokens
            seeds = direct_seed_nodes
        else:
            q_toks = _expand_query_synonyms(list(tokenize_text(q_text)))
            seeds = set()
        if q_toks or seeds:
            kw = {"permitted_engagements": permitted_engagements} if permitted_engagements is not None else {}
            lex_ranking = retriever._retrieve_lexical_ranking(
                q_toks, seeds, q_text, priority_only, max_results, **kw
            )
            if lex_ranking:
                rankings.append(lex_ranking)
    return rankings
