#!/usr/bin/env python3
"""
Pipeline B: Local Pre-Egress Sanitizer (Law 1 Anti-Leak Enforcer)
Target: Apple MacBook Pro 16" (M5 Pro, 48GB RAM, Apple Metal)

Non-Negotiable Engineering Law 1:
Never send unscrubbed raw transcripts to OpenAI gpt-4o. Run an on-device
sanitization pass that scrubs internal political dynamics, rate discussions,
personal positioning, and credential tokens before cloud dispatch.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Pattern, Tuple

logger = logging.getLogger("pipeline_b_extraction.pre_egress_sanitizer")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [PRE-EGRESS-SANITIZER] %(message)s"
    ))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


@dataclass
class SanitizationResult:
    """Outcome of pre-egress text sanitization."""
    sanitized_text: str
    redacted_signals: List[str] = field(default_factory=list)
    has_redactions: bool = False
    redaction_count: int = 0

    @property
    def is_clean(self) -> bool:
        return not self.has_redactions


class LocalEgressSanitizer:
    """
    On-device deterministic regex and token analyzer enforcing the Career
    Leverage Firewall and credential quarantine before cloud API egress.
    """

    PATTERNS: List[Tuple[Pattern[str], str, str]] = [
        # 1. High-Priority Credential Secrets
        (
            re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
            "[REDACTED_PRIVATE_KEY]",
            "private_key",
        ),
        (
            re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}", re.IGNORECASE),
            "[REDACTED_AWS_ACCESS_KEY]",
            "aws_access_key",
        ),
        (
            re.compile(r"sk-[a-zA-Z0-9_\-]{20,}", re.IGNORECASE),
            "[REDACTED_API_KEY]",
            "openai_api_key",
        ),
        (
            re.compile(r"gh[pousr][_\-][a-zA-Z0-9]{20,}", re.IGNORECASE),
            "[REDACTED_GITHUB_TOKEN]",
            "github_token",
        ),
        (
            re.compile(r"AIzaSy[0-9A-Za-z_\-]{33}", re.IGNORECASE),
            "[REDACTED_GOOGLE_API_KEY]",
            "google_api_key",
        ),
        (
            re.compile(r"hf_[a-zA-Z0-9]{34,}", re.IGNORECASE),
            "[REDACTED_HUGGINGFACE_TOKEN]",
            "huggingface_token",
        ),
        (
            re.compile(r"xox[baprs]-[0-9a-zA-Z]{10,48}", re.IGNORECASE),
            "[REDACTED_SLACK_TOKEN]",
            "slack_token",
        ),
        (
            re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{25,}", re.IGNORECASE),
            "Bearer [REDACTED_AUTH_TOKEN]",
            "auth_bearer_token",
        ),

        # 2. Executive Positioning, Career Leverage & Compensation Trajectory ($1.2M)
        (
            re.compile(r"\$1\.2M(\s+trajectory|\s+target|\s+positioning)?", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "career_trajectory_1_2m",
        ),
        (
            re.compile(r"\b(?:personal\s+)?negotiation\s+leverage(?:\s+against\s+leadership)?\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "negotiation_leverage",
        ),
        (
            re.compile(r"\b(?:executive\s+positioning|career\s+advancement)(?:\s+vector)?\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "career_leverage_vector",
        ),
        (
            re.compile(r"\bpartner\s+(?:track\s+)?compensation(?:\s+trajectories)?\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "partner_compensation",
        ),
        (
            re.compile(r"\b(?:internal|executive|shadow|confidential)\s+political\s+(?:power\s+)?map\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "political_power_map",
        ),
        (
            re.compile(r"\b(?:confidential\s+executive|private\s+career|executive\s+career(?:\s+positioning)?)\s+notes\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "executive_career_notes",
        ),
        (
            re.compile(r"\bstealth_score(?:\s*[:=]\s*\d+(?:\.\d+)?)?\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "stealth_score",
        ),
        (
            re.compile(r"\bINTERNAL_ADVISORY(?:\s+enclave)?\b", re.IGNORECASE),
            "[REDACTED_CAREER_LEVERAGE]",
            "internal_advisory_enclave",
        ),

        # 3. Commercial Rate Cards & SOW Financials
        (
            re.compile(r"\$\d{2,4}\s*/\s*(hr|hour|day)", re.IGNORECASE),
            "[REDACTED_COMMERCIAL_TERM]",
            "hourly_billing_rate",
        ),
        (
            re.compile(r"\b(blended\s+rate|bill\s+rate|cost\s+rate|rate\s+card)\s*[:=]?\s*\$?\d+", re.IGNORECASE),
            "[REDACTED_COMMERCIAL_TERM]",
            "rate_card_disclosure",
        ),

        # 4. Delimiter Hijacking & Prompt Injection Traps
        (
            re.compile(r"</?(?:raw_transcript|system|injection|instruction|prompt)>", re.IGNORECASE),
            "[REDACTED_TAG_BREAKOUT]",
            "raw_transcript_breakout",
        ),
        (
            re.compile(r"\[SYSTEM\s+OVERRIDE\]|SYSTEM\s+OVERRIDE:", re.IGNORECASE),
            "[REDACTED_SYSTEM_OVERRIDE]",
            "system_override_trap",
        ),
        (
            re.compile(r"\[INSTRUCTION\s+INJECTION\]|INSTRUCTION\s+INJECTION:", re.IGNORECASE),
            "[REDACTED_INSTRUCTION_INJECTION]",
            "instruction_injection_trap",
        ),
        (
            re.compile(r"\b(?:ignore\s+(?:prior|previous|all)\s+(?:rules|instructions|directives)|bypass\s+(?:rule|firewall|guardrail))\b", re.IGNORECASE),
            "[REDACTED_PROMPT_INJECTION]",
            "prompt_injection_directive",
        ),
        (
            re.compile(r"\b(?:you\s+are\s+now\s+in\s+)?(?:dan|developer|jailbreak)\s+mode\b", re.IGNORECASE),
            "[REDACTED_JAILBREAK_TRAP]",
            "jailbreak_mode_trap",
        ),
    ]

    def __init__(self, extra_patterns: List[Tuple[str, str, str]] | None = None):
        self._patterns = list(self.PATTERNS)
        if extra_patterns:
            for p_str, repl, label in extra_patterns:
                self._patterns.append((re.compile(p_str, re.IGNORECASE), repl, label))

    @staticmethod
    def _extract_match_signal(m: Any, label: str) -> str:
        val = m if isinstance(m, str) else (m[0] if m else "")
        return f"{label}:{val.strip()}" if val else label

    def _apply_pattern(
        self,
        sanitized: str,
        regex: Pattern[str],
        replacement: str,
        label: str,
    ) -> Tuple[str, List[str]]:
        matches = regex.findall(sanitized)
        if not matches:
            return sanitized, []
        signals = [self._extract_match_signal(m, label) for m in matches]
        return regex.sub(replacement, sanitized), signals

    def sanitize(self, raw_text: str) -> SanitizationResult:
        """
        Sanitizes text by stripping all confidential career leverage, credentials,
        and injection payloads. Returns immutable SanitizationResult.
        """
        if not raw_text:
            return SanitizationResult(sanitized_text="")

        sanitized = raw_text
        redacted_signals: List[str] = []

        for regex, replacement, label in self._patterns:
            sanitized, signals = self._apply_pattern(sanitized, regex, replacement, label)
            redacted_signals.extend(signals)

        count = len(redacted_signals)
        if count > 0:
            logger.warning(
                f"Sanitized {count} sensitive signals before cloud egress: "
                f"{[s.split(':')[0] for s in redacted_signals[:5]]}"
            )

        return SanitizationResult(
            sanitized_text=sanitized,
            redacted_signals=redacted_signals,
            has_redactions=count > 0,
            redaction_count=count,
        )

    def is_clean(self, raw_text: str) -> bool:
        """Returns True if the text contains zero redactable signals."""
        return self.sanitize(raw_text).is_clean


default_sanitizer = LocalEgressSanitizer()


def sanitize_for_cloud_egress(text: str) -> Tuple[str, List[str]]:
    """Helper functional interface returning (sanitized_text, redacted_signals)."""
    res = default_sanitizer.sanitize(text)
    return res.sanitized_text, res.redacted_signals
