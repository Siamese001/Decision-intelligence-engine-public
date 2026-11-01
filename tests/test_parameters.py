"""
tests/test_parameters.py

Comprehensive test suite verifying typed parameter resolution,
environment variable overrides (NEE_PARAM_*), and default fallbacks in Layer 0.
"""
import os
import unittest
from unittest import mock

from dual_pipeline.model_registry import ModelRegistry, ModelTier
from dual_pipeline.parameters import (
    compute_parameters_digest,
    get_bool_parameter,
    get_float_parameter,
    get_int_parameter,
    get_known_projects,
    get_str_parameter,
    reload_parameters,
)
from dual_pipeline.release_thresholds import (
    ReleaseThresholdPolicy,
    ReleaseVerdict,
)


class TestParameters(unittest.TestCase):
    def setUp(self):
        reload_parameters()

    def test_json_parameter_loading(self):
        """Verify parameters load cleanly from config/runtime/parameters.json."""
        timeout = get_float_parameter("timeouts.llm_sync_seconds")
        self.assertEqual(timeout, 60.0)

        audio_timeout = get_float_parameter("timeouts.audio_ingest_seconds")
        self.assertEqual(audio_timeout, 300.0)

        wal_busy = get_int_parameter("storage.sqlite_wal_busy_timeout_ms")
        self.assertEqual(wal_busy, 30000)

        margin = get_float_parameter("hitl.ambiguity_margin_threshold")
        self.assertEqual(margin, 0.20)

        retries = get_int_parameter("healing.max_retries")
        self.assertEqual(retries, 3)

    def test_environment_variable_override(self):
        """Verify NEE_PARAM_* overrides JSON parameters dynamically."""
        env_key = "NEE_PARAM_STORAGE_SQLITE_WAL_BUSY_TIMEOUT_MS"
        old_val = os.environ.get(env_key)

        try:
            os.environ[env_key] = "45000"
            val = get_int_parameter("storage.sqlite_wal_busy_timeout_ms")
            self.assertEqual(val, 45000)

            # Test float env override
            os.environ["NEE_PARAM_HITL_AMBIGUITY_MARGIN_THRESHOLD"] = "0.30"
            margin = get_float_parameter("hitl.ambiguity_margin_threshold")
            self.assertEqual(margin, 0.30)

            # Test bool env override
            os.environ["NEE_PARAM_TEST_FLAG"] = "true"
            flag = get_bool_parameter("test_flag", default=False)
            self.assertTrue(flag)
        finally:
            if old_val is not None:
                os.environ[env_key] = old_val
            else:
                os.environ.pop(env_key, None)
            os.environ.pop("NEE_PARAM_HITL_AMBIGUITY_MARGIN_THRESHOLD", None)
            os.environ.pop("NEE_PARAM_TEST_FLAG", None)

    def test_parameter_fallbacks(self):
        """Verify fallback defaults when key is not in JSON or env."""
        val = get_int_parameter("nonexistent.setting", default=42)
        self.assertEqual(val, 42)

        f_val = get_float_parameter("nonexistent.float", default=3.14)
        self.assertEqual(f_val, 3.14)

        b_val = get_bool_parameter("nonexistent.bool", default=True)
        self.assertTrue(b_val)

        s_val = get_str_parameter("nonexistent.str", default="default_str")
        self.assertEqual(s_val, "default_str")

    def test_parameters_digest_stability(self):
        """Verify canonical digest calculation is deterministic."""
        digest1 = compute_parameters_digest()
        digest2 = compute_parameters_digest()
        self.assertIsInstance(digest1, str)
        self.assertEqual(len(digest1), 64)
        self.assertEqual(digest1, digest2)

    def test_release_threshold_policy_integration(self):
        """Verify ReleaseThresholdPolicy sources from parameters."""
        policy = ReleaseThresholdPolicy()
        self.assertEqual(policy.output1_pass_threshold, 90)
        self.assertEqual(policy.output2_pass_threshold, 90)
        self.assertEqual(policy.output3_pass_threshold, 90)
        self.assertEqual(policy.advisory_floor, 80)

        # Test verdict evaluation works with policy
        verdict1 = policy.evaluate_output_verdict(1, 96)
        self.assertEqual(verdict1, ReleaseVerdict.PASS)

        verdict1_adv = policy.evaluate_output_verdict(1, 85)
        self.assertEqual(verdict1_adv, ReleaseVerdict.PASS_ADVISORY)

        verdict2 = policy.evaluate_output_verdict(2, 96)
        self.assertEqual(verdict2, ReleaseVerdict.PASS)

        verdict2_fail = policy.evaluate_output_verdict(2, 85)
        self.assertEqual(verdict2_fail, ReleaseVerdict.FAIL)

        verdict3 = policy.evaluate_output_verdict(3, 96)
        self.assertEqual(verdict3, ReleaseVerdict.PASS)

        verdict3_fail = policy.evaluate_output_verdict(3, 86)
        self.assertEqual(verdict3_fail, ReleaseVerdict.FAIL)

    def test_known_projects_from_registry(self):
        """Verify get_known_projects loads canonical taxonomy."""
        projects = get_known_projects()
        self.assertIn("apex_trans", projects)
        self.assertIn("conduit", projects)
        self.assertIn("regai", projects)
        self.assertGreaterEqual(len(projects), 12)

    @mock.patch.dict(
        os.environ,
        {
            "OPENAI_MODEL": "",
            "FRONTIER_HIGH_REASONING_MODEL": "",
            "OPENAI_REASONING_EFFORT": "",
            "FRONTIER_HIGH_REASONING_REASONING_EFFORT": "",
        },
        clear=False,
    )
    def test_model_registry_json_canonical(self):
        """Verify ModelRegistry loads cleanly from JSON and resolves tiers."""
        model, effort = ModelRegistry.resolve_tier_model(ModelTier.FRONTIER_HIGH_REASONING)
        self.assertEqual(model, "gpt-6.1-sol")
        self.assertEqual(effort, "max")


if __name__ == "__main__":
    unittest.main()

