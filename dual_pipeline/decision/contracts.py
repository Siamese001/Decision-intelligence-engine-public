"""Pipeline D (Decision Intelligence) Layer 0: Foundational Decision Contracts, Types, and Frames.

Zero-dependency data models defining the decision frames and evidence packs across Pipeline D.
No component in L0 may import from L1-L6.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class DecisionOption:
    """A concrete, evaluated alternative within a decision frame."""
    key: str
    title: str
    description: str
    confidence_score: float = 0.5
    confidence_tier: str = "MEDIUM"
    rationale: str = ""
    evidence_ids: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    trade_offs: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionEvidencePack:
    """Cited canonical and fast-path evidence bound to a decision inquiry."""
    decision_id: str
    question: str
    canonical_facts: List[Dict[str, Any]] = field(default_factory=list)
    fast_path_turns: List[Dict[str, Any]] = field(default_factory=list)
    graph_delta_signals: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionFrame:
    """Complete dialectical decision state terminating in an action or HITL escalation."""
    decision_id: str
    question: str
    options: List[DecisionOption] = field(default_factory=list)
    selected_option_key: Optional[str] = None
    confidence_margin: float = 0.0
    hitl_required: bool = False
    hitl_reason: Optional[str] = None
    terminal_state: str = "IN_PROGRESS"  # DECISION_PRODUCED | CLARIFICATION_REQUIRED | ABSTENTION
    metadata: Dict[str, Any] = field(default_factory=dict)
