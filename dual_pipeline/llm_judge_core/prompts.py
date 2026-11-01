"""Canonical judge prompts and evaluation rubrics."""
from __future__ import annotations

from dual_pipeline.prompts import CANONICAL_PROMPT_REGISTRY

HARDENED_SVP_JUDGE_PROMPT = CANONICAL_PROMPT_REGISTRY.get_prompt("HARDENED_SVP_JUDGE")
EXECUTIVE_EXOSKELETON_REVIEW_PROMPT = CANONICAL_PROMPT_REGISTRY.get_prompt("EXECUTIVE_EXOSKELETON_REVIEW")

