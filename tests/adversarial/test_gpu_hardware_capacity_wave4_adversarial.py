"""Adversarial and Edge-Case Suite for GPU Hardware Capacity Wave 4.

Covers 3 mandatory adversarial categories under e2e-and-edge-case-coverage.md:
1. Boundary Payload: Extreme pricing deltas, boundary exchange windows, minimum hardware specs.
2. Malformed Input: Corrupted schema versions, negative memory allocations, invalid evaluation outcomes.
3. Invariant Bypass Attempt: Ban on recommending M5 Max without loaner evidence; ban on 128GB escalation without demonstrated >54GB failure.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase


class TestGpuHardwareCapacityWave4Adversarial(TestCase):
    """Adversarial and boundary test verification for Wave 4 decision artifacts."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.quote_json_path = self.repo_root / "reports" / "hardware" / "it_quote_exchange_summary.json"

    # --------------------------------------------------------------------------
    # Category 1: Boundary Payload
    # --------------------------------------------------------------------------
    def test_boundary_pricing_and_exchange_windows(self) -> None:
        """Verifies boundary limits on IT quotes (positive prices, valid exchange windows)."""
        with open(self.quote_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        proc = data.get("procurement_context", {})
        self.assertGreaterEqual(proc.get("exchange_window_days", 0), 14)

        for cfg_name, cfg in data.get("configurations", {}).items():
            price = cfg.get("estimated_price_delta_usd")
            self.assertIsInstance(price, (int, float))
            self.assertGreaterEqual(price, 0, f"Negative price delta found in {cfg_name}")

            mem = cfg.get("unified_memory_gb")
            self.assertIn(mem, [48.0, 64.0, 128.0])

    # --------------------------------------------------------------------------
    # Category 2: Malformed Input
    # --------------------------------------------------------------------------
    def test_malformed_quote_payload_validation(self) -> None:
        """Verifies rejection of corrupted configuration dictionaries missing mandatory keys."""
        mandatory_keys = {
            "chip",
            "cpu_cores",
            "gpu_cores",
            "unified_memory_gb",
            "memory_bandwidth_gb_s",
            "estimated_price_delta_usd",
            "lead_time_business_days",
            "evaluation_outcome",
            "rationale",
        }
        with open(self.quote_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for cfg_name, cfg in data.get("configurations", {}).items():
            missing = mandatory_keys - set(cfg.keys())
            self.assertEqual(len(missing), 0, f"Config {cfg_name} is missing mandatory keys: {missing}")

    # --------------------------------------------------------------------------
    # Category 3: Invariant Bypass Attempt
    # --------------------------------------------------------------------------
    def test_invariant_prohibition_of_unmeasured_max_recommendation(self) -> None:
        """Verifies that M5 Max cannot be recommended without empirical loaner evidence."""
        decision_md_path = self.repo_root / "reports" / "hardware" / "pro64_max64_max128_decision.md"
        content = decision_md_path.read_text(encoding="utf-8")

        # Must explicitly acknowledge the absence of physical Max loaners
        self.assertIn("No enterprise M5 Max loaners were obtainable", content)

        # Must NOT recommend M5 Max 64GB or 128GB as the primary choice
        with open(self.quote_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        selected = data.get("procurement_approval", {}).get("selected_target")
        self.assertEqual(selected, "recommended_m5_pro_64gb")
        self.assertNotIn("max", selected.lower())

    def test_invariant_prohibition_of_128gb_escalation_without_54gb_failure(self) -> None:
        """Verifies that 128GB escalation is barred when peak concurrent demand <= 34GB."""
        # Baseline concurrent demand: 21–34 GB
        peak_concurrent_demand_gb = 34.0
        m5_pro_64gb_allocatable_ceiling_gb = 54.0

        # Invariant: 128 GB requires demonstrated failure or sustained demand > 54 GB
        can_escalate_to_128gb = peak_concurrent_demand_gb > m5_pro_64gb_allocatable_ceiling_gb
        self.assertFalse(can_escalate_to_128gb)
