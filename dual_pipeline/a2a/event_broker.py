"""A2A Asynchronous Event Broker & Task Dispatcher.

Coordinates parallel agent execution, enforces delegation budgets and timeouts,
and manages fan-out / fan-in orchestration.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional

from .agent_card import AgentCard, AgentRegistry
from .protocol import (
    A2ATask,
    A2ATaskStatus,
)
from .task_ledger import A2ATaskLedger

logger = logging.getLogger("dual_pipeline.a2a.event_broker")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] [A2A-BROKER] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

AgentHandler = Callable[[A2ATask], Coroutine[Any, Any, Dict[str, Any]]]


class A2AEventBroker:
    """Async event broker orchestrating parallel A2A agent tasks."""

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        ledger: Optional[A2ATaskLedger] = None,
        max_concurrent_tasks: int = 16,
    ) -> None:
        self.registry = registry or AgentRegistry()
        self.ledger = ledger or A2ATaskLedger(":memory:")
        self.max_concurrent_tasks = max_concurrent_tasks
        self._handlers: Dict[str, AgentHandler] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self._subscribers: Dict[str, List[Callable[[A2ATask], Coroutine[Any, Any, None]]]] = {}

    def register_agent(self, card: AgentCard, handler: AgentHandler) -> None:
        """Register an agent with its execution handler."""
        self.registry.register(card)
        self._handlers[card.agent_id] = handler
        logger.info(f"Registered A2A agent [{card.agent_id}] with role '{card.role}'")

    def subscribe(self, topic: str, callback: Callable[[A2ATask], Coroutine[Any, Any, None]]) -> None:
        """Subscribe to task lifecycle events (e.g. 'task.completed', 'task.failed')."""
        if topic not in self._subscribers:
            self._subscribers[topic] = []
        self._subscribers[topic].append(callback)

    async def _notify_subscribers(self, topic: str, task: A2ATask) -> None:
        callbacks = self._subscribers.get(topic, [])
        if callbacks:
            from config.request_deadline import remaining_timeout
            try:
                await asyncio.wait_for(
                    asyncio.gather(*[cb(task) for cb in callbacks], return_exceptions=True),
                    timeout=remaining_timeout(30.0),
                )
            except (asyncio.TimeoutError, TimeoutError):
                pass

    async def dispatch_task(self, task: A2ATask) -> A2ATask:
        """Dispatches an A2A task to its recipient agent handler with strict governance."""
        sender_card = self.registry.get(task.sender_agent_id)
        recipient_card = self.registry.get(task.recipient_agent_id)

        # Enforce Law 1 / Law 2 compute fork compatibility if cards are registered
        if sender_card and recipient_card:
            valid, reason = self.registry.validate_delegation_compatibility(sender_card, recipient_card)
            if not valid:
                task.status = A2ATaskStatus.FAILED
                task.error_message = reason
                self.ledger.record_task_submission(task)
                self.ledger.update_task_status(task.task_id, A2ATaskStatus.FAILED, error_message=reason)
                raise ValueError(reason)

        # Record submission in durable ledger
        self.ledger.record_task_submission(task)
        await self._notify_subscribers("task.submitted", task)

        handler = self._handlers.get(task.recipient_agent_id)
        if not handler:
            err = f"AGENT_UNAVAILABLE: No active handler registered for recipient '{task.recipient_agent_id}'."
            self.ledger.update_task_status(task.task_id, A2ATaskStatus.FAILED, error_message=err)
            raise KeyError(err)

        async with self._semaphore:
            # Transition to ACCEPTED then WORKING
            self.ledger.update_task_status(task.task_id, A2ATaskStatus.ACCEPTED)
            self.ledger.update_task_status(task.task_id, A2ATaskStatus.WORKING)
            await self._notify_subscribers("task.working", task)

            from config.request_deadline import remaining_timeout
            timeout = remaining_timeout(task.budget.timeout_seconds)
            try:
                output = await asyncio.wait_for(handler(task), timeout=timeout)
                updated = self.ledger.update_task_status(
                    task.task_id, A2ATaskStatus.COMPLETED, output_payload=output
                )
                await self._notify_subscribers("task.completed", updated)
                return updated
            except asyncio.TimeoutError:
                err = f"DELEGATION_TIMEOUT: Agent '{task.recipient_agent_id}' exceeded budget timeout of {timeout}s."
                updated = self.ledger.update_task_status(task.task_id, A2ATaskStatus.FAILED, error_message=err)
                await self._notify_subscribers("task.failed", updated)
                return updated
            except Exception as exc:
                err = f"AGENT_EXECUTION_ERROR: {type(exc).__name__}: {str(exc)}"
                updated = self.ledger.update_task_status(task.task_id, A2ATaskStatus.FAILED, error_message=err)
                await self._notify_subscribers("task.failed", updated)
                return updated

    async def fan_out_and_wait(self, tasks: List[A2ATask]) -> List[A2ATask]:
        """Dispatches multiple A2A tasks in parallel and waits for all completions."""
        if not tasks:
            return []
        results = await asyncio.gather(*[self.dispatch_task(t) for t in tasks], return_exceptions=True)
        final_tasks: List[A2ATask] = []
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                orig = tasks[i]
                orig.status = A2ATaskStatus.FAILED
                orig.error_message = str(res)
                final_tasks.append(orig)
            else:
                final_tasks.append(res)
        return final_tasks
