"""
Graph Freshness & Integrity Policy Registry (Wave 2).
Contains the operator-approved policies for supersession, TTL, and orphaned evidence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class GraphFreshnessPolicy:
    """Operator-approved parameters for graph freshness and maintenance."""

    # D1: Default supersession winner for FUNCTIONAL predicates
    supersession_strategy: Literal[
        "newest_source_unless_authority_differs",
        "highest_authority_only",
        "newest_source_only",
        "hitl_always"
    ] = "newest_source_unless_authority_differs"

    # D2: Default Time-to-Live (TTL) for standard meeting insights
    default_ttl_days: int = 90

    # D3: Orphaned Evidence Strategy
    orphaned_evidence_strategy: Literal[
        "tombstone_and_purge",
        "soft_delete_quarantine",
        "attempt_reconnect"
    ] = "tombstone_and_purge"

    # D4: Lineage gap enforcement (to be asked, defaulting to quarantine)
    lineage_gap_strategy: Literal["quarantine", "ignore", "downgrade_confidence"] = "quarantine"

    # D5: Remediation format (to be asked, defaulting to direct_sql)
    remediation_format: Literal["direct_sql", "agentic_tool_calls"] = "direct_sql"

def load_policy(path: Path | None = None) -> GraphFreshnessPolicy:
    """Load policy from JSON if provided, otherwise return defaults."""
    if path and path.exists():
        with open(path, "r") as f:
            data = json.load(f)
        return GraphFreshnessPolicy(**data)
    return GraphFreshnessPolicy()
