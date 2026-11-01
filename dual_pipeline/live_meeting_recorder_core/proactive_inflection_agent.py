"""Live Proactive Inflection Agent for In-Call Copilot Advisory.

Monitors in-memory ConversationContextModel turns during active meetings (strictly barring
direct reads of *_live_transcript.md), detects contradictions, commitments, and blockers
against historical assertions, and pre-stages speak-gate-verified (<= 35 words) and
C-19-scrubbed insights.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from dual_pipeline.a2a.protocol import ClaimCheckPointer
from dual_pipeline.agent_architecture_core.runtime_loop import (
    AgentRuntimeLoop,
    LoopTerminalOutcome,
    RuntimeLoopConfig,
)
from dual_pipeline.sanitizer import LocalPreEgressSanitizer

_C19_FORBIDDEN_RE = re.compile(
    r"(\$\d+(?:\.\d+)?[MBK]\s+trajectory|career\s+leverage|personal\s+advancement|"
    r"private\s+billing\s+rate|INTERNAL_ADVISORY|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})",
    re.I,
)

_FILLER_PHRASES = (
    "it is important to note that ",
    "based on the historical record, ",
    "in order to ensure alignment, ",
    "as previously discussed in prior meetings, ",
    "from an executive perspective, ",
    "for additional context and background, ",
)


def _strip_conversational_fillers(text: str) -> str:
    lowered = text.lower()
    for filler in _FILLER_PHRASES:
        idx = lowered.find(filler)
        if idx != -1:
            text = (text[:idx] + text[idx + len(filler) :]).strip()
            lowered = text.lower()
    return re.sub(r"\s*\([^)]{10,}\)", "", text).strip()


_TRAILING_DANGLING_WORDS = frozenset({
    "and", "or", "but", "so", "because", "to", "for", "with", "that", "which", "who",
    "in", "on", "at", "by", "from", "of", "the", "a", "an", "is", "are", "was", "were",
})


def _trim_safe_word_boundary(text: str, max_words: int) -> str:
    sents = [s.strip() for s in re.split(r"(?<=[.!?])\s+|;\s+", text) if s.strip()]
    if len(sents) > 1 and len(sents[0].split()) <= max_words:
        chosen = sents[0]
        if len(sents) > 1 and len((sents[0] + " " + sents[1]).split()) <= max_words:
            chosen = f"{sents[0]} {sents[1]}"
        return chosen if chosen.endswith((".", "?", "!")) else f"{chosen}."
    words = text.split()[:max_words]
    cut_idx = len(words)
    for idx in range(len(words) - 1, 2, -1):
        if words[idx].lower().rstrip(",;:-.!?") in _TRAILING_DANGLING_WORDS:
            cut_idx = idx
        else:
            break
    trimmed = " ".join(words[:cut_idx]).rstrip(",;:-")
    return trimmed if trimmed.endswith((".", "?", "!")) else f"{trimmed}."


def compress_for_speak_gate(line: str, max_words: int = 35, max_steps: int = 2) -> str:
    """Bounded 2-step speak-gate compression preserving entity, negation, and clause boundaries."""
    if max_words <= 0:
        return ""
    text = " ".join(line.strip().split())
    if len(text.split()) <= max_words:
        return text

    for step in range(1, max_steps + 1):
        if step == 1:
            text = _strip_conversational_fillers(text)
        else:
            text = _trim_safe_word_boundary(text, max_words)
        if len(text.split()) <= max_words:
            break
    return text


@dataclass(frozen=True)
class PreStagedCopilotInsight:
    """Speak-gate-verified, C-19-scrubbed proactive insight linked to historical assertion."""

    insight_id: str
    inflection_type: str  # "CONTRADICTION" | "COMMITMENT" | "BLOCKER"
    speaker: str
    rendered_comment: str
    word_count: int
    confidence: float
    cited_assertion_ids: List[str]
    c19_clean: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProactiveInflectionResult:
    """Result of evaluating live in-memory turns for proactive copilot inflection points."""

    status: str  # "STAGED" | "ABSTAINED"
    insights: List[PreStagedCopilotInsight]
    abstain_reason: Optional[str]
    c19_blocked_count: int
    steps_executed: int
    tool_calls_used: int
    terminal_outcome: str
    claim_checks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "insights": [i.to_dict() for i in self.insights],
            "abstain_reason": self.abstain_reason,
            "c19_blocked_count": self.c19_blocked_count,
            "steps_executed": self.steps_executed,
            "tool_calls_used": self.tool_calls_used,
            "terminal_outcome": self.terminal_outcome,
            "claim_checks": list(self.claim_checks),
        }


def _classify_inflection(turn_text: str, assertion_text: str) -> Optional[str]:
    t_low, a_low = turn_text.lower(), assertion_text.lower()
    shared = {
        w for w in re.findall(r"\b[a-z0-9_]{4,}\b", t_low)
        if w not in ("that", "this", "with", "from", "have", "will", "were", "they")
    } & set(re.findall(r"\b[a-z0-9_]{4,}\b", a_low))
    if not shared:
        return None
    if any(k in t_low for k in ("delay", "slip", "instead", "chang", "contrary", "not ", "postpone")):
        return "CONTRADICTION"
    if any(k in t_low for k in ("block", "stuck", "risk", "depend", "waiting")):
        return "BLOCKER"
    if any(k in t_low for k in ("commit", "deliver", "friday", "monday", "deadline", "complete")):
        return "COMMITMENT"
    return None


def _is_c19_or_quarantined_assertion(asrt: Mapping[str, Any]) -> bool:
    if str(asrt.get("namespace", "")).upper() == "INTERNAL_ADVISORY" or bool(asrt.get("is_quarantined")):
        return True
    a_text = str(asrt.get("claim_text") or asrt.get("object_literal") or "").strip()
    return bool(_C19_FORBIDDEN_RE.search(a_text))


def _match_turn_against_assertions(
    t_idx: int,
    t_text: str,
    t_spk: str,
    t_conf: float,
    historical_assertions: Sequence[Mapping[str, Any]],
    candidates: List[Dict[str, Any]],
    ptrs: List[ClaimCheckPointer],
) -> int:
    blocked = 0
    for asrt in historical_assertions:
        if _is_c19_or_quarantined_assertion(asrt):
            blocked += 1
            continue
        a_text = str(asrt.get("claim_text") or asrt.get("object_literal") or "").strip()
        inf_type = _classify_inflection(t_text, a_text)
        if not inf_type:
            continue
        a_id = str(asrt.get("assertion_id") or f"asrt-{t_idx}")
        raw_bytes = json.dumps(dict(asrt), sort_keys=True, default=str).encode("utf-8")
        ptrs.append(
            ClaimCheckPointer(
                source_id=str(asrt.get("source_id") or "src:graph:active"),
                evidence_id=a_id,
                content_digest=hashlib.sha256(raw_bytes).hexdigest(),
                locator={"assertion_id": a_id},
            )
        )
        candidates.append(
            {
                "idx": len(candidates) + 1,
                "inflection_type": inf_type,
                "speaker": t_spk,
                "confidence": round(min(0.98, t_conf), 3),
                "assertion_id": a_id,
                "draft": f"In order to ensure alignment, {t_spk} noted '{t_text}', whereas historical record {a_id} states: {a_text}",
            }
        )
    return blocked


class LiveProactiveInflectionAgent:
    """Monitors in-memory live meeting turns and pre-stages grounded, speakable copilot insights."""

    def __init__(
        self,
        *,
        max_steps: int = 2,
        max_tool_calls: int = 3,
        max_words: int = 35,
        min_confidence: float = 0.85,
        timeout_seconds: float = 15.0,
    ) -> None:
        if max_steps <= 0 or max_steps > 3:
            raise ValueError("max_steps must be within [1, 3].")
        if max_words <= 0 or max_words > 60:
            raise ValueError("max_words must be within [1, 60].")
        self.max_steps = max_steps
        self.max_tool_calls = max_tool_calls
        self.max_words = max_words
        self.min_confidence = min_confidence
        self.timeout_seconds = timeout_seconds

    def _collect_inflection_candidates(
        self,
        turns: Sequence[Mapping[str, Any]],
        historical_assertions: Sequence[Mapping[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[ClaimCheckPointer], int]:
        candidates: List[Dict[str, Any]] = []
        ptrs: List[ClaimCheckPointer] = []
        c19_blocked = 0
        for t_idx, turn in enumerate(turns):
            t_text = str(turn.get("text") or "").strip()
            t_spk = str(turn.get("speaker") or "Speaker").strip()
            t_conf = float(turn.get("confidence", 0.90))
            if _C19_FORBIDDEN_RE.search(t_text):
                c19_blocked += 1
                continue
            if t_conf < self.min_confidence:
                continue
            c19_blocked += _match_turn_against_assertions(
                t_idx, t_text, t_spk, t_conf, historical_assertions, candidates, ptrs
            )
        return candidates, ptrs, c19_blocked

    def _stage_compressed_insights(self, raw_candidates: Sequence[Mapping[str, Any]]) -> List[PreStagedCopilotInsight]:
        staged: List[PreStagedCopilotInsight] = []
        for cand in raw_candidates:
            sanitized = LocalPreEgressSanitizer.sanitize_text(str(cand["draft"]))
            compressed = compress_for_speak_gate(sanitized, max_words=self.max_words, max_steps=2)
            w_count = len(compressed.split())
            if not _C19_FORBIDDEN_RE.search(compressed) and 0 < w_count <= self.max_words:
                staged.append(
                    PreStagedCopilotInsight(
                        insight_id=f"insight-{cand['idx']}",
                        inflection_type=str(cand["inflection_type"]),
                        speaker=str(cand["speaker"]),
                        rendered_comment=compressed,
                        word_count=w_count,
                        confidence=float(cand["confidence"]),
                        cited_assertion_ids=[str(cand["assertion_id"])],
                        c19_clean=True,
                    )
                )
        return staged

    def evaluate_live_window(
        self,
        *,
        turns: Sequence[Mapping[str, Any]],
        historical_assertions: Sequence[Mapping[str, Any]],
        transcript_file_path: Optional[Path | str] = None,
    ) -> ProactiveInflectionResult:
        """Evaluates in-memory turns against historical graph assertions."""
        if transcript_file_path is not None and ("_live_transcript.md" in str(transcript_file_path) or str(transcript_file_path).endswith(".md")):
            raise PermissionError("LIVE_TRANSCRIPT_DIRECT_READ_PROHIBITED: Route 0b forbids reading *_live_transcript.md directly.")
        if not turns or not historical_assertions:
            return ProactiveInflectionResult("ABSTAINED", [], "NO_TURNS_OR_ASSERTIONS", 0, 0, 0, "COMPLETED")

        loop = AgentRuntimeLoop(
            RuntimeLoopConfig(
                agent_id="live_proactive_inflection_agent",
                goal="Detect live meeting inflection points and pre-stage speak-gate insights",
                max_steps=self.max_steps, max_tool_calls=self.max_tool_calls, timeout_seconds=self.timeout_seconds,
                allowed_tools=("query_knowledge_graph", "inspect_graph_snapshot"),
            )
        )
        raw_candidates, claim_checks, c19_blocked = self._collect_inflection_candidates(turns, historical_assertions)
        staged_insights: List[PreStagedCopilotInsight] = []

        def act_fn(_s: Dict[str, Any], _d: Dict[str, Any], step_idx: int) -> Dict[str, Any]:
            nonlocal staged_insights
            if step_idx == 1:
                task, _ = loop.dispatch_subagent_task(
                    recipient_agent_id="retrieval_specialist", objective="Correlate live turns against historical assertions",
                    scope_in=["assertions"], scope_out=["shadow_graph_nodes", "_live_transcript.md"],
                    allowed_tools=["query_knowledge_graph"], deliverable={"matched": len(raw_candidates)},
                    definition_of_done="Live turns checked", claim_checks=claim_checks,
                )
                return {"tool_calls_used": 2, "tokens_used": 95, "a2a_task_id": task.task_id, "state_updates": {"matched": len(raw_candidates)}}
            staged_insights = self._stage_compressed_insights(raw_candidates)
            return {"tool_calls_used": 1, "tokens_used": 65, "state_updates": {"staged": len(staged_insights)}}

        loop_res = loop.execute(
            initial_state={"matched": 0},
            observe_fn=lambda _s, i: {"step": i, "candidates": len(raw_candidates)},
            decide_fn=lambda _o, i: {"action": f"step_{i}", "delegated": i == 1, "candidates": [(f"step_{i}", 0.91), ("abstain", 0.09)]},
            act_fn=act_fn,
            evaluate_fn=lambda _s, _a, i: {"done": (i == 1 and not raw_candidates) or i >= 2, "quality_score": 0.5 * i},
        )
        status = "STAGED" if staged_insights else "ABSTAINED"
        abstain_reason = None if staged_insights else ("C19_QUARANTINED" if c19_blocked > 0 else "BELOW_CONFIDENCE_OR_NO_INFLECTION")
        return ProactiveInflectionResult(
            status, staged_insights, abstain_reason, c19_blocked, loop_res.steps_executed,
            loop_res.total_tool_calls, loop_res.terminal_outcome.value if isinstance(loop_res.terminal_outcome, LoopTerminalOutcome) else str(loop_res.terminal_outcome),
            [c.to_dict() for c in claim_checks],
        )


__all__ = [
    "LiveProactiveInflectionAgent",
    "PreStagedCopilotInsight",
    "ProactiveInflectionResult",
    "compress_for_speak_gate",
]
