"""Constants and regex patterns for governed query routing."""
from __future__ import annotations

import re

from ..core import ROOT

ROUTING_POLICY_PATH = ROOT / "requirements" / "query-routing-policy.json"
MAX_QUESTION_LENGTH = 4000

DELIVERY_PROFILES = frozenset(
    {
        "internal_technical",
        "internal_executive",
        "client_technical",
        "client_executive",
        "commercial",
    }
)
ARTIFACT_TYPES = frozenset(
    {"answer", "decision_brief", "architecture_deck", "commercial_case", "evidence_export"}
)
NEE_QUERY_TYPES = frozenset(
    {
        "relationship_explanation",
        "cross_workstream_synthesis",
        "gap_risk_inconsistency",
        "opportunity_nba",
        "candidate_promotion_review",
    }
)
EVIDENCE_QUERY_TYPES = frozenset(
    {
        "exact_lookup",
        "current_state_corroboration",
        "project_status_current",
        "project_status_history",
    }
)

LEXICON_RULE_KEYS = frozenset(
    {
        "general",
        "general_decisive",
        "operational",
        "bounded",
        "graph",
        "material",
        "multi_record",
        "recommendation",
        "recommendation_explicit",
        "risk",
        "risk_conclusive",
        "candidate",
        "synthesis",
        "relationship",
        "high_materiality",
        "independent_material",
        "project_status",
        "status_history",
    }
)

_QUOTED_OR_CODE = re.compile(r"`[^`]*`|'[^']*'|\"[^\"]*\"")
_NEGATED_CLAUSE = re.compile(
    r"\b(?:do\s+not|don't|never|no|not)\b[^.;]{0,120}(?=[.;]|$)", re.I
)
_NEGATED_PREPOSITIONAL_CLAUSE = re.compile(
    r"\bwithout\s+(?:consulting|using|referencing)\b[^,.;]{0,120}(?:,|;)", re.I
)
_META_WRAPPER = re.compile(r"^\s*(?:please\s+)?handle\s+this\s+exact\s+request\s*:\s*", re.I)
_REQUEST_TEXT_ENVELOPE = re.compile(
    r"^\s*request\s+text\s*:\s*(?P<quote>[\"'])(?P<body>.+)(?P=quote)\s*$",
    re.I | re.S,
)
_META_ROUTING_TOKEN = re.compile(
    r"\b(?:ignore|disregard)\s+(?:the\s+)?(?:word|term|phrase)\s+(?:[\"'`])?[\w-]+(?:[\"'`])?",
    re.I,
)
_TOP_LEVEL_JOINER = re.compile(r"\b(?:and|then)\b", re.I)
_OUTER_INSTRUCTION_WRAPPERS = frozenset(("'", '"', "`"))
_STATUS_WRITE_REQUEST = re.compile(r"^\s*(?:update|change|record|set)\b", re.I)
_INDEPENDENT_GENERAL_TARGET = re.compile(
    r"\b(?:email|message|paragraph|notes|minutes|transcript|text|copy|essay|letter|memo)\b",
    re.I,
)
