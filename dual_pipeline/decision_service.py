"""Canonical query front door for routing, evidence, and decisions."""

from __future__ import annotations

from .decision_service_core import (
    DecisionService,
    HandoffHandler,
    LazyDecisionService,
    _citations,
    _clarification,
    _default_general_handler,
    _default_workflow_handler,
    evaluate_programmatic_decision_plan,
)

__all__ = [
    "DecisionService",
    "HandoffHandler",
    "LazyDecisionService",
    "_citations",
    "_clarification",
    "_default_general_handler",
    "_default_workflow_handler",
    "evaluate_programmatic_decision_plan",
]
