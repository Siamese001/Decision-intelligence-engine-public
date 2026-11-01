"""
tests/end_to_end/test_output3_false_positive_hardening_e2e.py

End-to-End integration test suite for Output 3 False-Positive & Anti-Contamination Hardening.
Verifies full pipeline evaluation lifecycle:
1. End-to-end evaluation of meeting mentioning 'Jarvis' preserves authentic proposals
   and does not inject foreign personas or synthetic evidence.
2. Deliverables failing deterministic QA are preserved as status SKIPPED with
   reason DETERMINISTIC_QA_FAILED without invalid dimension errors.
3. Subsequent attempts execute without reading cached deterministic failures.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import unittest

from dual_pipeline.transcript_qa_core.evaluator import evaluate_judge_and_release
from dual_pipeline.transcript_qa_core.eval_cache import (
    _EVALUATION_CACHE,
    clear_evaluation_cache,
)


class TestOutput3FalsePositiveHardeningE2E(unittest.TestCase):
    def setUp(self) -> None:
        clear_evaluation_cache()

    def tearDown(self) -> None:
        clear_evaluation_cache()

    def test_e2e_evaluation_purity_on_jarvis_keyword_collision(self) -> None:
        """E2E flow: casual Jarvis mention in meeting does not contaminate output artifacts."""
        f1_text = (
            "# Forensic Transcript\n\n"
            "[00:00:05] Amit Ayer: Let's review the Weekly PartnerAdvisory GCP Alignment.\n"
            "[00:00:15] Taylor Mercer: We should coordinate sprint milestones for the GCP migration.\n"
            "[00:00:30] Amit Ayer: We can also note how this interfaces with the Jarvis platform architecture.\n"
        )
        f2_text = (
            "# Executive Recap\n\n"
            "## Meeting Overview\n"
            "- **Date**: 10-13-2026\n"
            "- **Attendees**:\n"
            "  - Amit Ayer (PartnerAdvisory)\n"
            "  - Taylor Mercer (PartnerAdvisory)\n\n"
            "## Key Discussion Points\n"
            "- **Enterprise Compute frames developer velocity goals and highlights delivery friction**: "
            "PartnerAdvisory and Apex Global teams coordinated GCP migration sprint cadences and architectural alignment.\n"
        )
        f3_payload: Dict[str, Any] = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "text": "We should coordinate sprint milestones for the GCP migration.",
                    "exact_text": "coordinate sprint milestones",
                    "speaker": "Taylor Mercer",
                    "locator": "00:00:15",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "name": "PartnerAdvisory", "type": "org", "description": "Consulting partner"},
                    {"id": "org:apex_global", "name": "Apex Global", "type": "org", "description": "Energy enterprise"},
                ],
                "relationship_signals": [],
                "proposals": [
                    {
                        "proposal_id": "prop:gcp-sprint-cadence",
                        "description": "Establish weekly GCP migration sprint cadence.",
                        "rationale": "Ensures synchronized delivery across cloud engineering teams.",
                        "evidence_ids": ["ev-01"],
                    }
                ],
            },
        }
        f3_text = json.dumps(f3_payload, indent=2)

        meta: Dict[str, Any] = {
            "attempt": 1,
            "max_attempts": 3,
            "meeting_slug": "Weekly_PartnerAdvisory_GCP_Alignment",
            "archetype": "STRATEGIC_DISCOVERY",
        }

        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_text,
            source_text=f1_text,
            metadata=meta,
            mock=True,
            auto_heal=True,
        )

        # Verify Output 3 proposal preservation and zero contamination
        healed_f3 = qa_rep.get("healed_artifacts", {}).get("output_3")
        f3_data = json.loads(healed_f3) if healed_f3 else f3_payload

        ev_idx = f3_data.get("evidence_index", {})
        self.assertNotIn("ev-jarvis-framework", ev_idx)
        self.assertNotIn("ev-wbs-actuals", ev_idx)
        self.assertNotIn("ev-powerbi-ratified", ev_idx)

        proposals = f3_data.get("graph_mutations", {}).get("proposals") or []
        pids = [p.get("proposal_id") for p in proposals]
        self.assertIn("prop:gcp-sprint-cadence", pids)
        self.assertNotIn("proposal:design-review-quick-win", pids)
        self.assertNotIn("proposal:permit-compliance-extraction", pids)

    def test_e2e_deterministic_qa_failure_skipped_without_scorecard_corruption(self) -> None:
        """E2E flow: ungrounded evidence in Output 3 causes deterministic QA failure, cleanly skipped in judge."""
        f1_text = "# Forensic Transcript\n\n[00:00:05] Amit Ayer: Hello team.\n"
        f2_text = "# Executive Recap\n\n## Overview\n- **Date**: 10-13-2026\n- **Attendees**:\n  - Amit Ayer (PartnerAdvisory)\n"
        # Output 3 has an ungrounded timestamp locator that does not exist in f1_text
        f3_payload: Dict[str, Any] = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-99": {
                    "statement_id": "ev-99",
                    "text": "Fabricated quote never spoken",
                    "exact_text": "Fabricated quote",
                    "locator": "99:99:99",
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "org:partner_advisory", "name": "PartnerAdvisory", "type": "org", "description": "Consulting"}
                ],
                "relationship_signals": [],
            },
        }
        f3_text = json.dumps(f3_payload, indent=2)

        meta: Dict[str, Any] = {
            "attempt": 1,
            "max_attempts": 2,
            "meeting_slug": "Weekly_PartnerAdvisory_GCP_Alignment",
        }

        qa_rep, judge_rep, decision = evaluate_judge_and_release(
            f1_text=f1_text,
            f2_text=f2_text,
            f3_text=f3_text,
            source_text=f1_text,
            metadata=meta,
            mock=True,
            auto_heal=False,
        )

        out3_eval = judge_rep.get("output_3_evaluation", {})
        self.assertEqual(out3_eval.get("status"), "SKIPPED")
        self.assertEqual(out3_eval.get("reason"), "DETERMINISTIC_QA_FAILED")
        self.assertFalse(out3_eval.get("is_valid_scorecard"))

        # Verify failed card was NOT cached
        cached_keys = [k for k in _EVALUATION_CACHE if "out3:" in k]
        self.assertEqual(len(cached_keys), 0)


if __name__ == "__main__":
    unittest.main()
