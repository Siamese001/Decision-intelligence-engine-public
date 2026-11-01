"""
copilot_insight.py

Typed Insight Synthesis for Live Executive Copilot under Wave 5.
Categorizes verified assertions into four high-salience operational insight categories:
1. contradiction: Conflicting commitments, status changes, or SLA divergences.
2. prior_decision: Established historical decisions, baseline commitments, or enterprise policies.
3. owner_gap: Missing, unassigned, or ambiguous ownership on actions and deliverables.
4. risk: Operational dependencies, failure modes, latency spikes, or compliance exposures.

Scores candidates by:
- Relevance: Entity and token overlap with current conversational dialogue.
- Novelty: Non-redundancy against recently emitted session answers.
- Stakes: Intrinsic operational impact priority (contradiction > risk > owner_gap > prior_decision).

Selects the single highest-scoring, speakable insight per conversational moment.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_insight")

_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{3,}\b|\b[A-Z0-9_\-]{2}\b")
_MARKUP_TAG_RE = re.compile(r"(\[.*?\]|```|<.*?>|\{.*?\})")

# Stakes weights by insight category
STAKES_WEIGHTS: Dict[str, float] = {
    "contradiction": 1.00,
    "risk": 0.90,
    "owner_gap": 0.85,
    "prior_decision": 0.75,
}

CONTRADICTION_PREDICATES: Set[str] = {
    "CONFLICTS_WITH",
    "CONTRADICTS",
    "SUPERSEDED_BY",
    "DISPUTES",
    "OVERRULES",
}

RISK_PREDICATES: Set[str] = {
    "HAS_RISK",
    "BLOCKS",
    "DEPENDS_ON",
    "MONITORS",
    "VULNERABLE_TO",
    "EXPOSES",
}

OWNER_GAP_PREDICATES: Set[str] = {
    "UNASSIGNED",
    "NEEDS_OWNER",
    "ORPHANED",
    "LACKS_LEAD",
}


@dataclass
class TypedInsight:
    """A scored, structured insight candidate anchored in a verified graph assertion."""
    insight_type: str
    assertion: Dict[str, Any]
    headline: str
    target: Optional[str]
    speaker: Optional[str]
    relevance: float
    novelty: float
    stakes: float
    total_score: float
    spoken_comment: str


def _tokenize_text(text: str) -> Set[str]:
    if not text:
        return set()
    return {w.lower() for w in _TOKEN_RE.findall(text) if not w.startswith("-")}


def _compute_jaccard(tokens1: Set[str], tokens2: Set[str]) -> float:
    if not tokens1 or not tokens2:
        return 0.0
    inter = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    return float(inter) / float(union) if union > 0 else 0.0


def _lower_first(text: str) -> str:
    cleaned = _MARKUP_TAG_RE.sub("", text).replace("?", "").strip().rstrip(".?!;, ")
    if not cleaned:
        return ""
    if len(cleaned) > 1 and cleaned[0].isupper() and not cleaned[1].isupper():
        return cleaned[0].lower() + cleaned[1:]
    return cleaned


def _classify_insight_type(assertion: Dict[str, Any], claim_text: str) -> str:
    """Determines the typed insight category based on predicate and semantic markers."""
    pred = str(assertion.get("predicate") or "").upper()
    claim_low = claim_text.lower()

    if pred in CONTRADICTION_PREDICATES or any(w in claim_low for w in ("conflict", "contradict", "supersedes", "instead of")):
        return "contradiction"

    if pred in RISK_PREDICATES or any(w in claim_low for w in ("risk", "outage", "latency spike", "breach", "failover", "alert", "blocker")):
        return "risk"

    subj = str(assertion.get("subject_node_id") or assertion.get("subject") or "").lower()
    if pred in OWNER_GAP_PREDICATES or any(w in claim_low for w in ("unassigned", "needs owner", "no owner", "unowned")) or subj in ("unassigned", "none"):
        return "owner_gap"

    return "prior_decision"


def _format_contradiction_frame(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, regarding {spk}'s point, note that {clean_claim}. How should we reconcile this with the earlier update?"
    if tgt:
        return f"{tgt}, note that {clean_claim}. How should we reconcile this with the earlier update?"
    return f"Note that {clean_claim}. How should we reconcile this with the earlier update?"


def _format_risk_frame(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, regarding {spk}'s point, {clean_claim}. How are we mitigating this dependency?"
    if tgt:
        return f"{tgt}, note that {clean_claim}. How are we mitigating this dependency?"
    return f"Note that {clean_claim}. How are we mitigating this dependency?"


def _format_owner_gap_frame(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, regarding {spk}'s point, {clean_claim}. Who will own delivering this?"
    if tgt:
        return f"{tgt}, {clean_claim}. Who will own delivering this?"
    if spk:
        return f"To {spk}'s point, {clean_claim}. Who will own delivering this?"
    return f"{clean_claim}. Who will own delivering this?"


def _format_prior_decision_frame(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, regarding {spk}'s point, our agreed baseline is that {clean_claim}."
    if tgt:
        return f"{tgt}, our agreed baseline is that {clean_claim}."
    if spk:
        return f"To {spk}'s point, our agreed baseline is that {clean_claim}."
    return f"Our agreed baseline is that {clean_claim}."


_INSIGHT_FRAME_DISPATCH = {
    "contradiction": _format_contradiction_frame,
    "risk": _format_risk_frame,
    "owner_gap": _format_owner_gap_frame,
    "prior_decision": _format_prior_decision_frame,
}


def _format_insight_frame(
    insight_type: str,
    clean_claim: str,
    target: Optional[str],
    speaker: Optional[str],
) -> str:
    """Renders a single-question, tag-free speakable comment tailored to the insight category."""
    spk = speaker.strip() if speaker and speaker.lower() not in ("unknown", "none") else ""
    tgt = target.strip() if target else ""
    formatter = _INSIGHT_FRAME_DISPATCH.get(insight_type, _format_prior_decision_frame)
    return formatter(clean_claim, tgt, spk)



def _compute_relevance(asrt_tokens: Set[str], context_tokens: Set[str]) -> float:
    """Calculates semantic overlap between assertion and live context."""
    if not context_tokens:
        return 0.5
    return _compute_jaccard(asrt_tokens, context_tokens)


def _compute_novelty(asrt_tokens: Set[str], recent_history: Optional[List[str]]) -> float:
    """Calculates novelty score against prior session comments, penalizing repeats."""
    if not recent_history or not asrt_tokens:
        return 1.0
    max_sim = 0.0
    for past in recent_history[-5:]:
        past_toks = _tokenize_text(past)
        if not past_toks:
            continue
        inter = len(asrt_tokens & past_toks)
        containment = float(inter) / float(len(asrt_tokens))
        jaccard = float(inter) / float(len(asrt_tokens | past_toks))
        sim = max(jaccard, containment)
        if sim > max_sim:
            max_sim = sim
    return max(0.0, 1.0 - max_sim)


def build_insight_candidate(
    assertion: Dict[str, Any],
    claim_text: str,
    context_tokens: Set[str],
    target: Optional[str] = None,
    speaker: Optional[str] = None,
    recent_history: Optional[List[str]] = None,
    max_words: Optional[int] = None,
) -> Optional[TypedInsight]:
    """Builds a scored TypedInsight candidate from a verified assertion."""
    if not claim_text:
        return None

    clean_claim = _lower_first(claim_text)
    if not clean_claim:
        return None

    asrt_tokens = _tokenize_text(claim_text)
    insight_type = _classify_insight_type(assertion, claim_text)

    relevance = _compute_relevance(asrt_tokens, context_tokens)
    novelty = _compute_novelty(asrt_tokens, recent_history)
    stakes = STAKES_WEIGHTS.get(insight_type, 0.70)

    # Fail closed if assertion has zero relevance to active context or novelty is depleted
    if (context_tokens and relevance <= 0.0) or novelty < 0.25:
        total_score = 0.0
    else:
        total_score = (0.45 * relevance) + (0.35 * stakes) + (0.20 * novelty)

    spoken = _format_insight_frame(insight_type, clean_claim, target, speaker)

    return TypedInsight(
        insight_type=insight_type,
        assertion=assertion,
        headline=f"[{insight_type.upper()}] {clean_claim[:60]}",
        target=target,
        speaker=speaker,
        relevance=relevance,
        novelty=novelty,
        stakes=stakes,
        total_score=total_score,
        spoken_comment=spoken,
    )


def _extract_context_text(dialogue_context: Any) -> str:
    if isinstance(dialogue_context, str):
        return dialogue_context
    if hasattr(dialogue_context, "text"):
        return str(dialogue_context.text)
    if hasattr(dialogue_context, "focus_turns"):
        return " ".join(t.get("text", "") for t in dialogue_context.focus_turns())
    if isinstance(dialogue_context, dict):
        return str(dialogue_context.get("text") or "")
    return ""


def select_best_insight(
    assertions: List[Dict[str, Any]],
    dialogue_context: Any = None,
    target: Optional[str] = None,
    speaker: Optional[str] = None,
    recent_history: Optional[List[str]] = None,
    max_words: Optional[int] = None,
) -> Optional[TypedInsight]:
    """Evaluates candidates and selects the single highest-scoring typed insight."""
    if not assertions:
        return None
    from dual_pipeline.parameters import get_copilot_speech_limits
    effective_max = get_copilot_speech_limits()["insight_max_words"] if max_words is None else max_words
    context_tokens = _tokenize_text(_extract_context_text(dialogue_context))

    candidates: List[TypedInsight] = []
    for a in assertions:
        claim = str(a.get("claim_text") or a.get("object_literal") or "")
        cand = build_insight_candidate(
            a,
            claim,
            context_tokens,
            target=target,
            speaker=speaker,
            recent_history=recent_history,
            max_words=effective_max,
        )
        if cand and cand.total_score > 0.0:
            candidates.append(cand)

    if not candidates:
        return None

    candidates.sort(key=lambda c: c.total_score, reverse=True)
    if effective_max is not None:
        fitting = [c for c in candidates if len(c.spoken_comment.split()) <= effective_max]
        if fitting:
            return fitting[0]
    return candidates[0]
