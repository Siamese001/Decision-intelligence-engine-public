"""
dual_pipeline/memory_guard.py

Preflight memory headroom checks to prevent uncatchable kernel OOM SIGKILL termination
when loading multi-gigabyte models or processing long audio recordings.
"""
from __future__ import annotations

import logging

from dual_pipeline.exceptions import InsufficientMemoryError

logger = logging.getLogger(__name__)

# Default safety threshold: 2.0 GB headroom minimum for lightweight operations, 4.0 GB for audio/model loading
DEFAULT_MIN_HEADROOM_BYTES = 2 * 1024 * 1024 * 1024
ACOUSTIC_MIN_HEADROOM_BYTES = 6 * 1024 * 1024 * 1024


def assert_memory_headroom(
    min_free_bytes: int = DEFAULT_MIN_HEADROOM_BYTES,
    context: str = "pipeline execution",
    allow_skip_on_missing_psutil: bool = True,
) -> int:
    """
    Checks available system memory against the required minimum headroom.
    Raises InsufficientMemoryError if available RAM is below threshold.
    Returns available memory in bytes.
    """
    try:
        import psutil
        mem = psutil.virtual_memory()
        available = mem.available
        if available < min_free_bytes:
            avail_gb = available / (1024**3)
            req_gb = min_free_bytes / (1024**3)
            msg = (
                f"Insufficient system memory for {context}: {avail_gb:.2f} GB available, "
                f"but {req_gb:.2f} GB is required. Halting to prevent kernel OOM SIGKILL."
            )
            logger.error(msg)
            raise InsufficientMemoryError(msg)
        return available
    except ImportError:
        if allow_skip_on_missing_psutil:
            logger.debug("psutil not available; skipping memory headroom check for %s", context)
            return -1
        raise
