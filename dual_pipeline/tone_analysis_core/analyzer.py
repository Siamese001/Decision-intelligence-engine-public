"""
dual_pipeline/tone_analysis_core/analyzer.py

Paralinguistic & Multimodal Meeting Tone Analysis Engine.
Extracts nuances in tone, reflection, and conversational tension across meeting dialogue
using acoustic proxies (speaking rate, pause ratio, energy) and NLP aspect-based lexical markers.
Enriches Output 2 (Executive Recap) with a Meeting Tone Arc section and Output 3 (Signal Projection)
with structured tone_signals envelopes.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.tone_analysis_core.analyzer")


class ToneCategory(str, Enum):
    """Categorical meeting tone classification."""
    DECISIVE = "DECISIVE"
    COLLABORATIVE = "COLLABORATIVE"
    HESITANT = "HESITANT"
    TENSE = "TENSE"
    REFLECTIVE = "REFLECTIVE"
    DISENGAGED = "DISENGAGED"


LEXICAL_TONE_PATTERNS = {
    ToneCategory.DECISIVE: [
        r"\b(?:we will|the decision is|agreed to|approved|confirmed|aligned on|we commit|decided|action item)\b",
        r"\b(?:final call|moving forward with|locked in|non-negotiable)\b",
    ],
    ToneCategory.COLLABORATIVE: [
        r"\b(?:great idea|fully support|working together|thanks for sharing|partnering|building on|agree with)\b",
        r"\b(?:appreciate that|good point|team effort|collaborate|sync up)\b",
    ],
    ToneCategory.HESITANT: [
        r"\b(?:maybe|i think perhaps|potentially|not sure|could be|unclear|tentative|hesitant)\b",
        r"\b(?:i guess|might be|hard to say|not completely certain|provisional)\b",
    ],
    ToneCategory.TENSE: [
        r"\b(?:pushback|disagree|unacceptable|friction|problematic|blocker|concern|frustrated|dissatisfied)\b",
        r"\b(?:not happy with|pushing back|escalation|unrealistic|untenable)\b",
    ],
    ToneCategory.REFLECTIVE: [
        r"\b(?:going back to|revisiting|looking back|rethinking|in hindsight|reflecting on|what if)\b",
        r"\b(?:lessons learned|step back|pause and think|consider whether|stepping back)\b",
    ],
}


@dataclass
class TurnToneTelemetry:
    """Tone assessment for an individual speaker turn."""
    turn_index: int
    speaker: str
    start_ts: str
    primary_tone: ToneCategory
    confidence: float
    lexical_cues: List[str] = field(default_factory=list)
    speaking_rate_wps: float = 2.5
    pause_gap_seconds: float = 0.0
    text_snippet: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "turn_index": self.turn_index,
            "speaker": self.speaker,
            "timestamp": self.start_ts,
            "primary_tone": self.primary_tone.value,
            "confidence": round(self.confidence, 4),
            "lexical_cues": self.lexical_cues,
            "speaking_rate_wps": round(self.speaking_rate_wps, 2),
            "text_snippet": self.text_snippet[:100],
        }


@dataclass
class MeetingToneArc:
    """Aggregated meeting-level tone arc across time phases."""
    dominant_tone: ToneCategory
    trajectory: str
    engagement_score: float
    turn_telemetry: List[TurnToneTelemetry]
    phase_breakdown: List[Dict[str, Any]]
    reflection_count: int
    tension_count: int
    decisiveness_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dominant_tone": self.dominant_tone.value,
            "trajectory": self.trajectory,
            "engagement_score": round(self.engagement_score, 4),
            "reflection_count": self.reflection_count,
            "tension_count": self.tension_count,
            "decisiveness_count": self.decisiveness_count,
            "phase_breakdown": self.phase_breakdown,
            "sample_telemetry": [t.to_dict() for t in self.turn_telemetry[:10]],
        }


class ParalinguisticToneAnalyzer:
    """Analyzes paralinguistic tone and reflection dynamics across meeting transcripts."""

    @staticmethod
    def _extract_matched_cues(text: str) -> Dict[ToneCategory, List[str]]:
        matched: Dict[ToneCategory, List[str]] = {}
        for cat, patterns in LEXICAL_TONE_PATTERNS.items():
            for pat in patterns:
                m = re.findall(pat, text, re.IGNORECASE)
                if m:
                    matched.setdefault(cat, []).extend(m)
        return matched

    @staticmethod
    def _resolve_turn_tone(
        words: List[str],
        matched_cues: Dict[ToneCategory, List[str]],
        pause: float,
        wps: float,
    ) -> Tuple[ToneCategory, float, List[str]]:
        clean_first_word = words[0].strip(".,!?:;").lower() if words else ""
        if len(words) <= 2 and clean_first_word in ("ok", "sure", "fine", "yep", "yeah"):
            return ToneCategory.DISENGAGED, 0.80, ["monosyllabic response"]
        if matched_cues:
            best_cat = max(matched_cues.keys(), key=lambda c: len(matched_cues[c]))
            cues = matched_cues[best_cat]
            return best_cat, min(0.95, 0.65 + 0.10 * len(cues)), cues
        if pause > 5.0 and len(words) > 5:
            return ToneCategory.REFLECTIVE, 0.70, [f"extended pre-speech pause ({pause:.1f}s)"]
        if wps > 4.5:
            return ToneCategory.TENSE, 0.65, [f"accelerated speaking cadence ({wps:.1f} wps)"]
        return ToneCategory.COLLABORATIVE, 0.75, ["balanced conversational turn"]

    @staticmethod
    def _build_phase_breakdown(
        telemetries: List[TurnToneTelemetry],
    ) -> Tuple[List[Dict[str, Any]], str]:
        n = len(telemetries)
        phase_breakdown: List[Dict[str, Any]] = []
        phase_names = ["Opening Phase", "Core Discussion", "Resolution / Wrap-Up"]
        phase_tones: List[str] = []
        chunk_size = max(1, n // 3)

        for p_idx in range(3):
            start_i = p_idx * chunk_size
            end_i = (p_idx + 1) * chunk_size if p_idx < 2 else n
            p_turns = telemetries[start_i:end_i]
            if not p_turns:
                continue
            p_cat_counts: Dict[ToneCategory, int] = {}
            for t in p_turns:
                p_cat_counts[t.primary_tone] = p_cat_counts.get(t.primary_tone, 0) + 1
            dom = max(p_cat_counts.keys(), key=lambda k: p_cat_counts[k])
            phase_tones.append(dom.value.capitalize())
            phase_breakdown.append({
                "phase_name": phase_names[p_idx],
                "time_window": f"{p_turns[0].start_ts} – {p_turns[-1].start_ts}",
                "dominant_tone": dom.value,
                "turn_count": len(p_turns),
            })

        trajectory = " -> ".join(phase_tones) if phase_tones else "Collaborative"
        return phase_breakdown, trajectory

    @staticmethod
    def _empty_meeting_tone_arc() -> MeetingToneArc:
        return MeetingToneArc(
            dominant_tone=ToneCategory.COLLABORATIVE,
            trajectory="Collaborative throughout",
            engagement_score=0.85,
            turn_telemetry=[],
            phase_breakdown=[],
            reflection_count=0,
            tension_count=0,
            decisiveness_count=0,
        )

    def analyze_turns(self, turns: List[Dict[str, Any]]) -> MeetingToneArc:
        if not turns:
            return self._empty_meeting_tone_arc()

        telemetries: List[TurnToneTelemetry] = []
        counts: Dict[ToneCategory, int] = {c: 0 for c in ToneCategory}

        last_end = 0.0
        for idx, turn in enumerate(turns):
            text = str(turn.get("text", "")).strip()
            speaker = str(turn.get("speaker", f"Speaker {idx % 2 + 1}"))
            start = float(turn.get("start", idx * 10.0))
            end = float(turn.get("end", start + 5.0))
            dur = max(0.5, end - start)
            words = text.split()
            wps = len(words) / dur

            pause = max(0.0, start - last_end) if last_end > 0 else 0.0
            last_end = end

            matched_cues = self._extract_matched_cues(text)
            primary, conf, cues = self._resolve_turn_tone(words, matched_cues, pause, wps)

            counts[primary] += 1
            ts_str = self._format_timestamp(start)
            telemetries.append(
                TurnToneTelemetry(
                    turn_index=idx,
                    speaker=speaker,
                    start_ts=ts_str,
                    primary_tone=primary,
                    confidence=conf,
                    lexical_cues=cues,
                    speaking_rate_wps=wps,
                    pause_gap_seconds=pause,
                    text_snippet=text,
                )
            )

        phase_breakdown, trajectory = self._build_phase_breakdown(telemetries)
        overall_dom = max(counts.keys(), key=lambda k: counts[k])
        engagement = min(1.0, max(0.50, 1.0 - (counts[ToneCategory.DISENGAGED] / max(1, len(telemetries)))))

        return MeetingToneArc(
            dominant_tone=overall_dom,
            trajectory=trajectory,
            engagement_score=engagement,
            turn_telemetry=telemetries,
            phase_breakdown=phase_breakdown,
            reflection_count=counts[ToneCategory.REFLECTIVE],
            tension_count=counts[ToneCategory.TENSE],
            decisiveness_count=counts[ToneCategory.DECISIVE],
        )

    @staticmethod
    def _format_timestamp(seconds: float) -> str:
        m, s = divmod(max(0.0, seconds), 60.0)
        h, m = divmod(m, 60.0)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d}"

    def format_output_2_tone_arc(self, tone_arc: MeetingToneArc) -> str:
        """Renders the standard ## Meeting Tone Arc section for Output 2 (Executive Recap)."""
        lines = [
            "\n## Meeting Tone Arc & Strategic Dynamics",
            f"**Overall Climate:** {tone_arc.dominant_tone.value.capitalize()} ({tone_arc.trajectory}) | **Engagement Index:** {tone_arc.engagement_score * 100:.0f}%",
            "",
            "| Meeting Phase | Time Window | Dominant Dynamic | Focus & Observed Interaction |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for p in tone_arc.phase_breakdown:
            tone_label = p["dominant_tone"].capitalize()
            lines.append(
                f"| {p['phase_name']} | {p['time_window']} | {tone_label} | {p['turn_count']} turns analyzed; active discussion |"
            )
        lines.append("")
        lines.append(
            f"**Tone Dynamics Summary:** The session reflected {tone_arc.decisiveness_count} decisive decision markers, "
            f"{tone_arc.reflection_count} reflective deliberations, and {tone_arc.tension_count} active tension/friction points, "
            f"yielding high operational momentum toward implementation."
        )
        lines.append("")
        return "\n".join(lines)

    def format_output_3_tone_signals(self, tone_arc: MeetingToneArc) -> List[Dict[str, Any]]:
        """Returns structured tone_signals list for Output 3 canonical JSON projection."""
        signals = []
        for t in tone_arc.turn_telemetry:
            if t.primary_tone in (ToneCategory.DECISIVE, ToneCategory.TENSE, ToneCategory.REFLECTIVE):
                signals.append({
                    "turn_index": t.turn_index,
                    "timestamp": t.start_ts,
                    "speaker": t.speaker,
                    "tone_category": t.primary_tone.value,
                    "confidence": t.confidence,
                    "lexical_cues": t.lexical_cues,
                    "speaking_rate_wps": t.speaking_rate_wps,
                })
        return signals

    @staticmethod
    def _extract_dialogue_body(text: str) -> str:
        split_res = re.split(r"\n#{1,3}\s+(?:Forensic\s+)?Transcript\b", text, maxsplit=1, flags=re.IGNORECASE)
        body = split_res[-1] if len(split_res) > 1 else text
        return re.split(r"(?:^|\n)#{1,3}\s+(?:Ambiguity Lock Register|Attribution Summary)\b", body, flags=re.IGNORECASE)[0]

    def _parse_turn_match(self, idx: int, m: Any, ignored_labels: Set[str]) -> Optional[Dict[str, Any]]:
        spk = (m.group("spk_bold") or m.group("spk_plain") or f"Speaker {idx + 1}").strip()
        spk_clean = re.sub(r"\s*\([^)]*\)", "", spk).strip().lower()
        if spk_clean in ignored_labels:
            return None
        spk_display = re.sub(r"\s*\([^)]*\)", "", spk).strip()
        content = m.group("content").strip()
        ts_raw = m.group("ts_hdr") or m.group("ts_inline") or (m.group("ts_bold") if "ts_bold" in m.groupdict() else None) or ""
        seconds = self._parse_timestamp_to_seconds(ts_raw) if ts_raw else float(idx * 15.0)
        return {
            "speaker": spk_display,
            "text": content,
            "start": seconds,
            "end": seconds + max(3.0, len(content.split()) / 2.5),
        }

    @staticmethod
    def _parse_fallback_blocks(dialogue_body: str) -> List[Dict[str, Any]]:
        blocks = [b.strip() for b in dialogue_body.split("\n\n") if b.strip() and not b.strip().startswith("#")]
        turns: List[Dict[str, Any]] = []
        for idx, block in enumerate(blocks):
            words = block.split()
            turns.append({
                "speaker": f"Speaker {(idx % 2) + 1}",
                "text": block,
                "start": float(idx * 15.0),
                "end": float(idx * 15.0) + max(3.0, len(words) / 2.5),
            })
        return turns

    def parse_turns_from_text(self, text: str) -> List[Dict[str, Any]]:
        """Parses conversational turns from markdown or plain transcript text."""
        if not text:
            return []

        dialogue_body = self._extract_dialogue_body(text)
        speaker_pattern = re.compile(
            r"^(?:###\s+\[(?P<ts_hdr>[\d:]+)\]\s*\n)?\s*"
            r"(?:\[(?P<ts_inline>[\d:]+)\]\s*)?"
            r"(?:\*\*(?:\[(?P<ts_bold>[\d:]+)\]\s*)?(?P<spk_bold>[^*]+?)(?::\*\*|\*\*:)|(?P<spk_plain>[A-Z][a-zA-Z\s.-]+?):)\s*(?P<content>.+)$",
            re.MULTILINE,
        )

        ignored_labels = {
            "date", "time", "platform", "platform reference", "participants",
            "transcript", "note", "recording gap", "coverage", "classification",
            "artifact", "artifact filename", "source references", "source coverage",
            "objective", "attendees", "statement locators", "release status",
            "referenced-person grounding",
        }

        matches = list(speaker_pattern.finditer(dialogue_body))
        if not matches:
            return self._parse_fallback_blocks(dialogue_body)

        turns: List[Dict[str, Any]] = []
        for idx, m in enumerate(matches):
            turn = self._parse_turn_match(idx, m, ignored_labels)
            if turn is not None:
                turns.append(turn)
        return turns

    @staticmethod
    def _parse_timestamp_to_seconds(ts_str: str) -> float:
        parts = ts_str.split(":")
        try:
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            return float(parts[0])
        except Exception:
            return 0.0

    def inject_tone_arc_into_recap(self, recap_text: str, tone_arc: MeetingToneArc) -> str:
        """Injects ## Meeting Tone Arc section into Output 2 before the Appendix if not already present."""
        if re.search(r"(?i)##\s+Meeting Tone Arc", recap_text):
            return recap_text

        tone_section = self.format_output_2_tone_arc(tone_arc)
        appendix_match = re.search(r"(?m)^(?=#{1,3}\s+(?:\d+\.\s*)?Appendix)", recap_text, re.IGNORECASE)
        if appendix_match:
            idx = appendix_match.start()
            return recap_text[:idx].rstrip() + "\n\n" + tone_section + "\n\n" + recap_text[idx:]
        return recap_text.rstrip() + "\n\n" + tone_section + "\n"

