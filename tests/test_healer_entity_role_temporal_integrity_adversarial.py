"""
tests/test_healer_entity_role_temporal_integrity_adversarial.py

Adversarial and regression test suite verifying Wave 2:
1. Formal name vs parenthetical nickname precedence in entity resolution.
2. Compound role splitting into atomic role:* entities with HAS_ROLE relationships.
3. Elimination of orphan duplicate stmt_hitl_role_* keys in evidence_index.
4. Preservation of verified C0/HITL roster roles against hardcoded healer defaults.
5. Single-letter initial keyword matching guard in evidence matching.
6. Dynamic date derivation for observed_at and stale_after from meeting metadata.
7. Archetype preservation for STRATEGIC_EXECUTIVE meetings.
8. Deterministic collateral grounding for LENS admin dashboard and review queue.
"""
from __future__ import annotations

import unittest
from typing import Any, Dict, List

from dual_pipeline.transcript_metadata_core.entity_resolver_graph import _clean_node_names
from dual_pipeline.transcript_qa_core.graph_healer import heal_graph_projection
from dual_pipeline.transcript_qa_core.graph_ontology_entities import (
    _find_evidence_id_for_concept,
    _heal_single_person_entity,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    _reconcile_single_resolution,
    _split_compound_roles,
    _upsert_role_relationship,
)


