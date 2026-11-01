"""Provider-free W4 daily decision-intelligence projections.

Backward-compatibility facade delegating to `dual_pipeline.daily_intelligence_core`.
"""
from __future__ import annotations

from .daily_intelligence_core import (
    DAILY_ARTIFACT_TYPES,
    DAILY_AUDIENCES,
    DAILY_STATES,
    DailyIntelligenceError,
    project_change_brief,
    project_daily_intelligence,
    project_decision_inbox,
    project_dependency_warnings,
    project_meeting_brief,
    project_readiness_gaps,
    validate_daily_artifact,
    validate_decision_record,
    validate_dependency_record,
)

__all__ = [
    "DAILY_ARTIFACT_TYPES",
    "DAILY_AUDIENCES",
    "DAILY_STATES",
    "DailyIntelligenceError",
    "project_change_brief",
    "project_daily_intelligence",
    "project_decision_inbox",
    "project_dependency_warnings",
    "project_meeting_brief",
    "project_readiness_gaps",
    "validate_daily_artifact",
    "validate_decision_record",
    "validate_dependency_record",
]
