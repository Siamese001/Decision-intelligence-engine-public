"""
dual_pipeline/speaker_attribution_core/pattern_engine.py

Speaker Pattern Engine for Long Unattributed Transcripts.
Implements industry best practices to establish speaker patterns across long transcripts
arriving without speaker assignments:
  1. Self-attribution cues ("This is Amit", "Amit speaking", "Speaking as Jen")
  2. Addressee / vocative cues ("Amit, do you agree?", "Thanks Mandy")
  3. Topic-shift boundary detection (lexical overlap drift and transition discourse markers)
  4. Interlocutor anchor propagation forward and backward across turns
  5. Structured SpeakerPatternMap generation
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("dual_pipeline.speaker_attribution_core.pattern_engine")

TOPIC_SHIFT_MARKERS = [
    r"\bmoving on to\b",
    r"\bnext item on the agenda\b",
    r"\blet'?s pivot to\b",
    r"\bswitching gears\b",
    r"\bto change topics\b",
    r"\banother thing to discuss\b",
    r"\bturning now to\b",
    r"\bon a different note\b",
]

SELF_ATTRIBUTION_PATTERNS = [
    r"\bthis is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?) speaking\b",
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?) here\b",
    r"\bmy name is ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    r"\bspeaking as ([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
]

ADDRESSEE_PATTERNS = [
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?),\s*(?:can you|do you|what do you|could you|would you)\b",
    r"\bthanks(?:,\s*|\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    r"\bthank you(?:,\s*|\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
    r"\bover to you(?:,\s*|\s+)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
]


@dataclass
class SpeakerPatternAnchor:
    """An anchored identity for a specific turn or cluster."""
    turn_index: int
    speaker_name: str
    cue_type: str  # "SELF_ATTRIBUTION", "ADDRESSEE_HANDOFF", "GRAPH_PRIOR"
    confidence: float
    evidence_text: str


@dataclass
class SpeakerPatternMap:
    """Structured pattern map summarizing speaker clusters, anchors, and transitions."""
    total_turns: int
    anchors: List[SpeakerPatternAnchor]
    assigned_turns: List[Dict[str, Any]]
    speaker_roster: List[str]
    topic_shift_turn_indices: List[int]
    propagation_confidence: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_turns": self.total_turns,
            "anchor_count": len(self.anchors),
            "anchors": [
                {
                    "turn_index": a.turn_index,
                    "speaker_name": a.speaker_name,
                    "cue_type": a.cue_type,
                    "confidence": a.confidence,
                    "evidence_text": a.evidence_text,
                }
                for a in self.anchors
            ],
            "speaker_roster": self.speaker_roster,
            "topic_shift_turn_indices": self.topic_shift_turn_indices,
            "propagation_confidence": round(self.propagation_confidence, 4),
        }


class SpeakerPatternEngine:
    """
    Recovers speaker identities and conversational structures from raw,
    unattributed or poorly attributed dialogue turns.
    """

    def __init__(self, known_participants: Optional[List[str]] = None):
        self.known_participants = [p.strip() for p in (known_participants or []) if p.strip()]
        self._known_names_lower = {p.lower(): p for p in self.known_participants}

    def analyze_and_pattern_turns(
        self, raw_turns: List[Dict[str, Any]]
    ) -> SpeakerPatternMap:
        """
        Executes turn analysis, anchor extraction, topic boundary detection,
        and bidirectional label propagation.
        """
        if not raw_turns:
            return SpeakerPatternMap(
                total_turns=0,
                anchors=[],
                assigned_turns=[],
                speaker_roster=[],
                topic_shift_turn_indices=[],
                propagation_confidence=0.0,
            )

        anchors: List[SpeakerPatternAnchor] = []
        topic_shifts: List[int] = []

        # Step 1: Scan turns for self-attributions, addressee cues, and topic shifts
        for idx, turn in enumerate(raw_turns):
            text = str(turn.get("text", "")).strip()

            # Detect topic shift
            for pat in TOPIC_SHIFT_MARKERS:
                if re.search(pat, text, re.IGNORECASE):
                    topic_shifts.append(idx)
                    break

            # Detect self-attribution (highest confidence anchor)
            for pat in SELF_ATTRIBUTION_PATTERNS:
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    candidate = m.group(1).strip()
                    canon = self._canonicalize_name(candidate)
                    if canon:
                        anchors.append(
                            SpeakerPatternAnchor(
                                turn_index=idx,
                                speaker_name=canon,
                                cue_type="SELF_ATTRIBUTION",
                                confidence=0.95,
                                evidence_text=m.group(0),
                            )
                        )
                        break

            # Detect addressee handoff (next turn belongs to addressee)
            for pat in ADDRESSEE_PATTERNS:
                m = re.search(pat, text, re.IGNORECASE)
                if m and idx + 1 < len(raw_turns):
                    candidate = m.group(1).strip()
                    canon = self._canonicalize_name(candidate)
                    if canon:
                        anchors.append(
                            SpeakerPatternAnchor(
                                turn_index=idx + 1,
                                speaker_name=canon,
                                cue_type="ADDRESSEE_HANDOFF",
                                confidence=0.85,
                                evidence_text=m.group(0),
                            )
                        )
                        break

        # Step 2: Establish default roster
        discovered_roster = list({a.speaker_name for a in anchors})
        full_roster = discovered_roster or self.known_participants or ["Speaker 1", "Speaker 2"]

        # Step 3: Bidirectional propagation from anchors
        assigned_turns: List[Dict[str, Any]] = []
        turn_speakers: Dict[int, str] = {}
        for a in anchors:
            turn_speakers[a.turn_index] = a.speaker_name

        # Propagate forward and backward between anchors
        n_turns = len(raw_turns)
        for i in range(n_turns):
            if i in turn_speakers:
                continue
            # Find nearest anchor before and after
            prev_anchor = None
            next_anchor = None
            for a in anchors:
                if a.turn_index < i:
                    if prev_anchor is None or a.turn_index > prev_anchor.turn_index:
                        prev_anchor = a
                elif a.turn_index > i:
                    if next_anchor is None or a.turn_index < next_anchor.turn_index:
                        next_anchor = a

            if prev_anchor and next_anchor:
                # Alternate between the two anchors if distance indicates alternating dialogue
                dist = i - prev_anchor.turn_index
                if prev_anchor.speaker_name != next_anchor.speaker_name:
                    turn_speakers[i] = prev_anchor.speaker_name if dist % 2 == 0 else next_anchor.speaker_name
                else:
                    turn_speakers[i] = prev_anchor.speaker_name
            elif prev_anchor:
                turn_speakers[i] = prev_anchor.speaker_name if (i - prev_anchor.turn_index) % 2 == 0 else self._pick_alternate(prev_anchor.speaker_name, full_roster)
            elif next_anchor:
                turn_speakers[i] = next_anchor.speaker_name if (next_anchor.turn_index - i) % 2 == 0 else self._pick_alternate(next_anchor.speaker_name, full_roster)
            else:
                # No anchors: assign rotating roster
                turn_speakers[i] = full_roster[i % len(full_roster)]

        for i, turn in enumerate(raw_turns):
            assigned = dict(turn)
            assigned["speaker"] = turn_speakers.get(i, "Unknown")
            assigned["pattern_turn_index"] = i
            assigned_turns.append(assigned)

        prop_conf = (
            min(1.0, 0.50 + 0.15 * len(anchors)) if anchors else 0.40
        )

        return SpeakerPatternMap(
            total_turns=n_turns,
            anchors=anchors,
            assigned_turns=assigned_turns,
            speaker_roster=list(dict.fromkeys(full_roster)),
            topic_shift_turn_indices=topic_shifts,
            propagation_confidence=prop_conf,
        )

    def _canonicalize_name(self, raw_candidate: str) -> Optional[str]:
        """Resolves candidate name against known participants or formats properly."""
        clean = raw_candidate.strip()
        if not clean:
            return None
        low = clean.lower()
        if low in self._known_names_lower:
            return self._known_names_lower[low]
        # Match by first name
        for k_low, k_orig in self._known_names_lower.items():
            if k_low.startswith(low) or low.startswith(k_low.split()[0]):
                return k_orig
        # Accept title-case name if >= 3 characters
        if clean.istitle() and len(clean) >= 3:
            return clean
        return None

    @staticmethod
    def _pick_alternate(current: str, roster: List[str]) -> str:
        """Selects an alternating interlocutor from roster."""
        for name in roster:
            if name != current:
                return name
        return "Interlocutor"
