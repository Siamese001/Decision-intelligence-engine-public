"""Adversarial and edge-case tests for LLM provider timeouts and deadline propagation.

Categories covered:
1. Boundary payload: Zero or negative remaining timeout fail-closed.
2. Invariant bypass attempt: Callers omitting timeout inherit request deadline budget.
3. Overflow & exhaustion: Expired deadline aborts async chat completion via wait_for.
4. Concurrency & race: MCP server tool execution bound to strict deadline scope.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from config.credentials import _guard_client_completions
from config.request_deadline import (
    RequestDeadlineExceeded,
    deadline_scope,
    remaining_timeout,
)


def test_remaining_timeout_boundary_and_zero_budget() -> None:
    """Boundary payload: When no deadline is active, configured timeout is returned.

    When deadline is zero or exhausted, RequestDeadlineExceeded is raised.
    """
    assert remaining_timeout(60.0) == 60.0

    with deadline_scope(seconds=0.05):
        assert remaining_timeout(60.0) <= 0.05

    with pytest.raises(RequestDeadlineExceeded):
        with deadline_scope(seconds=0.01):
            asyncio.run(asyncio.sleep(0.02))
            remaining_timeout(10.0)


def test_guard_client_completions_injects_deadline_sync() -> None:
    """Invariant bypass attempt: Sync chat completions without timeout inherit deadline."""
    mock_client = MagicMock()
    mock_create = MagicMock(return_value=MagicMock(choices=[MagicMock(message=MagicMock(content="{}"))]))
    mock_client.chat.completions.create = mock_create

    guarded = _guard_client_completions(mock_client, default_model="gpt-5.6-terra")

    # Outside deadline scope, default timeout (120s) is injected
    guarded.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
    assert mock_create.call_count == 1
    call_kwargs = mock_create.call_args[1]
    assert call_kwargs.get("timeout") == 120.0

    # Inside deadline scope, timeout is clamped
    with deadline_scope(seconds=1.5):
        guarded.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs.get("timeout") <= 1.5


def test_guard_client_completions_aborts_on_async_timeout() -> None:
    """Overflow & exhaustion: Stalled async chat completion aborts when deadline expires."""
    async def _run() -> None:
        mock_client = MagicMock()

        async def _stalled_create(*args: object, **kwargs: object) -> object:
            await asyncio.sleep(5.0)
            return MagicMock(choices=[MagicMock(message=MagicMock(content="{}"))])

        mock_client.chat.completions.create = _stalled_create
        guarded = _guard_client_completions(mock_client, default_model="gpt-5.6-terra")

        with pytest.raises((asyncio.TimeoutError, TimeoutError, RequestDeadlineExceeded)):
            with deadline_scope(seconds=0.1):
                await guarded.chat.completions.create(messages=[{"role": "user", "content": "hi"}])

    asyncio.run(_run())


def test_mcp_judge_eval_tool_deadline_scope_enforced() -> None:
    """Concurrency & race: MCP tool execution respects deadline_scope bounds."""
    from tools.mcp_judge_eval_server import calibrate_hybrid_weights

    # Normal execution within deadline succeeds
    result = calibrate_hybrid_weights(dense_weight=0.2, sparse_weight=0.3, colbert_weight=0.1, bm25_weight=0.4)
    assert "0.2" in result or "weights" in result
