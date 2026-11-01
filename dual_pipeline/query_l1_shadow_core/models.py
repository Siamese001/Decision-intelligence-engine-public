"""Data models and receipt sets for query L1 shadow execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..l1_control import BudgetState, L1ControlError
from ..query_planning import QueryPlanningResult


class ShadowReceiptError(L1ControlError):
    """Stable failure for a mismatched or incomplete shadow artifact chain."""


@dataclass(frozen=True)
class ShadowReceiptSet:
    mode: str
    planning: QueryPlanningResult | None
    attempt_zero: dict[str, Any] | None
    observation_zero: dict[str, Any] | None
    shadow_action: dict[str, Any] | None
    terminal: dict[str, Any] | None
    comparison: dict[str, Any] | None
    budget: BudgetState
    controller_terminal_reason: str | None

    @property
    def artifacts(self) -> tuple[dict[str, Any], ...]:
        values = []
        if self.planning is not None:
            values.append(self.planning.plan)
        for artifact in (
            self.attempt_zero,
            self.observation_zero,
            self.shadow_action,
            self.terminal,
            self.comparison,
        ):
            if artifact is not None:
                values.append(artifact)
        return tuple(values)
