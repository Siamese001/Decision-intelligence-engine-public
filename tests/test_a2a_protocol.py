"""Unit and Integration Tests for A2A Protocol Infrastructure (Wave 1).

Tests:
1. JSON-RPC 2.0 wire protocol and Claim-Check envelopes.
2. Agent Card declarations, model bindings (GPT-5.6-sol reason=high), and registry.
3. Compute posture isolation (Law 1 & Law 2).
4. Durable SQLite WAL task ledger persistence, idempotency, and crash recovery.
5. Async event broker parallel dispatch, fan-out, and budget timeouts.
"""
from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from dual_pipeline.a2a import (
    A2AEventBroker,
    A2AMethod,
    A2ARequest,
    A2AResponse,
    A2ATask,
    A2ATaskLedger,
    A2ATaskStatus,
    AgentCard,
    AgentRegistry,
    ClaimCheckPointer,
    ComputePosture,
    DelegationBudget,
    ModelBinding,
)


class TestA2AProtocolWire(unittest.TestCase):
    """Verifies JSON-RPC 2.0 serialization and Claim-Check primitives."""

    def test_jsonrpc_request_response_serialization(self) -> None:
        req = A2ARequest(
            method=A2AMethod.TASKS_SUBMIT.value,
            params={"task_id": "task-001", "objective": "Extract technical architecture"},
            id="req-123",
        )
        json_str = req.to_json()
        deserialized = A2ARequest.from_json(json_str)
        self.assertEqual(deserialized.method, A2AMethod.TASKS_SUBMIT.value)
        self.assertEqual(deserialized.id, "req-123")
        self.assertEqual(deserialized.params["task_id"], "task-001")

        # Response serialization
        resp = A2AResponse(
            id="req-123",
            result={"status": "ACCEPTED", "timestamp": 123456789.0},
        )
        resp_json = resp.to_json()
        deserialized_resp = A2AResponse.from_json(resp_json)
        self.assertEqual(deserialized_resp.id, "req-123")
        self.assertIsNone(deserialized_resp.error)
        self.assertEqual(deserialized_resp.result["status"], "ACCEPTED")

    def test_claim_check_pointer_serialization(self) -> None:
        pointer = ClaimCheckPointer(
            source_id="src_audio_01",
            evidence_id="ev_chunk_42",
            content_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            locator={"start_ms": 12000, "end_ms": 18500},
            canonical_digest="d41d8cd98f00b204e9800998ecf8427e",
        )
        p_dict = pointer.to_dict()
        rebuilt = ClaimCheckPointer.from_dict(p_dict)
        self.assertEqual(rebuilt.source_id, "src_audio_01")
        self.assertEqual(rebuilt.evidence_id, "ev_chunk_42")
        self.assertEqual(rebuilt.locator["start_ms"], 12000)

    def test_task_state_machine_transitions(self) -> None:
        task = A2ATask(
            task_id="t-1",
            parent_task_id=None,
            sender_agent_id="agent:coord",
            recipient_agent_id="agent:worker",
            objective="Process chunk",
            status=A2ATaskStatus.SUBMITTED,
        )
        # Valid progression
        task.transition_to(A2ATaskStatus.ACCEPTED)
        self.assertEqual(task.status, A2ATaskStatus.ACCEPTED)
        task.transition_to(A2ATaskStatus.WORKING)
        self.assertEqual(task.status, A2ATaskStatus.WORKING)
        task.transition_to(A2ATaskStatus.COMPLETED)
        self.assertEqual(task.status, A2ATaskStatus.COMPLETED)

        # Illegal transition from terminal state
        with self.assertRaises(ValueError):
            task.transition_to(A2ATaskStatus.WORKING)


