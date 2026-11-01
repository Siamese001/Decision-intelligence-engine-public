"""Constants and configuration defaults for PR promotion benchmarking."""

from __future__ import annotations

from typing import Sequence, Tuple

from tools.pr_governance_core.latency import PROMOTION_PHASES


DEFAULT_WORKLOADS: Tuple[str, ...] = ("documentation", "application")
DEFAULT_PERCENTILE_METHOD: str = "linear_interpolation"
