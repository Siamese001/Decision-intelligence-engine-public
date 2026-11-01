"""Constants, routing matrices, and regex patterns for L2 policy."""
from __future__ import annotations

import re
from types import MappingProxyType
from typing import Mapping

from ..core import ROOT
from .models import L2Policy, QueryTypePolicy

L2_POLICY_PATH = ROOT / "requirements" / "l2-policy.json"

QUERY_TYPE_POLICY_MATRIX: Mapping[str, QueryTypePolicy] = MappingProxyType(
    {
        "exact_lookup": QueryTypePolicy(
            L2Policy.FORBIDDEN,
            "Exact governed records are deterministically retrievable and directly citable.",
            None,
        ),
        "current_state_corroboration": QueryTypePolicy(
            L2Policy.FORBIDDEN,
            "Bounded current-state corroboration is a provider-free graph evidence utility.",
            None,
        ),
        "project_status_current": QueryTypePolicy(
            L2Policy.FORBIDDEN,
            "Current project status is dated, citation-only source evidence and is provider-free.",
            None,
        ),
        "project_status_history": QueryTypePolicy(
            L2Policy.FORBIDDEN,
            "Project-status history is dated, citation-only source evidence and is provider-free.",
            None,
        ),
        "relationship_explanation": QueryTypePolicy(
            L2Policy.REQUIRED,
            "Narrative explanation of governed graph relationships requires semantic synthesis.",
            "openai",
        ),
        "cross_workstream_synthesis": QueryTypePolicy(
            L2Policy.REQUIRED,
            "Cross-workstream conclusions require semantic synthesis.",
            "openai",
        ),
        "gap_risk_inconsistency": QueryTypePolicy(
            L2Policy.REQUIRED,
            "Gap, risk, conflict, and contradiction analysis requires semantic reasoning.",
            "openai",
        ),
        "opportunity_nba": QueryTypePolicy(
            L2Policy.REQUIRED,
            "Recommendations and prioritization require semantic reasoning.",
            "openai",
        ),
        "candidate_promotion_review": QueryTypePolicy(
            L2Policy.REQUIRED,
            "Candidate review requires proposal-only reasoning under HITL governance.",
            "openai",
        ),
    }
)

RETRIEVAL_MODE_LANES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "factual": ("approved",),
        "guidance": ("approved",),
        "candidate_review": ("approved", "candidate"),
        "strategic": ("approved", "candidate", "external_public"),
        "status_snapshot_current": ("approved", "candidate"),
        "status_snapshot_history": ("approved", "candidate"),
    }
)

_EXPLANATION = re.compile(
    r"\b(?:why|explain|implications?|consequences?|assess(?:ment|ed|ing)?|"
    r"analy(?:ze|zed|zing)|evaluat(?:e|ed|ing)|summari[sz](?:e|ed|ing))\b|"
    r"\b(?:give|provide)\s+(?:an\s+)?overview\b|\bwhat\s+does\b.+\bmean\b",
    re.I,
)
_HOW_EXPLANATION = re.compile(r"\bhow\b(?!\s+(?:many|much)\b)", re.I)
_COMPARISON = re.compile(
    r"\b(?:compare|comparison|versus|vs\.?|difference|across|better|best|"
    r"prefer(?:red|able)?|trade[ -]?offs?)\b|\bpros?\s+and\s+cons?\b",
    re.I,
)
_RECOMMENDATION = re.compile(
    r"\b(?:recommend|recommendation|should|next best|next action)\w*", re.I
)
_PRIORITIZATION = re.compile(
    r"\b(?:prioriti[sz]|priorit(?:y|ies)|rank|top|strongest|highest|most important)\w*",
    re.I,
)
_DEFENSE = re.compile(r"\b(?:defend|defense|rationale|justify|right areas?)\b", re.I)
_RISK = re.compile(r"\b(?:risk|gap|conflict|contradiction|inconsisten)\w*", re.I)
_CANDIDATE = re.compile(
    r"\b(?:promot(?:e|ion)|candidate\s+(?:review|assessment)|"
    r"review\s+(?:the\s+)?candidate)\b",
    re.I,
)
_BOUNDED_LOOKUP = re.compile(
    r"^\s*(?:show|get|find|look\s*up|lookup|retrieve|list|return)\b|"
    r"\bhow\s+(?:many|much)\b|\b(?:count|number)\s+of\b",
    re.I,
)
_BOUNDED_RECORD_LOOKUP = re.compile(
    r"^\s*(?:show|get|find|look\s*up|lookup|retrieve|list|return)\b.*"
    r"(?:\b(?:record|identifier|id)\b|\b[A-Za-z][A-Za-z0-9_]*-\d+\b)",
    re.I,
)
_DIRECT_REASONING_VERB = re.compile(
    r"\b(?:why|explain|assess|analyze|evaluate|summarize|compare|recommend|"
    r"prioritize|rank|defend|justify)\w*\b",
    re.I,
)
_HIGH_MATERIALITY = re.compile(
    r"\b(?:strategic|investment|funding|budget|capital|spend(?:ing)?|staffing|hiring|"
    r"headcount|workforce|(?:resource|team) allocation|architecture|governance|"
    r"client[- ]facing|executive|portfolio prioritization|"
    r"material (?:conflict|contradiction))\b",
    re.I,
)
_STRUCTURED_DELIVERABLE = re.compile(
    r"\b(?:all artifacts?|large structured deliverable|multiple deliverables?|"
    r"acceptance criteria|detailed technical design|high functional design|"
    r"functional architecture|technical architecture|implementation plan|"
    r"decision matrix|architecture decision records?)\b",
    re.I,
)
