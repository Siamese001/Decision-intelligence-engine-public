"""Data models for semantic cache."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SemanticCachePolicy:
    policy_id: str
    policy_version: str
    similarity_threshold: float
    max_candidates: int
    ttl_seconds: int
    allow_external_l2_replay: bool
    cacheable_outcomes: tuple[str, ...]
    policy_digest: str


@dataclass(frozen=True)
class SemanticCacheLookup:
    mode: str
    hit: bool
    reason: str
    entry_id: str | None = None
    similarity: float | None = None
    output: dict[str, Any] | None = None
    outcome: str | None = None
    source_run_id: str | None = None
    source_trace_id: str | None = None
    source_output_digest: str | None = None


@dataclass(frozen=True)
class PendingSemanticCacheEntry:
    entry_id: str
    outcome: str
    replay_eligible: bool


@dataclass(frozen=True)
class SemanticCacheWriteResult:
    status: str
    reason: str
    entry: PendingSemanticCacheEntry | None = None
