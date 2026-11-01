"""Finding models for Governed Graph Health Scout."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class DuplicateEntityFinding:
    node_id_a: str
    canonical_name_a: str
    node_type_a: str
    node_id_b: str
    canonical_name_b: str
    node_type_b: str
    similarity_score: float
    confidence_merge: float
    confidence_distinct: float
    margin_delta: float
    hitl_required: bool
    status: str = "PROPOSED"
    rationale: str = ""
    proposal_path: Optional[str] = None
    description_a: str = ""
    description_b: str = ""


@dataclass
class ContradictionFinding:
    subject_node_id: str
    subject_name: str
    predicate: str
    assertion_id_older: str
    assertion_id_newer: str
    claim_older: str
    claim_newer: str
    timestamp_older: str
    timestamp_newer: str
    confidence_supersede: float
    confidence_coexist: float
    margin_delta: float
    hitl_required: bool
    status: str = "PROPOSED_SUPERSESSION"
    rationale: str = ""
    proposal_path: Optional[str] = None
    scope_text_older: Optional[str] = None
    scope_text_newer: Optional[str] = None
    evidence_quote_older: Optional[str] = None
    evidence_quote_newer: Optional[str] = None
    source_title_older: Optional[str] = None
    source_title_newer: Optional[str] = None


@dataclass
class ClutterFinding:
    finding_type: str  # "orphan_node" | "stale_candidate"
    target_id: str
    label: str
    details: Dict[str, Any]
    action_recommended: str


@dataclass
class EdgeParityFinding:
    edge_id: str
    from_node_id: str
    to_node_id: str
    edge_type: str
    issue_type: str  # "unmapped_auto_linkable" | "unmapped_needs_assertion" | "dangling_endpoint"
    matching_assertion_id: Optional[str]
    description: str
    details: Dict[str, Any]
    action_recommended: str

