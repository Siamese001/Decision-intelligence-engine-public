"""Core package for PR promotion benchmarking and validation."""

from .constants import DEFAULT_PERCENTILE_METHOD, DEFAULT_WORKLOADS, PROMOTION_PHASES
from .engine import run_benchmark
from .qualifications import (
    validate_wave2_scope_qualifications,
    validate_wave3_reuse_qualifications,
)
from .replay import init_sandbox_git_repo, replay_candidate_promotion
from .stats import compute_stats, percentile

__all__ = [
    "DEFAULT_PERCENTILE_METHOD",
    "DEFAULT_WORKLOADS",
    "PROMOTION_PHASES",
    "compute_stats",
    "init_sandbox_git_repo",
    "percentile",
    "replay_candidate_promotion",
    "run_benchmark",
    "validate_wave2_scope_qualifications",
    "validate_wave3_reuse_qualifications",
]
