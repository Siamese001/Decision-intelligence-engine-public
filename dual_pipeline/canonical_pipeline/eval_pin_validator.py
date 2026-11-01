"""Canonical pipeline evaluation pin validator re-export."""
from __future__ import annotations

from dual_pipeline.transcript_qa_core.eval_pin_validator import (
    _is_valid_pin_dict,
    check_pin,
)

__all__ = ["_is_valid_pin_dict", "check_pin"]
