"""Governed graph entity and claim data contracts with taxonomy lane partitioning.

Wave 3 Deliverable:
- Defines formal taxonomy lanes (INTELLIGENT_AUTOMATION, ENTERPRISE_DATA_OFFICE, CORE_IT).
- Enforces structured metrics schema validation on claim contracts (cost_reduction_pct, active_user_count, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaxonomyLane(str, Enum):
    """Governed taxonomy lane partitions for knowledge graph entities and claims."""
    INTELLIGENT_AUTOMATION = "INTELLIGENT_AUTOMATION"
    ENTERPRISE_DATA_OFFICE = "ENTERPRISE_DATA_OFFICE"
    CORE_IT = "CORE_IT"
    UNCLASSIFIED = "UNCLASSIFIED"


@dataclass
class StructuredMetrics:
    """Validated structured metrics attached to claim contracts."""
    cost_reduction_pct: float | None = None
    active_user_count: int | None = None
    duration_seconds: float | None = None
    nte_budget: float | None = None
    inbound_reduction_pct: float | None = None
    additional_metrics: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate metric value bounds and types."""
        if self.cost_reduction_pct is not None:
            if not isinstance(self.cost_reduction_pct, (int, float)):
                raise TypeError("cost_reduction_pct must be numeric")
            if not (0.0 <= self.cost_reduction_pct <= 100.0):
                raise ValueError("cost_reduction_pct must be between 0.0 and 100.0")

        if self.inbound_reduction_pct is not None:
            if not isinstance(self.inbound_reduction_pct, (int, float)):
                raise TypeError("inbound_reduction_pct must be numeric")
            if not (0.0 <= self.inbound_reduction_pct <= 100.0):
                raise ValueError("inbound_reduction_pct must be between 0.0 and 100.0")

        if self.active_user_count is not None:
            if not isinstance(self.active_user_count, int) or isinstance(self.active_user_count, bool):
                raise TypeError("active_user_count must be an integer")
            if self.active_user_count < 0:
                raise ValueError("active_user_count must be non-negative")

        if self.duration_seconds is not None:
            if not isinstance(self.duration_seconds, (int, float)):
                raise TypeError("duration_seconds must be numeric")
            if self.duration_seconds < 0.0:
                raise ValueError("duration_seconds must be non-negative")

        if self.nte_budget is not None:
            if not isinstance(self.nte_budget, (int, float)):
                raise TypeError("nte_budget must be numeric")
            if self.nte_budget < 0.0:
                raise ValueError("nte_budget must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        """Serialize structured metrics to dictionary."""
        self.validate()
        data: dict[str, Any] = {}
        if self.cost_reduction_pct is not None:
            data["cost_reduction_pct"] = self.cost_reduction_pct
        if self.inbound_reduction_pct is not None:
            data["inbound_reduction_pct"] = self.inbound_reduction_pct
        if self.active_user_count is not None:
            data["active_user_count"] = self.active_user_count
        if self.duration_seconds is not None:
            data["duration_seconds"] = self.duration_seconds
        if self.nte_budget is not None:
            data["nte_budget"] = self.nte_budget
        data.update(self.additional_metrics)
        return data


@dataclass
class NodeContract:
    """Governed knowledge graph node contract with taxonomy lane tagging."""
    node_id: str
    canonical_name: str
    node_type: str
    taxonomy_lane: TaxonomyLane = TaxonomyLane.UNCLASSIFIED
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id or not self.node_id.strip():
            raise ValueError("node_id must be non-empty")
        if not self.canonical_name or not self.canonical_name.strip():
            raise ValueError("canonical_name must be non-empty")
        if not isinstance(self.taxonomy_lane, TaxonomyLane):
            self.taxonomy_lane = TaxonomyLane(str(self.taxonomy_lane))


@dataclass
class ClaimContract:
    """Governed claim contract with taxonomy lane and structured metrics."""
    claim_id: str
    claim_text: str
    subject_node_id: str
    taxonomy_lane: TaxonomyLane = TaxonomyLane.UNCLASSIFIED
    structured_metrics: StructuredMetrics | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.claim_id or not self.claim_id.strip():
            raise ValueError("claim_id must be non-empty")
        if not self.claim_text or not self.claim_text.strip():
            raise ValueError("claim_text must be non-empty")
        if not isinstance(self.taxonomy_lane, TaxonomyLane):
            self.taxonomy_lane = TaxonomyLane(str(self.taxonomy_lane))
        if self.structured_metrics is not None:
            self.structured_metrics.validate()
