"""Domain models for Graph-Constrained Hybrid Projection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Organization(str, Enum):
    """Canonical organization classes for boundary enforcement."""

    partner_advisory = "PartnerAdvisory"
    apex_global = "Apex Global Enterprises"
    CLIENT = "Apex Global Enterprises"
    DELIVERY_PARTNER = "PartnerAdvisory"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class EntityAffiliation:
    """Immutable stakeholder affiliation retrieved from deterministic graph spine."""

    stakeholder_id: str
    canonical_name: str
    organization: str
    is_client: bool
    is_delivery_partner: bool
    primary_role: Optional[str] = None
    provenance_source: str = ""
    confidence: str = "high"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SOWMilestoneConstraint:
    """Contractual SOW milestone constraint retrieved from graph assertions."""

    milestone_id: str
    title: str
    target_metric: str
    is_contractual: bool
    confidence_threshold: float
    source_reference: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceSpan:
    """Forensic evidence locator grounded in transcript dialogue or projection JSON."""

    evidence_id: str
    source_file: str
    speaker: str
    timestamp: Optional[str]
    verbatim_text: str
    attribution_mode: str = "EXPLICIT"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectionRow:
    """A single calibrated row in the capability gap analysis matrix."""

    dimension: str
    sub_dimension: str
    standard: str
    field_reality: str
    impact: str
    coaching: str
    evidence_ids: List[str] = field(default_factory=list)
    entities_mentioned: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationViolation:
    """Record of a failed boundary or metric validation check."""

    violation_type: str
    entity_name: str
    expected_org: str
    actual_org: str
    detail: str
    severity: str = "FATAL"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    """Summary of fail-closed pre-release entity & boundary validation."""

    passed: bool
    violations: List[ValidationViolation] = field(default_factory=list)
    checked_entities_count: int = 0
    checked_milestones_count: int = 0
    timestamp_utc: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
