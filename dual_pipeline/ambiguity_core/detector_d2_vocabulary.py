"""
dual_pipeline/ambiguity_core/detector_d2_vocabulary.py

Domain 2: Domain Vocabulary & Acronym Phonetics Detector.
Identifies phonetic confusions for domain acronyms, technical terms, and project vocabulary.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    AMBIGUITY_MARGIN_THRESHOLD,
    AmbiguityDomain,
    AmbiguityRecord,
    CandidateOption,
)

logger = logging.getLogger(__name__)

DEFAULT_GLOSSARY = {
    "NEE": ["NEA", "KNEE", "ME"],
    "NEXUS": ["NEXT US", "LEXUS", "TEXAS"],
    "BGE-M3": ["BGM3", "BG-M3", "BGE M3"],
    "WAL": ["WALL", "WHILE"],
    "HITL": ["HOTEL", "HITTLE", "TITLE"],
    "ASR": ["A SR", "ANSWER"],
    "APEX": ["FPO", "F PL"],
    "partner_advisory": ["SALEM", "SOLEMN"],
    "SSOT": ["S SORT", "ES ESS O TEE"],
    "ChatGPT": ["CHACHI", "CHACHI BIKKI", "CHAT GEE PEE TEE", "CHAT GPT", "CHATCHI"],
    "GPT-4": ["GEE PEE TEE FOUR", "GPT 4", "GB T4"],  # noqa: SSOT_EXEMPT
    "Claude": ["CLOD", "CLOUD"],
    "Opus": ["OPEN US", "HOPUS"],
}


def _create_d2_record(
    idx: int,
    canonical: str,
    raw_token: str,
    speaker: str,
    text: str,
    confidence: float,
    turn: Dict[str, Any],
    threshold: float,
) -> AmbiguityRecord:
    c1 = CandidateOption(
        option_id=f"opt_d2_{idx}_{canonical}",
        text=canonical,
        confidence=confidence,
        rationale=f"Canonical glossary term phonetically matching '{raw_token}'",
        evidence_refs=[f"turn_{idx}"],
        is_recommended=True,
    )
    c2 = CandidateOption(
        option_id=f"opt_d2_{idx}_{raw_token}",
        text=raw_token,
        confidence=round(1.0 - confidence, 2),
        rationale=f"Literal verbatim ASR token '{raw_token}'",
        evidence_refs=[f"turn_{idx}"],
    )
    return AmbiguityRecord.create(
        ambiguity_id=f"amb_d2_{idx:04d}_{canonical}",
        domain=AmbiguityDomain.D2_DOMAIN_VOCABULARY,
        segment_id=f"turn_{idx}",
        source_excerpt=f"{speaker}: ...{raw_token}... ({text[:60]})",
        candidates=[c1, c2],
        time_span_ms=(turn.get("start_ms", 0), turn.get("end_ms", 0)),
        is_material=True,
        threshold=threshold,
    )


def _scan_turn_phrases(
    idx: int,
    turn: Dict[str, Any],
    text_upper: str,
    glossary: Dict[str, List[str]],
    threshold: float,
) -> Tuple[List[AmbiguityRecord], Set[str]]:
    records: List[AmbiguityRecord] = []
    matched_phrases: Set[str] = set()
    text = turn.get("text", "")
    speaker = turn.get("speaker", "Speaker")

    for canonical, confusions in glossary.items():
        for conf in confusions:
            conf_u = conf.upper()
            if " " not in conf or conf_u not in text_upper or conf_u in matched_phrases:
                continue
            matched_phrases.add(conf_u)
            rec = _create_d2_record(
                idx=idx,
                canonical=canonical,
                raw_token=conf,
                speaker=speaker,
                text=text,
                confidence=0.90,
                turn=turn,
                threshold=threshold,
            )
            records.append(rec)
    return records, matched_phrases


def _match_single_word(
    w_upper: str,
    canonical: str,
    confusions: List[str],
) -> Optional[float]:
    if w_upper in [c.upper() for c in confusions]:
        return 0.90
    from rapidfuzz import fuzz

    ratio = fuzz.ratio(w_upper, canonical.upper()) / 100.0
    if 0.70 <= ratio < 1.0 and len(canonical) >= 3:
        return round(max(ratio, 0.55), 2)
    return None


def _scan_turn_words(
    idx: int,
    turn: Dict[str, Any],
    matched_phrases: Set[str],
    glossary: Dict[str, List[str]],
    threshold: float,
) -> List[AmbiguityRecord]:
    records: List[AmbiguityRecord] = []
    text = turn.get("text", "")
    speaker = turn.get("speaker", "Speaker")
    words = set(re.findall(r"\b[A-Za-z0-9\-_]+\b", text))

    for word in words:
        w_upper = word.upper()
        if any(w_upper in mp for mp in matched_phrases):
            continue
        for canonical, confusions in glossary.items():
            conf = _match_single_word(w_upper, canonical, confusions)
            if conf is None:
                continue
            rec = _create_d2_record(
                idx=idx,
                canonical=canonical,
                raw_token=word,
                speaker=speaker,
                text=text,
                confidence=conf,
                turn=turn,
                threshold=threshold,
            )
            records.append(rec)
            break
    return records


class VocabularyAcronymDetector:
    """Detects Domain 2 (Domain Vocabulary & Acronym Phonetics) ambiguities."""

    def __init__(
        self,
        margin_threshold: float = AMBIGUITY_MARGIN_THRESHOLD,
        glossary: Optional[Dict[str, List[str]]] = None,
    ) -> None:
        self.margin_threshold = margin_threshold
        self.glossary = glossary or DEFAULT_GLOSSARY

    def _prepare_glossary(self, project_acronyms: Optional[List[str]]) -> Dict[str, List[str]]:
        glossary = dict(self.glossary)
        if project_acronyms:
            for acr in project_acronyms:
                if acr.upper() not in glossary:
                    glossary[acr.upper()] = []
        return glossary

    def detect(
        self,
        turns: List[Dict[str, Any]],
        project_acronyms: Optional[List[str]] = None,
    ) -> List[AmbiguityRecord]:
        """Scans transcript turns for phonetic terminology confusions."""
        records: List[AmbiguityRecord] = []
        glossary = self._prepare_glossary(project_acronyms)

        for idx, turn in enumerate(turns):
            text = turn.get("text", "")
            if not text:
                continue
            phrase_records, matched = _scan_turn_phrases(
                idx, turn, text.upper(), glossary, self.margin_threshold
            )
            records.extend(phrase_records)
            word_records = _scan_turn_words(
                idx, turn, matched, glossary, self.margin_threshold
            )
            records.extend(word_records)

        return records
