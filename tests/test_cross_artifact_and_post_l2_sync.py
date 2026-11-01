"""
tests/test_cross_artifact_and_post_l2_sync.py

Unit tests verifying consistency between Post-L2 Review, Schema 5.7,
and Cross-Artifact Consistency QA Gates.
"""

from __future__ import annotations

import unittest

from dual_pipeline.transcript_pipeline_core.post_l2_review import _evaluate_semantic_sufficiency
from dual_pipeline.transcript_qa_core.consistency import run_cross_artifact_consistency


class TestCrossArtifactAndPostL2Sync(unittest.TestCase):
    def test_post_l2_review_accepts_schema_5_7_evidence_index_and_entity_upserts(self) -> None:
        """Verify that _check_sufficiency in Post-L2 Review recognizes Schema 5.7 structure."""
        f1_text = "Jennifer Pan: [00:01] Discussing observability requirements and architecture boundaries across enterprise systems."
        f2_text = "# Subject: Review\n\nDate: 09/14/2026\n\n# Meeting Objective\nAlign on architecture."
        f3_dict = {
            "schema_version": "5.7",
            "evidence_index": [
                {
                    "statement_id": "stmt-01",
                    "kind": "technical",
                    "text": "Observability preview table required.",
                }
            ],
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "system:lens",
                        "type": "System",
                        "mutation": "CONFIRM",
                    }
                ]
            },
        }
        is_sufficient, reasoning, directive = _evaluate_semantic_sufficiency(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_dict=f3_dict,
            metadata={"meeting_name": "Test Sync"},
        )
        self.assertTrue(is_sufficient)
        self.assertEqual(reasoning["missing_aspects"], [])
        self.assertIsNone(directive)

    def test_cross_artifact_consistency_resolves_attendees_list_and_clean_next_steps(self) -> None:
        """Verify that run_cross_artifact_consistency works with string attendees and '# Next Steps'."""
        f1_text = "Alex Sanchez: [00:01] Validating the deployment pipeline."
        f2_text = (
            "# Subject: Deploy Review | 09/14/2026\n\n"
            "Date: 09/14/2026\n"
            "Attendees:\n- Alex Sanchez (PartnerAdvisory)\n\n"
            "# Next Steps\n"
            "| # | Action Item | Accountable Owner | Responsible Party | Target Timing | Evidence IDs | Status |\n"
            "| 1 | Deploy service | Alex Sanchez | PartnerAdvisory | 09/15/2026 | ev-01 | Proposed |\n"
        )
        f3_text = '{"schema_version": "5.7"}'
        result = run_cross_artifact_consistency(
            q1={"passed": True},
            q2={"passed": True},
            q3={"passed": True},
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_text,
            metadata={"date": "09/14/2026", "attendees": ["Alex Sanchez (PartnerAdvisory)"]},
        )
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["gates"]["Gate_Cross_1_Entity_Alignment"]["passed"])
        self.assertTrue(result["gates"]["Gate_Cross_2_Temporal_Consistency"]["passed"])
        self.assertTrue(result["gates"]["Gate_Cross_3_Action_Item_Coverage"]["passed"])
        self.assertTrue(result["gates"]["Gate_Cross_4_Career_Firewall_Integrity"]["passed"])
