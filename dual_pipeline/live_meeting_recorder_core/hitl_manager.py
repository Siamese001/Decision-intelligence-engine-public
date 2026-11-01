"""
dual_pipeline/live_meeting_recorder_core/hitl_manager.py

Mandatory HITL (Human-In-The-Loop) Enforcement Manager for Live Transcription.
Enforces that HITL is permanently active for live transcription with zero ability
to remove, bypass, or disable HITL in the codebase.
"""
from __future__ import annotations

from typing import Any

FORBIDDEN_HITL_BYPASS_KEYS = {
    "bypass_speaker_hitl",
    "bypass_hitl",
    "bypass_roster_gate",
    "no_hitl",
    "skip_hitl",
    "disable_hitl",
    "no_interactive",
}


def _check_dict_item(k: Any, v: Any, path: str) -> None:
    current_key = str(k).lower()
    if current_key in FORBIDDEN_HITL_BYPASS_KEYS and bool(v):
        loc = f" at '{path}.{k}'" if path else f" ('{k}')"
        raise ValueError(
            f"HITL cannot be removed or bypassed for live transcription{loc}. "
            "Human-In-The-Loop resolution is mandatory."
        )
    if isinstance(v, (dict, list, tuple, set)):
        assert_no_hitl_bypass(v, path=f"{path}.{k}" if path else str(k))


def assert_no_hitl_bypass(data: Any, path: str = "") -> None:
    """
    Recursively inspects payloads, options, and parameters for any attempt
    to remove, bypass, or disable HITL in live transcription.
    Fails closed immediately if any bypass key is present with a truthy value.
    """
    if isinstance(data, dict):
        for k, v in data.items():
            _check_dict_item(k, v, path)
    elif isinstance(data, (list, tuple, set)):
        for idx, item in enumerate(data):
            assert_no_hitl_bypass(item, path=f"{path}[{idx}]")



__all__ = [
    "FORBIDDEN_HITL_BYPASS_KEYS",
    "assert_no_hitl_bypass",
]

