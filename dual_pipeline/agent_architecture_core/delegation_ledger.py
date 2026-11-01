"""Subagent delegation cost and token accounting ledger.

Instruments:
- Granular token (prompt, cached, completion) and USD expenditure tracking per delegation.
- Cached-token share observability across subagent workflows.
- Delegation-level budget ceilings and breach detection.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class DelegationCostRecord:
    """Detailed financial and token receipt for an individual subagent delegation."""

    task_id: str
    parent_task_id: str
    subagent_role: str
    model: str
    prompt_tokens: int = 0
    cached_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    status: str = "COMPLETE"  # COMPLETE, FAILED, ESCALATED
    cached_token_share: float = 0.0
    requested_model: Optional[str] = None
    resolved_model: Optional[str] = None
    dispatched_model: Optional[str] = None
    observed_model: Optional[str] = None
    evidence_strength: str = "CONFIGURED_ONLY"
    reservation_id: Optional[str] = None
    request_id: Optional[str] = None
    timestamp_iso: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DelegationCostLedger:
    """Thread-safe ledger recording token consumption and dollar expenditure per delegation."""

    def __init__(self, max_cost_usd: Optional[float] = None) -> None:
        self.max_cost_usd = max_cost_usd
        self._lock = threading.Lock()
        self._records: List[DelegationCostRecord] = []

    def record_delegation(
        self,
        *,
        task_id: str,
        parent_task_id: str,
        subagent_role: str,
        model: str,
        prompt_tokens: int = 0,
        cached_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
        status: str = "COMPLETE",
        requested_model: Optional[str] = None,
        resolved_model: Optional[str] = None,
        dispatched_model: Optional[str] = None,
        observed_model: Optional[str] = None,
        evidence_strength: Optional[str] = None,
        reservation_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> DelegationCostRecord:
        """Records token and USD cost for a completed or executed delegation."""
        pt = max(0, int(prompt_tokens))
        ct = max(0, int(cached_tokens))
        out_t = max(0, int(completion_tokens))
        cost = max(0.0, float(cost_usd))

        cached_share = round(ct / pt, 4) if pt > 0 else 0.0

        eff_requested = requested_model or model
        eff_resolved = resolved_model or model
        eff_dispatched = dispatched_model or model
        eff_strength = evidence_strength or (
            "OBSERVED_RESPONSE" if observed_model else "CONFIGURED_ONLY"
        )

        record = DelegationCostRecord(
            task_id=task_id,
            parent_task_id=parent_task_id,
            subagent_role=subagent_role,
            model=eff_dispatched,
            prompt_tokens=pt,
            cached_tokens=ct,
            completion_tokens=out_t,
            cost_usd=round(cost, 6),
            status=status,
            cached_token_share=cached_share,
            requested_model=eff_requested,
            resolved_model=eff_resolved,
            dispatched_model=eff_dispatched,
            observed_model=observed_model,
            evidence_strength=eff_strength,
            reservation_id=reservation_id,
            request_id=request_id,
        )

        with self._lock:
            self._records.append(record)
            total_spent = sum(r.cost_usd for r in self._records)
            if self.max_cost_usd is not None and total_spent > self.max_cost_usd:
                logger.error(
                    "Delegation budget ceiling breached: spent $%.4f > max $%.4f",
                    total_spent,
                    self.max_cost_usd,
                )
                from dual_pipeline.cost_tracker import BudgetExhaustionError

                raise BudgetExhaustionError(
                    f"DELEGATION_BUDGET_EXHAUSTED: Total delegations cost ${total_spent:.4f} "
                    f"exceeds ceiling of ${self.max_cost_usd:.4f}"
                )

        return record

    def get_records(self) -> List[DelegationCostRecord]:
        with self._lock:
            return list(self._records)

    def get_summary(self) -> Dict[str, Any]:
        """Calculates aggregate metrics across all tracked delegations."""
        with self._lock:
            records = list(self._records)

        total_prompt = sum(r.prompt_tokens for r in records)
        total_cached = sum(r.cached_tokens for r in records)
        total_completion = sum(r.completion_tokens for r in records)
        total_cost = round(sum(r.cost_usd for r in records), 6)
        delegation_count = len(records)

        by_role: Dict[str, Dict[str, Any]] = {}
        for r in records:
            if r.subagent_role not in by_role:
                by_role[r.subagent_role] = {
                    "count": 0,
                    "prompt_tokens": 0,
                    "cached_tokens": 0,
                    "completion_tokens": 0,
                    "cost_usd": 0.0,
                }
            entry = by_role[r.subagent_role]
            entry["count"] += 1
            entry["prompt_tokens"] += r.prompt_tokens
            entry["cached_tokens"] += r.cached_tokens
            entry["completion_tokens"] += r.completion_tokens
            entry["cost_usd"] = round(entry["cost_usd"] + r.cost_usd, 6)

        for role_entry in by_role.values():
            p_tok = role_entry["prompt_tokens"]
            c_tok = role_entry["cached_tokens"]
            role_entry["cached_token_share"] = round(c_tok / p_tok, 4) if p_tok > 0 else 0.0

        blended_share = round(total_cached / total_prompt, 4) if total_prompt > 0 else 0.0

        return {
            "delegation_count": delegation_count,
            "total_prompt_tokens": total_prompt,
            "total_cached_tokens": total_cached,
            "blended_cached_token_share": blended_share,
            "total_completion_tokens": total_completion,
            "total_cost_usd": total_cost,
            "by_role": by_role,
        }

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


__all__ = [
    "DelegationCostLedger",
    "DelegationCostRecord",
]
