"""
dual_pipeline/live_meeting_recorder_core/lexical_priming.py

Dynamic Project-Aware Lexical Priming and Phonetic Drift Normalizer for Live Meeting ASR.
Supplies domain terminology to MLX Whisper to prevent acoustic misrecognitions
(e.g., preventing 'ROCC' from being transcribed as 'Iraq') and performs targeted post-correction.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

# Canonical project domain ontologies (pure domain/technical terms; attendee names injected dynamically from roster)
PROJECT_DOMAIN_LEXICONS: Dict[str, List[str]] = {
    "apex_trans": [
        "ROCC", "Renewable Operations Control Center", "SCADA", "EMS",
        "Energy Management System", "Pano AI", "NERC-CIP", "CIP-005",
        "EDO", "Substation Onboarding", "DERMS", "ADMS", "PI Historian",
        "Apex Global Enterprises", "Apex Power & Grid", "APEX",
        "PartnerAdvisory", "Wind Turbines", "Solar Inverters",
    ],
    "project_nexus": [
        "NEA", "Apex Global Analytics", "Nexus", "ROCC", "SCADA", "EMS",
        "NERC-CIP", "CIP-005", "PartnerAdvisory",
    ],
    "project_lens": [
        "LENS", "Single Turbine", "Turbine Fleet",
        "PartnerAdvisory", "Apex Global Enterprises", "AWS", "BigQuery",
    ],
    "project_beacon": [
        "Project Beacon", "Sherpa", "Data Mesh", "Catalog", "Lakehouse",
        "BigQuery", "PartnerAdvisory", "LangChain",
        "Prompt Engineering", "Connect",
    ],
    "internal_consulting": [
        "PartnerAdvisory", "Florida Market", "Account Planning",
    ],
    "project_atlas": [
        "APEX", "Apex Power & Grid", "Rewire", "Grid Modernization",
        "Substation", "AMI", "Smart Meter", "PartnerAdvisory",
    ],
}

# Phonetic drift substitution rules parameterized by project
PHONETIC_DRIFT_PATTERNS: Dict[str, List[tuple[re.Pattern, str]]] = {
    "project_beacon": [
        (re.compile(r"\bSerpa\b", re.IGNORECASE), "Sherpa"),
        (re.compile(r"\b(?:lane\s+chain|lang\s+chain)\b", re.IGNORECASE), "LangChain"),
    ],
    "apex_trans": [
        # Match 'people in Iraq', 'enter metadata out there in Iraq', 'in Iraq would do this'
        (
            re.compile(r"\b(?:in|into|at|from)\s+iraq\b", re.IGNORECASE),
            "in the ROCC",
        ),
        (
            re.compile(r"\b(people|operators?|team)\s+(?:in|at)\s+iraq\b", re.IGNORECASE),
            r"\1 in the ROCC",
        ),
        (
            re.compile(r"\bthe\s+iraq\b", re.IGNORECASE),
            "the ROCC",
        ),
        # Match 'scater' / 'skater' / 'scata' -> SCADA
        # Match 'scater' / 'skater' / 'scata' -> SCADA
        (
            re.compile(r"\b(?:scater|skater|scata)\s+data\b", re.IGNORECASE),
            "SCADA data",
        ),
    ],
    "project_nexus": [
        (
            re.compile(r"\b(?:in|into|at|from)\s+iraq\b", re.IGNORECASE),
            "in the ROCC",
        ),
        (
            re.compile(r"\b(?:scater|skater|scata)\s+data\b", re.IGNORECASE),
            "SCADA data",
        ),
    ],
}


def _extract_roster(attendees: Optional[Sequence[Dict[str, Any]]]) -> List[str]:
    if not attendees:
        return []
    roster: List[str] = []
    for a in attendees:
        name = a.get("name") if isinstance(a, dict) else str(a)
        if name and name not in roster:
            roster.append(name)
    return roster


NARROW_TECHNICAL_ACRONYMS: frozenset[str] = frozenset({
    "NERC-CIP", "CIP-005", "ROCC", "SCADA", "EMS", "DERMS", "ADMS", "Pano AI",
})

ACRONYM_CORROBORATING_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "nerc-cip": ("nerc", "cip", "compliance", "cyber", "security", "firewall", "substation"),
    "cip-005": ("cip", "electronic security", "firewall", "perimeter"),
    "rocc": ("rocc", "control center", "renewables operations", "iraq"),
    "scada": ("scada", "telemetry", "ems", "grid", "supervisory"),
    "ems": ("ems", "energy management"),
    "derms": ("derms", "distributed energy"),
    "adms": ("adms", "advanced distribution"),
    "pano ai": ("pano", "wildfire", "cameras"),
}


def _merge_custom_terms(base_terms: Sequence[str], custom_terms: Optional[Sequence[str]]) -> List[str]:
    terms = list(base_terms)
    if not custom_terms:
        return terms
    for ct in custom_terms:
        if ct and ct not in terms:
            terms.append(ct)
    return terms


class LexicalPrimer:
    """Provides project-tuned vocabulary priming for Whisper and phonetic correction."""

    def __init__(
        self,
        project_name: Optional[str] = None,
        learned_aliases: Optional[Dict[str, str]] = None,
        speaker_memory: Optional[Any] = None,
    ) -> None:
        self.project_name = (project_name or "").strip().lower()
        self.lexicon = PROJECT_DOMAIN_LEXICONS.get(self.project_name, [])
        self.drift_rules = list(PHONETIC_DRIFT_PATTERNS.get(self.project_name, []))
        self.learned_aliases: Dict[str, str] = dict(learned_aliases or {})
        self.speaker_memory = speaker_memory
        self._load_runtime_learned_aliases()

    def _load_runtime_learned_aliases(self) -> None:
        import json
        from pathlib import Path
        cand = Path("config/runtime/learned_aliases.json")
        if cand.is_file():
            try:
                data = json.loads(cand.read_text(encoding="utf-8"))
                aliases = data.get("aliases", data) if isinstance(data, dict) else {}
                if isinstance(aliases, dict):
                    for k, v in aliases.items():
                        sv = str(v).strip()
                        if k and sv and ":" not in sv and str(k) not in self.learned_aliases:
                            self.learned_aliases[str(k)] = sv
            except Exception:
                pass

    @staticmethod
    def _append_profile_terms(prof: Any, person: str, extra: List[str]) -> None:
        for top in reversed(getattr(prof, "domain_topics", []) or []):
            if top and top not in extra:
                extra.append(str(top))
        for pa in getattr(prof, "phonetic_aliases", []) or []:
            entry = f"{person} ({pa})"
            if pa and entry not in extra:
                extra.append(entry)

    def _collect_learned_and_speaker_terms(
        self,
        roster: Sequence[str],
        learned_aliases: Optional[Dict[str, str]] = None,
        speaker_memory: Optional[Any] = None,
    ) -> List[str]:
        alias_map = {**self.learned_aliases, **(learned_aliases or {})}
        extra: List[str] = []
        for target in alias_map.values():
            st = str(target).strip()
            if st and ":" not in st and st not in extra:
                extra.append(st)
        mem = speaker_memory or self.speaker_memory
        if mem is None or not hasattr(mem, "get_profile"):
            return extra
        for person in roster:
            prof = mem.get_profile(person)
            if prof is not None:
                self._append_profile_terms(prof, person, extra)
        return extra

    def build_whisper_prompt(
        self,
        meeting_name: str = "",
        attendees: Optional[Sequence[Dict[str, Any]]] = None,
        custom_terms: Optional[Sequence[str]] = None,
        transcript_context: str = "",
        learned_aliases: Optional[Dict[str, str]] = None,
        speaker_memory: Optional[Any] = None,
        max_chars: int = 800,
    ) -> str:
        """Constructs a high-signal, compact Whisper terminology prompt under token limits."""
        roster = _extract_roster(attendees)
        learned_terms = self._collect_learned_and_speaker_terms(roster, learned_aliases, speaker_memory)
        merged_custom = _merge_custom_terms(learned_terms, custom_terms)
        terms = _merge_custom_terms(self.lexicon, merged_custom)
        context_text = f"{meeting_name} {transcript_context}".lower()

        # Gate narrow technical acronyms behind meeting topic or verified dialogue context
        filtered_terms: List[str] = []
        for t in terms:
            if t in NARROW_TECHNICAL_ACRONYMS:
                t_lower = t.lower()
                rel_kw = ACRONYM_CORROBORATING_KEYWORDS.get(t_lower, (t_lower,))
                if not any(kw in context_text for kw in rel_kw):
                    continue
            filtered_terms.append(t)

        roster_str = ", ".join(roster) if roster else "Enterprise stakeholders"
        meeting_ctx = f"discussing {meeting_name}. " if meeting_name else ""
        lex_str = ", ".join(filtered_terms[:36])

        prompt = f"{roster_str} {meeting_ctx}Domain terms: {lex_str}."
        budget = max(100, min(800, int(max_chars)))
        return prompt[:budget]

    def post_process_transcript(self, raw_text: str) -> str:
        """Applies project-specific phonetic drift corrections and learned aliases on transcribed utterances."""
        if not raw_text:
            return ""

        processed = raw_text
        for pattern, replacement in self.drift_rules:
            processed = pattern.sub(replacement, processed)

        for bad_alias, canonical_target in self.learned_aliases.items():
            if bad_alias and canonical_target and len(bad_alias) >= 3:
                processed = re.sub(rf"\b{re.escape(bad_alias)}\b", canonical_target, processed, flags=re.IGNORECASE)

        # Contextual Pano AI normalization
        if re.search(r"\b(?:smoke|fire|camera|wildfire|detect)\b", processed, re.IGNORECASE):
            processed = re.sub(r"\b(?:pan\s+up|pan-up)\b", "Pano AI", processed, flags=re.IGNORECASE)

        return processed

    def post_process_transcript_with_verbatim(self, raw_text: str) -> tuple[str, str]:
        """Applies project-specific phonetic drift corrections on transcribed utterances.

        Returns (normalized_text, verbatim_text).
        """
        if not raw_text:
            return "", ""
        normalized = self.post_process_transcript(raw_text)
        return normalized, raw_text
