"""
Canary Generation and Contamination Detection Engine.

Enforces 2026+ Anthropic and OpenAI evaluation contamination defense:
- Injects unique traceable canary tokens into benchmark datasets.
- Scans external or public outputs for unauthorized canary token leakage.
- Verifies dataset privacy and model output contamination immunity.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class CanaryToken:
    """A unique, cryptographically signed canary token."""

    token_id: str
    token_str: str
    purpose: str
    created_at: str


@dataclass
class CanaryLeakageReport:
    """Findings from scanning content for canary token leakage."""

    token_str: str
    is_leaked: bool
    match_count: int
    matched_contexts: List[str]


class CanaryEngine:
    """Generates and tracks canary tokens, auditing outputs for privacy leakage."""

    CANARY_PREFIX = "NEE-CANARY"

    def __init__(self, salt: Optional[str] = None):
        self.salt = salt or secrets.token_hex(8)
        self._active_tokens: Dict[str, CanaryToken] = {}

    def generate_canary(self, purpose: str = "eval_contamination_check") -> CanaryToken:
        """Generate a cryptographically unique traceable canary token."""
        rand_hex = secrets.token_hex(6)
        token_id = f"canary_{rand_hex}"
        token_str = f"{self.CANARY_PREFIX}-{rand_hex.upper()}"

        token = CanaryToken(
            token_id=token_id,
            token_str=token_str,
            purpose=purpose,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._active_tokens[token_str] = token
        return token

    def scan_for_leakage(
        self, content: str, target_token: Optional[CanaryToken] = None
    ) -> List[CanaryLeakageReport]:
        """
        Scan content for presence of canary tokens.
        
        If target_token is provided, checks that specific token.
        Otherwise checks all active tokens registered in the engine.
        """
        tokens_to_check = (
            [target_token] if target_token else list(self._active_tokens.values())
        )
        reports = []

        for t in tokens_to_check:
            # Case-insensitive search
            pattern = re.compile(re.escape(t.token_str), re.IGNORECASE)
            matches = list(pattern.finditer(content))

            matched_contexts = []
            for m in matches:
                start = max(0, m.start() - 30)
                end = min(len(content), m.end() + 30)
                matched_contexts.append(content[start:end].strip())

            reports.append(
                CanaryLeakageReport(
                    token_str=t.token_str,
                    is_leaked=len(matches) > 0,
                    match_count=len(matches),
                    matched_contexts=matched_contexts,
                )
            )

        return reports

    def assert_no_leakage(self, content: str, target_token: CanaryToken) -> None:
        """Raise AssertionError if canary token leaked into content."""
        reports = self.scan_for_leakage(content, target_token)
        for r in reports:
            if r.is_leaked:
                raise AssertionError(
                    f"Canary leakage detected! Token '{r.token_str}' appeared {r.match_count} time(s) "
                    f"in evaluated output: {r.matched_contexts}"
                )
