"""
dual_pipeline/decision_engine_core/constants.py

Shared regex tokens, vocabulary sets, and sanitation primitives for decision intelligence.
"""

from __future__ import annotations

import re
from typing import Sequence

from dual_pipeline._validators import require_text

TOKEN = re.compile(r"[a-z0-9][a-z0-9_-]+", re.I)
CRITERIA_CLAUSE = re.compile(
    r"\b(?:given|considering|based\s+on|using)\s+(?P<criteria>[^?]+)", re.I
)
SPLIT_LIST = re.compile(r"\s*(?:,|\band\b)\s*", re.I)
RISK_TERMS = frozenset(
    {"risk", "gap", "failure", "blocked", "conflict", "issue", "delay", "threat"}
)
CONSTRAINT_TERMS = frozenset(
    {"constraint", "dependency", "depends", "requirement", "budget", "cost", "capacity"}
)
FORBIDDEN_CRITERIA_PATTERNS = re.compile(
    r"[\r\n;{}<>]|--|/\*|\*/|\b(?:system|prompt|ignore|override|execute|eval)\b", re.I
)
PROMOTION_OPTION_TERMS = frozenset({"promote", "approve", "admit"})
RETENTION_OPTION_TERMS = frozenset({"keep", "retain", "defer", "hold"})
EVALUATIVE_COUNTERFACTUAL_PATTERN = re.compile(
    r"\b(?:what\s+(?:might|will|would|could)\s+work|what\s+(?:will|would|might)\s+not\s+work|what\s+won't\s+work|"
    r"pros\s+and\s+cons|what[- ]if|counterfactual|viability|feasibility|balanced\s+view|honest\s+analysis|"
    r"trade[- ]?offs?\s+of|pod\s+model|local\s+pods?)\b",
    re.I,
)
DEFAULT_EVALUATIVE_CRITERIA: tuple[str, ...] = (
    "operating_model_fit",
    "talent_and_screening_feasibility",
    "commercial_and_margin_viability",
    "governance_and_execution_risk",
)
DEFAULT_EVALUATIVE_OPTIONS: tuple[str, ...] = (
    "Viable / Supported Elements",
    "Blocked / Non-Viable Elements",
)


def is_evaluative_counterfactual_question(question: str) -> bool:
    return bool(EVALUATIVE_COUNTERFACTUAL_PATTERN.search(question))


def text_field(value: object, *, field: str) -> str:
    return require_text(value, field=field)


def unique_text_list(values: Sequence[str] | None, *, field: str) -> list[str]:
    if values is None:
        return []
    result = [text_field(item, field=field) for item in values]
    return list(dict.fromkeys(result))


def extract_tokens(value: object) -> set[str]:
    text = str(value).casefold()
    text = re.sub(r"\b(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)%", r"\1pct \2pct", text)
    text = re.sub(r"\b(\d+(?:\.\d+)?)%", r"\1pct", text)
    text = re.sub(r"\b(\d+):(\d+)\b", r"\1to\2", text)
    return {match.group(0).casefold() for match in TOKEN.finditer(text)}


def sanitize_criterion(value: str) -> str | None:
    cleaned = value.strip(" -\"'`.,;:!?\t\r\n")
    if not cleaned or len(cleaned) < 2 or len(cleaned) > 80:
        return None
    if FORBIDDEN_CRITERIA_PATTERNS.search(cleaned):
        return None
    if not re.search(r"[a-zA-Z0-9]", cleaned):
        return None
    return cleaned


def extract_criteria(question: str) -> list[str]:
    match = CRITERIA_CLAUSE.search(question)
    if match is None:
        if is_evaluative_counterfactual_question(question):
            return list(DEFAULT_EVALUATIVE_CRITERIA)
        if re.search(
            r"\b(?:review\s+gaps|incorporate\b.*\bresources?|should\s+we\s+add\b.*\bto\b|staffing|intro(?:duce)?\s+email)\b",
            question,
            re.I,
        ):
            return ["role_fit", "org_optics_alignment", "current_sprint_evidence_grounding"]
        return []
    raw_criteria = match.group("criteria")
    if FORBIDDEN_CRITERIA_PATTERNS.search(raw_criteria):
        return []
    values: list[str] = []
    for value in SPLIT_LIST.split(raw_criteria):
        sanitized = sanitize_criterion(value)
        if sanitized:
            values.append(sanitized)
    return list(dict.fromkeys(values))
