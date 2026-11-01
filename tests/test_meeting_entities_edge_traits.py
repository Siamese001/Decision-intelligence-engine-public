"""Unit tests for 4 Edge Traits normalization and change set propagation.

Verifies:
1. Normalization extracts ASSERTION TYPE, CONFIDENCE, EVIDENCE (sticky note), and LINEAGE.
2. Candidate bundle strictly validates against normalized-candidate-bundle schema.
3. Edge traits are preserved in bundle extensions under 'com.partner_advisory.nee.edge-traits'.
4. Graph change set carries edge traits into operation payloads and change set extensions.
5. Predicate is resolved accurately from edge traits.
"""

from __future__ import annotations

import unittest

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope


class TestMeetingEntitiesEdgeTraits(unittest.TestCase):
    def test_four_edge_traits_normalization_and_change_set(self) -> None:
        """Verify 4 Edge Traits are cleanly parsed into candidate bundle extensions and change set payloads."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-edge-traits-test",
            "meeting": {
                "meeting_id": "meeting-edge-traits-test",
                "title": "Platform Scaling Architecture Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-edge-traits-test.md",
                "source_hash": "e" * 64,
                "attendees": ["Jen Bricker", "Carlos Salazar"],
                "classification": "internal",
                "audiences": ["internal_consulting"],
                "template_version": "5.7",
            },
            "speakers": [
                {
                    "speaker_ref": "speaker-jen-bricker",
                    "reported_name": "Jen Bricker",
                    "email": "jen.bricker@consulting.example.com",
                    "role_or_org": "Technical Services Dept",
                    "statements": [
                        {
                            "statement_id": "stmt-01",
                            "kind": "technical",
                            "text": "The Nexus stream router handles all inter-department event dispatching.",
                            "locator": {"kind": "timestamp", "value": "01:23"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [],
            "adapter": {
                "name": "nee-evidence-envelope-intake",
                "version": "1.0.0",
            },
            "extensions": {
                "com.partner_advisory.nee.canonical-signal-projection": {
                    "entity_upserts": [
                        {
                            "id": "stakeholder:jen_bricker",
                            "type": "stakeholder",
                            "name": "Jen Bricker",
                            "description": "Technical Services Lead",
                            "mutation": "CONFIRM",
                        },
                        {
                            "id": "system:nexus_stream_router",
                            "type": "system",
                            "name": "Nexus Stream Router",
                            "description": "Event dispatch engine",
                            "mutation": "CREATE",
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-edge-traits-01",
                            "from_id": "stakeholder:jen_bricker",
                            "to_id": "system:nexus_stream_router",
                            "predicate": "OWNS_OR_LEADS",
                            "assertion_type": "architecture_relationship",
                            "confidence": {
                                "score": 0.94,
                                "tier": "HIGH",
                                "rationale": "Direct first-person confirmation of technical ownership",
                            },
                            "evidence": {
                                "quote": "I personally oversee the Nexus stream router deployment.",
                                "locator": {"kind": "timestamp", "value": "01:23"},
                                "turn_offset": 3,
                            },
                            "lineage": {
                                "source_id": "meeting-edge-traits-test",
                                "speaker": "Jen Bricker",
                                "org": "PartnerAdvisory",
                                "recorded_at": "2026-09-10T10:01:23-04:00",
                            },
                            "evidence_ids": ["stmt-01"],
                        }
                    ],
                }
            },
        }

        sealed_pkg = seal_artifact(package)
        validate_artifact(sealed_pkg)

        # 1. Normalize
        bundle = normalize_evidence_envelope(sealed_pkg, run_context="edge_traits_run")
        # Candidate bundle must strictly adhere to the contract schema (no unknown fields)
        validate_artifact(bundle)

        # 2. Verify candidate items
        rel_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "architecture_relationship"]
        self.assertEqual(len(rel_cands), 1)
        rel_cand = rel_cands[0]
        self.assertEqual(rel_cand["assertion_type"], "architecture_relationship")
        self.assertEqual(rel_cand["extraction_confidence"], 0.94)
        self.assertEqual(rel_cand["normalized_text"], "Jen Bricker OWNS_OR_LEADS Nexus Stream Router.")

        # Ensure no disallowed fields leaked directly onto candidate item
        self.assertNotIn("confidence_tier", rel_cand)
        self.assertNotIn("sticky_note", rel_cand)
        self.assertNotIn("lineage", rel_cand)
        self.assertNotIn("speaker_or_author", rel_cand)

        # 3. Verify bundle extensions carry rich edge traits
        edge_traits_map = bundle["extensions"].get("com.partner_advisory.nee.edge-traits", {})
        self.assertIn(rel_cand["candidate_id"], edge_traits_map)
        trait = edge_traits_map[rel_cand["candidate_id"]]

        # Trait 1: ASSERTION TYPE
        self.assertEqual(trait["assertion_type"], "architecture_relationship")
        self.assertEqual(trait["relationship_predicate"], "OWNS_OR_LEADS")

        # Trait 2: CONFIDENCE
        self.assertEqual(trait["confidence"]["score"], 0.94)
        self.assertEqual(trait["confidence"]["tier"], "HIGH")
        self.assertEqual(trait["confidence"]["rationale"], "Direct first-person confirmation of technical ownership")

        # Trait 3: EVIDENCE (Sticky note)
        self.assertIsNotNone(trait["evidence"])
        self.assertEqual(trait["evidence"]["quote"], "I personally oversee the Nexus stream router deployment.")
        self.assertEqual(trait["evidence"]["locator"]["kind"], "timestamp")
        self.assertEqual(trait["evidence"]["locator"]["value"], "01:23")

        # Trait 4: LINEAGE
        self.assertIsNotNone(trait["lineage"])
        self.assertEqual(trait["lineage"]["speaker"], "Jen Bricker")
        self.assertEqual(trait["lineage"]["org"], "PartnerAdvisory")
        self.assertEqual(trait["speaker_or_author"], "Jen Bricker")

        # 4. Identity Resolution & Dispositions
        db_path = ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite"
        if not db_path.exists():
            return  # Skip DB-dependent change set portion if graph not available

        resolutions = resolve_bundle(bundle, db_path)
        routes = [
            {
                "subject_id": c["candidate_id"],
                "intake_route": (
                    "NEW_RELATIONSHIP"
                    if c["candidate_type"] == "architecture_relationship"
                    else "NEW_ENTITY"
                    if c["candidate_type"] == "entity_mention"
                    else "NEW_ASSERTION"
                ),
                "reason_codes": [],
            }
            for c in bundle["candidates"]
        ]
        dispositions = build_dispositions(bundle, routes, resolutions)

        # 5. Build Change Set
        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=db_path,
        )
        self.assertIsNotNone(change_set)
        validate_artifact(change_set)

        # 6. Verify change set carries edge traits in payloads and extensions
        change_set_traits = change_set["extensions"].get("com.partner_advisory.nee.edge-traits", {})
        self.assertIn(rel_cand["candidate_id"], change_set_traits)

        payloads = change_set["extensions"]["com.partner_advisory.nee.operation-payloads"]
        rel_ops = [op for op in change_set["operations"] if op["operation"] == "add_relationship"]
        self.assertEqual(len(rel_ops), 1)
        rel_op = rel_ops[0]
        payload = payloads[rel_op["operation_id"]]
        self.assertEqual(payload["predicate"], "OWNS_OR_LEADS")
        self.assertIn("edge_traits", payload)
        self.assertEqual(payload["edge_traits"]["speaker_or_author"], "Jen Bricker")
        self.assertEqual(payload["edge_traits"]["confidence"]["tier"], "HIGH")


if __name__ == "__main__":
    unittest.main()
