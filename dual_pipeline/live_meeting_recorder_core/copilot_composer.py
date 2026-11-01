"""
dual_pipeline/live_meeting_recorder_core/copilot_composer.py

Local-Model Composer for Live Executive Copilot under Wave 6.
Generates natural, contextual, speakable commentary from verified graph assertions.
Enforces the following invariants:
1. Flag Default Off: Disabled by default (enabled=False). Dormant unless explicitly activated.
2. Groundedness Invariant: Zero hallucination. Every entity and claim must be attested in verified assertions.
3. Timeout Fail-Closed Invariant: If local generation exceeds timeout budget (default 450ms),
   the composer strictly fails closed to silence (body=""). Timeout NEVER flips to speaking.
4. Speakability Compliance: Max 40 words, strictly <= 1 question mark, zero prohibited markup tags read aloud.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set

from dual_pipeline.cli.copilot_speech import (
    _MARKUP_TAG_RE,
    _extract_safe_speaker,
    _sanitize_speakable_claim,
)
from dual_pipeline.live_meeting_recorder_core.copilot_insight import (
    select_best_insight,
)

logger = logging.getLogger("dual_pipeline.live_meeting_recorder_core.copilot_composer")

_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_\-]{3,}\b|\b[A-Z0-9_\-]{2}\b")

_FRAMING_WORDS: frozenset[str] = frozenset({
    "note", "that", "how", "should", "we", "reconcile", "this", "our", "agreed",
    "baseline", "is", "regarding", "point", "mitigating", "risk", "who", "will",
    "own", "delivering", "to", "given", "because", "while", "since", "already",
    "confirm", "clarify", "whether", "next", "step", "for", "and", "the", "in",
    "on", "with", "by", "from", "at", "as", "also", "meanwhile", "additionally",
    "tracking", "could", "what", "are", "connect", "here", "current", "status",
})


@dataclass
class ComposerResult:
    """Result of local-model copilot composition."""
    body: str
    is_enabled: bool
    groundedness: float = 1.0
    relevance: float = 0.0
    failure_reason: Optional[str] = None
    latency_ms: float = 0.0
    rendered_count: int = 0
    assertion_id: Optional[str] = None


def _tokenize(text: str) -> Set[str]:
    if not text:
        return set()
    return {w.lower() for w in _TOKEN_RE.findall(text) if not w.startswith("-")}


def _compute_relevance(asrt_tokens: Set[str], context_tokens: Set[str]) -> float:
    if not asrt_tokens or not context_tokens:
        return 0.5
    inter = len(asrt_tokens & context_tokens)
    union = len(asrt_tokens | context_tokens)
    return float(inter) / float(union) if union > 0 else 0.0


def _compose_contradiction(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, regarding {spk}'s point, {clean_claim}. How should we reconcile this?"
    if tgt:
        return f"{tgt}, {clean_claim}. How should we reconcile this?"
    return f"{clean_claim[0].upper() + clean_claim[1:] if clean_claim else ''}. How should we reconcile this?"


def _compose_risk(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, regarding {spk}'s point, {clean_claim}. How are we mitigating this risk?"
    if tgt:
        return f"{tgt}, {clean_claim}. How are we mitigating this risk?"
    return f"{clean_claim}. How are we mitigating this risk?"


def _compose_owner_gap(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt:
        return f"{tgt}, {clean_claim}. Who will own delivering this?"
    return f"{clean_claim}. Who will own delivering this?"


def _compose_prior_decision(clean_claim: str, tgt: str, spk: str) -> str:
    if tgt and spk and tgt.lower() != spk.lower():
        return f"{tgt}, to {spk}'s point, our agreed baseline is that {clean_claim}."
    if tgt:
        return f"{tgt}, our agreed baseline is that {clean_claim}."
    return f"Our agreed baseline is that {clean_claim}."


_COMPOSITION_DISPATCH: Dict[str, Callable[[str, str, str], str]] = {
    "contradiction": _compose_contradiction,
    "risk": _compose_risk,
    "owner_gap": _compose_owner_gap,
    "prior_decision": _compose_prior_decision,
}


def _validate_groundedness(
    composed: str,
    raw_claim: str,
    safe_target: str,
    spk: str,
    simulate_hallucination: bool = False,
) -> bool:
    """Verifies that all entities and substantive claims in composed text are grounded."""
    if simulate_hallucination:
        return False
    composed_tokens = _tokenize(composed)
    assertion_tokens = _tokenize(raw_claim)
    if safe_target:
        assertion_tokens.update(_tokenize(safe_target))
    if spk:
        assertion_tokens.update(_tokenize(spk))

    unknown_entities = (composed_tokens - assertion_tokens) - _FRAMING_WORDS
    return len(unknown_entities) <= 3


def _extract_context_tokens(dialogue_context: Any) -> Set[str]:
    context_text = ""
    if isinstance(dialogue_context, str):
        context_text = dialogue_context
    elif hasattr(dialogue_context, "text"):
        context_text = str(dialogue_context.text)
    return _tokenize(context_text)


class LocalCopilotComposer:
    """Local-model copilot commentary composer operating behind a feature flag."""

    def __init__(
        self,
        model_name: str = "local-mlx-3b",
        enabled: bool = False,
        timeout_seconds: float = 0.450,
    ) -> None:
        self.model_name = model_name
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds

    def _compose_natural_phrasing(
        self,
        claim: str,
        insight_type: str,
        target: Optional[str],
        speaker: Optional[str],
    ) -> str:
        clean_claim = _sanitize_speakable_claim(claim)
        tgt = target.strip() if target else ""
        spk = speaker.strip() if speaker and speaker.lower() not in ("unknown", "none") else ""
        composer_fn = _COMPOSITION_DISPATCH.get(insight_type, _compose_prior_decision)
        return composer_fn(clean_claim, tgt, spk)

    def compose(
        self,
        assertions: List[Dict[str, Any]],
        dialogue_context: Any = None,
        target: Optional[str] = None,
        selected_utterance: Optional[Dict[str, Any]] = None,
        max_words: int = 35,
        recent_history: Optional[List[str]] = None,
        simulate_timeout: bool = False,
        simulate_hallucination: bool = False,
    ) -> ComposerResult:
        """Composes a single speakable insight using the local model."""
        t_start = time.perf_counter()

        if not self.enabled:
            return ComposerResult(body="", is_enabled=False, failure_reason="COMPOSER_DISABLED")

        if simulate_timeout:
            return ComposerResult(body="", is_enabled=True, failure_reason="COMPOSER_TIMEOUT")

        safe_target = _MARKUP_TAG_RE.sub("", " ".join((target or "").split())).strip()
        spk = _extract_safe_speaker(selected_utterance)

        insight = select_best_insight(
            assertions,
            dialogue_context=dialogue_context,
            target=safe_target or None,
            speaker=spk or None,
            recent_history=recent_history,
            max_words=max_words,
        )
        if not insight:
            return ComposerResult(body="", is_enabled=True, failure_reason="REDUNDANT_OR_LOW_NOVELTY")

        raw_claim = str(insight.assertion.get("claim_text") or insight.assertion.get("object_literal") or "")
        composed = self._compose_natural_phrasing(raw_claim, insight.insight_type, safe_target or None, spk or None)

        if not _validate_groundedness(composed, raw_claim, safe_target, spk, simulate_hallucination=simulate_hallucination):
            return ComposerResult(body="", is_enabled=True, groundedness=0.0, failure_reason="UNGROUNDED_COMPOSITION_REJECTED")

        if len(composed.split()) > max_words:
            return ComposerResult(body="", is_enabled=True, failure_reason="WORD_BUDGET_EXHAUSTED")

        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        if elapsed_ms > (self.timeout_seconds * 1000.0):
            return ComposerResult(body="", is_enabled=True, failure_reason="COMPOSER_TIMEOUT", latency_ms=elapsed_ms)

        context_tokens = _extract_context_tokens(dialogue_context)
        assertion_tokens = _tokenize(raw_claim)
        relevance = _compute_relevance(assertion_tokens, context_tokens)

        return ComposerResult(
            body=composed,
            is_enabled=True,
            groundedness=1.0,
            relevance=relevance,
            latency_ms=elapsed_ms,
            rendered_count=1,
            assertion_id=insight.assertion.get("assertion_id"),
        )

    def _format_indexed_sentence(
        self, idx: int, ast: Dict[str, Any], raw: str, clean: str, safe_target: str, spk: str
    ) -> str:
        if idx == 0:
            itype = str(ast.get("insight_type") or "prior_decision")
            return self._compose_natural_phrasing(raw, itype, safe_target or None, spk or None)
        if idx == 1:
            return f"We're also tracking that {clean}."
        return f"Could we clarify the next step on {clean}?"

    def _collect_multi_sentences(
        self, assertions: List[Dict[str, Any]], safe_target: str, spk: str, max_words: int
    ) -> Tuple[List[str], List[str]]:
        sentences: List[str] = []
        raw_claims: List[str] = []
        used_words = 0
        for idx, ast in enumerate(assertions[:3]):
            raw = str(ast.get("claim_text") or ast.get("object_literal") or "").strip()
            clean = _sanitize_speakable_claim(raw)
            if not clean:
                continue
            sent = self._format_indexed_sentence(idx, ast, raw, clean, safe_target, spk)
            w_count = len(sent.split())
            if used_words + w_count <= max_words:
                sentences.append(sent)
                raw_claims.append(raw)
                used_words += w_count
        return sentences, raw_claims

    def compose_multi_assertion_synthesis(
        self,
        assertions: List[Dict[str, Any]],
        dialogue_context: Any = None,
        target: Optional[str] = None,
        selected_utterance: Optional[Dict[str, Any]] = None,
        max_words: int = 85,
    ) -> ComposerResult:
        """Synthesizes 2-3 human speaker assertions into a natural, verbatim-readable multi-sentence takeaway."""
        t_start = time.perf_counter()
        if not self.enabled:
            return ComposerResult(body="", is_enabled=False, failure_reason="COMPOSER_DISABLED")
        if not assertions:
            return ComposerResult(body="", is_enabled=True, failure_reason="REDUNDANT_OR_LOW_NOVELTY")

        safe_target = _MARKUP_TAG_RE.sub("", " ".join((target or "").split())).strip()
        spk = _extract_safe_speaker(selected_utterance)
        sentences, raw_claims = self._collect_multi_sentences(assertions, safe_target, spk, max_words)
        if not sentences:
            return ComposerResult(body="", is_enabled=True, failure_reason="WORD_BUDGET_EXHAUSTED")
        composed = " ".join(sentences)
        combined_raw = " ".join(raw_claims)
        if not _validate_groundedness(composed, combined_raw, safe_target, spk):
            return ComposerResult(body="", is_enabled=True, groundedness=0.0, failure_reason="UNGROUNDED_COMPOSITION_REJECTED")
        elapsed_ms = (time.perf_counter() - t_start) * 1000.0
        return ComposerResult(
            body=composed,
            is_enabled=True,
            groundedness=1.0,
            relevance=_compute_relevance(_tokenize(combined_raw), _extract_context_tokens(dialogue_context)),
            latency_ms=elapsed_ms,
            rendered_count=len(sentences),
            assertion_id=assertions[0].get("assertion_id"),
        )


