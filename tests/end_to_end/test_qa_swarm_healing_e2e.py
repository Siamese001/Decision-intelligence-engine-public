"""tests/end_to_end/test_qa_swarm_healing_e2e.py

End-to-end test suite verifying active defect detection across all 15 QA roles
and bounded micro-healing autonomy within 2 iterations.
"""
from __future__ import annotations

import json
import unittest
from typing import Any, Dict

from dual_pipeline.transcript_qa_core.micro_healing_dispatcher import (
    MicroHealingDispatcher,
    validate_anti_overfitting,
)
from dual_pipeline.transcript_qa_core.qa_swarm import SWARM_ROLES, QASwarm


class TestQASwarmHealingE2E(unittest.TestCase):
    """E2E suite verifying 15-role QA swarm active evaluation and autonomous micro-healing."""

    def setUp(self):
        self.swarm = QASwarm()
        self.dispatcher = MicroHealingDispatcher(max_iterations=2, qa_swarm=self.swarm)

    def test_e2e_seeded_defect_detection_across_all_roles(self):
        """Verifies that the 15-agent QA swarm flags 100% of seeded defects across dimensions."""
        # Seeded defective artifacts
        bad_f1 = (
            "Speaker ?: We need to discuss the migration.\n"
            "Unassigned Speaker: Agreed, but we have [GAP_DETECTED] here.\n"
            "[CORRUPT_AUDIO] during the security segment."
        )
        bad_f2 = (
            "Date: 2026-10-14\n"
            "Attendees: Alice Smith, Bob Jones, Charlie Brown\n"
            "This project sucks and wtf happened with the deployment?\n"
            "[TODO] Insert executive summary.\n"
            "### Action Items\n"
            "- [ ] Fix the deployment pipeline\n"
            "### Risks\n"
            "Critical Risk: Server failure could take down the cluster.\n"
        )
        bad_f3 = {
            "invalid_key_only": True,
            "auto_apply": True,
            "approval_required": False,
            "nodes": [
                {"id": "entity:nexus", "type": "Project"},
                {"id": "ENTITY:NEXUS", "type": "Project"},
            ],
            "api_key": "sk_test_redacted_placeholder",
        }

        eval_res = self.swarm.evaluate_all(bad_f1, bad_f2, json.dumps(bad_f3))
        self.assertFalse(eval_res["swarm_passed"])
        results = eval_res["agent_results"]
        self.assertEqual(len(results), 15)

        # Check key role failures
        expected_fails = [
            "Forensic Accuracy Auditor",
            "Speaker Attribution Validator",
            "Tone and Intent Classifier",
            "Executive Recap Summarizer",
            "Action Item Extractor",
            "Risk & Mitigation Assessor",
            "Output 3 Schema Validator",
            "Entity Coreference Resolver",
            "Assertion Freshness Checker",
            "Graph Mutability Validator",
            "Security & PII Scanner",
            "Cross-Artifact Reconciler",
            "Holistic Release Arbiter",
        ]
        for role in expected_fails:
            self.assertEqual(
                results[role]["status"],
                "FAIL",
                f"Role '{role}' was expected to FAIL on seeded defect, but got {results[role]['status']}",
            )
            self.assertGreater(len(results[role]["findings"]), 0)

    def test_e2e_micro_healing_dispatcher_resolves_defects_within_two_iterations(self):
        """Verifies that the micro-healing dispatcher repairs detected defects within 2 iterations."""
        bad_f1 = (
            "Speaker ?: We need to discuss the migration.\n"
            "Unassigned Speaker: Agreed, but we have [GAP_DETECTED] here.\n"
        )
        bad_f2 = (
            "Date: 2026-10-14\n"
            "Attendees: Alice Smith, Bob Jones\n"
            "This project sucks and wtf is happening?\n"
            "[TODO] Complete overview.\n"
            "### Action Items\n"
            "- [ ] Complete the migration\n"
            "### Risks & Mitigations\n"
            "Critical Risk: Data loss if migration fails.\n"
        )
        bad_f3 = {
            "nodes": [
                {"id": "node:test", "name": "Test Node"},
                {"id": "NODE:TEST", "name": "Test Node Duplicate"},
            ],
            "auto_apply": True,
            "approval_required": False,
            "secret": "sk_test_redacted_placeholder",
        }

        meta = {"attendees": ["Alice Smith", "Bob Jones"]}
        dispatch_res = self.dispatcher.dispatch_healing(bad_f1, bad_f2, bad_f3, metadata=meta)

        self.assertTrue(dispatch_res.success)
        self.assertLessEqual(dispatch_res.iterations, 2)
        self.assertEqual(dispatch_res.terminal_reason, "ALL_QA_ROLES_PASSED")
        self.assertEqual(len(dispatch_res.remaining_defects), 0)

        # Verify healed properties
        # Output 1: speaker placeholder and gap resolved
        self.assertNotIn("Speaker ?", dispatch_res.healed_f1)
        self.assertNotIn("[GAP_DETECTED]", dispatch_res.healed_f1)
        self.assertIn("Alice Smith", dispatch_res.healed_f1)

        # Output 2: tone, summary, date, and actions healed
        self.assertNotIn("wtf", dispatch_res.healed_f2.lower())
        self.assertNotIn("sucks", dispatch_res.healed_f2.lower())
        self.assertNotIn("[TODO]", dispatch_res.healed_f2)
        self.assertIn("10/14/2026", dispatch_res.healed_f2)
        self.assertIn("### Executive Summary", dispatch_res.healed_f2)
        self.assertIn("Owner:", dispatch_res.healed_f2)

        # Output 3: mutability, coreference, security healed
        healed_f3 = dispatch_res.healed_f3
        self.assertFalse(healed_f3.get("auto_apply"))
        self.assertTrue(healed_f3.get("approval_required"))
        self.assertEqual(len(healed_f3["nodes"]), 1)
        self.assertNotIn("sk-ant-api03", json.dumps(healed_f3))

    def test_e2e_clean_artifacts_pass_without_unnecessary_healing(self):
        """Verifies that clean artifacts pass immediately without triggering redundant iterations."""
        clean_f1 = (
            "Alice Smith: Good morning team, let us review the sprint.\n"
            "Bob Jones: Everything is looking on track for release."
        )
        clean_f2 = (
            "Date: 10/14/2026\n"
            "Attendees: Alice Smith, Bob Jones\n"
            "### Executive Summary\n"
            "The team reviewed sprint progress and confirmed release schedule.\n\n"
            "### Key Decisions\n"
            "- Approved production deployment date.\n\n"
            "### Action Items\n"
            "- [ ] Tag release candidate (Owner: Alice Smith)\n"
        )
        clean_f3 = {
            "canonical": True,
            "contract_id": "urn:nee:graph:release:1.0.0",
            "schema_version": "1.0.0",
            "auto_apply": False,
            "approval_required": True,
            "nodes": [{"id": "node:release", "type": "Artifact"}],
            "entities": [],
            "assertions": [],
        }

        meta = {"attendees": ["Alice Smith", "Bob Jones"]}
        res = self.dispatcher.dispatch_healing(clean_f1, clean_f2, clean_f3, metadata=meta)

        self.assertTrue(res.success)
        self.assertEqual(res.iterations, 0)
        self.assertEqual(res.terminal_reason, "ALL_QA_ROLES_PASSED")

    def test_e2e_anti_overfitting_validation_on_healed_outputs(self):
        """Verifies that healed deliverables pass anti-overfitting and word salad linters."""
        f2_with_salad = (
            "Date: 10/14/2026\n"
            "Attendees: Alice Smith\n"
            "### Executive Summary\n"
            "We established an aspirational operating concept for the bounded execution wedge.\n\n"
            "### Key Decisions\n"
            "- Approved plan.\n\n"
            "### Action Items\n"
            "- [ ] Complete review (Owner: Alice Smith)\n"
        )
        clean_f1 = "Alice Smith: Meeting started and finished."
        clean_f3 = {"canonical": True, "nodes": [], "entities": [], "assertions": []}

        res = self.dispatcher.dispatch_healing(clean_f1, f2_with_salad, clean_f3)
        self.assertTrue(res.success)
        is_clean, violations = validate_anti_overfitting(res.healed_f2, res.healed_f3)
        self.assertTrue(is_clean)
        self.assertEqual(len(violations), 0)
        self.assertNotIn("aspirational operating concept", res.healed_f2)
        self.assertNotIn("bounded execution wedge", res.healed_f2)


if __name__ == "__main__":
    unittest.main()
