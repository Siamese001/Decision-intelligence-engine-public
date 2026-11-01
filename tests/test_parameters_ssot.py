"""tests/test_parameters_ssot.py

Test suite verifying Layer 0 SSOT parameter consolidation for:
- Engineering output limits (words, chars, progress words)
- Wave plan scaling word bounds
- Live copilot speech limits
- Schema validation & policy registry alignment
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

import jsonschema
import yaml

from dual_pipeline.parameters import (
    compute_scaled_plan_word_limit,
    get_copilot_speech_limits,
    get_engineering_output_limits,
    get_planning_word_limits,
    reload_parameters,
)


class TestParametersSSOT(unittest.TestCase):
    def setUp(self) -> None:
        reload_parameters()

    def test_schema_conformance(self) -> None:
        """Verify parameters.json strictly conforms to parameters.schema.json."""
        root = Path(__file__).resolve().parent.parent
        schema_path = root / "config" / "runtime" / "parameters.schema.json"
        data_path = root / "config" / "runtime" / "parameters.json"

        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)
        with open(data_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        jsonschema.validate(instance=data, schema=schema)

    def test_engineering_output_limits_defaults(self) -> None:
        """Verify default engineering output thresholds match SSOT governance."""
        limits = get_engineering_output_limits()
        self.assertEqual(limits["max_words"], 300)
        self.assertEqual(limits["target_words"], 120)
        self.assertEqual(limits["target_min_words"], 100)
        self.assertEqual(limits["target_max_words"], 140)
        self.assertEqual(limits["max_chars"], 3500)
        self.assertEqual(limits["progress_max_words"], 20)

    def test_engineering_output_env_override(self) -> None:
        """Verify NEE_PARAM_ENGINEERING_OUTPUT_* overrides JSON parameters."""
        env_key = "NEE_PARAM_ENGINEERING_OUTPUT_MAX_WORDS"
        old_val = os.environ.get(env_key)
        try:
            os.environ[env_key] = "400"
            limits = get_engineering_output_limits()
            self.assertEqual(limits["max_words"], 400)
        finally:
            if old_val is not None:
                os.environ[env_key] = old_val
            else:
                os.environ.pop(env_key, None)
            reload_parameters()

    def test_planning_word_limits_defaults(self) -> None:
        """Verify wave-based planning word limits match SSOT parameters."""
        plan_limits = get_planning_word_limits()
        self.assertEqual(plan_limits["max_plan_words"], 1000)
        self.assertEqual(plan_limits["max_scaled_plan_words"], 2500)
        self.assertEqual(plan_limits["base_plan_words"], 400)
        self.assertEqual(plan_limits["words_per_wave"], 250)
        self.assertEqual(plan_limits["words_per_deliverable"], 25)

    def test_compute_scaled_plan_word_limit(self) -> None:
        """Verify scaling formula correctly enforces floors, growth, and ceiling."""
        # Small plan (1 wave, 2 deliverables): 400 + 250*1 + 25*2 = 700 -> clamped to floor 1000
        self.assertEqual(compute_scaled_plan_word_limit(1, 2), 1000)
        # Moderate plan (4 waves, 12 deliverables): 400 + 250*4 + 25*12 = 1700
        self.assertEqual(compute_scaled_plan_word_limit(4, 12), 1700)
        # Massive plan (10 waves, 50 deliverables): 400 + 2500 + 1250 = 4150 -> clamped to ceiling 2500
        self.assertEqual(compute_scaled_plan_word_limit(10, 50), 2500)

    def test_copilot_speech_limits_expanded(self) -> None:
        """Verify expanded copilot speech parameters resolve from SSOT."""
        copilot_limits = get_copilot_speech_limits()
        self.assertEqual(copilot_limits["cli_default_max_words"], 35)
        self.assertEqual(copilot_limits["insight_max_words"], 40)
        self.assertEqual(copilot_limits["cumulative_max_words"], 90)
        self.assertEqual(copilot_limits["multi_line_cap_words"], 85)
        self.assertEqual(copilot_limits["speaker_summary_max_words"], 130)
        self.assertEqual(copilot_limits["supporting_details_max_words"], 220)
        self.assertEqual(copilot_limits["total_content_max_words"], 350)
        self.assertEqual(copilot_limits["max_sentence_words"], 28)

    def test_policy_registry_alignment(self) -> None:
        """Verify policy_registry.yaml invariants align with runtime parameters."""
        root = Path(__file__).resolve().parent.parent
        policy_path = root / "governance" / "policy_registry.yaml"
        with open(policy_path, "r", encoding="utf-8") as f:
            registry = yaml.safe_load(f)

        policy_map = {p["id"]: p for p in registry.get("policies", [])}

        # Check registered invariants
        self.assertIn("INV-OUTPUT-MAX-WORDS", policy_map)
        self.assertEqual(policy_map["INV-OUTPUT-MAX-WORDS"]["threshold"], 300)

        self.assertIn("INV-OUTPUT-MAX-CHARS", policy_map)
        self.assertEqual(policy_map["INV-OUTPUT-MAX-CHARS"]["threshold"], 3500)

        self.assertIn("INV-OUTPUT-PROGRESS-MAX-WORDS", policy_map)
        self.assertEqual(policy_map["INV-OUTPUT-PROGRESS-MAX-WORDS"]["threshold"], 20)

        self.assertIn("INV-WAVE-PLAN-MAX-WORDS", policy_map)
        self.assertEqual(policy_map["INV-WAVE-PLAN-MAX-WORDS"]["threshold"], 1000)

        self.assertIn("INV-PR-QUEUE-MAX-WORDS", policy_map)
        self.assertEqual(policy_map["INV-PR-QUEUE-MAX-WORDS"]["threshold"], 1000)

        self.assertIn("INV-SSOT-LINK-02", policy_map)
        self.assertEqual(policy_map["INV-SSOT-LINK-02"]["threshold"], 40)


if __name__ == "__main__":
    unittest.main()
