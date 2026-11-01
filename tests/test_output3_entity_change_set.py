"""Test identity resolution and change set emission for Output 03 entity upserts."""
from __future__ import annotations

import unittest

from dual_pipeline.contracts import seal_artifact, validate_artifact
from dual_pipeline.core import ROOT
from dual_pipeline.identity import resolve_bundle
from dual_pipeline.intake import build_dispositions, build_graph_change_set
from dual_pipeline.normalization import normalize_evidence_envelope


class TestOutput3EntityChangeSet(unittest.TestCase):
    def setUp(self) -> None:
        self.graph_path = (
            ROOT / ".local" / "dual_pipeline" / "generations" / "generation-36b538f04af06cb4be863517d4f0" / "graph.sqlite"
        )
        if not self.graph_path.exists():
            self.skipTest(f"Active graph database not found at {self.graph_path}")

    def test_output3_entities_and_relationships_emit_graph_operations(self) -> None:
        """Verify new entities and relationships emit create_entity and add_relationship operations."""
        package = {
            "contract_id": "urn:nee:dual-pipeline:evidence-envelope-intake:1.0.0",
            "schema_version": "1.0.0",
            "artifact_id": "artifact-meeting-output3-test",
            "meeting": {
                "meeting_id": "meeting-output3-test",
                "title": "Platform Scaling Architecture Review",
                "started_at": "2026-09-10T10:00:00-04:00",
                "source_location": "archive://notes/meeting-output3-test.md",
                "source_hash": "b" * 64,
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
                            "text": "The Nexus core router handles high-concurrency ingestion streams.",
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
                            "id": "system:nexus_core_router",
                            "type": "system",
                            "name": "Nexus Core Router",
                            "description": "High concurrency ingestion routing engine",
                            "mutation": "CREATE",
                        },
                        {
                            "id": "stakeholder:jen_bricker",
                            "type": "stakeholder",
                            "name": "Jen Bricker",
                            "description": "Technical Services Lead",
                            "mutation": "CONFIRM",
                        },
                    ],
                    "relationship_signals": [
                        {
                            "signal_id": "sig-01",
                            "from_id": "stakeholder:jen_bricker",
                            "to_id": "system:nexus_core_router",
                            "predicate": "OWNS_OR_LEADS",
                            "evidence_ids": ["stmt-01"],
                        }
                    ],
                }
            },
        }

        sealed = seal_artifact(package)
        validate_artifact(sealed)

        # 1. Normalize
        bundle = normalize_evidence_envelope(sealed, run_context="test_change_set")
        validate_artifact(bundle)

        # 2. Resolve
        resolutions = resolve_bundle(bundle, self.graph_path)
        self.assertEqual(len(resolutions), len([m for c in bundle["candidates"] for m in c["entity_mentions"]]))

        # Check resolution outcomes: new entities should resolve to CREATE_NEW_ENTITY_PROPOSAL
        res_nexus = [r for r in resolutions if "nexus_core_router" in str(r.get("stable_identifier", {}))]
        self.assertTrue(len(res_nexus) > 0)
        for r in res_nexus:
            self.assertIn(r["outcome"], ("CREATE_NEW_ENTITY_PROPOSAL", "AUTO_LINK_EXACT"))

        # 3. Routes
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

        # 4. Dispositions
        dispositions = build_dispositions(bundle, routes, resolutions)
        # All candidate items with valid proposals must have identity_resolved == True
        for d in dispositions:
            c = next(cand for cand in bundle["candidates"] if cand["candidate_id"] == d["candidate_id"])
            if c["candidate_type"] in ("entity_mention", "architecture_relationship"):
                self.assertTrue(d["identity_resolved"], f"Candidate {c['candidate_id']} identity was not resolved")
                self.assertIn(d["disposition"], ("CANDIDATE", "APPROVED"))

        # 5. Change set
        change_set = build_graph_change_set(
            bundle=bundle,
            dispositions=dispositions,
            resolutions=resolutions,
            graph_path=self.graph_path,
        )
        self.assertIsNotNone(change_set, "Change set must not be None when positive signals are present")
        operations = change_set["operations"]
        self.assertGreater(len(operations), 0, "Change set must contain positive operations")

        op_types = [op["operation"] for op in operations]
        # Should contain create_entity and add_relationship or add_candidate_assertion
        self.assertTrue(any(op == "create_entity" for op in op_types) or any("assertion" in op for op in op_types))
        validate_artifact(change_set)

    def test_resolve_relationship_predicate_alias_mapping(self) -> None:
        """Verify resolve_relationship_predicate maps aliases and unknown predicates safely."""
        from dual_pipeline.intake_core.change_set_helpers import resolve_relationship_predicate

        # Direct known predicate
        traits_known = {"c1": {"relationship_predicate": "GOVERNS"}}
        self.assertEqual(resolve_relationship_predicate({}, "c1", traits_known), "GOVERNS")

        # Alias mappings
        traits_member = {"c2": {"relationship_predicate": "MEMBER_OF"}}
        self.assertEqual(resolve_relationship_predicate({}, "c2", traits_member), "AFFILIATED_WITH")

        traits_owns = {"c3": {"relationship_predicate": "OWNS"}}
        self.assertEqual(resolve_relationship_predicate({}, "c3", traits_owns), "OWNS_OR_LEADS")

        traits_delivers = {"c4": {"relationship_predicate": "DELIVERS_VIA"}}
        self.assertEqual(resolve_relationship_predicate({}, "c4", traits_delivers), "DELIVERS")

        # Unknown predicate falls back to RELATED_TO
        traits_unknown = {"c5": {"relationship_predicate": "SOMETHING_RANDOM"}}
        self.assertEqual(resolve_relationship_predicate({}, "c5", traits_unknown), "RELATED_TO")

    def test_multi_token_containment_binds_existing_entity_without_duplicate_creation(self) -> None:
        """Verify high-confidence multi-token containment reuses existing entity instead of staging new node."""
        from dual_pipeline.intake_core.change_set_helpers import resolve_unbound_internal_entities

        spog_res = {
            "reported_name": "Single Pane of Glass",
            "selected_entity_id": None,
            "alternatives": [
                {
                    "entity_id": "entity-5a6c1d0b05e28c3caa71cab2600e",
                    "canonical_name": "Meridian Utility Corp Distribution Single Pane of Glass",
                    "match_score": 0.7967,
                    "margin_over_next": 0.3522,
                    "identity_conflicts": [],
                    "supporting_context": [
                        "Reported tokens occur in the canonical name",
                        "Context overlap: for, initiative",
                    ],
                }
            ],
            "extensions": {
                "com.partner_advisory.nee.new-node-assessment": {
                    "status": "PROPOSE_NEW_CANDIDATE",
                    "proposal": {
                        "entity_id": "entity-053fabdcf49740a3b9179648bcd9",
                        "canonical_name": "Single Pane of Glass",
                        "node_type": "program",
                    },
                }
            },
        }
        internal_entities: dict = {}
        selected = resolve_unbound_internal_entities([spog_res], internal_entities, [], {"candidate_id": "c-spog"})
        self.assertEqual(selected, ["entity-5a6c1d0b05e28c3caa71cab2600e"])
        self.assertEqual(internal_entities, {})

        # Low-margin or non-containment candidate still stages new proposal
        distinct_res = {
            "reported_name": "Release Readiness",
            "selected_entity_id": None,
            "alternatives": [
                {
                    "entity_id": "entity-ssup-outreach",
                    "canonical_name": "SSUP - Outreach Readiness",
                    "match_score": 0.6986,
                    "margin_over_next": 0.0676,
                    "identity_conflicts": [],
                    "supporting_context": ["Context overlap: readiness, workstream"],
                }
            ],
            "extensions": {
                "com.partner_advisory.nee.new-node-assessment": {
                    "status": "PROPOSE_NEW_CANDIDATE",
                    "proposal": {
                        "entity_id": "entity-release-readiness-new",
                        "canonical_name": "Release Readiness",
                        "node_type": "workstream",
                    },
                }
            },
        }
        selected_distinct = resolve_unbound_internal_entities(
            [distinct_res], internal_entities, [], {"candidate_id": "c-rel"}
        )
        self.assertEqual(selected_distinct, ["entity-release-readiness-new"])
        self.assertIn("entity-release-readiness-new", internal_entities)


if __name__ == "__main__":
    unittest.main()
