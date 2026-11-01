"""A2A (Agent-to-Agent) Protocol Subsystem.

Provides declarative Agent Cards, JSON-RPC 2.0 communication, claim-check envelopes,
durable SQLite WAL task ledgers, and parallel event broker orchestration.
"""
from __future__ import annotations

from .agent_card import (
    AgentCard,
    AgentRegistry,
    ComputePosture,
    ModelBinding,
)
from .event_broker import (
    A2AEventBroker,
    AgentHandler,
)
from .protocol import (
    A2AError,
    A2AMethod,
    A2ARequest,
    A2AResponse,
    A2ATask,
    A2ATaskStatus,
    ClaimCheckPointer,
    DelegationBudget,
)
from .task_ledger import A2ATaskLedger

__all__ = [
    "A2AError",
    "A2AEventBroker",
    "A2AMethod",
    "A2ARequest",
    "A2AResponse",
    "A2ATask",
    "A2ATaskLedger",
    "A2ATaskStatus",
    "AgentCard",
    "AgentHandler",
    "AgentRegistry",
    "ClaimCheckPointer",
    "ComputePosture",
    "DelegationBudget",
    "ModelBinding",
]
