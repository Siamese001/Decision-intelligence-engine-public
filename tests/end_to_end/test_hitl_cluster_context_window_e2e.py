"""
tests/end_to_end/test_hitl_cluster_context_window_e2e.py

Class B E2E Receipt Test for Wave 3:
Validates best-in-class HITL context windows, 2-before/2-after boundary counts,
speaker_utterance.schema.json compliance, context_digest verification,
and 1-click cluster propagation across a 12-turn cluster.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import unittest

import jsonschema

from dual_pipeline.exceptions import HITLRequiredError
from dual_pipeline.speaker_attribution_core.review_context import SpeakerReviewContextError
from dual_pipeline.transcript_stages_core.cluster_attribution import (
    apply_cluster_attribution_resolution,
    build_cluster_review_items,
)
from dual_pipeline.transcript_stages_core.l1_attribution import (
    assemble_native_hitl_transcript,
    check_generic_speakers,
)


class TestHitlClusterContextWindowE2E(unittest.TestCase):
    """End-to-end verification of cluster review context windows, schema, and propagation."""

    def setUp(self) -> None:
        schema_path = Path("contracts/dual_pipeline/speaker_utterance.schema.json")
        with open(schema_path, "r", encoding="utf-8") as f:
            self.schema = json.load(f)
        self.review_item_schema = self.schema["$defs"]["reviewItem"]
        self.resolver = jsonschema.RefResolver.from_schema(self.schema)

        # Build a representative 14-turn transcript containing a 12-turn Speaker 1 cluster
        # with turns at the very start (turn 0) and very end (turn 13) boundaries.
        self.raw_transcript = (
            "[00:00] Speaker 1: Good morning and welcome to the quarterly strategy session.\n"
            "[00:30] Speaker 1: Today we are reviewing our primary capital allocation priorities.\n"
            "[01:00] Speaker 1: We also need to evaluate our regulatory risk posture.\n"
            "[01:30] Speaker 1: In particular, the new compliance mandates take effect next month.\n"
            "[02:00] Speaker 1: The audit team has prepared preliminary findings for our review.\n"
            "[02:30] Speaker 1: We have identified three distinct implementation scenarios.\n"
            "[03:00] Speaker 1: First, an accelerated timeline with external vendor augmentation.\n"
            "[03:30] Speaker 1: Second, an internal phased rollout spanning six months.\n"
            "[04:00] Speaker 1: Third, a targeted pilot focusing exclusively on core systems.\n"
            "[04:30] Speaker 1: Each option carries specific trade-offs regarding cost and velocity.\n"
            "[05:00] Bob: I recommend we examine the second option first due to resource constraints.\n"
            "[05:30] Speaker 1: That is a sensible recommendation given our staffing capacity.\n"
            "[06:00] Bob: Let us also review the timeline for the audit readiness checkpoint.\n"
            "[06:30] Speaker 1: Agreed, we will schedule that review for early next week."
        )

        self.metadata = {
            "project": "project_alpha",
            "meeting_name": "Quarterly Strategy Alignment",
            "date": "10/14/2026",
            "platform": "Microsoft Teams",
            "roster": [
                {"name": "Alice Martin", "role": "Managing Director", "org": "PartnerAdvisory"},
                {"name": "Bob Vance", "role": "VP Operations", "org": "Client"},
            ],
            "attendees": {
                "PartnerAdvisory": ["Alice Martin (Managing Director)"],
                "Client": ["Bob Vance (VP Operations)"],
            },
            "speaker_map": {"Bob": "Bob Vance"},
        }

    def test_e2e_cluster_context_window_and_propagation(self) -> None:
        """Verify 2-before/2-after context windows, schema conformity, and cluster propagation."""
        # 1. Gate detection raises HITLRequiredError with review_items and review_contexts
        with self.assertRaises(HITLRequiredError) as ctx:
            check_generic_speakers(
                self.raw_transcript,
                self.metadata["speaker_map"],
                is_undiarized_audio=False,
                metadata=self.metadata,
            )

        err = ctx.exception
        self.assertIn("Speaker 1", err.unmapped_speakers)
        self.assertTrue(len(err.review_items) >= 1)
        self.assertTrue(len(err.review_contexts) >= 1)
        self.assertTrue(bool(err.context_digest))
        self.assertEqual(len(err.context_digest), 64)

        review_item = err.review_items[0]
        self.assertEqual(review_item["item_type"], "homogeneous_cluster")
        # 12 turns for Speaker 1 in the transcript
        self.assertEqual(len(review_item["target_utterance_ids"]), 12)

        # 2. Schema compliance against speaker_utterance.schema.json
        jsonschema.validate(
            instance=review_item,
            schema=self.review_item_schema,
            resolver=self.resolver,
        )

        # 3. Context window inspection: 2-before / 2-after with explicit boundary counts
        # First turn (turn 0) is at the transcript start boundary -> before_unavailable = 2
        first_ctx = review_item["review_contexts"][0]
        self.assertEqual(first_ctx["before_unavailable"], 2)
        self.assertTrue(any(t["is_target"] for t in first_ctx["turns"]))

        # Last turn (turn 13) is at the transcript end boundary -> after_unavailable = 2
        last_ctx = review_item["review_contexts"][-1]
        self.assertEqual(last_ctx["after_unavailable"], 2)

        # 4. User-facing prompt inspection: verify UNRESOLVED styling and lack of n=0 interval
        self.assertIn("**UNRESOLVED — Speaker 1**", err.prompt)
        self.assertIn("Transcript start: 2 earlier turn(s) unavailable at this boundary.", err.prompt)
        self.assertIn("Transcript end: 2 later turn(s) unavailable at this boundary.", err.prompt)

        for opt in err.options:
            label = opt.get("label", "")
            # Prohibit uninformative [lo%-hi%, n=0] credible intervals
            self.assertNotIn("n=0", label)
            self.assertNotIn("[5%–95%", label)

        # 5. Stale / forged context_digest rejection (fail-closed)
        stale_digest = "deadbeef" * 8
        with self.assertRaises(SpeakerReviewContextError):
            apply_cluster_attribution_resolution(
                self.raw_transcript,
                review_item,
                resolved_speaker="Alice Martin",
                context_digest=stale_digest,
                metadata=self.metadata,
                speaker_map=self.metadata["speaker_map"],
            )

        # 6. Authentic cluster decision with valid context_digest resolves all 12 turns
        valid_digest = review_item["context_digest"]
        resolved_text, updated_map = apply_cluster_attribution_resolution(
            self.raw_transcript,
            review_item,
            resolved_speaker="Alice Martin",
            context_digest=valid_digest,
            metadata=self.metadata,
            speaker_map=self.metadata["speaker_map"],
        )

        # Confirm all 12 turns of Speaker 1 were rewritten to Alice Martin
        self.assertNotIn("Speaker 1", resolved_text)
        self.assertEqual(resolved_text.count("**Alice Martin:**"), 12)
        self.assertEqual(updated_map["Speaker 1"], "Alice Martin")

        # 7. Assembled Output 01 transcript verification
        output_01 = assemble_native_hitl_transcript(
            resolved_text,
            self.metadata,
            updated_map,
        )
        self.assertIn("# Quarterly Strategy Alignment -- HITL-Resolved Transcript", output_01)
        self.assertIn("Alice Martin (Managing Director)", output_01)
        self.assertIn("Bob Vance (VP Operations)", output_01)
        self.assertIn("## Ambiguity Lock Register", output_01)
        # ALR documents the resolution from Speaker 1 to Alice Martin
        self.assertIn("Alice Martin resolved via transcript attribution", output_01)
        # Dialogue body itself has zero generic Speaker 1 tokens
        dialogue_section = output_01.split("## Ambiguity Lock Register")[0]
        self.assertNotIn("Speaker 1", dialogue_section)


if __name__ == "__main__":
    unittest.main()