class TestHealerEntityRoleTemporalIntegrityAdversarial(unittest.TestCase):
    """Verifies deterministic post-processing integrity and anti-corruption invariants."""

    def test_01_nickname_precedence_preserves_formal_name(self) -> None:
        """Formal two-part names must not be overwritten by parenthetical nicknames."""
        c_raw, c_name, clean_canon, clean_disp, nicknames = _clean_node_names(
            canon_name="Eduardo (Ed) More",
            disp_name=None,
        )
        self.assertEqual(clean_canon, "Eduardo More")
        self.assertEqual(c_name, "Eduardo More")
        self.assertEqual(nicknames, ["Ed"])

    def test_02_compound_role_splitting_atomic_roles(self) -> None:
        """Compound roles with '&', '/', or 'and' must split into atomic roles."""
        roles_amp = _split_compound_roles("Principal Architect & Technology Strategist")
        self.assertEqual(roles_amp, ["Principal Architect", "Technology Strategist"])

        roles_slash = _split_compound_roles("Rule Specialist / VP")
        self.assertEqual(roles_slash, ["Rule Specialist", "VP"])

        roles_and = _split_compound_roles("Product Manager and Lead Engineer")
        self.assertEqual(roles_and, ["Product Manager", "Lead Engineer"])

    def test_03_upsert_role_relationship_atomic_nodes_and_signals(self) -> None:
        """Compound titles produce multiple atomic role entities and HAS_ROLE signals."""
        entity_upserts: List[Dict[str, Any]] = []
        relationship_signals: List[Dict[str, Any]] = []
        ev_entry = {"evidence_id": "ev_hitl_role_amit_ayer"}

        _upsert_role_relationship(
            entity_upserts=entity_upserts,
            relationship_signals=relationship_signals,
            role="Principal Architect & Technology Strategist",
            person_id="person:amit-ayer",
            p_slug="amit_ayer",
            ev_id="ev_hitl_role_amit_ayer",
            ev_entry=ev_entry,
            is_human=True,
            prov_str="HUMAN_OPERATOR",
        )

        role_ids = [e["id"] for e in entity_upserts]
        self.assertIn("role:principal-architect", role_ids)
        self.assertIn("role:technology-strategist", role_ids)
        self.assertNotIn("role:principal-architect-technology-strategist", role_ids)

        sig_to_ids = [s["to_id"] for s in relationship_signals]
        self.assertIn("role:principal-architect", sig_to_ids)
        self.assertIn("role:technology-strategist", sig_to_ids)

    def test_04_reconcile_single_resolution_no_duplicate_stmt_orphan_keys(self) -> None:
        """evidence_index must not contain duplicate unreferenced stmt_hitl_role_* keys."""
        res = {
            "person_name": "Eduardo More",
            "resolved_role": "Rule Specialist / VP",
            "organization": "Apex Global Enterprises",
            "provenance": "HUMAN_OPERATOR",
            "is_human_confirmed": True,
            "resolution_id": "res-01",
            "ledger_entry_id": "led-01",
        }
        entity_upserts: List[Dict[str, Any]] = []
        relationship_signals: List[Dict[str, Any]] = []
        evidence_index: Dict[str, Any] = {}

        _reconcile_single_resolution(
            res=res,
            entity_upserts=entity_upserts,
            relationship_signals=relationship_signals,
            evidence_index=evidence_index,
        )

        self.assertIn("ev_hitl_role_eduardo-more", evidence_index)
        self.assertNotIn("stmt_hitl_role_eduardo-more", evidence_index)

    def test_05_verified_roster_role_preserved_against_hardcoded_overwrite(self) -> None:
        """C0/HITL verified role 'Principal Architect & Technology Strategist' is preserved."""
        ent = {
            "id": "person:amit-ayer",
            "type": "person",
            "name": "Amit Ayer",
            "role": "Principal Architect & Technology Strategist",
            "properties": {"role": "Principal Architect & Technology Strategist", "organization": "PartnerAdvisory"},
            "evidence_ids": ["ev_hitl_role_amit_ayer"],
        }
        single_facts = {
            "person:amit-ayer": "Lead Client Partner at PartnerAdvisory.",
        }
        evidence_index = {
            "ev_hitl_role_amit_ayer": {"text": "Verified Principal Architect & Technology Strategist"}
        }
        changes: List[str] = []

        _heal_single_person_entity(ent, single_facts, evidence_index, changes)
        self.assertEqual(ent["description"], "Principal Architect & Technology Strategist at PartnerAdvisory.")
        self.assertEqual(ent["evidence_ids"], ["ev_hitl_role_amit_ayer"])

    def test_06_single_letter_initial_evidence_matching_guard(self) -> None:
        """Evidence concept search must not match single-letter initials like 'c' against arbitrary text."""
        evidence_index = {
            "ev-01": {"text": "The wind turbine gearbox component failed inspection.", "exact_text": ""},
            "ev-02": {"text": "Discussion between team members regarding timeline.", "exact_text": ""},
        }
        # Searching for 'alex c' should match 'alex' (len>=3) and ignore 'c' (len < 3)
        matched = _find_evidence_id_for_concept(evidence_index, ["alex", "c"], fallback_id="ev-02")
        # Since 'alex' is not in ev-01 or ev-02, it must not match the 'c' in 'component' or 'inspection'
        self.assertEqual(matched, ["ev-02"])

    def test_07_dynamic_date_derivation_from_metadata(self) -> None:
        """Healed decisions and risks must derive observed_at and stale_after from metadata date."""
        data: Dict[str, Any] = {
            "meeting_id": "test_meeting_20261115",
            "evidence_index": {"ev-01": {"text": "Discussion", "exact_text": "Discussion"}},
            "graph_mutations": {
                "decision_updates": [
                    {"decision_id": "dec-01", "description": "Approve architecture baseline"}
                ],
                "risk_updates": [
                    {"risk_id": "risk-01", "description": "Schedule slippage risk"}
                ],
            },
        }
        metadata = {
            "date": "2026-11-15",
            "project": "project_lens",
        }
        changes: List[str] = []

        heal_graph_projection(data, changes, metadata=metadata)

        dec = data["graph_mutations"]["decision_updates"][0]
        risk = data["graph_mutations"]["risk_updates"][0]
        self.assertEqual(dec.get("observed_at"), "11/15/2026")
        self.assertEqual(dec.get("stale_after"), "11/15/2027")
        self.assertEqual(risk.get("observed_at"), "11/15/2026")
        self.assertEqual(risk.get("stale_after"), "11/15/2027")

    def test_08_strategic_executive_archetype_preserved(self) -> None:
        """STRATEGIC_EXECUTIVE meeting archetype in metadata must not be clobbered to ARCHITECTURE_DEEP_DIVE."""
        data: Dict[str, Any] = {
            "meeting_id": "exec_sync_20261016",
            "evidence_index": {"ev-01": {"text": "Executive strategy", "exact_text": "Executive strategy"}},
            "graph_mutations": {"entity_upserts": [], "relationship_signals": []},
        }
        metadata = {
            "archetype": "STRATEGIC_EXECUTIVE",
            "date": "10/16/2026",
            "project": "project_lens",
        }
        changes: List[str] = []

        heal_graph_projection(data, changes, metadata=metadata)
        # STRATEGIC_EXECUTIVE maps to canonical MeetingArchetype.STRATEGIC_DISCOVERY and must NOT be clobbered to ARCHITECTURE_DEEP_DIVE
        self.assertEqual(data.get("meeting_archetype"), "STRATEGIC_DISCOVERY")
        self.assertEqual(data["graph_mutations"].get("meeting_archetype"), "STRATEGIC_DISCOVERY")
        self.assertNotEqual(data.get("meeting_archetype"), "ARCHITECTURE_DEEP_DIVE")

    def test_09_collateral_entity_grounding_admin_dashboard_and_review_queue(self) -> None:
        """Evidence citing admin dashboard and review queue grounds corresponding system entities."""
        data: Dict[str, Any] = {
            "meeting_id": "ocr_review_20261016",
            "evidence_index": {
                "ev-06": {
                    "text": "Operator monitoring nameplate OCR throughput in the LENS Admin Dashboard.",
                    "exact_text": "LENS Admin Dashboard screenshot showing asset statistics.",
                },
                "ev-07": {
                    "text": "Field engineers processing ambiguous extractions in the LENS Review Queue.",
                    "exact_text": "LENS Review Queue interface with pending approval cards.",
                },
            },
            "graph_mutations": {
                "entity_upserts": [
                    {"id": "system:lens", "type": "system", "name": "Project LENS"},
                ],
                "relationship_signals": [],
            },
        }
        metadata = {
            "date": "10/16/2026",
            "project": "project_lens",
        }
        changes: List[str] = []

        heal_graph_projection(data, changes, metadata=metadata)

        entity_ids = [e.get("id") for e in data["graph_mutations"]["entity_upserts"]]
        self.assertIn("system:lens-admin-dashboard", entity_ids)
        self.assertIn("system:lens-review-queue", entity_ids)


if __name__ == "__main__":
    unittest.main()