class TestAgentCardAndRegistry(unittest.TestCase):
    """Verifies Agent Card discovery and Law 1/2 compute posture validation."""

    def test_agent_card_with_gpt_5_6_sol(self) -> None:
        model = ModelBinding(
            model_id="gpt-5.6-sol",
            provider="openai",
            reasoning_effort="high",
            temperature=0.0,
        )
        card = AgentCard(
            agent_id="urn:nee:agent:tech-arch:1.0.0",
            name="TechArchExtractor",
            version="1.0.0",
            role="Technical Systems Architect",
            domain_focus="Extract distributed architecture and technical specs",
            compute_posture=ComputePosture.CLIENT_SAFE,
            model_binding=model,
            skills=["ExtractArchitecture", "ValidateContracts"],
        )
        self.assertEqual(card.model_binding.model_id, "gpt-5.6-sol")
        self.assertEqual(card.model_binding.reasoning_effort, "high")
        self.assertEqual(card.compute_posture, ComputePosture.CLIENT_SAFE)

        # Agent Card JSON projection
        card_json = card.to_agent_card_json()
        self.assertIn("gpt-5.6-sol", card_json)
        self.assertIn("CLIENT_SAFE", card_json)

    def test_compute_posture_boundary_enforcement(self) -> None:
        registry = AgentRegistry()

        local_card = AgentCard(
            agent_id="urn:nee:agent:advisory-dynamics:1.0.0",
            name="StakeholderDynamics",
            version="1.0.0",
            role="Advisory Specialist",
            domain_focus="Power mapping",
            compute_posture=ComputePosture.INTERNAL_ADVISORY,
            model_binding=ModelBinding(model_id="mlx-lm/Meta-Llama-3.1-8B-Instruct-4bit", provider="mlx"),
        )
        cloud_card = AgentCard(
            agent_id="urn:nee:agent:cloud-synthesis:1.0.0",
            name="CloudSynthesis",
            version="1.0.0",
            role="Executive Synthesizer",
            domain_focus="Client deliverables",
            compute_posture=ComputePosture.CLIENT_SAFE,
            model_binding=ModelBinding(model_id="gpt-5.6-sol", provider="openai"),
        )
        registry.register(local_card)
        registry.register(cloud_card)

        # Law 1 enforcement: Internal advisory cannot delegate to cloud client-safe
        valid, reason = registry.validate_delegation_compatibility(local_card, cloud_card)
        self.assertFalse(valid)
        self.assertIn("COMPUTE_FORK_VIOLATION", reason)

        # Reverse is allowed (cloud coordinator delegating to local specialist)
        valid2, _ = registry.validate_delegation_compatibility(cloud_card, local_card)
        self.assertTrue(valid2)


class TestA2ATaskLedger(unittest.TestCase):
    """Verifies SQLite WAL durable task persistence and recovery."""

    def test_task_durability_and_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "a2a_ledger.sqlite"
            ledger = A2ATaskLedger(db_path)

            task = A2ATask(
                task_id="task-wal-1",
                parent_task_id=None,
                sender_agent_id="agent:coord",
                recipient_agent_id="agent:worker",
                objective="Transcribe chunk",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"chunk_index": 1},
            )

            # Record submission
            recorded = ledger.record_task_submission(task)
            self.assertEqual(recorded.task_id, "task-wal-1")

            # Idempotent re-submission returns existing record
            resubmitted = ledger.record_task_submission(task)
            self.assertEqual(resubmitted.task_id, "task-wal-1")

            # State transition
            updated = ledger.update_task_status(
                "task-wal-1",
                A2ATaskStatus.ACCEPTED,
            )
            self.assertEqual(updated.status, A2ATaskStatus.ACCEPTED)

            updated = ledger.update_task_status(
                "task-wal-1",
                A2ATaskStatus.WORKING,
            )
            self.assertEqual(updated.status, A2ATaskStatus.WORKING)

            # Simulate process crash and restart with new ledger instance
            new_ledger = A2ATaskLedger(db_path)
            pending = new_ledger.recover_pending_tasks()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].task_id, "task-wal-1")
            self.assertEqual(pending[0].status, A2ATaskStatus.WORKING)

            # Complete task
            completed = new_ledger.update_task_status(
                "task-wal-1",
                A2ATaskStatus.COMPLETED,
                output_payload={"facts": [{"id": "fact-1"}]},
            )
            self.assertEqual(completed.status, A2ATaskStatus.COMPLETED)
            self.assertEqual(len(new_ledger.recover_pending_tasks()), 0)


