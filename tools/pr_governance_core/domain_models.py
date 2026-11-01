"""Domain Intelligence Data Models & Contracts.

Defines typed Pydantic models and modality enums for multi-tier domain classification.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, model_validator


class ModalityScope(str, Enum):
    """Artifact & change modality scope."""
    DOCUMENT_WRITTEN = "DOCUMENT_WRITTEN"
    MEETING_SPOKEN = "MEETING_SPOKEN"
    CODE_ARCHITECTURE = "CODE_ARCHITECTURE"
    CODE_GOVERNANCE = "CODE_GOVERNANCE"
    CODE_PLATFORM = "CODE_PLATFORM"
    DATA_MIGRATION = "DATA_MIGRATION"
    UNKNOWN = "UNKNOWN"


class ConfidenceTier(str, Enum):
    """Calibrated confidence tier."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class DomainArchetype(BaseModel):
    """Canonical domain definition and semantic profile."""
    label: str
    description: str = ""
    modality: ModalityScope = ModalityScope.UNKNOWN
    name_keywords: List[str] = Field(default_factory=list)
    path_prefixes: List[str] = Field(default_factory=list)
    semantic_anchors: List[str] = Field(default_factory=list)


class DomainClassificationEnvelope(BaseModel):
    """Complete domain classification result envelope."""
    domain: str
    sub_domain: Optional[str] = None
    modality: ModalityScope = ModalityScope.UNKNOWN
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    confidence_tier: ConfidenceTier = Field(default=ConfidenceTier.HIGH)
    rationale: str = ""
    evidence_citations: List[str] = Field(default_factory=list)
    secondary_domain: Optional[str] = None
    ambiguity_margin: Optional[float] = None
    raw_scores: Dict[str, float] = Field(default_factory=dict)
    classified_by: str = "domain_intelligence_agent"

    @model_validator(mode="after")
    def _compute_tier(self) -> "DomainClassificationEnvelope":
        if self.confidence >= 0.85:
            self.confidence_tier = ConfidenceTier.HIGH
        elif self.confidence >= 0.70:
            self.confidence_tier = ConfidenceTier.MEDIUM
        else:
            self.confidence_tier = ConfidenceTier.LOW
        return self
