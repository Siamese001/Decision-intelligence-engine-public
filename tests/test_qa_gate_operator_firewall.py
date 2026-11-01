"""tests/test_qa_gate_operator_firewall.py

Wave 5: Automated QA Gate 2 (Gate 2.13) & Gate 3 (Gate 3.13) Enforcement
validating Constraint C-29 (Operator Thought-Framing Question Firewall & Exclusion).
"""
from __future__ import annotations

import unittest
from typing import Any, Dict

from dual_pipeline.transcript_qa_core.gate2 import (
    _check_c29_operator_thought_framing_firewall,
)
from dual_pipeline.transcript_qa_core.gate3 import (
    _check_c29_operator_thought_framing_exclusion,
)


class TestQAGateOperatorFirewall(unittest.TestCase):
    """Verifies deterministic enforcement of Constraint C-29 across Gate 2 and Gate 3."""

    def setUp(self) -> None:
        self.clean_f2 = """# Meeting Objective
Review Zone 5 confidence threshold relaxation and OCR cross-validation.

# Key Discussion Points & Findings
- **Dual-LLM Agreement**: Jake Stark walked through the 6-zone pipeline architecture and explained threshold tuning.

# Recommendations & Decisions Required
- **Relax Zone 5 Threshold**: PartnerAdvisory and client team ratified adjusting STP confidence threshold from 95% to 75%.

# Action & Governance Ledger
| Item | Owner | Target Date | Status |
| --- | --- | --- | --- |
| Benchmark OCR model precision against test set | Alex Sanchez | 10/15/2026 | In Progress |
"""

        self.clean_f3: Dict[str, Any] = {
            "schema_version": "5.7",
            "metadata": {
                "meeting_name": "Test_Sync",
                "date": "09/25/2026",
            },
            "evidence_index": {
                "ev-001": {
                    "verbatim_quote": "Jake Stark walked through the 6-zone pipeline architecture",
                    "speaker": "Jake Stark",
                    "timestamp": "10:15",
                    "kind": "summary",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:zone5_stp_pipeline",
                        "type": "system",
                        "name": "Zone 5 STP Pipeline",
                        "description": "Enterprise straight-through processing system",
                    },
                    {
                        "id": "person:jake_stark",
                        "type": "person",
                        "name": "Jake Stark",
                        "description": "PartnerAdvisory LENS Lead",
                    }
                ],
                "relationship_signals": [
                    {
                        "from_id": "person:jake_stark",
                        "to_id": "system:zone5_stp_pipeline",
                        "predicate": "maintains",
                        "_audit_rationale": "Jake Stark oversees and maintains Zone 5 pipeline tuning",
                    }
                ],
                "decision_updates": [
                    {
                        "decision_id": "decision:relax_zone5_threshold",
                        "decision_question": "Should Zone 5 straight-through threshold be lowered?",
                        "description": "Relaxed Zone 5 confidence threshold from 95% to 75%",
                        "rationale": "Client leadership ratified threshold adjustment to increase throughput",
                        "observed_at": "09/25/2026",
                    }
                ],
                "proposal_updates": [],
                "risk_updates": [],
                "action_updates": [],
            },
        }

    def test_gate2_passes_clean_output2(self) -> None:
        """Verifies clean Output 02 passes Gate 2.13."""
        passed, failures = _check_c29_operator_thought_framing_firewall(self.clean_f2)
        self.assertTrue(passed)
        self.assertEqual(len(failures), 0)

    def test_gate2_fails_on_explicit_thought_framing_marker(self) -> None:
        """Verifies Output 02 containing an explicit thought-framing marker fails Gate 2.13."""
        leaked_f2 = self.clean_f2 + """
# Action & Governance Ledger
| Investigate Amit's exploratory question on synthetic fallback | Amit Ayer | 10/12/2026 | Open |
"""
        passed, failures = _check_c29_operator_thought_framing_firewall(leaked_f2)
        self.assertFalse(passed)
        self.assertTrue(any("C29_VIOLATION" in f for f in failures))

    def test_gate2_fails_on_unratified_operator_framing_question(self) -> None:
        """Verifies that an unratified question from metadata leaking into Action section triggers failure."""
        leaked_f2 = self.clean_f2 + """
# Action & Governance Ledger
| Evaluate whether to bypass Zone 4 validation completely | Alex Sanchez | 10/12/2026 | Open |
"""
        metadata = {
            "operator_framing_questions": [
                {"query": "Evaluate whether to bypass Zone 4 validation completely?", "ratified": False}
            ]
        }
        passed, failures = _check_c29_operator_thought_framing_firewall(leaked_f2, metadata=metadata)
        self.assertFalse(passed)
        self.assertTrue(any("C29_VIOLATION" in f for f in failures))

    def test_gate2_passes_when_operator_question_was_ratified(self) -> None:
        """Verifies that if an operator question was explicitly ratified by attendees, it is permitted."""
        ratified_f2 = self.clean_f2 + """
# Action & Governance Ledger
| Evaluate whether to bypass Zone 4 validation completely | Alex Sanchez | 10/12/2026 | Open |
"""
        metadata = {
            "operator_framing_questions": [
                {"query": "Evaluate whether to bypass Zone 4 validation completely?", "ratified": True}
            ]
        }
        passed, failures = _check_c29_operator_thought_framing_firewall(ratified_f2, metadata=metadata)
        self.assertTrue(passed)

    def test_gate3_passes_clean_output3(self) -> None:
        """Verifies clean Output 03 passes Gate 3.13."""
        passed, failures, details = _check_c29_operator_thought_framing_exclusion(self.clean_f3)
        self.assertTrue(passed)
        self.assertEqual(len(failures), 0)
        self.assertTrue(details["passed"])

    def test_gate3_fails_on_banned_operator_framing_entity(self) -> None:
        """Verifies Output 03 extracting an entity node from operator framing question fails Gate 3.13."""
        bad_f3 = dict(self.clean_f3)
        bad_mutations = dict(self.clean_f3["graph_mutations"])
        bad_entities = list(bad_mutations["entity_upserts"]) + [
            {
                "id": "decision:amit_exploratory_thought_bypass",
                "type": "decision",
                "name": "Amit's exploratory inquiry on bypass",
                "description": "Operator hypothetical framing prompt",
            }
        ]
        bad_mutations["entity_upserts"] = bad_entities
        bad_f3["graph_mutations"] = bad_mutations

        passed, failures, details = _check_c29_operator_thought_framing_exclusion(bad_f3)
        self.assertFalse(passed)
        self.assertFalse(details["passed"])
        self.assertTrue(any("decision:amit_exploratory_thought_bypass" in f for f in failures))

    def test_gate3_fails_on_unratified_operator_inquiry_in_mutations(self) -> None:
        """Verifies Output 03 decision update citing unratified operator framing inquiry fails Gate 3.13."""
        bad_f3 = dict(self.clean_f3)
        bad_mutations = dict(self.clean_f3["graph_mutations"])
        bad_decisions = list(bad_mutations["decision_updates"]) + [
            {
                "decision_id": "decision:zone4_bypass",
                "decision_question": "Should Zone 4 be bypassed?",
                "description": "Bypass Zone 4 validation",
                "rationale": "Based on Amit's thought-framing query during the sync",
                "observed_at": "09/25/2026",
            }
        ]
        bad_mutations["decision_updates"] = bad_decisions
        bad_f3["graph_mutations"] = bad_mutations

        passed, failures, _ = _check_c29_operator_thought_framing_exclusion(bad_f3)
        self.assertFalse(passed)
        self.assertTrue(any("cites unratified operator thought-framing" in f for f in failures))

    def test_gate3_fails_when_mutation_matches_unratified_metadata_query(self) -> None:
        """Verifies Output 03 extracting mutation matching unratified query from metadata triggers failure."""
        bad_f3 = dict(self.clean_f3)
        bad_mutations = dict(self.clean_f3["graph_mutations"])
        bad_proposals = [
            {
                "proposal_id": "proposal:synthetic_routing_layer",
                "description": "Deploy autonomous synthetic routing layer immediately",
                "rationale": "Team proposed deployment",
            }
        ]
        bad_mutations["proposal_updates"] = bad_proposals
        bad_f3["graph_mutations"] = bad_mutations

        metadata = {
            "operator_framing_questions": [
                {"query": "Deploy autonomous synthetic routing layer immediately?", "ratified": False}
            ]
        }
        passed, failures, _ = _check_c29_operator_thought_framing_exclusion(bad_f3, metadata=metadata)
        self.assertFalse(passed)
        self.assertTrue(any("C29_VIOLATION" in f for f in failures))


if __name__ == "__main__":
    unittest.main()
