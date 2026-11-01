"""
dual_pipeline/sandbox.py

Output sandboxing and prompt injection detection for LLM-generated graph extractions.
Neutralizes ANSI escapes, control characters, null bytes, and tags hostile instruction
injections as UNVERIFIED_TEXT_REQUIRES_HITL.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("dual_pipeline.sandbox")

ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07")
NULL_BYTE_PATTERN = re.compile(r"\x00")
CONTROL_CHAR_PATTERN = re.compile(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]")

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions|prompts|rules|commands)\b"),
    re.compile(r"(?i)\b(?:system\s+prompt|system\s+instruction|system\s+override)\s*[:=]"),
    re.compile(r"(?i)<\|(?:im_start|im_end|endoftext|system|user|assistant)\|>"),
    re.compile(r"(?i)\b(?:admin\s+mode|jailbreak|developer\s+mode)\s+(?:activated|enabled|on)\b"),
    re.compile(r"(?i)\b(?:you\s+are\s+now|act\s+as\s+an?)\s+(?:unrestricted|DAN|jailbroken|evil)\b"),
    re.compile(r"(?i)\b(?:do\s+not\s+follow|bypass)\s+(?:safety|governance|rules|filters)\b"),
]

MAX_ASSERTION_LENGTH = 8000
MAX_ENTITY_NAME_LENGTH = 256


def sanitize_text(text: Optional[str], max_length: int = MAX_ASSERTION_LENGTH) -> str:
    """Strips null bytes, ANSI escapes, and dangerous control characters, enforcing character bounds."""
    if not text:
        return ""
    # Strip null bytes
    cleaned = NULL_BYTE_PATTERN.sub("", text)
    # Strip ANSI escapes
    cleaned = ANSI_ESCAPE_PATTERN.sub("", cleaned)
    # Strip control chars except newline and tab
    cleaned = CONTROL_CHAR_PATTERN.sub("", cleaned)
    # Truncate
    return cleaned[:max_length]


def scan_for_prompt_injection(text: str) -> List[str]:
    """Scans text for prompt injection and instruction hijacking signatures."""
    if not text:
        return []
    matches = []
    for pat in PROMPT_INJECTION_PATTERNS:
        m = pat.search(text)
        if m:
            matches.append(m.group(0))
    return matches


def sandbox_assertion(assertion: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitizes text fields and flags prompt-injection patterns in assertions."""
    clean = dict(assertion)
    # Text fields to sanitize
    for k in ("claim_text", "assertion_text", "text", "description", "rationale", "statement"):
        if k in clean and isinstance(clean[k], str):
            clean[k] = sanitize_text(clean[k], max_length=MAX_ASSERTION_LENGTH)

    # Check for prompt injection in all string values
    injection_threats: List[str] = []
    for k, val in clean.items():
        if isinstance(val, str):
            threats = scan_for_prompt_injection(val)
            if threats:
                injection_threats.extend(threats)

    if injection_threats:
        logger.warning(
            "Prompt injection detected in assertion '%s': %s. Flagging UNVERIFIED_TEXT_REQUIRES_HITL.",
            clean.get("assertion_id", "unnamed"),
            injection_threats,
        )
        clean["status"] = "UNVERIFIED_TEXT_REQUIRES_HITL"
        clean["requires_hitl"] = True
        clean["threat_warning"] = f"Prompt injection markers detected: {injection_threats}"

    return clean


def sandbox_graph_mutations(mutations: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Sanitizes all graph mutation lists (entities, signals, assertions, decisions, risks)
    and tags risky content for human-in-the-loop verification.
    """
    clean_mutations = dict(mutations)
    detected_threats: List[str] = []

    # Sanitize entities
    if "entity_upserts" in clean_mutations:
        cleaned_entities = []
        for ent in clean_mutations["entity_upserts"]:
            if isinstance(ent, dict):
                e = dict(ent)
                if "name" in e and isinstance(e["name"], str):
                    e["name"] = sanitize_text(e["name"], max_length=MAX_ENTITY_NAME_LENGTH)
                    threats = scan_for_prompt_injection(e["name"])
                    if threats:
                        e["requires_hitl"] = True
                        e["status"] = "UNVERIFIED_TEXT_REQUIRES_HITL"
                        detected_threats.extend(threats)
                if "description" in e and isinstance(e["description"], str):
                    e["description"] = sanitize_text(e["description"])
                    threats = scan_for_prompt_injection(e["description"])
                    if threats:
                        e["requires_hitl"] = True
                        e["status"] = "UNVERIFIED_TEXT_REQUIRES_HITL"
                        detected_threats.extend(threats)
                cleaned_entities.append(e)
            else:
                cleaned_entities.append(ent)
        clean_mutations["entity_upserts"] = cleaned_entities

    # Sanitize assertion_updates
    for cat in ("assertion_updates", "assertions"):
        if cat in clean_mutations:
            clean_mutations[cat] = [
                sandbox_assertion(a) if isinstance(a, dict) else a
                for a in clean_mutations[cat]
            ]

    # Sanitize decision_updates, risk_updates, action_updates
    for cat in ("decision_updates", "risk_updates", "action_updates", "proposal_updates"):
        if cat in clean_mutations:
            cleaned_items = []
            for item in clean_mutations[cat]:
                if isinstance(item, dict):
                    it = dict(item)
                    for k in ("decision", "decision_question", "decision_text", "risk", "action", "proposal", "description"):
                        if k in it and isinstance(it[k], str):
                            it[k] = sanitize_text(it[k])
                            threats = scan_for_prompt_injection(it[k])
                            if threats:
                                it["requires_hitl"] = True
                                it["status"] = "UNVERIFIED_TEXT_REQUIRES_HITL"
                                detected_threats.extend(threats)
                    cleaned_items.append(it)
                else:
                    cleaned_items.append(item)
            clean_mutations[cat] = cleaned_items

    return clean_mutations, detected_threats
