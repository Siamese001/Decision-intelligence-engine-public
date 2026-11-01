"""
dual_pipeline/live_meeting_recorder_core/prefetch_bridge.py

Backward-compatible re-export module for CopilotPrefetchBridge.
SSOT implementation is consolidated into dual_pipeline.live_meeting_recorder_core.copilot_retriever.
"""
from __future__ import annotations

from .copilot_retriever import (
    C19_FORBIDDEN_MARKERS,
    PRIORITY_RELATIONSHIPS,
    CopilotGraphRetriever,
    CopilotPrefetchBridge,
    has_c19_marker,
)

__all__ = [
    "C19_FORBIDDEN_MARKERS",
    "PRIORITY_RELATIONSHIPS",
    "CopilotGraphRetriever",
    "CopilotPrefetchBridge",
    "has_c19_marker",
]
