"""
dual_pipeline/live_meeting_recorder_core/chat_attribution_collector.py

Captures, parses, and persists operator inline chat messages during live sessions.
Extracts speaker hints, quotes, active speaker declarations, and attendee absences
as structured InlineChatAttribution records for downstream post-recording HITL confirmation.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Regular expressions for quote matching
# Delimiters: :, =, -, –, —, or whitespace before quote
_SPEAKER_TO_QUOTE_RE = re.compile(
    r"""^(?P<speaker>[^\n:=–—"“'‘”’]+?)(?:\s*[:=–—-]\s*|\s+)(?:["“](?P<quote_dq>[^"”\r\n]+)["”]|['‘](?P<quote_sq>[^'’\r\n]+)['’])$""",
    re.IGNORECASE,
)

_QUOTE_TO_SPEAKER_RE = re.compile(
    r"""^(?:["“](?P<quote_dq>[^"”\r\n]+)["”]|['‘](?P<quote_sq>[^'’\r\n]+)['’])(?:\s*[:=–—-]\s*|\s+)(?P<speaker>[^\n:=–—"“'‘”’]+)$""",
    re.IGNORECASE,
)

_ISOLATED_QUOTE_RE = re.compile(
    r"""^[\s]*(?:["“](?P<quote_dq>[^"”\r\n]+)["”]|['‘](?P<quote_sq>[^'’\r\n]+)['’])[\s]*$""",
    re.IGNORECASE,
)

# e.g., Peter Ashley not on call / absent / not present
_ABSENCE_RE = re.compile(
    r"""^(?P<speaker>[A-Za-z0-9\s._'-]+?)\s+(?:not\s+on\s+call|absent|not\s+present|not\s+attending|invited\s*/\s*not\s+present)\b""",
    re.IGNORECASE,
)

# e.g., Marcus Vance speaking now / Aaron screen now / Aaron sharing APEX_TRANS Insights Hub
_ACTIVE_SPEAKER_RE = re.compile(
    r"""^(?P<speaker>[A-Za-z0-9._'-]+(?:\s+[A-Za-z0-9._'-]+){0,4})\s+(?:speaking\s+now|screen\s+now|sharing\b.*|live\s+data\s+showing|presenting\b.*)$""",
    re.IGNORECASE,
)


@dataclass
class InlineChatAttribution:
    raw_message: str
    attribution_type: str  # "quote_to_speaker" | "active_speaker" | "absence" | "role_hint"
    speaker_name: str
    quoted_text: Optional[str] = None
    session_elapsed_seconds: Optional[float] = None
    chat_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confirmation_status: str = "PENDING"  # "PENDING" | "CONFIRMED" | "CORRECTED" | "SKIPPED"
    confirmation_receipt_id: Optional[str] = None
    resolved_speaker: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InlineChatAttribution":
        return cls(**data)


class ChatAttributionCollector:
    """Parses and records inline chat messages containing speaker attribution hints."""

    def __init__(
        self,
        attendees: Optional[List[str]] = None,
        output_path: Optional[Path] = None,
    ) -> None:
        self.attendees = attendees or []
        self.output_path = Path(output_path) if output_path else None
        self.attributions: List[InlineChatAttribution] = []

    @staticmethod
    def _get_attendee_name(att: Any) -> str:
        if isinstance(att, dict):
            return str(att.get("name", ""))
        return str(att)

    def _is_known_attendee(self, candidate_name: str) -> bool:
        """Check if candidate name matches an attendee by full name, first name, or nickname alias."""
        clean = candidate_name.strip().lower()
        if not clean or not self.attendees:
            return False
        from .attendees import _matches_attendee

        for att in self.attendees:
            att_name = self._get_attendee_name(att).strip().lower()
            if att_name and _matches_attendee(clean, att_name):
                return True
        return False

    def _resolve_roster_name(self, candidate_name: str) -> str:
        """Match candidate name to roster attendees case-insensitively, handling first names, aliases, or typos."""
        clean = candidate_name.strip()
        if not clean:
            return candidate_name
        from .attendees import resolve_roster_name

        resolved = resolve_roster_name(clean, self.attendees)
        if resolved != clean:
            return resolved
        clean_lower = clean.lower()
        for att in self.attendees:
            att_name = self._get_attendee_name(att)
            if clean_lower in att_name.lower():
                return att_name
        return clean

    def _parse_absence(self, text: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        m_abs = _ABSENCE_RE.match(text)
        if not m_abs:
            return None
        spk_raw = m_abs.group("speaker").strip()
        if self._is_known_attendee(spk_raw) or (spk_raw and spk_raw[0].isupper()):
            spk = self._resolve_roster_name(spk_raw)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="absence",
                speaker_name=spk,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": spk_raw},
            )
        return None

    def _parse_assignment(self, text: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        if "=" not in text:
            return None
        left, right = text.split("=", 1)
        left_raw, right_raw = left.strip(), right.strip()
        left_clean, right_clean = left_raw.strip("\"'“‘”’ "), right_raw.strip("\"'“‘”’ ")
        if not left_clean or not right_clean:
            return None

        left_is_att = self._is_known_attendee(left_clean)
        right_is_att = self._is_known_attendee(right_clean)

        if left_is_att and not right_is_att:
            spk = self._resolve_roster_name(left_clean)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="quote_to_speaker",
                speaker_name=spk,
                quoted_text=right_clean,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": left_clean},
            )
        if right_is_att and not left_is_att:
            spk = self._resolve_roster_name(right_clean)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="quote_to_speaker",
                speaker_name=spk,
                quoted_text=left_clean,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": right_clean},
            )
        if left_raw.startswith(('"', "'", "“", "‘")) and not right_raw.startswith(('"', "'", "“", "‘")):
            spk = self._resolve_roster_name(right_clean)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="quote_to_speaker",
                speaker_name=spk,
                quoted_text=left_clean,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": right_clean},
            )
        if right_raw.startswith(('"', "'", "“", "‘")) and not left_raw.startswith(('"', "'", "“", "‘")):
            spk = self._resolve_roster_name(left_clean)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="quote_to_speaker",
                speaker_name=spk,
                quoted_text=right_clean,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": left_clean},
            )
        return None

    def _parse_speaker_to_quote(self, text: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        m = _SPEAKER_TO_QUOTE_RE.match(text)
        if not m:
            return None
        spk_raw = m.group("speaker").strip()
        quote = (m.group("quote_dq") or m.group("quote_sq") or "").strip()
        if not quote:
            return None
        if self._is_known_attendee(spk_raw) or (spk_raw and spk_raw[0].isupper() and len(spk_raw.split()) <= 4):
            spk = self._resolve_roster_name(spk_raw)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="quote_to_speaker",
                speaker_name=spk,
                quoted_text=quote,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": spk_raw},
            )
        return None

    def _parse_quote_to_speaker(self, text: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        m = _QUOTE_TO_SPEAKER_RE.match(text)
        if not m:
            return None
        quote = (m.group("quote_dq") or m.group("quote_sq") or "").strip()
        spk_raw = m.group("speaker").strip()
        if not quote:
            return None
        if self._is_known_attendee(spk_raw) or (spk_raw and spk_raw[0].isupper() and len(spk_raw.split()) <= 4):
            spk = self._resolve_roster_name(spk_raw)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="quote_to_speaker",
                speaker_name=spk,
                quoted_text=quote,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": spk_raw},
            )
        return None

    def _parse_role_hint(self, text: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        m_role = re.match(
            r"^\s*(?P<speaker>[A-Z][a-zA-Z\.\-\s]{1,35}?)\s*(?:-\s*|\(|is\s+(?:a\s+|an\s+|the\s+)?|:\s*(?:role|title)\s*[:=]?\s*)"
            r"(?P<role>(?:Managing\s+Director|Senior\s+Director|Executive\s+Director|Director|Partner|Principal(?:\s+Architect|\s+Consultant)?|Vice\s+President|VP|Senior\s+Consultant|Consultant|Project\s+Manager|Product\s+Manager|Tech\s+Lead|Engineering\s+Manager|Enterprise\s+Architect|Architect|Lead)[^\"'“‘”’\)]*?)\s*\)?\s*$",
            text,
            re.IGNORECASE,
        )
        if not m_role:
            return None
        spk_raw = m_role.group("speaker").strip()
        role_raw = m_role.group("role").strip().rstrip(".,;)")
        if not spk_raw or not role_raw:
            return None
        org_val = None
        if "," in role_raw:
            parts = [p.strip() for p in role_raw.split(",", 1)]
            role_clean, org_val = parts[0], parts[1]
        elif " at " in role_raw.lower():
            idx = role_raw.lower().find(" at ")
            role_clean, org_val = role_raw[:idx].strip(), role_raw[idx + 4:].strip()
        else:
            role_clean = role_raw
        spk = self._resolve_roster_name(spk_raw)
        meta: Dict[str, Any] = {"raw_speaker": spk_raw, "role": role_clean}
        if org_val:
            meta["org"] = org_val
        return InlineChatAttribution(
            raw_message=text,
            attribution_type="role_hint",
            speaker_name=spk,
            quoted_text=None,
            session_elapsed_seconds=elapsed_seconds,
            metadata=meta,
        )

    def _parse_active_speaker(self, text: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        m_act = _ACTIVE_SPEAKER_RE.match(text)
        if not m_act:
            return None
        spk_raw = m_act.group("speaker").strip()
        if self._is_known_attendee(spk_raw) or (spk_raw and spk_raw[0].isupper() and len(spk_raw.split()) <= 3):
            spk = self._resolve_roster_name(spk_raw)
            return InlineChatAttribution(
                raw_message=text,
                attribution_type="active_speaker",
                speaker_name=spk,
                session_elapsed_seconds=elapsed_seconds,
                metadata={"raw_speaker": spk_raw},
            )
        return None

    def _parse_single_clause(self, clause: str, elapsed_seconds: Optional[float]) -> Optional[InlineChatAttribution]:
        text = clause.strip()
        if not text:
            return None

        for parse_fn in (
            self._parse_absence,
            self._parse_speaker_to_quote,
            self._parse_quote_to_speaker,
            self._parse_role_hint,
            self._parse_assignment,
            self._parse_active_speaker,
        ):
            res = parse_fn(text, elapsed_seconds)
            if res:
                return res

        return None

    def parse_message(
        self,
        raw_message: str,
        elapsed_seconds: Optional[float] = None,
    ) -> List[InlineChatAttribution]:
        """Parses a raw operator message, supporting multiple sub-clauses separated by ';' or newlines."""
        if not raw_message or not raw_message.strip():
            return []

        results: List[InlineChatAttribution] = []
        # Split on semicolons or newlines
        clauses = re.split(r"[;\n]+", raw_message)
        for clause in clauses:
            clause_clean = clause.strip()
            if not clause_clean:
                continue

            # Check if this clause is an isolated continuation quote following a parsed attribution
            m_iso = _ISOLATED_QUOTE_RE.match(clause_clean)
            if m_iso and results:
                cont_quote = (m_iso.group("quote_dq") or m_iso.group("quote_sq") or "").strip()
                if cont_quote:
                    results[-1].metadata.setdefault("continuation_quotes", []).append(cont_quote)
                    results[-1].raw_message += f"\n{clause_clean}"
                    continue

            parsed = self._parse_single_clause(clause_clean, elapsed_seconds)
            if parsed:
                results.append(parsed)

        return results

    def _is_duplicate(self, attr: InlineChatAttribution) -> bool:
        norm_speaker = attr.speaker_name.strip().lower()
        norm_quote = (attr.quoted_text or "").strip().lower()
        norm_type = attr.attribution_type
        for existing in self.attributions:
            ex_speaker = existing.speaker_name.strip().lower()
            ex_quote = (existing.quoted_text or "").strip().lower()
            ex_type = existing.attribution_type
            if norm_type == ex_type and norm_speaker == ex_speaker and norm_quote == ex_quote:
                return True
        return False

    def record_attribution(
        self,
        raw_message: str,
        elapsed_seconds: Optional[float] = None,
    ) -> List[InlineChatAttribution]:
        """Parses raw chat message and appends matched non-duplicate attributions to store."""
        records = self.parse_message(raw_message, elapsed_seconds)
        added: List[InlineChatAttribution] = []
        for r in records:
            if not self._is_duplicate(r):
                self.attributions.append(r)
                added.append(r)
        if added and self.output_path:
            try:
                self.save()
            except Exception as exc:
                logger.warning("Failed to auto-save inline chat attributions: %s", exc)
        return added

    def save(self, path: Optional[Path] = None) -> Path:
        """Serializes attributions to JSON."""
        target = Path(path) if path else self.output_path
        if not target:
            raise ValueError("No output path specified for saving inline chat attributions.")
        target.parent.mkdir(parents=True, exist_ok=True)
        data = [a.to_dict() for a in self.attributions]
        target.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return target

    def load(self, path: Optional[Path] = None) -> List[InlineChatAttribution]:
        """Loads attributions from JSON."""
        target = Path(path) if path else self.output_path
        if not target or not target.exists():
            return []
        data = json.loads(target.read_text(encoding="utf-8"))
        self.attributions = [InlineChatAttribution.from_dict(d) for d in data]
        return self.attributions

    @property
    def pending_attributions(self) -> List[InlineChatAttribution]:
        return [a for a in self.attributions if a.confirmation_status == "PENDING"]

    @property
    def confirmed_attributions(self) -> List[InlineChatAttribution]:
        return [a for a in self.attributions if a.confirmation_status == "CONFIRMED"]
