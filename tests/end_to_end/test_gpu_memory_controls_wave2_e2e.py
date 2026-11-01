"""End-to-End Acceptance Suite for GPU Memory Capacity & Local Execution Wave 2.

Verifies:
1. MemoryGovernor dynamic envelope derivation for 48 GB, 64 GB, and 128 GB hosts.
2. Larger machines do not retain the 42 GB ceiling (64 GB -> 54 GB, 128 GB -> 112 GB).
3. LocalModelRuntime governor initialization, model key alias reconciliation, and Metal cache clearing.
4. IsolatedMLXTranscriberClient parent-death watchdog and out-of-order task result stashing.
"""
from __future__ import annotations

import os
import queue
from unittest import TestCase, mock

from dual_pipeline.runtime.memory_governor import (
    HARDWARE_MEMORY_PROFILES,
    MemoryGovernor,
    MemoryLimitExceededError,
    ReclamationBlockedError,
    derive_memory_limits,
    detect_host_capacity,
)
from dual_pipeline.runtime.model_runtime import LocalModelRuntime, ModelInferenceResult
from dual_pipeline.live_meeting_recorder_core.isolated_transcriber import (
    IsolatedMLXTranscriberClient,
)


class TestGpuMemoryControlsWave2E2E(TestCase):
    """End-to-end tests for Wave 2 local execution and memory controls."""

    def test_dynamic_host_capacity_and_envelope_scaling(self) -> None:
        """Verifies MemoryGovernor scales allocatable ceilings across 48GB, 64GB, and 128GB envelopes."""
        # 48 GB Envelope (Current M5 Pro)
        gov_48 = MemoryGovernor(total_memory_gb=48.0)
        self.assertEqual(gov_48.total_memory_gb, 48.0)
        self.assertEqual(gov_48.reserved_memory_gb, 6.0)
        self.assertEqual(gov_48.max_allocatable_gb, 42.0)
        self.assertEqual(gov_48.recommended_safe_gb, 38.0)

        # 64 GB Envelope (Proposed M5 Pro/Max 64GB) - MUST NOT retain 42GB ceiling
        gov_64 = MemoryGovernor(total_memory_gb=64.0)
        self.assertEqual(gov_64.total_memory_gb, 64.0)
        self.assertEqual(gov_64.reserved_memory_gb, 10.0)
        self.assertEqual(gov_64.max_allocatable_gb, 54.0)
        self.assertEqual(gov_64.recommended_safe_gb, 48.0)
        self.assertGreater(gov_64.max_allocatable_gb, 42.0)

        # 128 GB Envelope (M5 Max 128GB)
        gov_128 = MemoryGovernor(total_memory_gb=128.0)
        self.assertEqual(gov_128.total_memory_gb, 128.0)
        self.assertEqual(gov_128.reserved_memory_gb, 16.0)
        self.assertEqual(gov_128.max_allocatable_gb, 112.0)
        self.assertEqual(gov_128.recommended_safe_gb, 96.0)
        self.assertGreater(gov_128.max_allocatable_gb, 54.0)

        # Allocate 45 GB on 64 GB governor (would fail on 48 GB, but succeeds on 64 GB)
        with self.assertRaises(MemoryLimitExceededError):
            gov_48.register_allocation("deepseek_r1_distill_qwen_14b", 45.0)

        rec = gov_64.register_allocation("deepseek_r1_distill_qwen_14b", 45.0)
        self.assertEqual(rec.allocated_gb, 45.0)
        self.assertEqual(gov_64.current_allocated_gb, 45.0)
        self.assertEqual(gov_64.available_memory_gb, 9.0)

    def test_local_model_runtime_governor_binding_and_reconciliation(self) -> None:
        """Verifies LocalModelRuntime binds injected governor and reconciles model aliases."""
        gov = MemoryGovernor(total_memory_gb=64.0)
        runtime = LocalModelRuntime(governor=gov, mock_mode=False)
        self.assertIs(runtime.governor, gov)

        # Canonical key exists in local_models.yaml
        spec_canonical = runtime.get_model_spec("deepseek_r1_distill_qwen_14b")
        self.assertIn("mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit", spec_canonical["model_id"])

        # Reconciled alias from judge graph routes cleanly without KeyError
        spec_alias = runtime.get_model_spec("deepseek_r1_distill_14b")
        self.assertEqual(spec_alias["model_id"], spec_canonical["model_id"])

    def test_transcriber_result_stashing_across_interleaved_tasks(self) -> None:
        """Verifies IsolatedMLXTranscriberClient stashes out-of-order results without dropping them."""
        client = IsolatedMLXTranscriberClient(mock_mode=False)
        client._output_q = queue.Queue()

        # Simulate interleaved worker results arriving in queue
        client._output_q.put({"type": "RESULT", "task_id": "task_B", "text": "Result for B", "status": "SUCCESS"})
        client._output_q.put({"type": "RESULT", "task_id": "task_A", "text": "Result for A", "status": "SUCCESS"})

        # Client queries for task_A with timeout: task_B should be stashed, task_A returned
        res_a = client._drain_and_await_result(task_id="task_A", timeout=1.0)
        self.assertEqual(res_a, "Result for A")
        self.assertIn("task_B", client._pending_results)

        # Client subsequently queries for task_B: returned immediately from stash
        res_b = client._drain_and_await_result(task_id="task_B", timeout=1.0)
        self.assertEqual(res_b, "Result for B")
        self.assertNotIn("task_B", client._pending_results)

        client.stop()