class TestA2AEventBroker(unittest.IsolatedAsyncioTestCase):
    """Verifies asynchronous event broker, timeouts, and parallel fan-out."""

    async def test_parallel_fan_out_and_wait(self) -> None:
        broker = A2AEventBroker()

        card_coord = AgentCard(
            agent_id="urn:nee:agent:coord:1.0.0",
            name="Coordinator",
            version="1.0.0",
            role="Coordinator",
            domain_focus="Dispatching",
            compute_posture=ComputePosture.CLIENT_SAFE,
            model_binding=ModelBinding(model_id="gpt-5.6-sol", provider="openai"),
        )
        card_worker = AgentCard(
            agent_id="urn:nee:agent:worker:1.0.0",
            name="Worker",
            version="1.0.0",
            role="Worker",
            domain_focus="Parallel tasks",
            compute_posture=ComputePosture.CLIENT_SAFE,
            model_binding=ModelBinding(model_id="gpt-5.6-sol", provider="openai"),
        )

        async def worker_handler(task: A2ATask) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            val = task.input_payload.get("val", 0)
            return {"doubled": val * 2}

        broker.register_agent(card_coord, lambda t: asyncio.sleep(0))
        broker.register_agent(card_worker, worker_handler)

        # Create 4 parallel tasks
        tasks = [
            A2ATask(
                task_id=f"t-fan-{i}",
                parent_task_id="parent-001",
                sender_agent_id="urn:nee:agent:coord:1.0.0",
                recipient_agent_id="urn:nee:agent:worker:1.0.0",
                objective=f"Process item {i}",
                status=A2ATaskStatus.SUBMITTED,
                input_payload={"val": i},
                budget=DelegationBudget(timeout_seconds=5.0),
            )
            for i in range(4)
        ]

        t0 = time.time()
        results = await broker.fan_out_and_wait(tasks)
        elapsed = time.time() - t0

        self.assertEqual(len(results), 4)
        for i, res in enumerate(results):
            self.assertEqual(res.status, A2ATaskStatus.COMPLETED)
            self.assertEqual(res.output_payload["doubled"], i * 2)

        # Verifies tasks ran concurrently (4 * 0.05s executed in ~0.05s - 0.12s, well under 0.20s)
        self.assertLess(elapsed, 0.20)

    async def test_delegation_timeout_enforcement(self) -> None:
        broker = A2AEventBroker()

        card_slow = AgentCard(
            agent_id="urn:nee:agent:slow:1.0.0",
            name="SlowWorker",
            version="1.0.0",
            role="SlowWorker",
            domain_focus="Hanging tasks",
            compute_posture=ComputePosture.CLIENT_SAFE,
            model_binding=ModelBinding(model_id="gpt-5.6-sol", provider="openai"),
        )

        async def slow_handler(task: A2ATask) -> dict[str, Any]:
            await asyncio.sleep(0.5)
            return {"done": True}

        broker.register_agent(card_slow, slow_handler)

        task = A2ATask(
            task_id="t-timeout",
            parent_task_id=None,
            sender_agent_id="urn:nee:agent:coord:1.0.0",
            recipient_agent_id="urn:nee:agent:slow:1.0.0",
            objective="Timeout test",
            status=A2ATaskStatus.SUBMITTED,
            budget=DelegationBudget(timeout_seconds=0.08),
        )

        result = await broker.dispatch_task(task)
        self.assertEqual(result.status, A2ATaskStatus.FAILED)
        self.assertIn("DELEGATION_TIMEOUT", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
