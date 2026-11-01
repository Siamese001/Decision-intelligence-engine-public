"""
Acceptance Test Suite for Wave 1: Local Runtime, Contracts, Provenance, and Safety Foundation.

Verifies:
1. Canonical local model registry YAML syntax, hardware targets, and model definitions.
2. Apple Metal unified memory governor (48GB envelope, 6GB safety reserve, ceiling rejections).
3. Local model runtime execution, memory reservation lifecycle, and JSON extraction.
4. Process-level network policy, zero-egress enforcement, and Law 2 advisory protection.
5. Fail-closed circuit breaker gateway and fallback authorization.
6. Cryptographic wave receipt generator, parent chaining, and sequential lock creation.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from dual_pipeline.policy.circuit_breaker import (
    BreakerState,
    CircuitBreakerGateway,
    LocalExecutionHaltedError,
    get_circuit_breaker,
)
from dual_pipeline.policy.network_policy import (
    NetworkEgressForbiddenError,
    NetworkPolicy,
    enforce_zero_egress,
    get_network_policy,
)
from dual_pipeline.runtime.memory_governor import (
    MemoryGovernor,
    MemoryLimitExceededError,
    get_memory_governor,
)
from dual_pipeline.runtime.model_runtime import (
    LocalModelRuntime,
    ModelInferenceResult,
)
from tools.record_wave_receipt import (
    record_wave_receipt,
    verify_receipt,
)


class TestWave1RuntimeFoundation(unittest.TestCase):
    """Verifies all Wave 1 deliverables and acceptance criteria."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        get_memory_governor().reset()
        get_network_policy().set_offline_mode(False)
        get_circuit_breaker().reset()

    def tearDown(self) -> None:
        get_network_policy().set_offline_mode(False)
        get_network_policy().uninstall_socket_interceptor()
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. Local Model Registry Tests
    # -------------------------------------------------------------------------
    def test_canonical_model_registry_schema(self) -> None:
        """Verifies local_models.yaml contains required models and 48GB hardware envelope."""
        config_path = Path("dual_pipeline/config/local_models.yaml")
        self.assertTrue(config_path.is_file(), "local_models.yaml must exist")

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        self.assertEqual(data.get("schema_version"), "1.0.0")
        hw = data.get("hardware_target", {})
        self.assertEqual(hw.get("chip"), "Apple M5 Pro")
        self.assertEqual(hw.get("unified_memory_gb"), 48.0)
        self.assertEqual(hw.get("os_reserved_memory_gb"), 6.0)
        self.assertEqual(hw.get("max_allocatable_memory_gb"), 42.0)

        models = data.get("models", {})
        required_keys = [
            "whisper_large_v3_turbo",
            "llama_3_1_8b_instruct",
            "qwen_2_5_14b_instruct",
            "qwen_2_5_32b_instruct",
            "deepseek_r1_distill_qwen_14b",
            "qwen_2_5_vl_7b_instruct",
            "bge_m3_retriever",
        ]
        for key in required_keys:
            self.assertIn(key, models, f"Model key '{key}' missing from registry")
            spec = models[key]
            self.assertTrue(spec.get("supports_metal"), f"{key} must support Metal")
            self.assertGreater(spec.get("memory_footprint_gb", 0), 0.0)

    # -------------------------------------------------------------------------
    # 2. Memory Governor Tests
    # -------------------------------------------------------------------------
    def test_memory_governor_allocation_and_ceilings(self) -> None:
        """Verifies MemoryGovernor enforces 48GB envelope and rejects over-allocations."""
        gov = MemoryGovernor(total_memory_gb=48.0, reserved_memory_gb=6.0, max_allocatable_gb=42.0)
        self.assertEqual(gov.current_allocated_gb, 0.0)
        self.assertEqual(gov.available_memory_gb, 42.0)

        # Allocate 14B model (9.2 GB) + Whisper (2.5 GB) + BGE-M3 (4.5 GB) = 16.2 GB
        gov.register_allocation("qwen_2_5_14b", 9.2)
        gov.register_allocation("whisper_turbo", 2.5)
        gov.register_allocation("bge_m3", 4.5)
        self.assertEqual(gov.current_allocated_gb, 16.2)
        self.assertEqual(gov.available_memory_gb, 25.8)

        # Allocate 32B model (19.5 GB) -> Total = 35.7 GB (<= 42.0 GB, permitted)
        gov.register_allocation("qwen_2_5_32b", 19.5)
        self.assertEqual(gov.current_allocated_gb, 35.7)

        # Attempt to allocate another 15 GB -> 35.7 + 15 = 50.7 GB > 42 GB (Must FAIL)
        with self.assertRaises(MemoryLimitExceededError):
            gov.register_allocation("over_budget_model", 15.0)

        # Release allocations
        gov.release_allocation("qwen_2_5_32b")
        self.assertEqual(gov.current_allocated_gb, 16.2)
        status = gov.get_status()
        self.assertTrue(status["is_safe_headroom"])

    # -------------------------------------------------------------------------
    # 3. Local Model Runtime Tests
    # -------------------------------------------------------------------------
    def test_local_model_runtime_lifecycle(self) -> None:
        """Verifies LocalModelRuntime loads, generates, and extracts structured JSON."""
        gov = MemoryGovernor()
        runtime = LocalModelRuntime(governor=gov, mock_mode=True)

        # Generate text
        res = runtime.generate(
            model_key="qwen_2_5_14b_instruct",
            prompt="Analyze architectural trade-offs.",
            system_prompt="You are a system architect.",
            response_format=None,
        )
        self.assertIsInstance(res, ModelInferenceResult)
        self.assertIn("LOCAL_METAL_INFERENCE", res.content)
        self.assertTrue(res.is_mock)
        self.assertGreater(gov.current_allocated_gb, 0.0)

        # Generate structured JSON
        json_res = runtime.generate(
            model_key="qwen_2_5_14b_instruct",
            prompt="Extract canonical entities.",
            response_format="json_object",
        )
        self.assertIsNotNone(json_res.parsed_json)
        self.assertEqual(json_res.parsed_json.get("status"), "SUCCESS")

        # Unload model
        unloaded = runtime.unload_model("qwen_2_5_14b_instruct")
        self.assertTrue(unloaded)
        self.assertEqual(gov.current_allocated_gb, 0.0)

    # -------------------------------------------------------------------------
    # 4. Network Policy & Zero-Egress Tests
    # -------------------------------------------------------------------------
    def test_network_policy_zero_egress(self) -> None:
        """Verifies NetworkPolicy blocks outbound calls when offline and protects Law 2."""
        policy = NetworkPolicy(offline_mode_default=False)

        # Online mode permits localhost and external
        policy.assert_egress_allowed("api.openai.com", 443, namespace="CLIENT_SAFE")

        # Law 2 violation check: INTERNAL_ADVISORY cannot egress even if online!
        with self.assertRaises(NetworkEgressForbiddenError):
            policy.assert_egress_allowed("api.openai.com", 443, namespace="INTERNAL_ADVISORY")

        # Offline mode allows loopback
        policy.set_offline_mode(True)
        policy.assert_egress_allowed("localhost", 8080, namespace="CLIENT_SAFE")
        policy.assert_egress_allowed("127.0.0.1", 9000, namespace="CLIENT_SAFE")

        # Offline mode blocks external endpoints
        with self.assertRaises(NetworkEgressForbiddenError):
            policy.assert_egress_allowed("api.openai.com", 443, namespace="CLIENT_SAFE")

        # Context manager check
        with enforce_zero_egress() as scoped_policy:
            self.assertTrue(scoped_policy.is_offline)
            with self.assertRaises(NetworkEgressForbiddenError):
                scoped_policy.assert_egress_allowed("8.8.8.8", 53)

    # -------------------------------------------------------------------------
    # 5. Circuit Breaker Gateway Tests
    # -------------------------------------------------------------------------
    def test_circuit_breaker_fail_closed_and_fallback(self) -> None:
        """Verifies CircuitBreakerGateway enforces fail-closed default and routes to fallback."""
        breaker = CircuitBreakerGateway(failure_threshold=2, allow_cloud_fallback=False)
        self.assertEqual(breaker.state, BreakerState.CLOSED)

        # Successful calls keep breaker closed
        res = breaker.route_execution(local_fn=lambda: "local_success")
        self.assertEqual(res, "local_success")
        self.assertEqual(breaker.state, BreakerState.CLOSED)

        # Record failures to trip breaker
        breaker.record_failure("test_model", "Schema validation error 1")
        self.assertEqual(breaker.state, BreakerState.CLOSED)
        breaker.record_failure("test_model", "Schema validation error 2")
        self.assertEqual(breaker.state, BreakerState.OPEN)

        # Default posture: Cloud fallback DISABLED -> raises LocalExecutionHaltedError
        with self.assertRaises(LocalExecutionHaltedError):
            breaker.route_execution(
                local_fn=lambda: "local",
                cloud_fallback_fn=lambda: "cloud_fallback",
            )

        # Authorize cloud fallback -> routes cleanly
        breaker.set_cloud_fallback_authorization(True)
        fallback_res = breaker.route_execution(
            local_fn=lambda: "local",
            cloud_fallback_fn=lambda: "cloud_fallback_success",
        )
        self.assertEqual(fallback_res, "cloud_fallback_success")

        # Reset breaker
        breaker.reset()
        self.assertEqual(breaker.state, BreakerState.CLOSED)

    # -------------------------------------------------------------------------
    # 6. Record Wave Receipt & Sequential Lock Tests
    # -------------------------------------------------------------------------
    def test_record_wave_receipt_and_chaining(self) -> None:
        """Verifies record_wave_receipt creates valid cryptographic receipts and locks."""
        # Create dummy deliverable and manifest
        deliverable_file = self.workspace / "deliverable.py"
        deliverable_file.write_text("print('wave 1 code')\n", encoding="utf-8")

        manifest_file = self.workspace / "manifest.json"
        manifest_file.write_text(json.dumps({
            "wave": 1,
            "deliverables": [{"path": str(deliverable_file), "type": "CODE"}],
        }), encoding="utf-8")

        receipt_path = self.workspace / "wave-1.json"
        lock_dir = self.workspace / "locks"

        receipt = record_wave_receipt(
            wave=1,
            manifest_path=manifest_file,
            output_path=receipt_path,
            parent_path=None,
            immutable=True,
            lock_dir=lock_dir,
        )

        self.assertEqual(receipt["status"], "COMPLETE")
        self.assertTrue(receipt_path.is_file())
        self.assertTrue((lock_dir / "wave-1.complete").is_file())

        # Verify receipt integrity
        self.assertTrue(verify_receipt(receipt_path))

        # Test parent chaining for Wave 2
        receipt_w2_path = self.workspace / "wave-2.json"
        receipt_w2 = record_wave_receipt(
            wave=2,
            manifest_path=None,
            output_path=receipt_w2_path,
            parent_path=receipt_path,
            immutable=False,
            lock_dir=lock_dir,
        )
        self.assertEqual(receipt_w2["parent_receipt_digest"], receipt["receipt_digest"])
        self.assertTrue((lock_dir / "wave-2.complete").is_file())


if __name__ == "__main__":
    unittest.main()
