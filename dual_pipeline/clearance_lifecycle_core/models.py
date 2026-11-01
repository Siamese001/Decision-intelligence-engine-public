"""
dual_pipeline/clearance_lifecycle_core/models.py

Models for the Three-Phase Progressive Clearance Lifecycle.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ClearancePhase(str, Enum):
    """The three progressive clearance lifecycle phases."""
    PHASE_0_PREFLIGHT = "PHASE_0_PREFLIGHT"
    PHASE_1_INFLIGHT = "PHASE_1_INFLIGHT"
    PHASE_2_CLEARANCE = "PHASE_2_CLEARANCE"


class PreflightContextSnapshot(BaseModel):
    """Context snapshot assembled during Phase 0 (T-5 min)."""
    meeting_name: str
    date_iso: str
    attendees: List[str] = Field(default_factory=list)
    project_acronyms: List[str] = Field(default_factory=list)
    bayesian_priors: Dict[str, Any] = Field(default_factory=dict)
    snapshot_hash: str = ""


class C10LockEntry(BaseModel):
    """Entry in the append-only Ambiguity Lock Register (Rule C-10)."""
    lock_id: str
    ambiguity_id: str
    domain: str
    segment_id: str
    candidate_summary: str
    requires_hitl: bool
    status: str = "OPEN"  # OPEN, CLEARED, RETAINED_EXPLICIT
    resolution_payload: Optional[Dict[str, Any]] = None
    prev_hash: str = "0" * 64
    entry_hash: str = ""
