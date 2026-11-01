"""Receptive watchlist injector & soft-signal transition extractor.

Generates bounded prompt context for stale graph edges and extracts soft
role-transition signals (e.g., roll-offs, handovers, replacements) from incoming text.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence

from unified_pipeline.temporal_reconciliation.staleness_auditor import (
    StalenessAuditReport,
    StalenessRecord,
)

REPLACEMENT_VERBS = (
    r"has taken over(?:\s+for)?",
    r"is taking over(?:\s+for)?",
    r"taking over from",
    r"replaces",
    r"is replacing",
    r"stepped in for",
    r"stepping in for",
    r"covering for",
)

ROLLOFF_VERBS = (
    r"is no longer on",
    r"rolled off",
    r"has rolled off",
    r"stepped down from",
    r"transitioned off",
    r"left",
)

FORMER_ROLE_PREFIXES = (
    r"(?:former|previous|ex-)\s*(?:tech lead|lead|pm|owner)",
)


@dataclass(frozen=True)
class WatchlistEntry:
    """Stale edge formatted for receptive extraction injection."""

    watchlist_id: str
    edge_id: str
    entity_id: str
    entity_name: str
    edge_type: str
    target_id: str
    target_name: str
    last_attested_date: str
    age_days: int
    aliases: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransitionSignal:
    """Extracted soft-signal candidate proposing role retirement or replacement."""

    signal_id: str
    candidate_type: str  # 'REPLACEMENT' or 'RETRACTION'
    supersedes_edge_id: str
    retracted_entity_id: str
    retracted_entity_name: str
    replacement_name: Optional[str]
    workstream_node_id: str
    workstream_name: str
    verbatim_quote: str
    confidence_score: float
    detection_method: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ReceptiveWatchlistInjector:
    """Injects stale entities into prompt context and extracts transition signals."""

    def build_watchlist(
        self,
        report: StalenessAuditReport,
        max_items: int = 15,
    ) -> List[WatchlistEntry]:
        entries: List[WatchlistEntry] = []
        seen_edges: set[str] = set()

        for rec in report.records:
            if rec.edge_id in seen_edges:
                continue
            seen_edges.add(rec.edge_id)

            aliases = [rec.from_node_name]
            parts = rec.from_node_name.split()
            if len(parts) > 1 and parts[0] not in aliases:
                aliases.append(parts[0])

            entries.append(
                WatchlistEntry(
                    watchlist_id=f"wl_{len(entries)+1:02d}",
                    edge_id=rec.edge_id,
                    entity_id=rec.from_node_id,
                    entity_name=rec.from_node_name,
                    edge_type=rec.edge_type,
                    target_id=rec.to_node_id,
                    target_name=rec.to_node_name,
                    last_attested_date=rec.source_date,
                    age_days=rec.age_days,
                    aliases=aliases,
                )
            )
            if len(entries) >= max_items:
                break
        return entries

    def format_prompt_context(self, entries: Sequence[WatchlistEntry]) -> str:
        if not entries:
            return ""
        lines = [
            "### TEMPORAL RECONCILIATION WATCHLIST (Proactive Update Seeking)",
            "The following roles have not been attested recently and may be outdated.",
            "If any participant mentions that an individual has rolled off, transitioned,",
            "or been replaced on their workstream, extract the transition explicitly:",
        ]
        for e in entries:
            lines.append(
                f"- [{e.watchlist_id}] {e.entity_name} ({e.edge_type}) -> {e.target_name} "
                f"[Last attested: {e.last_attested_date} ({e.age_days}d ago), Edge: {e.edge_id}]"
            )
        return "\n".join(lines)

    def extract_transition_signals(
        self,
        text: str,
        watchlist: Sequence[WatchlistEntry],
    ) -> List[TransitionSignal]:
        signals: List[TransitionSignal] = []
        if not text or not watchlist:
            return signals

        clean_text = " ".join(text.split())
        for entry in watchlist:
            signal = self._scan_text_for_entry(clean_text, entry)
            if signal:
                signals.append(signal)
        return signals

    def _has_target(self, text: str, entry: WatchlistEntry) -> bool:
        t_name = re.escape(entry.target_name)
        t_id = re.escape(entry.target_id)
        pat = rf"\b(?:{t_name}|{t_id})\b"
        return bool(re.search(pat, text, re.IGNORECASE))

    def _scan_text_for_entry(self, text: str, entry: WatchlistEntry) -> Optional[TransitionSignal]:
        if not self._has_target(text, entry):
            return None

        alias_pattern = "|".join(re.escape(a) for a in entry.aliases)

        # 1. Check for explicit replacement: e.g. "Bailey Morton has taken over for Abhijit Mahishi on FCDB"
        repl_verb_pat = "|".join(REPLACEMENT_VERBS)
        repl_re = re.compile(
            rf"(?P<new_lead>[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:{repl_verb_pat})\s+(?P<old_lead>{alias_pattern})\b",
            re.IGNORECASE,
        )
        repl_match = repl_re.search(text)
        if repl_match:
            new_lead = repl_match.group("new_lead").strip()
            quote = self._extract_quote(text, repl_match.start(), repl_match.end())
            return TransitionSignal(
                signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                candidate_type="REPLACEMENT",
                supersedes_edge_id=entry.edge_id,
                retracted_entity_id=entry.entity_id,
                retracted_entity_name=entry.entity_name,
                replacement_name=new_lead,
                workstream_node_id=entry.target_id,
                workstream_name=entry.target_name,
                verbatim_quote=quote,
                confidence_score=0.95,
                detection_method="EXPLICIT_REPLACEMENT",
            )

        # 2. Check for standalone roll-off: e.g. "Abhijit is no longer on the FCDB team"
        rolloff_verb_pat = "|".join(ROLLOFF_VERBS)
        rolloff_re = re.compile(
            rf"\b(?P<old_lead>{alias_pattern})\s+(?:{rolloff_verb_pat})\b",
            re.IGNORECASE,
        )
        rolloff_match = rolloff_re.search(text)
        if rolloff_match:
            quote = self._extract_quote(text, rolloff_match.start(), rolloff_match.end())
            return TransitionSignal(
                signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                candidate_type="RETRACTION",
                supersedes_edge_id=entry.edge_id,
                retracted_entity_id=entry.entity_id,
                retracted_entity_name=entry.entity_name,
                replacement_name=None,
                workstream_node_id=entry.target_id,
                workstream_name=entry.target_name,
                verbatim_quote=quote,
                confidence_score=0.90,
                detection_method="STANDALONE_ROLLOFF",
            )

        # 3. Check for former role cues: e.g. "former tech lead Abhijit Mahishi on FCDB"
        former_pat = "|".join(FORMER_ROLE_PREFIXES)
        former_re = re.compile(
            rf"\b(?:{former_pat})\s+(?P<old_lead>{alias_pattern})\b",
            re.IGNORECASE,
        )
        former_match = former_re.search(text)
        if former_match:
            quote = self._extract_quote(text, former_match.start(), former_match.end())
            return TransitionSignal(
                signal_id=f"sig_{uuid.uuid4().hex[:12]}",
                candidate_type="RETRACTION",
                supersedes_edge_id=entry.edge_id,
                retracted_entity_id=entry.entity_id,
                retracted_entity_name=entry.entity_name,
                replacement_name=None,
                workstream_node_id=entry.target_id,
                workstream_name=entry.target_name,
                verbatim_quote=quote,
                confidence_score=0.90,
                detection_method="FORMER_ROLE_CUE",
            )

        return None

    def _extract_quote(self, text: str, start_idx: int, end_idx: int, margin: int = 25) -> str:
        start = max(0, start_idx - margin)
        end = min(len(text), end_idx + margin)
        return text[start:end].strip()
