"""A2A (Agent-to-Agent) Protocol Wire Primitives & Task Lifecycle.

Implements JSON-RPC 2.0 compliant agent-to-agent communication,
claim-check pointer envelopes, and deterministic task state progression.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Union


class A2ATaskStatus(str, Enum):
    """Deterministic A2A task lifecycle states."""
    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    WORKING = "WORKING"
    INPUT_REQUIRED = "INPUT_REQUIRED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class A2AMethod(str, Enum):
    """Standard A2A JSON-RPC 2.0 method endpoints."""
    DISCOVER = "a2a.discover"
    TASKS_SUBMIT = "a2a.tasks.submit"
    TASKS_STATUS = "a2a.tasks.status"
    TASKS_STREAM = "a2a.tasks.stream"
    TASKS_CANCEL = "a2a.tasks.cancel"
    TASKS_COMPLETE = "a2a.tasks.complete"
    INTEREST_EXCHANGE = "a2a.interest.exchange"


@dataclass(frozen=True)
class ClaimCheckPointer:
    """Bounded pointer to durable evidence or large artifacts.
    
    Prevents multi-megabyte raw blobs from bloating JSON-RPC payloads.
    Directly aligns with urn:nee:dual-pipeline:a2a-fact-vector-packet-intake:1.0.0.
    """
    source_id: str
    evidence_id: str
    content_digest: str  # SHA-256 hex
    locator: Dict[str, Any] = field(default_factory=dict)
    canonical_digest: Optional[str] = None
    verification_state: str = "fresh_capture_verified"
    uri: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        if self.canonical_digest is None:
            res.pop("canonical_digest", None)
        if self.uri is None:
            res.pop("uri", None)
        return res

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ClaimCheckPointer:
        return cls(
            source_id=str(data["source_id"]),
            evidence_id=str(data["evidence_id"]),
            content_digest=str(data["content_digest"]),
            locator=dict(data.get("locator", {})),
            canonical_digest=data.get("canonical_digest"),
            verification_state=str(data.get("verification_state", "fresh_capture_verified")),
            uri=data.get("uri"),
        )


@dataclass
class DelegationBudget:
    """Resource constraints bounding agent delegation."""
    max_turns: int = 5
    max_tool_calls: int = 20
    timeout_seconds: float = 60.0
    max_cost: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DelegationBudget:
        return cls(
            max_turns=int(data.get("max_turns", 5)),
            max_tool_calls=int(data.get("max_tool_calls", 20)),
            timeout_seconds=float(data.get("timeout_seconds", 60.0)),
            max_cost=float(data.get("max_cost", 1.0)),
        )


@dataclass
class A2ATask:
    """Durable unit of work executed by an A2A agent."""
    task_id: str
    parent_task_id: Optional[str]
    sender_agent_id: str
    recipient_agent_id: str
    objective: str
    status: A2ATaskStatus
    scope_in: List[str] = field(default_factory=list)
    scope_out: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    budget: DelegationBudget = field(default_factory=DelegationBudget)
    claim_checks: List[ClaimCheckPointer] = field(default_factory=list)
    input_payload: Dict[str, Any] = field(default_factory=dict)
    output_payload: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    correlation_id: str = ""
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        if not self.correlation_id:
            self.correlation_id = self.parent_task_id or self.task_id
        if not self.idempotency_key:
            raw = f"{self.sender_agent_id}:{self.recipient_agent_id}:{self.objective}:{self.created_at}"
            self.idempotency_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def transition_to(self, new_status: A2ATaskStatus, error: Optional[str] = None) -> None:
        """Enforces legal task state transitions."""
        valid_transitions: Dict[A2ATaskStatus, set[A2ATaskStatus]] = {
            A2ATaskStatus.SUBMITTED: {A2ATaskStatus.ACCEPTED, A2ATaskStatus.CANCELLED, A2ATaskStatus.FAILED},
            A2ATaskStatus.ACCEPTED: {A2ATaskStatus.WORKING, A2ATaskStatus.CANCELLED, A2ATaskStatus.FAILED},
            A2ATaskStatus.WORKING: {
                A2ATaskStatus.INPUT_REQUIRED,
                A2ATaskStatus.AUTH_REQUIRED,
                A2ATaskStatus.HUMAN_APPROVAL_REQUIRED,
                A2ATaskStatus.COMPLETED,
                A2ATaskStatus.FAILED,
                A2ATaskStatus.CANCELLED,
            },
            A2ATaskStatus.INPUT_REQUIRED: {A2ATaskStatus.WORKING, A2ATaskStatus.CANCELLED, A2ATaskStatus.FAILED},
            A2ATaskStatus.AUTH_REQUIRED: {A2ATaskStatus.WORKING, A2ATaskStatus.CANCELLED, A2ATaskStatus.FAILED},
            A2ATaskStatus.HUMAN_APPROVAL_REQUIRED: {
                A2ATaskStatus.WORKING,
                A2ATaskStatus.COMPLETED,
                A2ATaskStatus.CANCELLED,
                A2ATaskStatus.FAILED,
            },
            A2ATaskStatus.COMPLETED: set(),
            A2ATaskStatus.FAILED: set(),
            A2ATaskStatus.CANCELLED: set(),
        }

        allowed = valid_transitions.get(self.status, set())
        if new_status not in allowed:
            raise ValueError(
                f"INVALID_TASK_TRANSITION: Cannot transition A2A task from '{self.status.value}' to '{new_status.value}'."
            )

        self.status = new_status
        self.updated_at = time.time()
        if error:
            self.error_message = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "sender_agent_id": self.sender_agent_id,
            "recipient_agent_id": self.recipient_agent_id,
            "objective": self.objective,
            "status": self.status.value,
            "scope_in": list(self.scope_in),
            "scope_out": list(self.scope_out),
            "allowed_tools": list(self.allowed_tools),
            "budget": self.budget.to_dict(),
            "claim_checks": [c.to_dict() for c in self.claim_checks],
            "input_payload": dict(self.input_payload),
            "output_payload": dict(self.output_payload) if self.output_payload else None,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> A2ATask:
        return cls(
            task_id=str(data["task_id"]),
            parent_task_id=data.get("parent_task_id"),
            sender_agent_id=str(data["sender_agent_id"]),
            recipient_agent_id=str(data["recipient_agent_id"]),
            objective=str(data["objective"]),
            status=A2ATaskStatus(str(data["status"])),
            scope_in=list(data.get("scope_in", [])),
            scope_out=list(data.get("scope_out", [])),
            allowed_tools=list(data.get("allowed_tools", [])),
            budget=DelegationBudget.from_dict(data.get("budget", {})),
            claim_checks=[ClaimCheckPointer.from_dict(c) for c in data.get("claim_checks", [])],
            input_payload=dict(data.get("input_payload", {})),
            output_payload=dict(data["output_payload"]) if data.get("output_payload") else None,
            error_message=data.get("error_message"),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            correlation_id=str(data.get("correlation_id", "")),
            idempotency_key=str(data.get("idempotency_key", "")),
        )


# ─────────────────────────────────────────────────────────────────────────────
# JSON-RPC 2.0 Wire Protocol Models
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class A2AError:
    code: int
    message: str
    data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            res["data"] = self.data
        return res


@dataclass
class A2ARequest:
    method: str
    params: Dict[str, Any] = field(default_factory=dict)
    id: Optional[Union[str, int]] = field(default_factory=lambda: str(int(time.time() * 1000)))
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        payload: Dict[str, Any] = {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
        }
        if self.id is not None:
            payload["id"] = self.id
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes) -> A2ARequest:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0" or "method" not in data:
            raise ValueError("MALFORMED_JSONRPC: Expected valid JSON-RPC 2.0 request payload.")
        return cls(
            method=str(data["method"]),
            params=dict(data.get("params", {})),
            id=data.get("id"),
            jsonrpc="2.0",
        )


@dataclass
class A2AResponse:
    id: Optional[Union[str, int]]
    result: Optional[Any] = None
    error: Optional[A2AError] = None
    jsonrpc: str = "2.0"

    def to_json(self) -> str:
        payload: Dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            payload["error"] = self.error.to_dict()
        else:
            payload["result"] = self.result
        return json.dumps(payload, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str | bytes) -> A2AResponse:
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("jsonrpc") != "2.0":
            raise ValueError("MALFORMED_JSONRPC: Expected valid JSON-RPC 2.0 response payload.")
        error_obj = None
        if "error" in data and isinstance(data["error"], dict):
            e = data["error"]
            error_obj = A2AError(code=int(e["code"]), message=str(e["message"]), data=e.get("data"))
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=error_obj,
            jsonrpc="2.0",
        )


A2AMessage = Union[A2ARequest, A2AResponse]

