"""
dual_pipeline/token_optimization/prompt_prefix_aligner.py

Agent 2 Prompt Prefix Aligner & Dynamic Reasoning Economizer.
Enforces static prompt prefix stability across invocations to achieve >= 90%
prompt cache hit rates, and dynamically budgets reasoning tokens based on
document complexity.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Standard cache boundary delimiter for frontier reasoning providers
CACHE_BOUNDARY_DELIMITER = "\n<!-- PROMPT_CACHE_BOUNDARY_ALIGNMENT -->\n"


class PromptPrefixAligner:
    """
    Normalizes and aligns static prompt prefixes to maximize cache hit rates,
    and dynamically budgets reasoning tokens based on input complexity.
    """

    @staticmethod
    def normalize_text_block(text: str) -> str:
        """Strips trailing whitespace and normalizes multiple newlines."""
        if not text:
            return ""
        lines = [line.rstrip() for line in text.strip().splitlines()]
        return "\n".join(lines)

    @classmethod
    def align_static_prefix(
        cls,
        system_prompt: str,
        static_blocks: Sequence[str],
        stage_tag: str = "l2_stage",
    ) -> str:
        """
        Assembles a deterministic, byte-identical static prefix.
        Normalizes internal spacing and aligns prompt cache boundaries.
        """
        cleaned_prompt = cls.normalize_text_block(system_prompt)
        cleaned_blocks: List[str] = []
        for block in static_blocks:
            norm = cls.normalize_text_block(block)
            if norm and norm not in cleaned_blocks:
                cleaned_blocks.append(norm)

        elements = [cleaned_prompt] + cleaned_blocks
        prefix = "\n\n".join(elements) + CACHE_BOUNDARY_DELIMITER
        return prefix

    @staticmethod
    def compute_prefix_hash(prefix: str) -> str:
        """Returns deterministic SHA-256 fingerprint of the static prefix."""
        return hashlib.sha256(prefix.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_cache_hit_rate(prefix_a: str, prefix_b: str) -> float:
        """
        Computes the prompt cache hit rate between two prompt prefixes.
        Measured as common leading prefix length over max prefix length.
        """
        if not prefix_a and not prefix_b:
            return 1.0
        if not prefix_a or not prefix_b:
            return 0.0

        min_len = min(len(prefix_a), len(prefix_b))
        max_len = max(len(prefix_a), len(prefix_b))

        match_len = 0
        for i in range(min_len):
            if prefix_a[i] == prefix_b[i]:
                match_len += 1
            else:
                break

        return round(match_len / max_len, 4)

    @classmethod
    def _calculate_custom_stage_budget(
        cls, stage: str, words: int, turns: int, complexity: float
    ) -> Dict[str, Any]:
        if stage in ("surgical_healer", "query_router"):
            budget, effort, tier = (
                (1024, "low", "SURGICAL_COMPACT")
                if words < 1500
                else (2048, "medium", "SURGICAL_COMPACT")
            )
        elif stage == "decomposed_judge":
            budget, effort, tier = (
                (2048, "medium", "JUDGE_BOUNDED")
                if words < 3000
                else (4096, "high", "JUDGE_BOUNDED")
            )
        elif words < 1500 and turns < 25:
            budget, effort, tier = 2048, "low", "MINIMAL"
        elif words <= 5000 and turns <= 80:
            budget, effort, tier = 4096, "medium", "STANDARD"
        else:
            budget, effort, tier = 8192, "high", "DEEP"
        return {
            "budget_tokens": budget,
            "reasoning_effort": effort,
            "complexity_score": round(complexity, 2),
            "tier": tier,
            "word_count": words,
        }

    @classmethod
    def calculate_reasoning_token_budget(
        cls,
        source_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        stage: str = "stage_03",
    ) -> Dict[str, Any]:
        """Dynamically budgets reasoning tokens based on transcript or document complexity."""
        meta = metadata or {}
        words = len(source_text.split())
        if "reasoning_budget_override" in meta:
            return {
                "budget_tokens": int(meta["reasoning_budget_override"]),
                "reasoning_effort": meta.get("reasoning_effort", "medium"),
                "complexity_score": 1.0,
                "tier": "CUSTOM_OVERRIDE",
                "word_count": words,
            }
        turns = len(re.findall(r"\*\*[^\*]+\*\*:", source_text))
        complexity = (words / 3000.0) + (turns / 50.0)
        if stage == "stage_03":
            return cls._calculate_stage03_budget(words, meta, complexity)
        return cls._calculate_custom_stage_budget(stage, words, turns, complexity)

    @classmethod
    def _calculate_stage03_budget(cls, words: int, meta: Dict[str, Any], complexity: float) -> Dict[str, Any]:
        from storage.credentials import CredentialManager
        effort = meta.get("stage03_reasoning_effort") or meta.get("reasoning_effort") or CredentialManager.get_env("REASONING_EFFORT") or "medium"
        if effort in ("high", "max"):
            budget, tier = 8192, "DEEP_STRUCTURED"
        elif effort == "medium":
            budget, tier = (4096 if words >= 1500 else 2048), "STANDARD_STRUCTURED"
        else:
            budget, tier = (2048 if words < 5000 else 4096), "FAST_STRUCTURED"
        return {
            "budget_tokens": budget,
            "reasoning_effort": effort,
            "complexity_score": round(complexity, 2),
            "tier": tier,
            "word_count": words,
        }

    @staticmethod
    def apply_reasoning_budget_to_provider(
        provider: Any,
        budget_info: Optional[Dict[str, Any]],
    ) -> None:
        """Applies calculated reasoning budget to the provider instance if supported."""
        if not budget_info or not isinstance(budget_info, dict):
            return

        if hasattr(provider, "set_reasoning_budget"):
            provider.set_reasoning_budget(budget_info.get("budget_tokens"))
        elif hasattr(provider, "reasoning_budget"):
            provider.reasoning_budget = budget_info.get("budget_tokens")

        if hasattr(provider, "set_reasoning_effort"):
            provider.set_reasoning_effort(budget_info.get("reasoning_effort"))
        elif hasattr(provider, "reasoning_effort"):
            provider.reasoning_effort = budget_info.get("reasoning_effort")

    @staticmethod
    def resolve_gemini_thinking_config(budget_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Builds Google Gemini thinkingConfig with bounded thinkingBudget and thinkingLevel."""
        if not budget_info or not isinstance(budget_info, dict):
            return {"thinkingLevel": "LOW", "thinkingBudget": 1024}
        effort = str(budget_info.get("reasoning_effort", "low")).upper()
        level = "HIGH" if effort in ("HIGH", "MAX", "XHIGH") else ("MEDIUM" if effort == "MEDIUM" else "LOW")
        budget = int(budget_info.get("budget_tokens", 2048))
        return {"thinkingLevel": level, "thinkingBudget": max(256, min(16384, budget))}
