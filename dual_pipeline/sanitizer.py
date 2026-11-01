import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(r"-----BEGIN (?:[A-Z\s]+)?PRIVATE KEY-----"),
]

BILLING_RATE_PATTERNS = [
    re.compile(r"\$\d{2,3}/hr", re.IGNORECASE),
    re.compile(r"hourly rate", re.IGNORECASE),
]

ADVISORY_PATTERNS = [
    re.compile(r"\bINTERNAL_ADVISORY\b", re.IGNORECASE),
    re.compile(r"\bis_quarantined\b", re.IGNORECASE),
    re.compile(r"\bcareer leverage\b", re.IGNORECASE),
    re.compile(r"\bstealth_score\b", re.IGNORECASE),
]

class EgressSanitizationError(Exception):
    def __init__(self, message: str = ""):
        super().__init__("[REDACTED SANITIZATION ERROR]")

class LocalPreEgressSanitizer:
    @classmethod
    def _iter_text_fragments(cls, value: Any) -> List[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            out: List[str] = []
            for item in value:
                out.extend(cls._iter_text_fragments(item))
            return out
        if isinstance(value, dict):
            out = []
            for k, v in value.items():
                if k in ("role", "type", "id", "index"):
                    continue
                out.extend(cls._iter_text_fragments(v))
            return out
        return []

    @staticmethod
    def _assert_no_pattern_match(content: str, patterns: List[re.Pattern], label: str) -> None:
        for p in patterns:
            if p.search(content):
                logger.error("Audit Event: %s detected during egress sanitization", label)
                raise EgressSanitizationError(label)

    @classmethod
    def sanitize_text(cls, content: Any, allow_internal_operator: bool = False) -> Any:
        if not content:
            return content
        if not isinstance(content, str):
            for fragment in cls._iter_text_fragments(content):
                cls.sanitize_text(fragment, allow_internal_operator=allow_internal_operator)
            return content
        if not allow_internal_operator:
            from .live_meeting_recorder_core.copilot_retriever import has_c19_marker
            if has_c19_marker(content):
                logger.error("Audit Event: C-19 marker detected during egress sanitization")
                raise EgressSanitizationError("C-19 marker")
            cls._assert_no_pattern_match(content, ADVISORY_PATTERNS, "INTERNAL_ADVISORY pattern")

        cls._assert_no_pattern_match(content, SECRET_PATTERNS, "Secret pattern")
        cls._assert_no_pattern_match(content, BILLING_RATE_PATTERNS, "Billing rate pattern")
        return content

    @classmethod
    def scrub_text(cls, content: str, replacement: str = "[REDACTED_C19]") -> str:
        """Scrubs sensitive C-19, secret, billing rate, and internal advisory patterns before persistence."""
        if not content or not isinstance(content, str):
            return content
        scrubbed = content
        from .live_meeting_recorder_core.c19_firewall import C19_FORBIDDEN_MARKERS, C19_FORBIDDEN_PATTERNS
        for pat in (*C19_FORBIDDEN_PATTERNS, *ADVISORY_PATTERNS, *SECRET_PATTERNS, *BILLING_RATE_PATTERNS):
            scrubbed = pat.sub(replacement, scrubbed)
        for marker in sorted(C19_FORBIDDEN_MARKERS, key=len, reverse=True):
            scrubbed = re.sub(re.escape(marker), replacement, scrubbed, flags=re.IGNORECASE)
        return scrubbed

    @classmethod
    def sanitize_messages(cls, messages: List[Dict[str, Any]], allow_internal_operator: bool = False) -> List[Dict[str, Any]]:
        for msg in messages:
            if isinstance(msg, dict):
                for fragment in cls._iter_text_fragments(msg):
                    cls.sanitize_text(fragment, allow_internal_operator=allow_internal_operator)
            else:
                cls.sanitize_text(msg, allow_internal_operator=allow_internal_operator)
        return messages
