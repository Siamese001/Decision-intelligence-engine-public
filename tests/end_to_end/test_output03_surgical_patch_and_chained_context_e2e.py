"""
tests/end_to_end/test_output03_surgical_patch_and_chained_context_e2e.py

End-to-End and adversarial test suite verifying Wave 3:
1. Chained context propagation from Stage 02 (executive_recap) to Stage 03 (graph extraction).
2. Output 02 surgical patcher retains transcript grounding (output_1).
3. Output 03 surgical critique patcher repairs targeted entity, relationship, and evidence defects.
4. Robustness against malformed or empty critique envelopes.
5. Cross-artifact coherence validation (X3) on patched deliverables.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

from dual_pipeline.canonical_pipeline.strategies.synthesis import (
    FullMeetingSynthesisStrategy,
    NotesMeetingSynthesisStrategy,
)
from dual_pipeline.prompts.contracts.versioned_judge_contracts import (
    MeetingXExitContract,
)
from dual_pipeline.transcript_qa_core.surgical_patcher import SurgicalPatcher


class TestOutput03SurgicalPatchAndChainedContextE2E(unittest.TestCase):
    """End-to-End verification of Stage 02/03 chained context and surgical remediation."""

    def test_01_chained_context_passed_to_stage03_fresh_run(self) -> None:
        """Fresh synthesis run must deliver resolved Output 02 to Stage 03."""
        mock_l1 = MagicMock()
        mock_l2_recap = MagicMock()
        mock_l2_graph = MagicMock()

        mock_l1.execute.return_value = "### Forensic Transcript\nSpeaker 1: Hello"
        mock_l2_recap.execute.return_value = "# Subject: Test Exec Recap\n\n## Meeting Objective\nDiscuss LENS."
        mock_l2_graph.execute.return_value = '{"meeting_id": "m1", "evidence_index": {}, "graph_mutations": {}}'

        mock_provider = MagicMock()
        strategy = FullMeetingSynthesisStrategy(
            provider=mock_provider,
            l2_forensic=mock_l1,
            l2_recap=mock_l2_recap,
            l2_graph=mock_l2_graph,
        )

        metadata: Dict[str, Any] = {"project": "project_lens", "date": "10/16/2026"}
        with tempfile.TemporaryDirectory() as tmpdir:
            pa_context = {"output_dir": tmpdir, "metadata": metadata}
            result = strategy.synthesize(
                source_text="audio bytes",
                metadata=metadata,
                attempt=1,
                pa_context=pa_context,
            )

            self.assertIsNotNone(result.output_2)
            self.assertIsNotNone(result.output_3)

            # Verify that mock_l2_graph.execute received non-empty executive_recap matching out_2
            call_kwargs = mock_l2_graph.execute.call_args[1]
            self.assertIn("Discuss LENS", str(call_kwargs.get("executive_recap")))

    def test_02_chained_context_passed_to_stage03_frozen_checkpoint(self) -> None:
        """When Output 02 is frozen, frozen content must be passed into Stage 03."""
        mock_l1 = MagicMock()
        mock_l2_recap = MagicMock()
        mock_l2_graph = MagicMock()

        mock_l1.execute.return_value = "### Forensic Transcript"
        mock_l2_graph.execute.return_value = '{"meeting_id": "m1", "evidence_index": {}, "graph_mutations": {}}'

        mock_provider = MagicMock()
        strategy = FullMeetingSynthesisStrategy(
            provider=mock_provider,
            l2_forensic=mock_l1,
            l2_recap=mock_l2_recap,
            l2_graph=mock_l2_graph,
        )

        mock_sm = MagicMock()
        mock_sm.is_frozen.side_effect = lambda k: k == "output_2"
        mock_sm.get_frozen_content.return_value = "# Subject: Frozen Recap\n\n## Meeting Objective\nPrior approved."

        metadata: Dict[str, Any] = {"project": "project_lens", "date": "10/16/2026"}
        with tempfile.TemporaryDirectory() as tmpdir:
            pa_context = {"output_dir": tmpdir, "metadata": metadata, "state_machine": mock_sm}
            with patch("dual_pipeline.canonical_pipeline.strategies.synthesis._resolve_state_machine", return_value=mock_sm):
                result = strategy.synthesize(
                    source_text="audio bytes",
                    metadata=metadata,
                    attempt=2,
                    pa_context=pa_context,
                )

                call_kwargs = mock_l2_graph.execute.call_args[1]
                self.assertIn("Prior approved", str(call_kwargs.get("executive_recap")))

    def test_03_output2_surgical_patch_passes_transcript_grounding(self) -> None:
        """SurgicalPatcher.apply_surgical_patch must pass output_1 to DeterministicHealer.heal_output_2."""
        orig_recap = (
            "# Subject: Initial Recap\n\n"
            "## Meeting Objective\n"
            "Original objective text.\n\n"
            "## Key Discussion Points & Findings\n"
            "Key discussion points.\n\n"
            "## Action & Governance Ledger\n"
            "No formal actions.\n"
        )
        transcript = "**Attendees:** Eduardo More, Amit Ayer\n**Date:** 10/16/2026"
        metadata = {"date": "10/16/2026", "attendees": ["Eduardo More", "Amit Ayer"]}

        patched, audit = SurgicalPatcher.apply_surgical_patch(
            original_text=orig_recap,
            target_section="Meeting Objective",
            new_section_content="Surgically updated objective covering LENS Phase 1 delivery.",
            auto_heal=True,
            metadata=metadata,
            output_1=transcript,
        )

        self.assertIn("Surgically updated objective covering LENS Phase 1 delivery.", patched)
        self.assertIn("Key discussion points.", patched)
        self.assertTrue(audit.get("target_found"))

    def test_04_output3_surgical_critique_patch_repairs_entities_and_signals(self) -> None:
        """apply_output3_critique_patch must surgically repair missing entities/signals without full resynthesis."""
        orig_payload = {
            "meeting_id": "ed_amit_20261016",
            "evidence_index": {
                "ev-01": {"text": "Discussion between Ed and Amit.", "exact_text": "Discussion between Ed and Amit."}
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:lens", "type": "system", "name": "Project LENS"}
                ],
                "relationship_signals": [],
            },
        }
        orig_json = json.dumps(orig_payload)

        critique_env = {
            "entity_upserts": [
                {
                    "id": "person:eduardo-more",
                    "type": "person",
                    "name": "Eduardo More",
                    "role": "Atlas Lead",
                    "evidence_ids": ["ev-01"],
                },
                {
                    "id": "person:amit-ayer",
                    "type": "person",
                    "name": "Amit Ayer",
                    "role": "Principal Architect & Technology Strategist",
                    "evidence_ids": ["ev-01"],
                },
            ],
            "relationship_signals": [
                {
                    "signal_id": "sig-role-ed-more",
                    "from_id": "person:eduardo-more",
                    "to_id": "role:atlas-lead",
                    "relationship": "HAS_ROLE",
                    "evidence_ids": ["ev-01"],
                }
            ],
            "evidence_index": {
                "ev-roster": {
                    "text": "Eduardo More (Atlas Lead), Amit Ayer (Principal Architect & Technology Strategist)",
                    "exact_text": "Eduardo More (Atlas Lead), Amit Ayer (Principal Architect & Technology Strategist)",
                    "speaker": "Roster",
                }
            },
        }

        metadata = {"date": "10/16/2026", "attendees": ["Eduardo More", "Amit Ayer"]}

        patched_json, audit = SurgicalPatcher.apply_output3_critique_patch(
            original_json=orig_json,
            critique_envelope=critique_env,
            metadata=metadata,
            output_1="Eduardo More and Amit Ayer discussed LENS architecture.",
        )

        self.assertTrue(audit.get("patch_applied"))
        patched_data = json.loads(patched_json)
        ent_ids = [e["id"] for e in patched_data["graph_mutations"]["entity_upserts"]]
        self.assertIn("person:eduardo-more", ent_ids)
        self.assertIn("person:amit-ayer", ent_ids)
        self.assertIn("ev-roster", patched_data["evidence_index"])

        # Check X3 cross-output coherence passes
        valid, errors = MeetingXExitContract.validate_x3_cross_output_coherence(
            f1_text="Eduardo More and Amit Ayer discussed LENS architecture.",
            f2_text="# Subject: Recap\n\n## Action & Governance Ledger\n| 1. | Task | Eduardo More |",
            f3_data=patched_data,
            metadata=metadata,
        )
        self.assertTrue(valid, f"X3 validation errors: {errors}")

    def test_05_output3_surgical_patch_adversarial_malformed_inputs(self) -> None:
        """Adversarial boundary check: invalid JSON, empty inputs, or null envelopes fail closed safely."""
        # Empty string
        res, audit = SurgicalPatcher.apply_output3_critique_patch("")
        self.assertEqual(res, "")
        self.assertFalse(audit.get("patch_applied"))

        # Invalid JSON
        res_inv, audit_inv = SurgicalPatcher.apply_output3_critique_patch("{bad json")
        self.assertEqual(res_inv, "{bad json")
        self.assertFalse(audit_inv.get("patch_applied"))

        # None critique envelope
        valid_json = '{"meeting_id": "m1", "evidence_index": {}, "graph_mutations": {}}'
        res_none, audit_none = SurgicalPatcher.apply_output3_critique_patch(valid_json, critique_envelope=None)
        self.assertIsInstance(res_none, str)
        self.assertIn("meeting_id", res_none)


if __name__ == "__main__":
    unittest.main()
