"""End-to-End Acceptance Suite for GPU Hardware Capacity Wave 4.

Verifies:
1. Decision report reports/hardware/pro64_max64_max128_decision.md exists and covers:
   - Final recommendation (M5 Pro 64 GB) with capacity, compute, and economic rationale.
   - Four-wave empirical evidence synthesis (Wave 1 baseline, Wave 2 controls, Wave 3 benchmarks).
   - Comparative analysis table for Pro 64, Max 64, and Max 128.
   - Explicit evidence-limited declaration regarding absence of physical Max loaners.
   - Pre-registered revisit and escalation triggers.
2. IT quote and exchange summary reports/hardware/it_quote_exchange_summary.json:
   - Conforms to schema version 1.0.0.
   - Contains all four evaluated configurations with pricing, lead times, and rationales.
   - Strictly omits personal PII, employee names, and private credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from dual_pipeline.sanitizer import LocalPreEgressSanitizer


class TestGpuHardwareCapacityWave4E2E(TestCase):
    """End-to-end tests for Wave 4 hardware decision report and IT exchange summary."""

    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.decision_md_path = self.repo_root / "reports" / "hardware" / "pro64_max64_max128_decision.md"
        self.quote_json_path = self.repo_root / "reports" / "hardware" / "it_quote_exchange_summary.json"
        self.sanitizer = LocalPreEgressSanitizer()

    def test_decision_report_existence_and_core_sections(self) -> None:
        """Verifies that the decision markdown report exists and contains all mandatory sections."""
        self.assertTrue(self.decision_md_path.exists(), f"Missing decision report at {self.decision_md_path}")
        content = self.decision_md_path.read_text(encoding="utf-8")

        # Mandatory Headers & Governance markers
        self.assertIn("# Hardware Capacity & GPU Configuration Decision Report", content)
        self.assertIn("Apple M5 Pro, 18 CPU Cores, 20 GPU Cores, 64 GB Unified Memory", content)
        self.assertIn("Evidence-Limited Recommendation", content)

        # Core Analysis Sections
        self.assertIn("1. Executive Summary & Final Decision", content)
        self.assertIn("2. Four-Wave Empirical Evidence Synthesis", content)
        self.assertIn("3. Comparative Evaluation of Configurations", content)
        self.assertIn("4. IT Procurement & Exchange Path Summary", content)
        self.assertIn("5. Revisit & Escalation Triggers", content)
        self.assertIn("6. Wave 4 Governance Completion Sign-Off", content)

        # Key findings cited from prior waves
        self.assertIn("leaked speech workers", content)
        self.assertIn("35.1 GB Metal", content)
        self.assertIn("5.48x speedup", content)
        self.assertIn("NumPy Accelerate outperformed MPS by 20x", content)
        self.assertIn("0.805", content)

    def test_it_quote_exchange_summary_schema_and_integrity(self) -> None:
        """Verifies IT quote summary structure, pricing deltas, and zero PII."""
        self.assertTrue(self.quote_json_path.exists(), f"Missing IT quote summary at {self.quote_json_path}")
        with open(self.quote_json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertEqual(data.get("schema_version"), "1.0.0")
        proc_ctx = data.get("procurement_context", {})
        self.assertEqual(proc_ctx.get("organization"), "PartnerAdvisory Enterprise Consulting")
        self.assertEqual(proc_ctx.get("exchange_window_days"), 30)

        # Configurations
        configs = data.get("configurations", {})
        self.assertIn("current_host_m5_pro_48gb", configs)
        self.assertIn("recommended_m5_pro_64gb", configs)
        self.assertIn("evaluated_m5_max_64gb", configs)
        self.assertIn("evaluated_m5_max_128gb", configs)

        # Verify selected target
        rec = configs["recommended_m5_pro_64gb"]
        self.assertEqual(rec["evaluation_outcome"], "RECOMMENDED_FOR_ORDER")
        self.assertEqual(rec["unified_memory_gb"], 64.0)
        self.assertEqual(rec["gpu_cores"], 20)
        self.assertEqual(rec["estimated_price_delta_usd"], 400)

        # Verify rejected targets
        max64 = configs["evaluated_m5_max_64gb"]
        self.assertEqual(max64["evaluation_outcome"], "REJECTED_UNJUSTIFIED_COMPUTE_PREMIUM")

        max128 = configs["evaluated_m5_max_128gb"]
        self.assertEqual(max128["evaluation_outcome"], "REJECTED_CAPACITY_OVERKILL_POST_LEAK_FIX")

        # Verify revisit triggers exist
        triggers = data.get("revisit_triggers", [])
        self.assertGreaterEqual(len(triggers), 3)

    def test_zero_personal_pii_in_reports(self) -> None:
        """Verifies that hardware decision artifacts do not leak personal operator details."""
        raw_md = self.decision_md_path.read_text(encoding="utf-8")
        raw_json = self.quote_json_path.read_text(encoding="utf-8")

        forbidden_tokens = ["amit", "ayer", "@consulting.example.com", "mitchell", "tenika"]
        for token in forbidden_tokens:
            self.assertNotIn(token.lower(), raw_md.lower(), f"Forbidden PII token '{token}' in decision report")
            self.assertNotIn(token.lower(), raw_json.lower(), f"Forbidden PII token '{token}' in IT summary")
        scrubbed = self.sanitizer.sanitize_text(raw_json)
        self.assertTrue(isinstance(scrubbed, str) or hasattr(scrubbed, "sanitized_text"))
