"""
tests/test_parameter_propagation.py

Dynamic Parameter Mutation & Environment Variable Override Propagation Test Suite.
Verifies that:
1. Environment variable overrides (NEE_PARAM_*) propagate dynamically and immediately.
2. Dynamic mutations and reload operations do not retain stale cached state.
3. Type conversions and fallback resilience work cleanly under edge cases.
4. Parameter registry digestion remains immutable and file-bound.
"""
from __future__ import annotations

import os
import unittest

from dual_pipeline.parameters import (
    ParameterRegistry,
    compute_parameters_digest,
    get_bool_parameter,
    get_float_parameter,
    get_int_parameter,
    get_str_parameter,
    reload_parameters,
)


class TestParameterPropagation(unittest.TestCase):
    def setUp(self):
        reload_parameters()

    def tearDown(self):
        reload_parameters()

    def test_env_override_immediate_propagation(self):
        """Verify setting an environment variable immediately changes the accessor return."""
        env_key = "NEE_PARAM_TIMEOUTS_LLM_SYNC_SECONDS"
        original_env = os.environ.get(env_key)

        try:
            # Baseline from JSON
            base_val = get_float_parameter("timeouts.llm_sync_seconds")
            self.assertEqual(base_val, 60.0)

            # Override via env
            os.environ[env_key] = "120.5"
            new_val = get_float_parameter("timeouts.llm_sync_seconds")
            self.assertEqual(new_val, 120.5)

            # Unset env - returns back to baseline
            del os.environ[env_key]
            restored_val = get_float_parameter("timeouts.llm_sync_seconds")
            self.assertEqual(restored_val, 60.0)
        finally:
            if original_env is not None:
                os.environ[env_key] = original_env
            elif env_key in os.environ:
                del os.environ[env_key]

    def test_typed_env_parsing_robustness(self):
        """Verify type conversion resilience for integer, boolean, float, and string."""
        cases = [
            ("NEE_PARAM_TEST_INT", "test.int", "42", get_int_parameter, 42, 0),
            ("NEE_PARAM_TEST_FLOAT", "test.float", "3.1415", get_float_parameter, 3.1415, 0.0),
            ("NEE_PARAM_TEST_BOOL_TRUE", "test.bool.true", "true", get_bool_parameter, True, False),
            ("NEE_PARAM_TEST_BOOL_NUM", "test.bool.num", "1", get_bool_parameter, True, False),
            ("NEE_PARAM_TEST_BOOL_FALSE", "test.bool.false", "0", get_bool_parameter, False, True),
            ("NEE_PARAM_TEST_STR", "test.str", "custom_value", get_str_parameter, "custom_value", ""),
        ]

        for env_var, dotted_key, str_val, accessor, expected_val, default_val in cases:
            try:
                os.environ[env_var] = str_val
                val = accessor(dotted_key, default=default_val)
                self.assertEqual(val, expected_val, f"Failed for {env_var}")
            finally:
                os.environ.pop(env_var, None)

    def test_env_override_invalid_type_graceful_fallback(self):
        """Verify invalid values in env variables fall back gracefully to defaults."""
        try:
            os.environ["NEE_PARAM_TEST_INVALID_INT"] = "not_a_number"
            val = get_int_parameter("test.invalid_int", default=99)
            self.assertEqual(val, 99)

            os.environ["NEE_PARAM_TEST_INVALID_FLOAT"] = "invalid_float"
            f_val = get_float_parameter("test.invalid_float", default=4.5)
            self.assertEqual(f_val, 4.5)
        finally:
            os.environ.pop("NEE_PARAM_TEST_INVALID_INT", None)
            os.environ.pop("NEE_PARAM_TEST_INVALID_FLOAT", None)

    def test_reload_clears_cached_data(self):
        """Verify reload_parameters re-reads from disk and clears internal cache."""
        reg = ParameterRegistry()
        data1 = reg.get("timeouts.llm_sync_seconds")
        self.assertEqual(data1, 60.0)

        # Clear cache and reload
        reg.clear_cache()
        self.assertIsNone(reg._cached_data)

        reloaded = reg.reload()
        self.assertIsNotNone(reg._cached_data)
        self.assertIn("timeouts", reloaded)

    def test_digest_deterministic_and_env_independent(self):
        """Canonical digest must reflect JSON file state only, independent of env overrides."""
        d1 = compute_parameters_digest()
        env_key = "NEE_PARAM_STORAGE_SQLITE_WAL_BUSY_TIMEOUT_MS"
        try:
            os.environ[env_key] = "999999"
            d2 = compute_parameters_digest()
            self.assertEqual(d1, d2, "Parameters digest must not be altered by transient environment variables")
        finally:
            os.environ.pop(env_key, None)


if __name__ == "__main__":
    unittest.main()
