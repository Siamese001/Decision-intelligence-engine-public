"""Shadow-first implementation gates and chained receipts for strategic reasoning.

Thin backward-compatibility facade delegating to dual_pipeline.strategic_waves_core.
"""
from __future__ import annotations

from dual_pipeline.strategic_waves_core import (
    PROGRAM_ID,
    WAVES,
    WaveSpec,
    run_strategic_waves,
)

__all__ = ["PROGRAM_ID", "WAVES", "WaveSpec", "run_strategic_waves"]
