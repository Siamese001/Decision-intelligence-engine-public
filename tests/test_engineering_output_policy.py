#!/usr/bin/env python3
"""tests/test_engineering_output_policy.py

Verification suite for Wave 1 of the concise runtime output governance plan:
1. Validates canonical semantic contract in .agents/rules/engineering-output.md.
2. Validates quantitative invariant registrations in governance/policy_registry.yaml.
3. Validates reconciliation across conflicting presentation rules, prompt contracts, and skills.
4. Validates surface classifications and host delivery boundary (instruction-only vs machine-enforced).
5. Validates preservation of non-engineering routes (copilot direct-answer, query direct-answer, HITL, runtime evidence).
6. Validates generated AGENTS.md index size ceiling and drift-free check.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class TestEngineeringOutputPolicy(unittest.TestCase):
    """Rigorous policy verification for engineering output governance."""

    def setUp(self) -> None:
        self.rules_dir = ROOT / ".agents" / "rules"
        self.policy_registry_path = ROOT / "governance" / "policy_registry.yaml"

    def test_canonical_rule_file_exists_and_declares_contract(self) -> None:
        """Verify that .agents/rules/engineering-output.md defines the 8 canonical contract principles."""
        rule_path = self.rules_dir / "engineering-output.md"
        self.assertTrue(rule_path.is_file(), "engineering-output.md must exist.")

        content = rule_path.read_text(encoding="utf-8")
        # 1. Budget ceilings
        self.assertIn("300 whitespace-delimited words", content)
        self.assertIn("3,500 raw characters", content)
        self.assertIn("20 words", content)
        self.assertIn("INV-OUTPUT-MAX-WORDS", content)
        self.assertIn("INV-OUTPUT-MAX-CHARS", content)
        self.assertIn("INV-OUTPUT-PROGRESS-MAX-WORDS", content)

        # 2. Eight Core Reporting Principles
        self.assertIn("Concise Completion Budget", content)
        self.assertIn("No Process Inventory", content)
        self.assertIn("Success Requires Observed Behavior", content)
        self.assertIn("Claim Scoped to Demonstrated Effect", content)
        self.assertIn("Freshness and Failure Retention", content)
        self.assertIn("Minimal Progress Updates", content)
        self.assertIn("No Length Loopholes", content)
        self.assertIn("Factual and Internal Operational Records", content)

        # 3. Observed effect requirement
        self.assertIn("running the normal user entry point with meaningful input", content)
        self.assertIn("independent verification path", content)

    def test_policy_registry_registers_output_invariants(self) -> None:
        """Verify policy_registry.yaml defines INV-OUTPUT-MAX-WORDS, INV-OUTPUT-MAX-CHARS, and INV-OUTPUT-PROGRESS-MAX-WORDS."""
        with open(self.policy_registry_path, "r", encoding="utf-8") as f:
            registry = yaml.safe_load(f)

        policies = {p["id"]: p for p in registry.get("policies", [])}

        # Check INV-OUTPUT-MAX-WORDS
        self.assertIn("INV-OUTPUT-MAX-WORDS", policies)
        p_words = policies["INV-OUTPUT-MAX-WORDS"]
        self.assertEqual(p_words["threshold"], 300)
        self.assertEqual(p_words["unit"], "words")
        self.assertEqual(p_words["rule_reference"], ".agents/rules/engineering-output.md")

        # Check INV-OUTPUT-MAX-CHARS
        self.assertIn("INV-OUTPUT-MAX-CHARS", policies)
        p_chars = policies["INV-OUTPUT-MAX-CHARS"]
        self.assertEqual(p_chars["threshold"], 3500)
        self.assertEqual(p_chars["unit"], "characters")
        self.assertEqual(p_chars["rule_reference"], ".agents/rules/engineering-output.md")

        # Check INV-OUTPUT-PROGRESS-MAX-WORDS
        self.assertIn("INV-OUTPUT-PROGRESS-MAX-WORDS", policies)
        p_prog = policies["INV-OUTPUT-PROGRESS-MAX-WORDS"]
        self.assertEqual(p_prog["threshold"], 20)
        self.assertEqual(p_prog["unit"], "words")
        self.assertEqual(p_prog["rule_reference"], ".agents/rules/engineering-output.md")

    def test_presentation_clauses_reconciled(self) -> None:
        """Verify conflicting presentation instructions have been reconciled to reference engineering-output.md."""
        # 1. Scratchpad rule
        scratchpad_text = (self.rules_dir / "scratchpad-reasoning-transparency.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", scratchpad_text)
        self.assertIn("strictly omit visible scratchpads", scratchpad_text)

        # 2. Wave progression rule
        wave_text = (self.rules_dir / "wave-progression.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", wave_text)
        self.assertIn("Ordinary engineering completion reports in user chat do NOT render this table", wave_text)
        self.assertIn("User-Facing Completion Response Separation", wave_text)

        # 3. Progress indicators rule
        prog_text = (self.rules_dir / "progress-indicators.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", prog_text)
        self.assertIn("INV-OUTPUT-PROGRESS-MAX-WORDS", prog_text)

        # 4. Conversational communication style rule
        style_text = (self.rules_dir / "conversational-communication-style.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", style_text)
        self.assertIn("Engineering Turn Separation", style_text)

        # 5. Unified agent architecture rule
        arch_text = (self.rules_dir / "unified-agent-architecture.md").read_text(encoding="utf-8")
        self.assertIn("Internal execution ledgers", arch_text)
        self.assertIn("engineering-output.md", arch_text)

        # 6. Conformance map rule
        conf_text = (self.rules_dir / "conformance-map.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", conf_text)
        self.assertIn("Engineering Output & Concise Reporting", conf_text)

        # 7. Scratchpad prompt contract
        prompt_path = ROOT / "governance" / "decision_intelligence" / "AGENT_REASONING_SCRATCHPAD_PROMPT.md"
        prompt_text = prompt_path.read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", prompt_text)
        self.assertIn("omit visible scratchpads", prompt_text)

    def test_skills_reference_canonical_rule(self) -> None:
        """Verify that nee-governance, nee-orchestration, and nee-testing-protocol skills reference engineering-output.md."""
        skills_dir = ROOT / ".agents" / "skills"

        # nee-governance
        gov_text = (skills_dir / "nee-governance" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", gov_text)
        self.assertIn("Engineering Output Reporting Contract", gov_text)

        # nee-orchestration
        orch_text = (skills_dir / "nee-orchestration" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", orch_text)
        self.assertIn("Engineering Output Reporting", orch_text)

        # nee-testing-protocol
        test_text = (skills_dir / "nee-testing-protocol" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", test_text)

    def test_hooks_json_scratchpad_guard_reconciled(self) -> None:
        """Verify nee-scratchpad-transparency-guard in hooks.json enforces engineering-output.md."""
        hooks_path = ROOT / ".agents" / "hooks.json"
        hooks_data = json.loads(hooks_path.read_text(encoding="utf-8"))
        hooks = {h["id"]: h for h in hooks_data.get("hooks", [])}

        self.assertIn("nee-scratchpad-transparency-guard", hooks)
        guard = hooks["nee-scratchpad-transparency-guard"]
        self.assertIn("engineering-output.md", guard.get("description", ""))
        self.assertIn("engineering-output.md", guard.get("instructions", ""))
        self.assertIn("strictly omit visible scratchpads", guard.get("instructions", ""))

    def test_surface_classification_and_host_delivery_boundary(self) -> None:
        """Verify delivery surface classification: saved summary vs repo response vs IDE answer vs host cards."""
        rule_path = self.rules_dir / "engineering-output.md"
        content = rule_path.read_text(encoding="utf-8")

        # The four surfaces must be explicitly declared and classified
        self.assertIn("Saved Summary / Walkthrough", content)
        self.assertIn("Repository-Owned Response", content)
        self.assertIn("IDE Answer (Chat Stream)", content)
        self.assertIn("Host Activity Cards", content)

        # Verify enforcement authority classification
        self.assertIn("Machine-Enforced", content)
        self.assertIn("Instruction-Only", content)
        self.assertIn("Antigravity host hooks lack a final-text interception/redaction callback", content)

        # Verify host hooks spec: Antigravity hooks guide confirms supported events do not provide final text interception
        hooks_guide_path = Path.home() / ".gemini" / "antigravity" / "builtin" / "skills" / "agy-customizations" / "docs" / "hooks.md"
        if hooks_guide_path.exists():
            guide_text = hooks_guide_path.read_text(encoding="utf-8")
            supported_events = ["PreToolUse", "PostToolUse", "PreInvocation", "PostInvocation", "Stop"]
            for ev in supported_events:
                self.assertIn(ev, guide_text)
            # Confirm no PostTurn or FinalResponse hook exists in Antigravity
            self.assertNotIn("PostTurn", guide_text)
            self.assertNotIn("FinalResponse", guide_text)
            self.assertNotIn("PostCompletion", guide_text)

    def test_non_engineering_routes_preserved(self) -> None:
        """Verify that direct-answer for copilot/query, HITL decision structure, and operational safeguards remain intact."""
        rule_path = self.rules_dir / "engineering-output.md"
        content = rule_path.read_text(encoding="utf-8")

        # Non-engineering routes preserved
        self.assertIn("Copilot Mode (`nee copilot`)", content)
        self.assertIn("Knowledge Queries (`nee query`)", content)
        self.assertIn("strictly exempt from the 120/300-word engineering turn completion ceiling", content)
        self.assertIn("| Ref | Graph Path | Confidence | Verified Evidence |", content)
        self.assertIn("HITL Governance", content)
        self.assertIn("PR Queue / Branch Summaries", content)

        query_skill_text = (ROOT / ".agents" / "skills" / "nee-query" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("Easy-to-Read Executive Notes", query_skill_text)
        self.assertIn("strictly exempt from the 120/300-word engineering turn completion ceiling", query_skill_text)
        self.assertIn("| Ref | Graph Path | Confidence | Verified Evidence |", query_skill_text)

        # Runtime evidence & storage isolation preserved
        with open(self.policy_registry_path, "r", encoding="utf-8") as f:
            registry = yaml.safe_load(f)
        policy_ids = {p["id"] for p in registry.get("policies", [])}
        self.assertIn("INV-INGESTION-ENGINEERING-ISOLATION", policy_ids)
        self.assertIn("INV-GROUNDING-MANDATORY-ASSERTION", policy_ids)
        self.assertIn("INV-HITL-AMBIGUITY-MARGIN", policy_ids)

    def test_generated_agents_index_compliance(self) -> None:
        """Verify tools/generate_agents_index.py passes check and stays <= 12288 bytes."""
        from tools.generate_agents_index import check_agents_index
        errors = check_agents_index(ROOT, ROOT / "AGENTS.md")
        self.assertEqual(errors, [], f"AGENTS.md generator check failed: {errors}")

        agents_content = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("engineering-output.md", agents_content)
        self.assertIn("INV-OUTPUT-MAX-WORDS", agents_content)
        self.assertLessEqual(len(agents_content.encode("utf-8")), 12288)


if __name__ == "__main__":
    unittest.main()
