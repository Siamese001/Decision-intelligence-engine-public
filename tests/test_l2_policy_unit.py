"""Unit tests for L2Policy and L2Config in dual_pipeline/l2_policy.py.

Verifies:
1. Enum invariants: L2Policy, QueryAssuranceProfile, L2ReasonCode, L2Disposition.
2. Validation helper functions: _strict_keys, _positive_int, _price.
3. PricingPolicy and ModelPolicy data structures.
4. L2Config model_for_assurance dispatch.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dual_pipeline.l2_policy import (
    L2Config,
    L2Disposition,
    L2Policy,
    L2ReasonCode,
    ModelPolicy,
    OutputPolicy,
    PricingPolicy,
    QueryAssuranceProfile,
    ReliabilityPolicy,
    RequestBudgetPolicy,
    _positive_int,
    _price,
    _strict_keys,
)


class TestL2PolicyUnit(unittest.TestCase):
    """Unit test suite for L2 policy enums, validators, and config models."""

    def test_enum_invariants(self) -> None:
        self.assertEqual(L2Policy.FORBIDDEN, "forbidden")
        self.assertEqual(L2Policy.CONDITIONAL, "conditional")
        self.assertEqual(L2Policy.REQUIRED, "required")

        self.assertEqual(QueryAssuranceProfile.STANDARD, "standard")
        self.assertEqual(QueryAssuranceProfile.ENHANCED, "enhanced")

        self.assertIn("EXACT_LOOKUP_DETERMINISTIC", L2ReasonCode.__members__)
        self.assertIn("MATERIAL_CONFLICT_PRESENT", L2ReasonCode.__members__)
        self.assertIn("USER_REQUESTED_EXPLANATION", L2ReasonCode.__members__)

        self.assertEqual(L2Disposition.SKIPPED, "SKIPPED")
        self.assertEqual(L2Disposition.FAILED, "FAILED")

    def test_validation_helpers(self) -> None:
        # _positive_int
        self.assertEqual(_positive_int(10, label="test_val"), 10)
        self.assertEqual(_positive_int(0, label="test_val", allow_zero=True), 0)
        with self.assertRaises(ValueError):
            _positive_int(-1, label="test_val")
        with self.assertRaises(ValueError):
            _positive_int(True, label="test_val")  # bool disallowed

        # _price
        self.assertEqual(_price(1.5, label="price_val"), 1.5)
        self.assertEqual(_price(0, label="price_val"), 0.0)
        with self.assertRaises(ValueError):
            _price(-0.5, label="price_val")

        # _strict_keys
        sample_dict = {"a": 1, "b": 2}
        _strict_keys(sample_dict, required={"a", "b"}, label="dict_test")
        with self.assertRaises(ValueError):
            _strict_keys(sample_dict, required={"a"}, label="dict_test")

    def test_model_for_assurance_dispatch(self) -> None:
        pricing = PricingPolicy(
            input_per_million=2.5,
            cached_input_per_million=1.25,
            output_per_million=10.0,
        )
        standard_model = ModelPolicy(
            provider="openai",
            model_family="gpt-4o-mini",
            model="gpt-4o-mini",
            assurance_profile=QueryAssuranceProfile.STANDARD,
            reasoning_effort="low",
            maximum_primary_calls_per_run=1,
            maximum_repair_calls_per_run=1,
            maximum_escalation_calls_per_run=1,
            pricing=pricing,
        )
        enhanced_model = ModelPolicy(
            provider="openai",
            model_family="gpt-4o",
            model="gpt-4o",
            assurance_profile=QueryAssuranceProfile.ENHANCED,
            reasoning_effort="high",
            maximum_primary_calls_per_run=2,
            maximum_repair_calls_per_run=2,
            maximum_escalation_calls_per_run=2,
            pricing=pricing,
        )
        config = L2Config(
            schema_version="1.0.0",
            mode="governed",
            provider_class="openai",
            provider_egress_allowed=True,
            endpoint="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            local_env_file="env_di",
            standard=standard_model,
            enhanced=enhanced_model,
            output=OutputPolicy(structured_output_required=True, maximum_output_tokens=4096),
            reliability=ReliabilityPolicy(retry_count=2, timeout_seconds=30),
            request_budget=RequestBudgetPolicy(maximum_provider_request_chars=50000),
            config_digest="digest_abc123",
        )

        self.assertEqual(config.model_for_assurance(QueryAssuranceProfile.STANDARD).model, "gpt-4o-mini")
        self.assertEqual(config.model_for_assurance("standard").model, "gpt-4o-mini")
        self.assertEqual(config.model_for_assurance(QueryAssuranceProfile.ENHANCED).model, "gpt-4o")
        self.assertEqual(config.model_for_assurance("enhanced").model, "gpt-4o")

    def test_gpt56_luna_model_configuration(self) -> None:
        import json
        import tempfile

        from dual_pipeline.l2_policy import load_l2_config
        raw_config = {
            "schema_version": "4.0.0",
            "mode": "conditional",
            "provider_class": "openai",
            "provider_egress_allowed": True,
            "endpoint": "https://api.openai.com/v1/responses",
            "api_key_env": "OPENAI_API_KEY",
            "local_env_file": "env_di",
            "standard": {
                "provider": "openai",
                "model_family": "gpt-5.6-luna",
                "model": "gpt-5.6-luna",
                "assurance_profile": "standard",
                "reasoning_effort": "xhigh",
                "maximum_primary_calls_per_run": 1,
                "maximum_repair_calls_per_run": 0,
                "maximum_escalation_calls_per_run": 0,
                "pricing_usd_per_million_tokens": {"input": 0, "cached_input": 0, "output": 0},
            },
            "enhanced": {
                "provider": "openai",
                "model_family": "gpt-5.6-luna",
                "model": "gpt-5.6-luna",
                "assurance_profile": "enhanced",
                "reasoning_effort": "max",
                "maximum_primary_calls_per_run": 1,
                "maximum_repair_calls_per_run": 1,
                "maximum_escalation_calls_per_run": 1,
                "pricing_usd_per_million_tokens": {"input": 0, "cached_input": 0, "output": 0},
            },
            "output": {"structured_output_required": True, "maximum_output_tokens": 32768},
            "reliability": {"retry_count": 0, "timeout_seconds": 300},
            "request_budget": {"maximum_provider_request_chars": 256000},
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(raw_config, tf)
            tmp_path = tf.name
        try:
            config = load_l2_config(Path(tmp_path))
            self.assertEqual(config.standard.model, "gpt-5.6-luna")
            self.assertEqual(config.enhanced.model, "gpt-5.6-luna")
        finally:
            Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
