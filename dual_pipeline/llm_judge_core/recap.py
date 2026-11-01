"""Compatibility facade re-exporting ExecutiveRecapJudge."""
from __future__ import annotations

from dual_pipeline.llm_judge_core.judges.recap import (
    ExecutiveRecapJudge,
    SYSTEM_PROMPT,
    _build_mock_result,
    _check_appendix_defect,
    _finalize_envelope,
    _run_llm_inference,
)

__all__ = [
    "ExecutiveRecapJudge",
    "SYSTEM_PROMPT",
    "_build_mock_result",
    "_check_appendix_defect",
    "_finalize_envelope",
    "_run_llm_inference",
]
