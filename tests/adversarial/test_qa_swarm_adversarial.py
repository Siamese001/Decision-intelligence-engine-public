"""tests/adversarial/test_qa_swarm_adversarial.py

Adversarial test suite for 15-Role QA Swarm and Micro-Healing Dispatcher.
Covers mandatory edge-case categories:
1. Concurrency & Retry Loop: Strict loop termination at max_iterations on unhealable defects.
2. Anti-overfitting & Word Salad Injection: Detection and sanitization of prohibited phrases.
3. C-29 Thought-Framing Firewall: Prevention of operator thinking leaks into deliverables.
4. Invariant Bypass Attempt: Forced remediation of auto_apply=True and approval_required=False.
5. Boundary & Malformed Input: Empty, null, and non-dict/non-string payloads handled safely.
6. Credential Leak Attack: Interception and redaction of AWS/OpenAI keys and C-19 tokens.
"""
from __future__ import annotations

import json
import unittest
from typing import Any, Dict

from dual_pipeline.transcript_qa_core.micro_healing_dispatcher import (
    C29_EXPLICIT_MARKERS,
    WORD_SALAD_PATTERNS,
    MicroHealingDispatcher,
    clean_anti_overfitting_markers,
    validate_anti_overfitting,
)
from dual_pipeline.transcript_qa_core.qa_swarm import QASwarm


class MockUnhealableSwarm(QASwarm):
    """Simulates a QA swarm that persistently fails to verify loop termination."""

    def evaluate_all(self, f1: str, f2: str, f3: str) -> Dict[str, Any]:
        return {
            "swarm_passed": False,
            "agent_results": {
                "Forensic Accuracy Auditor": {
                    "role": "Forensic Accuracy Auditor",
                    "status": "FAIL",
                    "findings": ["Persistent unhealable defect"],
                },
                "Holistic Release Arbiter": {
                    "role": "Holistic Release Arbiter",
                    "status": "FAIL",
                    "findings": ["Release blocked"],
                },
            },
        }


