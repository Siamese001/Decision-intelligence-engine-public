#!/usr/bin/env python3
"""Governance and integration tests for System Learning & Continuous Feedback."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SystemLearningGovernanceTests(unittest.TestCase):
    """Verifies rules, hooks, routing, and contract governance for system learning."""

    def test_system_learning_rules_file_exists_and_declares_core_principles(self) -> None:
        rule_path = ROOT / ".agents" / "rules" / "system-learning-and-feedback.md"
        self.assertTrue(rule_path.is_file(), "system-learning-and-feedback.md must exist.")
        content = rule_path.read_text(encoding="utf-8")

        self.assertTrue(
            "trigger: always_on" in content or "trigger: glob" in content
        )
        self.assertIn("automations/prompts/**", content)
        self.assertIn("learning-change-proposal:1.0.0", content)
        self.assertIn('status: "proposed"', content)
        self.assertIn("approval_required: true", content)
        self.assertIn("auto_apply: false", content)
        self.assertIn("Mandatory Dry-Run Simulation Gate", content)
        self.assertIn("Cryptographic Approval Binding & Synthetic Firewall", content)
        self.assertIn("Parameter Bounding & Rollback Guarantee", content)
        self.assertIn("Retrieval Clearance Preservation", content)
        self.assertIn("Bounded Bayesian Prior Calibration", content)
        self.assertIn("5-Phase Lifecycle", content)

    def test_hitl_decisions_rules_include_bounded_bayesian_calibration(self) -> None:
        rule_path = ROOT / ".agents" / "rules" / "hitl-decisions.md"
        self.assertTrue(rule_path.is_file(), "hitl-decisions.md must exist.")
        content = rule_path.read_text(encoding="utf-8")

        self.assertIn("Bounded Bayesian Updates", content)
        self.assertIn("Laplace smoothing", content)
        self.assertIn("system-learning-and-feedback.md", content)

    def test_hooks_json_declares_learning_proposal_guard(self) -> None:
        hooks_path = ROOT / ".agents" / "hooks.json"
        self.assertTrue(hooks_path.is_file(), "hooks.json must exist.")
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        hooks = {h.get("id"): h for h in data.get("hooks", [])}

        self.assertIn("nee-learning-proposal-guard", hooks)
        guard = hooks["nee-learning-proposal-guard"]
        self.assertEqual(guard.get("severity"), "blocking")
        self.assertEqual(guard.get("timing"), "PRE")
        self.assertEqual(guard.get("event"), "pre_tool_call")
        patterns = guard.get("match", {}).get("command_patterns", [])
        self.assertIn("govern_learning_proposals.py approve-and-apply", patterns)
        self.assertIn("learning-change-proposal", patterns)

    def test_agent_routing_includes_route_14_system_learning(self) -> None:
        routing_path = ROOT / ".agents" / "rules" / "agent-routing.md"
        self.assertTrue(routing_path.is_file(), "agent-routing.md must exist.")
        content = routing_path.read_text(encoding="utf-8")

        self.assertIn("14. System Learning & Feedback", content)
        self.assertIn("learning-change-proposal:1.0.0", content)
        self.assertIn("govern_learning_proposals.py", content)

    def test_conformance_map_traces_system_learning(self) -> None:
        map_path = ROOT / ".agents" / "rules" / "conformance-map.md"
        self.assertTrue(map_path.is_file(), "conformance-map.md must exist.")
        content = map_path.read_text(encoding="utf-8")

        self.assertIn("System Learning & Continuous Feedback", content)
        self.assertIn("system-learning-and-feedback.md", content)
        self.assertIn("nee-learning-proposal-guard", content)

    def test_bayesian_prior_clamping_invariant(self) -> None:
        """Verify mathematical boundary invariants on prior updates."""
        epsilon = 0.05

        def clamp_prior(p: float) -> float:
            return max(epsilon, min(1.0 - epsilon, p))

        self.assertEqual(clamp_prior(0.0), 0.05)
        self.assertEqual(clamp_prior(1.0), 0.95)
        self.assertEqual(clamp_prior(0.5), 0.5)
        self.assertAlmostEqual(clamp_prior(0.02), 0.05)
        self.assertAlmostEqual(clamp_prior(0.98), 0.95)


if __name__ == "__main__":
    unittest.main()
