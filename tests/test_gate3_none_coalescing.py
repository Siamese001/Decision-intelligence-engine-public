"""Test suite verifying null/NoneType field resilience in Gate 3.6 fragment extraction."""

from __future__ import annotations

import unittest

from dual_pipeline.transcript_qa_core.gate3 import _extract_fragment_targets


class Gate3NoneCoalescingTests(unittest.TestCase):
    """Verify that null/NoneType fields across mutations do not raise TypeError."""

    def test_extract_fragment_targets_with_none_fields(self):
        """Mutations containing None for description, rationale, etc. must not raise TypeError."""
        data = {
            "evidence_index": {
                "ev_01": {"text": None, "exact_text": None},
                "ev_02": {"text": "Valid text", "exact_text": "Valid exact"},
            }
        }
        mutations = {
            "decision_updates": [
                {"decision_id": "dec_01", "decision_question": None, "rationale": None},
                {"decision_id": None, "decision_question": "Question?", "rationale": "Rationale"},
            ],
            "risk_updates": [
                {"risk_id": "rsk_01", "description": None},
            ],
            "proposal_updates": [
                {"proposal_id": "prop_01", "proposal_text": None, "rationale": None},
            ],
            "entity_upserts": [
                {"id": "ent_01", "name": None, "description": None},
                {"id": None, "name": "Some Entity", "description": None},
            ],
        }

        # Should execute cleanly without TypeError
        targets = _extract_fragment_targets(data, mutations)
        self.assertIsInstance(targets, list)
        self.assertEqual(len(targets), 8)
        for item_id, txt in targets:
            self.assertIsInstance(item_id, str)
            self.assertIsInstance(txt, str)

    def test_check_evidence_grounding_with_none_fields(self):
        """Evidence containing None for exact_text must not raise TypeError when failing grounding."""
        from dual_pipeline.transcript_qa_core.gate3 import _check_evidence_grounding

        evidence_index = {
            "ev_01": {"exact_text": None, "text": "Something"},
        }
        # f1_text present but exact_text is None
        ok, failures = _check_evidence_grounding(evidence_index, "Some arbitrary transcript text.")
        self.assertIsInstance(ok, bool)
        self.assertIsInstance(failures, list)

    def test_run_qa_gate_3_resilient_to_none_fields(self):
        """Full Gate 3 QA execution does not crash on NoneType fields."""
        from dual_pipeline.transcript_qa_core.gate3 import run_qa_gate_3

        payload = {
            "schema_version": "1.0.0",
            "evidence_index": {
                "ev_01": {"kind": "summary", "text": "Valid text", "exact_text": "Valid text"},
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "person:test_user", "name": None, "description": None, "type": "person"},
                ],
                "relationship_signals": [],
                "decision_updates": [
                    {
                        "decision_id": "dec_01",
                        "decision_question": None,
                        "rationale": None,
                        "observed_at": "09/28/2026",
                        "stale_after": "10/28/2026",
                    },
                ],
                "risk_updates": [
                    {
                        "risk_id": "rsk_01",
                        "description": None,
                        "observed_at": "09/28/2026",
                        "stale_after": "10/28/2026",
                    },
                ],
                "proposal_updates": [
                    {"proposal_id": "prop_01", "proposal_text": None, "rationale": None},
                ],
                "action_updates": [],
            },
        }
        res = run_qa_gate_3(payload, f1_text="Valid text from transcript.")
        self.assertIn("passed", res)
        self.assertIn("failures", res)


if __name__ == "__main__":
    unittest.main()