class TestQASwarmAdversarial(unittest.TestCase):
    """Adversarial test suite for QA swarm and micro-healing dispatcher."""

    def setUp(self):
        self.swarm = QASwarm()
        self.dispatcher = MicroHealingDispatcher(max_iterations=2, qa_swarm=self.swarm)

    def test_adversarial_retry_loop_strictly_terminates_at_max_iterations(self):
        """Unhealable defects must halt at exactly max_iterations=2 with success=False."""
        unhealable_swarm = MockUnhealableSwarm()
        dispatcher = MicroHealingDispatcher(max_iterations=2, qa_swarm=unhealable_swarm)

        res = dispatcher.dispatch_healing(
            f1="Alice: Hello world.",
            f2="### Executive Summary\nOverview.\n\n### Action Items\n- [ ] Task (Owner: Alice)\n\n### Key Decisions\n- Approved.",
            f3={"canonical": True, "nodes": [], "entities": [], "assertions": []},
        )

        self.assertFalse(res.success)
        self.assertEqual(res.iterations, 2)
        self.assertEqual(res.terminal_reason, "MAX_ITERATIONS_EXCEEDED")
        self.assertIn("Forensic Accuracy Auditor", res.remaining_defects)
        self.assertEqual(len(res.audit_trail), 2)

    def test_adversarial_word_salad_injection_defense(self):
        """Injected word salad buzzwords must be detected and cleansed from recap."""
        recap_with_salad = (
            "### Executive Summary\n"
            "We leveraged an aspirational operating concept to define a bounded execution wedge.\n"
            "Our tactical task-tracker monitored synthetic monday-through-friday execution.\n\n"
            "### Key Decisions\n- Approved delivery.\n\n"
            "### Action Items\n- [ ] Deploy (Owner: Lead)\n"
        )
        graph_payload = {"canonical": True, "nodes": [], "entities": [], "assertions": []}

        # 1. Verification of detection
        is_clean, violations = validate_anti_overfitting(recap_with_salad, graph_payload)
        self.assertFalse(is_clean)
        self.assertGreaterEqual(len(violations), 3)

        # 2. Cleansing verification
        cleaned_recap, actions = clean_anti_overfitting_markers(recap_with_salad)
        for pattern in WORD_SALAD_PATTERNS:
            self.assertNotIn(pattern, cleaned_recap)

        # 3. Post-clean validation
        post_clean, post_viols = validate_anti_overfitting(cleaned_recap, graph_payload)
        self.assertTrue(post_clean)
        self.assertEqual(len(post_viols), 0)

    def test_adversarial_c29_operator_thought_framing_defense(self):
        """C-29 operator inquiry markers injected into deliverables must be intercepted."""
        f2_c29 = (
            "### Executive Summary\n"
            "The operator framed an exploratory question regarding cloud migration.\n\n"
            "### Key Decisions\n- Approved.\n\n"
            "### Action Items\n- [ ] Review (Owner: Lead)\n"
        )
        f3_c29 = {
            "canonical": True,
            "entities": [],
            "assertions": [{"id": "a1", "notes": "Hypothetical framing: What if we rebuild?"}],
        }

        is_clean, violations = validate_anti_overfitting(f2_c29, f3_c29)
        self.assertFalse(is_clean)
        self.assertTrue(any("C-29" in v for v in violations))

        # Cleansing cleans the text marker
        cleaned_f2, _ = clean_anti_overfitting_markers(f2_c29)
        self.assertNotIn("exploratory question", cleaned_f2.lower())

    def test_adversarial_graph_mutability_bypass_enforcement(self):
        """Graph mutability bypass attempts (auto_apply=True, approval_required=False) must be overridden."""
        bypass_f3 = {
            "canonical": True,
            "auto_apply": True,
            "approval_required": False,
            "entities": [],
            "assertions": [],
            "nodes": [],
        }

        # Swarm evaluation detects mutability breach
        eval_res = self.swarm.evaluate_all("Alice: Hi.", "### Summary\nHi.\n\n### Decisions\nOk.", json.dumps(bypass_f3))
        self.assertEqual(eval_res["agent_results"]["Graph Mutability Validator"]["status"], "FAIL")

        # Dispatcher heals by enforcing immutable governance defaults
        clean_f1 = "Alice: Hi.\nBob: Hi Alice."
        clean_f2 = "Attendees: Alice, Bob\n### Executive Summary\nHi.\n\n### Key Decisions\n- Approved.\n\n### Action Items\n- [ ] Task (Owner: Alice)"
        res = self.dispatcher.dispatch_healing(clean_f1, clean_f2, bypass_f3)

        self.assertTrue(res.success)
        self.assertFalse(res.healed_f3["auto_apply"])
        self.assertTrue(res.healed_f3["approval_required"])

    def test_adversarial_boundary_empty_and_corrupt_payloads(self):
        """Boundary empty string, None, and corrupt JSON strings must not crash the dispatcher."""
        res_empty = self.dispatcher.dispatch_healing("", "", "")
        self.assertIsInstance(res_empty.success, bool)
        self.assertLessEqual(res_empty.iterations, 2)

        res_malformed_json = self.dispatcher.dispatch_healing(
            "Alice: Valid dialogue turn.",
            "Attendees: Alice\n### Executive Summary\nSummary.\n\n### Key Decisions\n- Approved.\n\n### Action Items\n- [ ] Task (Owner: Alice)",
            "{bad json: not closed",
        )
        self.assertTrue(res_malformed_json.success)
        self.assertIsInstance(res_malformed_json.healed_f3, dict)
        self.assertTrue(res_malformed_json.healed_f3.get("canonical"))

    def test_adversarial_credential_and_c19_leak_prevention(self):
        """Cloud secrets and confidential career advisory tokens are aggressively scrubbed."""
        f1_leak = "Alice: The production secret is sk_test_redacted_placeholder and AWS key AKIA_REDACTED_EXAMPLE."
        f2_leak = (
            "Attendees: Alice\n"
            "### Executive Summary\n"
            "Targeting executive compensation band promotion through INTERNAL_ADVISORY notes.\n\n"
            "### Key Decisions\n- Approved.\n\n"
            "### Action Items\n- [ ] Review (Owner: Alice)"
        )
        f3_leak = {
            "canonical": True,
            "entities": [],
            "assertions": [],
            "token": "sk_test_redacted_placeholder",
            "advisory": "INTERNAL_ADVISORY",
        }

        # Evaluation flags security failure
        eval_res = self.swarm.evaluate_all(f1_leak, f2_leak, json.dumps(f3_leak))
        self.assertEqual(eval_res["agent_results"]["Security & PII Scanner"]["status"], "FAIL")

        # Healing scrubs tokens completely
        res = self.dispatcher.dispatch_healing(f1_leak, f2_leak, f3_leak)
        self.assertTrue(res.success)
        healed_bundle = f"{res.healed_f1}\n{res.healed_f2}\n{json.dumps(res.healed_f3)}"

        self.assertNotIn("AKIA_REDACTED_EXAMPLE", healed_bundle)
        self.assertNotIn("sk-proj-", healed_bundle)
        self.assertNotIn("sk-live-", healed_bundle)
        self.assertNotIn("executive compensation band", healed_bundle)
        self.assertNotIn("INTERNAL_ADVISORY", healed_bundle)


if __name__ == "__main__":
    unittest.main()
