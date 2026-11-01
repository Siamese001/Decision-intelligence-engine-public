"""Unit tests for Output 03 schema validation with 4 foundational edge traits.

Validates:
1. Backward compatibility with legacy (from_id, relationship, to_id, evidence_ids).
2. Rich 4-trait signals (ASSERTION TYPE, CONFIDENCE, EVIDENCE sticky note, LINEAGE).
3. Field normalization: predicate <-> relationship, evidence_ids backfilled from sticky note.
4. Value bounds enforcement: confidence score clamped to [0.0, 1.0].
"""
from __future__ import annotations

import unittest

from dual_pipeline.transcript_models import (
    CanonicalSignalProjectionV57,
    EdgeConfidence,
    EdgeEvidenceStickyNote,
    EdgeLineage,
    RelationshipSignal,
)


class TestOutput3EdgeTraitsSchema(unittest.TestCase):
    def test_legacy_relationship_signal_backward_compatibility(self) -> None:
        """Legacy 3-tuple relationship signals must continue validating."""
        sig = RelationshipSignal.model_validate({
            "signal_id": "sig-legacy-01",
            "from_id": "stakeholder:amit_ayer",
            "to_id": "system:nexus_stream_router",
            "relationship": "OWNS_OR_LEADS",
            "evidence_ids": ["stmt-01"],
        })
        self.assertEqual(sig.signal_id, "sig-legacy-01")
        self.assertEqual(sig.relationship, "OWNS_OR_LEADS")
        self.assertEqual(sig.predicate, "OWNS_OR_LEADS")
        self.assertEqual(sig.evidence_ids, ["stmt-01"])
        self.assertIsNone(sig.assertion_type)
        self.assertIsNone(sig.confidence)

    def test_rich_four_traits_relationship_signal(self) -> None:
        """Rich signals with all 4 edge traits validate cleanly."""
        data = {
            "signal_id": "sig-delivery-accountability-01",
            "from_id": "stakeholder:stephan_marceau",
            "to_id": "workstream:delivery_staffing_fulfillment",
            "predicate": "DELIVERY_ACCOUNTABILITY_FOR",
            "assertion_type": "structural_accountability",
            "confidence": {
                "score": 0.82,
                "tier": "HIGH",
                "rationale": "Direct first-person commitment by Client Partner; discounted for conditionality.",
            },
            "evidence": {
                "statement_id": "stmt-conditional-staffing-accountability",
                "direct_quote": "I think it includes, right, that I'm on the hook for it, if we agree to this.",
                "locator": {
                    "kind": "timestamp",
                    "value": "00:29:36",
                },
                "turn_offset": 12789,
            },
            "lineage": {
                "source_id": "source-florida-account-planning-2026-09-18",
                "speaker": "Cameron Mercer",
                "speaker_role": "Client Partner",
                "speaker_org": "PartnerAdvisory",
                "recorded_at": "2026-09-18T09:29:36-04:00",
            },
        }
        sig = RelationshipSignal.model_validate(data)
        self.assertEqual(sig.assertion_type, "structural_accountability")
        self.assertEqual(sig.relationship, "DELIVERY_ACCOUNTABILITY_FOR")
        self.assertEqual(sig.predicate, "DELIVERY_ACCOUNTABILITY_FOR")
        self.assertEqual(sig.evidence_ids, ["stmt-conditional-staffing-accountability"])

        # Check confidence trait
        self.assertIsInstance(sig.confidence, EdgeConfidence)
        self.assertEqual(sig.confidence.score, 0.82)
        self.assertEqual(sig.confidence.tier, "HIGH")

        # Check evidence sticky note trait
        self.assertIsInstance(sig.evidence, EdgeEvidenceStickyNote)
        self.assertEqual(sig.evidence.direct_quote, "I think it includes, right, that I'm on the hook for it, if we agree to this.")
        self.assertEqual(sig.evidence.locator["value"], "00:29:36")

        # Check lineage trait
        self.assertIsInstance(sig.lineage, EdgeLineage)
        self.assertEqual(sig.lineage.speaker, "Cameron Mercer")
        self.assertEqual(sig.lineage.speaker_org, "PartnerAdvisory")

    def test_canonical_signal_projection_full_model_validation(self) -> None:
        """Full CanonicalSignalProjectionV57 validates payload containing 4 edge traits."""
        payload = {
            "schema_version": "5.7",
            "meeting_id": "meeting-20260918-roles-responsibilities",
            "classification": "confidential",
            "audiences": ["internal_consulting"],
            "evidence_index": {
                "stmt-01": {
                    "statement_id": "stmt-01",
                    "text": "I think it includes, right, that I'm on the hook for it, if we agree to this.",
                    "source_classification": "PROPOSAL",
                    "speaker": "Cameron Mercer",
                    "locator": {"kind": "timestamp", "value": "00:29:36"},
                }
            },
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "stakeholder:stephan_marceau",
                        "type": "stakeholder",
                        "name": "Cameron Mercer",
                        "mutation": "CONFIRM",
                        "evidence_ids": ["stmt-01"],
                    },
                    {
                        "id": "workstream:delivery_staffing_fulfillment",
                        "type": "workstream",
                        "name": "Delivery Staffing Fulfillment",
                        "mutation": "CREATE",
                        "evidence_ids": ["stmt-01"],
                    },
                ],
                "relationship_signals": [
                    {
                        "signal_id": "sig-01",
                        "from_id": "stakeholder:stephan_marceau",
                        "to_id": "workstream:delivery_staffing_fulfillment",
                        "predicate": "DELIVERY_ACCOUNTABILITY_FOR",
                        "assertion_type": "structural_accountability",
                        "confidence": 0.82,
                        "evidence": {
                            "statement_id": "stmt-01",
                            "direct_quote": "I think it includes, right, that I'm on the hook for it, if we agree to this.",
                            "locator": {"kind": "timestamp", "value": "00:29:36"},
                        },
                        "lineage": {
                            "source_id": "source-florida-2026-09-18",
                            "speaker": "Cameron Mercer",
                            "speaker_org": "PartnerAdvisory",
                        },
                    }
                ],
            },
            "evidence_only_statement_ids": [],
            "_strategic_analysis": "Strategic responsibility clarification between account lead and client partner.",
        }
        validated = CanonicalSignalProjectionV57.model_validate(payload)
        self.assertEqual(len(validated.graph_mutations.relationship_signals), 1)
        rel = validated.graph_mutations.relationship_signals[0]
        self.assertEqual(rel.assertion_type, "structural_accountability")
        self.assertEqual(rel.confidence, 0.82)
        self.assertEqual(rel.evidence.direct_quote, "I think it includes, right, that I'm on the hook for it, if we agree to this.")

    def test_confidence_score_out_of_bounds_fails(self) -> None:
        """Confidence score must be strictly in [0.0, 1.0]."""
        with self.assertRaises(ValueError):
            EdgeConfidence(score=1.5)
        with self.assertRaises(ValueError):
            EdgeConfidence(score=-0.1)


if __name__ == "__main__":
    unittest.main()
