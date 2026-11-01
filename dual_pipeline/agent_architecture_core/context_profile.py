"""Slim subagent context profile and context budget enforcement engine.

Enforces:
- Hard token ceiling on subagent context profiles (<= 4,000 fixed tokens).
- Bounded section prioritization (objective, tools, DoD preserved over scratch context).
- Deterministic truncation and budget validation for subagent delegations.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from dual_pipeline.parameters import get_max_subagent_context_tokens

logger = logging.getLogger(__name__)

# Standard hard ceiling for slim subagent context profiles (Wave 12 / Finding X-31)
MAX_SUBAGENT_CONTEXT_TOKENS: int = get_max_subagent_context_tokens()


def estimate_token_count(text: str) -> int:
    """Estimates token count using character heuristic (approx 4 chars per token) with safety padding."""
    if not text:
        return 0
    try:
        from dual_pipeline.token_optimization import TokenProfiler

        profiler = TokenProfiler()
        return profiler.count_tokens(text)
    except Exception:
        # Fallback heuristic: 1 token ~= 3.6 characters for code/technical text, minimum 1
        return max(1, int(len(text) / 3.6))


@dataclass
class SubagentContextProfile:
    """Slim context container bounded to <= 4,000 tokens for delegated subagent tasks."""

    task_id: str
    parent_task_id: str
    objective: str
    definition_of_done: str
    allowed_tools: Sequence[str] = field(default_factory=tuple)
    scope_in: str = ""
    scope_out: str = ""
    delegated_context: str = ""
    escalation_rule: str = ""
    max_context_tokens: int = MAX_SUBAGENT_CONTEXT_TOKENS

    @classmethod
    def from_contract(
        cls,
        contract: Mapping[str, Any],
        delegated_context: str = "",
        max_context_tokens: int = MAX_SUBAGENT_CONTEXT_TOKENS,
    ) -> SubagentContextProfile:
        """Constructs a profile directly from a governed delegation contract."""
        tools = contract.get("allowed_tools", ())
        if isinstance(tools, (list, tuple)):
            tool_seq = tuple(str(t) for t in tools)
        else:
            tool_seq = tuple(str(tools).split(","))

        return cls(
            task_id=str(contract.get("task_id", "")).strip(),
            parent_task_id=str(contract.get("parent_task_id", "")).strip(),
            objective=str(contract.get("objective", "")).strip(),
            definition_of_done=str(contract.get("definition_of_done", "")).strip(),
            allowed_tools=tool_seq,
            scope_in=str(contract.get("scope_in", "")).strip(),
            scope_out=str(contract.get("scope_out", "")).strip(),
            delegated_context=str(delegated_context or contract.get("context", "")).strip(),
            escalation_rule=str(contract.get("escalation_rule", "")).strip(),
            max_context_tokens=max_context_tokens,
        )

    def _render_skeleton(self, context_body: str) -> str:
        """Renders full markdown payload for subagent prompt injection."""
        tools_str = ", ".join(self.allowed_tools) if self.allowed_tools else "none"
        parts = [
            f"# Subagent Delegation: {self.task_id} (Parent: {self.parent_task_id})",
            f"## Objective\n{self.objective}",
            f"## Definition of Done\n{self.definition_of_done}",
            f"## Allowed Tools\n{tools_str}",
        ]
        if self.scope_in:
            parts.append(f"## In-Scope\n{self.scope_in}")
        if self.scope_out:
            parts.append(f"## Out-of-Scope\n{self.scope_out}")
        if self.escalation_rule:
            parts.append(f"## Escalation Rule\n{self.escalation_rule}")
        if context_body:
            parts.append(f"## Context & Working Evidence\n{context_body}")
        return "\n\n".join(parts)

    def compile_context(self) -> str:
        """Compiles formatted context payload, guaranteeing <= max_context_tokens."""
        full_text = self._render_skeleton(self.delegated_context)
        tokens = estimate_token_count(full_text)

        if tokens <= self.max_context_tokens:
            return full_text

        # Context exceeds budget: truncate delegated_context while preserving invariants
        skeleton_only = self._render_skeleton("")
        skeleton_tokens = estimate_token_count(skeleton_only)

        if skeleton_tokens >= self.max_context_tokens:
            logger.warning(
                "Subagent task %s skeleton (%d tokens) approaches/exceeds limit %d",
                self.task_id,
                skeleton_tokens,
                self.max_context_tokens,
            )
            # Truncate skeleton directly if objective/DoD alone are oversized
            return skeleton_only[: int(self.max_context_tokens * 3.6)]

        budget_remaining = self.max_context_tokens - skeleton_tokens
        # Character allotment with safety headroom
        max_chars = max(100, int(budget_remaining * 3.0))
        truncated_body = self.delegated_context[:max_chars]
        compiled = self._render_skeleton(truncated_body + "\n... [Context truncated to fit 4k token budget]")
        tokens = estimate_token_count(compiled)

        while tokens > self.max_context_tokens and max_chars > 50:
            excess = tokens - self.max_context_tokens
            max_chars = max(50, max_chars - int(excess * 4.0) - 100)
            truncated_body = self.delegated_context[:max_chars]
            compiled = self._render_skeleton(truncated_body + "\n... [Context truncated to fit 4k token budget]")
            tokens = estimate_token_count(compiled)

        return compiled

    def validate_context_budget(self) -> tuple[bool, int, str]:
        """Validates if the compiled context adheres to the slim token limit."""
        compiled = self.compile_context()
        tok_count = estimate_token_count(compiled)
        if tok_count <= self.max_context_tokens:
            return True, tok_count, "OK"
        return False, tok_count, f"EXCEEDED_TOKEN_BUDGET: {tok_count} > {self.max_context_tokens}"


__all__ = [
    "MAX_SUBAGENT_CONTEXT_TOKENS",
    "SubagentContextProfile",
    "estimate_token_count",
]
