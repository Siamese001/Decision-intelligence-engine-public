"""GPU Optimization Wave Runner Core Package."""
from __future__ import annotations

from .declarations import WAVE_DECLARATIONS
from .receipt import (
    canonical_digest,
    verify_ci_enrollment,
    verify_predecessor_receipt,
    write_immutable_receipt,
)
from .telemetry import (
    collect_hardware_software_telemetry,
    collect_model_config_corpus_digests,
    compute_file_sha256,
    get_git_commit,
)

__all__ = [
    "WAVE_DECLARATIONS",
    "canonical_digest",
    "verify_ci_enrollment",
    "verify_predecessor_receipt",
    "write_immutable_receipt",
    "collect_hardware_software_telemetry",
    "collect_model_config_corpus_digests",
    "compute_file_sha256",
    "get_git_commit",
]
