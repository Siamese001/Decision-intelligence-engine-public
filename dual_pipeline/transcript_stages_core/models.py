"""Data models, errors, and provider protocols for transcript stages."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from pydantic import BaseModel, Field

from dual_pipeline.exceptions import HITLRequiredError  # noqa: F401

from .constants import (
    INTERNAL_ADVISORY_NAMESPACE,
)


class AgenticLeverageNode(BaseModel):
    node_type: str = Field(..., description="Must be: INFLUENCE_MAP, INFO_ASYMMETRY, partner_advisory_MARGIN_WEDGE, CAREER_VISIBILITY")
    key_players: List[str] = Field(..., description="The NEE or PartnerAdvisory executives involved.")
    political_rationale: str = Field(..., description="CHAIN OF THOUGHT: What is the unstated power dynamic? Who is actually blocking this? Who is missing context?")
    networking_wedge: str = Field(..., description="SELFISH ACTION: Exactly who should I ping after this meeting, and what specific insight should I offer them to look like a visionary?")
    verbatim_quote: str = Field(..., description="The exact transcript quote proving this dynamic.")
    speaker: str = Field(..., description="The person who exposed this dynamic.")


class BipartiteSignalProjection(BaseModel):
    client_safe: Dict[str, Any]
    agentic_leverage: List[AgenticLeverageNode] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RestrictedAdvisoryProjection(BaseModel):
    """Encapsulated restricted internal advisory enclave projection."""
    schema_version: str = "restricted-advisory-1.0"
    namespace: str = INTERNAL_ADVISORY_NAMESPACE
    is_quarantined: int = 1
    stealth_score: float = 1.0
    advisory_nodes: List[AgenticLeverageNode] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SynthesisTruncationError(RuntimeError):
    """Raised when an LLM generation hits token completion limits mid-stream."""
    pass


class L2SynthesisProvider(Protocol):
    def synthesize_signal_triage(self, transcript: str) -> str: ...
    def synthesize_forensic(self, system_prompt: str, user_content: str) -> str: ...
    def synthesize_recap(self, system_prompt: str, user_content: str) -> str: ...
    def synthesize_graph(self, system_prompt: str, user_content: str) -> str: ...
    def synthesize_metadata(self, system_prompt: str, user_content: str) -> str: ...
    def synthesize_agentic_leverage(self, system_prompt: str, user_content: str) -> List[AgenticLeverageNode]: ...
