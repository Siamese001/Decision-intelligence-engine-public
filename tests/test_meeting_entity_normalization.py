"""Test meeting normalization entity upserts and intent profile generation."""
from __future__ import annotations

import unittest

from dual_pipeline.contracts import validate_artifact
from dual_pipeline.core import seal_artifact
from dual_pipeline.normalization import normalize_evidence_envelope
from dual_pipeline.normalization_core.meeting_entities import (
    map_entity_type,
)


class TestMeetingEntityNormalization(unittest.TestCase):
    def test_map_entity_type_all_registered_types(self) -> None:
        """Verify all domain prefixes and types map to active creatable registry types."""
        self.assertEqual(map_entity_type("person"), "stakeholder")
        self.assertEqual(map_entity_type("person:carlos_salazar"), "stakeholder")
        self.assertEqual(map_entity_type("stakeholder"), "stakeholder")
        self.assertEqual(map_entity_type("system"), "technical_anchor")
        self.assertEqual(map_entity_type("system:rule_engine"), "technical_anchor")
        self.assertEqual(map_entity_type("technical_anchor"), "technical_anchor")
        self.assertEqual(map_entity_type("org"), "org")
        self.assertEqual(map_entity_type("org:partner_advisory"), "org")
        self.assertEqual(map_entity_type("workstream"), "workstream")
        self.assertEqual(map_entity_type("workstream:lens"), "workstream")
        self.assertEqual(map_entity_type("project"), "program")
        self.assertEqual(map_entity_type("policy"), "control")
        self.assertEqual(map_entity_type("risk"), "risk")
        self.assertEqual(map_entity_type("deliverable"), "deliverable")
        self.assertEqual(map_entity_type("open_question"), "open_question")

    def test_normalize_evidence_envelope_entity_upserts_and_profiles(self) -> None:
        """Verify normalize_evidence_envelope emits candidates and intent profiles for entity_upserts."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-test-entities",
            "meeting": {
                "meeting_id": "meeting-test-entities",
                "title": "Architecture Alignment Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-test-entities.md",
                "source_hash": "a" * 64,
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
                            "text": "The Nexus integration layer bridges data between PartnerAdvisory and the client hub.",
                            "locator": {"kind": "timestamp", "value": "01:23"},
                            "gemini_confidence": 0.95,
                        }
                    ],
                }
            ],
            "actions": [
                {
                    "action_id": "act-01",
                    "action": "Complete Nexus integration schema validation by Friday.",
                    "reported_owner": "Carlos Salazar",
                    "due_date": "2026-09-18",
                    "status": "planned",
                    "dependency": None,
                    "requested_follow_up": None,
                    "locator": {"kind": "timestamp", "value": "02:45"},
                }
            ],
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
                            "description": "Technical Services Dept Lead",
                            "mutation": "CONFIRM",
                        },
                        {
                            "id": "system:nexus",
                            "type": "system",
                            "name": "Nexus System",
                            "description": "Integration layer core engine",
                            "mutation": "CREATE",
                        },
                        {
                            "id": "org:partner_advisory",
                            "type": "org",
                            "name": "PartnerAdvisory",
                            "description": "Consulting organization",
                            "mutation": "CONFIRM",
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-01",
                            "from_id": "stakeholder:jen_bricker",
                            "to_id": "system:nexus",
                            "predicate": "OWNS_OR_LEADS",
                            "evidence_ids": ["stmt-01"],
                        },
                        {
                            "signal_id": "sig-02",
                            "from_id": "system:nexus",
                            "to_id": "org:partner_advisory",
                            "predicate": "AFFILIATED_WITH",
                            "evidence_ids": ["stmt-01"],
                        },
                    ],
                }
            },
        }

        sealed = seal_artifact(package)
        validate_artifact(sealed)

        bundle = normalize_evidence_envelope(sealed, run_context="test_run")
        validate_artifact(bundle)

        # 1 statement candidate + 1 action candidate + 3 entity candidates + 2 relationship candidates = 7 candidates
        self.assertEqual(len(bundle["candidates"]), 7)

        # Verify entity candidates are present
        entity_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "entity_mention"]
        self.assertEqual(len(entity_cands), 3)

        # Verify intent profiles exist for all mentions in the bundle
        profiles = bundle["extensions"].get("com.partner_advisory.nee.entity-intent-profiles", {})
        self.assertGreaterEqual(len(profiles), 3)

        # Check intent profile shape and content
        for c in entity_cands:
            for m in c["entity_mentions"]:
                self.assertIn(m["mention_id"], profiles)
                p = profiles[m["mention_id"]]
                self.assertEqual(p["entity_intent"], "resolve_or_create")
                self.assertEqual(p["declaration_status"], "DECLARED")
                self.assertIn(p["node_type"], ("stakeholder", "technical_anchor", "org"))

        # Relationship candidates also have intent profiles for their mentions
        rel_cands = [c for c in bundle["candidates"] if c["candidate_type"] == "architecture_relationship"]
        self.assertEqual(len(rel_cands), 2)
        for rc in rel_cands:
            for m in rc["entity_mentions"]:
                self.assertIn(m["mention_id"], profiles)
                p = profiles[m["mention_id"]]
                self.assertEqual(p["entity_intent"], "resolve_or_create")

    def test_sanitize_locator_maps_document_unit_kinds(self) -> None:
        """Verify document unit locator kinds normalize to valid sourceLocator.kind schema enums."""
        from dual_pipeline.normalization_core.meeting import _sanitize_locator

        self.assertEqual(
            _sanitize_locator({"kind": "line_block", "value": "L1-L10"}),
            {"kind": "section", "value": "line_block:L1-L10"},
        )
        self.assertEqual(
            _sanitize_locator({"kind": "paragraph", "value": "p2"}),
            {"kind": "section", "value": "paragraph:p2"},
        )
        self.assertEqual(
            _sanitize_locator({"kind": "table_block", "value": "T1"}),
            {"kind": "cell", "value": "table_block:T1"},
        )
        self.assertEqual(
            _sanitize_locator({"kind": "slide", "value": "3"}),
            {"kind": "page", "value": "slide:3"},
        )
        self.assertEqual(
            _sanitize_locator({"kind": "custom_unknown", "value": "x"}),
            {"kind": "section", "value": "custom_unknown:x"},
        )


if __name__ == "__main__":
    unittest.main()
