"""Data models for identity resolution and dialogue entity extraction."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SpeakerResolutionResult:
    """Outcome of resolving an active speaker turn token."""
    canonical_name: str
    node_id: Optional[str] = None
    organization: Optional[str] = None
    role: Optional[str] = None
    confidence: float = 1.0
    status: str = "RESOLVED"  # RESOLVED | HITL_REQUIRED | UNKNOWN
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    resolution_basis: str = ""


@dataclass
class ASRRepairRecord:
    """Record of a phonetic or acronym normalization performed on dialogue text."""
    raw_fragment: str
    canonical_text: str
    source_locator: int  # line number
    confidence: float
    category: str  # "attendee_vocative" | "contract_acronym" | "domain_lexicon"
    resolution_basis: str


@dataclass
class HITLOption:
    """Candidate option presented in a ranked drop-down HITL card."""
    candidate: str
    expansion: str
    confidence: float
    basis: str


@dataclass
class HITLDecisionCard:
    """Structured HITL decision card for operator resolution."""
    item_id: str
    source_locator: str
    raw_token: str
    context_snippet: str
    options: List[HITLOption] = field(default_factory=list)
    resolved_choice: Optional[str] = None


@dataclass
class ReferencedEntity:
    """In-dialogue entity mentioned during conversation."""
    token: str
    canonical_name: str
    organization: str
    role: str
    location: Optional[str] = None
    charter_or_pod: Optional[str] = None
    source_locators: List[int] = field(default_factory=list)
    resolution_basis: str = ""
    confidence: float = 1.0
    affected_statement_ids: str = ""
    context_notes: str = ""
