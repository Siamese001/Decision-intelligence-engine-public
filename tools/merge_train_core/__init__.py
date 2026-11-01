"""Merge Train Core package for speculative queuing and Data Highway fast-tracking."""

from __future__ import annotations

from .data_highway import DataHighwayResult, verify_and_route_data_highway
from .speculative_queue import SpeculativeCohortReceipt, assemble_speculative_cohort

__all__ = [
    "DataHighwayResult",
    "SpeculativeCohortReceipt",
    "assemble_speculative_cohort",
    "verify_and_route_data_highway",
]
