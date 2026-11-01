"""
tests/test_no_transcript_overfitting_gate.py

Verification suite for Wave 1: Transcript Healer Anti-Overfitting & Purity Gate.
Validates:
1. Output 3 healing purity: casual mentions of 'Jarvis' do not inject hardcoded kickoff
   proposals, foreign personas, or synthetic evidence IDs.
2. Output 2 healing purity: recap healing does not inject unanchored or foreign speaker
   personas or synthetic discussion bullets.
3. Overfitting linter gate: tools/lint_no_transcript_overfitting.py reports zero violations.
"""
from __future__ import annotations

import copy
import subprocess
import sys
import unittest
from typing import Any, Dict, List

from dual_pipeline.transcript_qa_core.graph_healer import (
    CORE_PROTECTED_ENTITY_IDS,
    heal_graph_projection,
)
from dual_pipeline.transcript_qa_core.recap_grounding_healer import (
    heal_header_attendees,
)
from dual_pipeline.transcript_qa_core.recap_key_discussion_healer import (
    heal_key_discussion_anchors,
)


class TestNoTranscriptOverfittingGate(unittest.TestCase):
    def test_output3_healing_purity_on_collision_text(self) -> None:
        """Verify casual 'Jarvis' mention does not inject foreign proposals, personas, or evidence."""
        data: Dict[str, Any] = {
            "schema_version": "5.7",
            "evidence_index": {
                "ev-01": {
                    "statement_id": "ev-01",
                    "text": "PartnerAdvisory and Apex Global aligned on cloud modernization milestones.",
                    "exact_text": "cloud modernization",
                    "speaker": "Amit Ayer",
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
                        "proposal_id": "prop:cloud-sprint",
                        "description": "Establish weekly cloud modernization sprint cadence.",
                        "rationale": "Align delivery teams across milestones.",
                        "evidence_ids": ["ev-01"],
                    }
                ],
            },
        }
        metadata = {
            "source_text": "Amit mentioned the Jarvis platform in passing while discussing architecture roadmap.",
            "output_1": "Amit Ayer: Amit mentioned the Jarvis platform in passing while discussing architecture roadmap.",
        }
        changes: List[str] = []
        healed = heal_graph_projection(copy.deepcopy(data), changes, metadata=metadata)

        # 1. Prohibited synthetic evidence must not be present
        ev_idx = healed.get("evidence_index", {})
        self.assertNotIn("ev-jarvis-framework", ev_idx)
        self.assertNotIn("ev-wbs-actuals", ev_idx)
        self.assertNotIn("ev-powerbi-ratified", ev_idx)

        # 2. Proposals must not be overwritten by hardcoded Jarvis proposals
        mutations = healed.get("graph_mutations", {})
        proposals = mutations.get("proposals") or mutations.get("proposal_updates") or []
        pids = [p.get("proposal_id") for p in proposals]
        self.assertIn("prop:cloud-sprint", pids)
        self.assertNotIn("proposal:design-review-quick-win", pids)
        self.assertNotIn("proposal:permit-compliance-extraction", pids)
        self.assertNotIn("proposal:wbs-auditor-anomalous-charges", pids)
        self.assertNotIn("proposal:spatial-file-upload-web-map", pids)

        # 3. Foreign personas must not be injected
        entities = mutations.get("entity_upserts") or mutations.get("entities") or []
        eids = [e.get("id") or e.get("entity_id") for e in entities]
        self.assertNotIn("person:sharon-diaram", eids)
        self.assertNotIn("person:daniel-brankin", eids)
        self.assertNotIn("workstream:permit-compliance", eids)

    def test_output2_recap_speaker_and_discussion_purity(self) -> None:
        """Verify recap healing does not inject unlisted personas or fabricated discussion points."""
        recap_text = (
            "# Executive Recap\n\n"
            "## Meeting Overview\n"
            "- **Date**: 10-13-2026\n"
            "- **Attendees**:\n"
            "  - Amit Ayer (PartnerAdvisory)\n"
            "  - Taylor Mercer (PartnerAdvisory)\n\n"
            "## Key Discussion Points\n"
            "- PartnerAdvisory team reviewed GCP infrastructure milestones and budget allocations.\n"
        )
        changes: List[str] = []
        healed_recap = heal_header_attendees(recap_text, changes=changes)
        self.assertNotIn("Christian Moberg", healed_recap)
        self.assertNotIn("Nathan Sutton", healed_recap)
        self.assertNotIn("Ben", healed_recap)

        healed_discussion = heal_key_discussion_anchors(recap_text, changes=changes)
        self.assertNotIn("Christian Moberg", healed_discussion)
        self.assertNotIn("01:12:06", healed_discussion)
        self.assertNotIn("Nathan Sutton", healed_discussion)

    def test_overfitting_linter_zero_violations(self) -> None:
        """Verify tools/lint_no_transcript_overfitting.py exits 0 with zero violations."""
        proc = subprocess.run(
            [sys.executable, "tools/lint_no_transcript_overfitting.py"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Linter detected overfitting violations:\n{proc.stdout}\n{proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
