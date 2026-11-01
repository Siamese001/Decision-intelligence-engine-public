"""dual_pipeline/source_classification/models.py

Domain models and Single Source of Truth (SSOT) for source classification,
epistemic authority rankings, independent axes, and source profile representations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


class SemanticFamily(str, Enum):
    """The canonical 8 semantic source families plus mixed and unknown."""
    RAW_TRANSCRIPT = "raw_transcript"
    DERIVED_RECORD = "derived_record"
    CORRESPONDENCE = "correspondence"
    COMMERCIAL_CONTRACT = "commercial_contract"
    WORKING_SOURCE = "working_source"
    VISUAL_COLLATERAL = "visual_collateral"
    RESEARCH_INTELLIGENCE = "research_intelligence"
    STRUCTURED_OPERATIONAL = "structured_operational"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class MediaModality(str, Enum):
    """Physical media modality of the ingested source."""
    AUDIO = "audio"
    TEXT = "text"
    VISUAL = "visual"
    STRUCTURED = "structured"
    DOCUMENT = "document"
    MULTIMODAL = "multimodal"


class ApprovalState(str, Enum):
    """Formal business/governance approval state of the document or record."""
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    EXECUTED = "executed"
    SUPERSEDED = "superseded"
    UNKNOWN = "unknown"


class ClassificationBasis(str, Enum):
    """Provenance and rationale for the assigned source classification."""
    DETERMINISTIC_MARKER = "deterministic_marker"
    CONTENT_SIGNATURE = "content_signature"
    EXPLICIT_DESIGNATION = "explicit_designation"
    CONTAINER_FALLBACK = "container_fallback"
    AMBIGUOUS_UNKNOWN = "ambiguous_unknown"


# Canonical Authority Rank SSOT (1 = Strongest, 9 = Weakest)
# Grounded in dual_pipeline/signal_extraction.py and epistemic evidence corroboration.
AUTHORITY_RANK_WEIGHTS: Dict[int, float] = {
    1: 0.95,  # Executed / Signed Contract, Executed SOW, Official Charter
    2: 0.85,  # Board Approved / Enterprise Policy / Governed Standard
    3: 0.75,  # Governed Specification / Recorded & Diarized Official Meeting
    4: 0.60,  # Official Intent / Direct Stakeholder Commitment / Primary Architecture Spec
    5: 0.45,  # Working Draft / Standard Project Deliverable / Default Baseline
    6: 0.35,  # Meeting Notes / AI Summary / Informal Minutes / Working Notes
    7: 0.25,  # Informal Correspondence / Slack Thread / Email Discussion
    8: 0.15,  # Derivative Summary / External Reference / Contextual Mention
    9: 0.10,  # Unanchored / Speculative / Rumor / Low Authority
}


def canonical_authority_weight(authority_rank: Optional[int]) -> float:
    """Return an epistemic reliability weight (0.0 to 1.0) derived from source authority rank 1-9.
    
    Invariant: Rank 1 is strongest (weight 0.95); Rank 9 is weakest (weight 0.10).
    Default for unranked or unknown sources is 0.45 (Rank 5).
    """
    if authority_rank is None:
        return 0.45
    rank = max(1, min(9, int(authority_rank)))
    return AUTHORITY_RANK_WEIGHTS.get(rank, 0.45)


def is_sufficient_authority(authority_rank: Optional[int], max_rank_threshold: int = 3) -> bool:
    """Determine whether an authority rank satisfies a governed promotion threshold.
    
    Under the canonical SSOT (1=strongest, 9=weakest), sufficiency requires rank <= max_rank_threshold.
    For example, for approved fact promotion requiring strong authority (threshold <= 3),
    ranks 1, 2, and 3 pass; ranks 4 through 9 fail.
    """
    if authority_rank is None:
        return False
    return int(authority_rank) <= max_rank_threshold


def adapt_historical_authority_rank(raw_value: Any, convention: str = "rank_1_strongest") -> int:
    """Explicit adapter for historical or alternative authority rank representations.
    
    Prevents silent reinterpretation by logging conversion rationale and rejecting invalid ranges.
    Supported conventions:
    - 'rank_1_strongest' (Canonical SSOT): values 1..9 preserved.
    - 'inverted_10_highest': historical legacy scale where 9 or 10 was strongest; mapped to 1..9.
    """
    if raw_value is None:
        return 5
    try:
        val = int(raw_value)
    except (ValueError, TypeError) as err:
        logger.warning("Invalid authority rank %r; adapting to default Rank 5: %s", raw_value, err)
        return 5

    if convention == "rank_1_strongest":
        if 1 <= val <= 9:
            return val
        clamped = max(1, min(9, val))
        logger.info("Clamped out-of-range authority_rank %d to %d under %s", val, clamped, convention)
        return clamped
    elif convention == "inverted_10_highest":
        # Legacy: 10/9 -> 1, 8 -> 2, 7 -> 3, 6 -> 4, 5 -> 5, 4 -> 6, 3 -> 7, 2 -> 8, 1 -> 9
        adapted = max(1, min(9, 10 - val))
        logger.info("Adapted legacy inverted authority_rank %d -> %d under %s", val, adapted, convention)
        return adapted
    else:
        raise ValueError(f"Unknown authority rank adaptation convention: {convention}")


@dataclass
class SourceSection:
    """Represents a bounded subsection of a composite/mixed source."""
    section_id: str
    semantic_family: SemanticFamily
    start_locator: str
    end_locator: str
    authority_rank: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "semantic_family": self.semantic_family.value,
            "start_locator": self.start_locator,
            "end_locator": self.end_locator,
            "authority_rank": self.authority_rank,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceSection:
        return cls(
            section_id=str(data["section_id"]),
            semantic_family=SemanticFamily(str(data["semantic_family"])),
            start_locator=str(data["start_locator"]),
            end_locator=str(data["end_locator"]),
            authority_rank=int(data["authority_rank"]) if data.get("authority_rank") is not None else None,
        )


@dataclass
class SourceProfile:
    """Canonical model capturing the multi-dimensional semantics of an ingested source.
    
    Ensures that source semantic family, media modality, container type, confidentiality,
    author, approval state, dates, and deliverable selections remain strictly independent.
    """
    source_id: str
    semantic_family: SemanticFamily
    media_modality: MediaModality
    container_type: str
    confidentiality: str = "internal"
    author: Optional[str] = None  # None if unknown; never fabricated
    approval_state: ApprovalState = ApprovalState.UNKNOWN
    observation_date: Optional[str] = None
    effective_date: Optional[str] = None
    deliverable_selection: List[str] = field(default_factory=list)
    classification_basis: ClassificationBasis = ClassificationBasis.AMBIGUOUS_UNKNOWN
    classification_version: str = "1.0.0"
    authority_rank: int = 5
    origin_group_id: Optional[str] = None
    parent_transformations: List[Dict[str, Any]] = field(default_factory=list)
    coverage_scope: Dict[str, Any] = field(default_factory=dict)
    sections: List[SourceSection] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.authority_rank = max(1, min(9, int(self.authority_rank)))

    def to_dict(self) -> Dict[str, Any]:
        """Convert SourceProfile to a dictionary conforming to the extension schema."""
        return {
            "profile_version": self.classification_version,
            "source_id": self.source_id,
            "semantic_family": self.semantic_family.value,
            "media_modality": self.media_modality.value,
            "container_type": self.container_type,
            "confidentiality": self.confidentiality,
            "author": self.author,
            "approval_state": self.approval_state.value,
            "observation_date": self.observation_date,
            "effective_date": self.effective_date,
            "deliverable_selection": list(self.deliverable_selection),
            "classification_basis": self.classification_basis.value,
            "authority_rank": self.authority_rank,
            "origin_group_id": self.origin_group_id,
            "parent_transformations": list(self.parent_transformations),
            "coverage_scope": dict(self.coverage_scope),
            "sections": [s.to_dict() for s in self.sections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SourceProfile:
        """Construct SourceProfile from a dictionary."""
        sections = [
            SourceSection.from_dict(s)
            for s in data.get("sections", [])
        ]
        return cls(
            source_id=str(data.get("source_id", "unnamed_source")),
            semantic_family=SemanticFamily(str(data.get("semantic_family", SemanticFamily.UNKNOWN.value))),
            media_modality=MediaModality(str(data.get("media_modality", MediaModality.DOCUMENT.value))),
            container_type=str(data.get("container_type", "unknown")),
            confidentiality=str(data.get("confidentiality", "internal")),
            author=data.get("author"),
            approval_state=ApprovalState(str(data.get("approval_state", ApprovalState.UNKNOWN.value))),
            observation_date=data.get("observation_date"),
            effective_date=data.get("effective_date"),
            deliverable_selection=list(data.get("deliverable_selection", [])),
            classification_basis=ClassificationBasis(
                str(data.get("classification_basis", ClassificationBasis.AMBIGUOUS_UNKNOWN.value))
            ),
            classification_version=str(data.get("profile_version", "1.0.0")),
            authority_rank=int(data.get("authority_rank", 5)),
            origin_group_id=data.get("origin_group_id"),
            parent_transformations=list(data.get("parent_transformations", [])),
            coverage_scope=dict(data.get("coverage_scope", {})),
            sections=sections,
        )
