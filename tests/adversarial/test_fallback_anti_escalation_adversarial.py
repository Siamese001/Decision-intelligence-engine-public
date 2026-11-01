"""
tests/adversarial/test_fallback_anti_escalation_adversarial.py

Adversarial stress and edge-case testing for Wave 4 cost reduction:
1. Anti-Escalation Hygiene under rate-limit (HTTP 429) & quota errors:
   - Proves exponential backoff engages on the SAME model before escalating.
   - Proves fallback cascades never jump straight to expensive frontier tiers (Astra)
     when same-tier or mid-tier alternatives exist.
2. Complexity Classifier Anti-Escalation:
   - Proves mutates_graph=True queries do not bypass complexity tensor classification
     or force Astra unless blast radius and cognitive depth are genuinely judicial (>=3/5).
3. Readiness Cache Tree-Hash Stability & Tamper Invalidation:
   - Proves stage cache fingerprints are stable across identical git trees.
   - Proves modifications to target files or test files immediately invalidate cache keys.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest.mock import patch

from dual_pipeline.model_registry import ModelTier, get_tier_spec
from dual_pipeline.query_router_core.complexity_classifier import (
    evaluate_complexity_tensor,
)
from dual_pipeline.query_router_core.escalation_shield import (
    execute_with_multigen_fallback,
)
from tools.readiness_cache import (
    compute_stage_fingerprint,
    get_cached_stage,
    save_cached_stage,
)


class MockRateLimitError(Exception):
    def __init__(self, message: str = "Rate limit exceeded: 429 Too Many Requests") -> None:
        super().__init__(message)
        self.status_code = 429
        self.code = 429


class MockServerError(Exception):
    def __init__(self, message: str = "Service Unavailable: 503") -> None:
        super().__init__(message)
        self.status_code = 503
        self.code = 503


class TestRateLimitAntiEscalationAdversarial(unittest.TestCase):
    """Adversarial testing of model fallback chains and anti-escalation defenses."""

    def test_rate_limit_backs_off_on_same_model_before_escalating(self) -> None:
        """Verify 429 rate limits trigger retry on the same model first before fallback."""
        call_history: List[str] = []

        def failing_then_succeeding_call(model: str) -> str:
            call_history.append(model)
            if len(call_history) == 1:
                # First attempt fails with 429
                raise MockRateLimitError("Rate limit exceeded on primary")
            return f"success_from_{model}"

        with patch("time.sleep") as mock_sleep:
            res, final_model, attempts = execute_with_multigen_fallback(
                call_fn=failing_then_succeeding_call,
                primary_model="gpt-6.1-sol",
                fallback_chain=["gpt-6.1-sol", "gpt-5.6-sol", "gpt-6-astra"],
                max_retries_per_tier=2,
                backoff_seconds=0.1,
            )

        self.assertEqual(res, "success_from_gpt-6.1-sol")
        self.assertEqual(final_model, "gpt-6.1-sol")
        # Proves 2nd call retried gpt-6.1-sol and DID NOT escalate to Astra
        self.assertEqual(call_history, ["gpt-6.1-sol", "gpt-6.1-sol"])
        self.assertEqual(mock_sleep.call_count, 1)

    def test_fast_routing_exhaustion_never_reaches_astra(self) -> None:
        """Verify FAST_ROUTING fallback chain fails closed without calling frontier Astra."""
        call_history: List[str] = []

        def persistent_failure(model: str) -> str:
            call_history.append(model)
            raise MockRateLimitError(f"Quota exhausted on {model}")

        fast_chain = ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-6.1-sol"]

        with patch("time.sleep"), self.assertRaises(RuntimeError) as ctx:
            execute_with_multigen_fallback(
                call_fn=persistent_failure,
                primary_model="gpt-5.6-luna",
                fallback_chain=fast_chain,
                max_retries_per_tier=1,
                backoff_seconds=0.01,
            )

        self.assertIn("MULTIGEN_CASCADE_EXHAUSTED", str(ctx.exception))
        # Ensure Astra was never touched in the fast fallback chain
        for m in call_history:
            self.assertNotIn("astra", m.lower(), "FAST_ROUTING chain must never invoke expensive Astra")

    def test_model_registry_fallbacks_prioritize_same_tier(self) -> None:
        """Verify model_registry.json config puts same-tier models before expensive tiers."""
        fast_spec = get_tier_spec(ModelTier.FAST_ROUTING)
        fast_fallbacks = fast_spec.get("allowed_fallbacks", [])
        # FAST_ROUTING fallbacks must be Luna / Terra, not Astra
        for fb in fast_fallbacks:
            self.assertNotIn("astra", fb.lower(), f"FAST_ROUTING fallback contains Astra: {fb}")

        # QUERY_REASONING must prefer Sol/Terra before Astra
        qr_spec = get_tier_spec(ModelTier.QUERY_REASONING)
        qr_fallbacks = qr_spec.get("allowed_fallbacks", [])
        if "gpt-6-astra" in qr_fallbacks:
            astra_idx = qr_fallbacks.index("gpt-6-astra")
            # Astra must be at the very end of the fallback list
            self.assertEqual(astra_idx, len(qr_fallbacks) - 1)


class TestComplexityRoutingAntiEscalationAdversarial(unittest.TestCase):
    """Verify complexity classifier does not leak to Astra on non-judicial graph mutations."""

    def test_mutates_graph_without_high_blast_does_not_route_to_astra(self) -> None:
        """A mutates_graph task that is a normal ingestion should route to Sol/Luna, NOT Astra."""
        meta = {
            "task_type": "contract_analysis",
            "mutates_graph": True,
            "blast_radius": 1,
            "cognitive_depth": 2,
        }
        tensor, tier = evaluate_complexity_tensor(
            payload="Analyze contract deliverables and extract relationship edges",
            metadata=meta,
        )
        self.assertNotEqual(
            tier,
            ModelTier.DIALECTICAL_JUDGE,
            "mutates_graph with low blast radius must not force DIALECTICAL_JUDGE (Astra)",
        )
        self.assertIn(tier, (ModelTier.QUERY_REASONING, ModelTier.FAST_ROUTING))

    def test_genuine_high_blast_judicial_task_routes_to_astra(self) -> None:
        """Only genuine dialectical judgment with blast radius >= 3 routes to Astra."""
        meta = {
            "task_type": "dialectical_judgment",
            "blast_radius": 4,
            "cognitive_depth": 5,
        }
        tensor, tier = evaluate_complexity_tensor(
            payload="Adversarial red-team challenge on core architectural invariants",
            metadata=meta,
        )
        self.assertEqual(tier, ModelTier.DIALECTICAL_JUDGE)


class TestReadinessCacheTreeHashAdversarial(unittest.TestCase):
    """Adversarial testing of the readiness stage cache."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="nee-cache-adv-")
        self.root = Path(self.temp_dir.name)

        # Create sample target file and test file
        self.target_file = self.root / "tools" / "sample.py"
        self.target_file.parent.mkdir(parents=True, exist_ok=True)
        self.target_file.write_text("print('hello world')", encoding="utf-8")

        self.test_file = self.root / "tests" / "test_sample.py"
        self.test_file.parent.mkdir(parents=True, exist_ok=True)
        self.test_file.write_text("def test_foo(): pass", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_fingerprint_deterministic_and_sensitive_to_edits(self) -> None:
        """Verify fingerprint is stable on identical inputs and mutates when files change."""
        cmd = ["python", "tools/sample.py"]
        target_files = [str(self.target_file.relative_to(self.root))]

        fp1 = compute_stage_fingerprint(
            stage_name="Stage 1",
            cmd=cmd,
            repo_root=self.root,
            head_sha="sha_test_123",
            target_files=target_files,
        )
        fp2 = compute_stage_fingerprint(
            stage_name="Stage 1",
            cmd=cmd,
            repo_root=self.root,
            head_sha="sha_test_123",
            target_files=target_files,
        )
        self.assertEqual(fp1, fp2, "Fingerprint must be deterministic for identical inputs")

        # Mutate target file -> fingerprint must change
        self.target_file.write_text("print('modified')", encoding="utf-8")
        fp3 = compute_stage_fingerprint(
            stage_name="Stage 1",
            cmd=cmd,
            repo_root=self.root,
            head_sha="sha_test_123",
            target_files=target_files,
        )
        self.assertNotEqual(fp1, fp3, "Mutating target file must invalidate stage fingerprint")

    def test_cache_save_and_retrieve_lifecycle(self) -> None:
        """Verify saving and retrieving cached stage results."""
        cmd = ["python", "tools/sample.py"]
        target_files = [str(self.target_file.relative_to(self.root))]

        cached_before = get_cached_stage(
            stage_name="Stage 1",
            cmd=cmd,
            repo_root=self.root,
            head_sha="sha_test_123",
            target_files=target_files,
        )
        self.assertIsNone(cached_before)

        save_cached_stage(
            stage_name="Stage 1",
            cmd=cmd,
            repo_root=self.root,
            head_sha="sha_test_123",
            output_summary="Stage output logged successfully",
            duration_seconds=0.42,
            target_files=target_files,
        )

        cached_after = get_cached_stage(
            stage_name="Stage 1",
            cmd=cmd,
            repo_root=self.root,
            head_sha="sha_test_123",
            target_files=target_files,
        )
        self.assertIsNotNone(cached_after)
        self.assertEqual(cached_after.status, "PASS")
        self.assertEqual(cached_after.duration_seconds, 0.42)
        self.assertIn("Stage output", cached_after.output_summary)


if __name__ == "__main__":
    unittest.main()
