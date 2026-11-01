"""
tests/test_wave4_healers_and_context_unit.py

Wave 4 Unit Tests: Cross-Meeting Context, D6 Ambiguity & Output 1–3 Healer Purification.
Validates purification of compound roles, enforcement of authoritative HITL/speaker
resolutions across Output 1–3 healers, line-level attribution, and GraphDB-first precedence.
"""

from __future__ import annotations

import pytest
from typing import Any, Dict, List

from dual_pipeline.transcript_qa_core.recap_healer import (
    _build_authoritative_title_map,
    _heal_output_2_placeholder_titles,
)
from dual_pipeline.transcript_qa_core.appendix_healer import (
    _extract_attendee_details,
)
from dual_pipeline.transcript_qa_core.graph_healer import (
    _heal_roster_attendees,
)
from dual_pipeline.transcript_qa_core.graph_ontology_entities import (
    _heal_single_person_entity,
    _heal_person_and_golden_entities,
)
from dual_pipeline.transcript_stages_core.speaker_role_reconciler import (
    reconcile_speaker_role_resolutions,
    _upsert_role_relationship,
)
from dual_pipeline.transcript_stages_core.offline_synthesizer import (
    OfflineTranscriptSynthesizer,
)
from dual_pipeline.transcript_stages_core.attribution_builder import (
    AttributionSummaryBuilder,
)
from dual_pipeline.ambiguity_core.detector_d6_entity import (
    DEFAULT_ENTERPRISE_CANDIDATES,
    EntityClarificationDetector,
)
from dual_pipeline.transcript_pipeline_core.cross_meeting_context import (
    CrossMeetingContextLoader,
    CrossMeetingEntity,
)


class TestWave4RecapAndAppendixHealers:
    """Verifies Output 2 recap and appendix healers enforce atomic roles and strip compound roles."""

    def test_recap_healer_authoritative_title_map_resolutions(self):
        meta = {
            "roster": [{"name": "Amit Ayer", "role": "Lead Client Partner / Principal Architect & Technology Strategist"}],
            "hitl_role_resolutions": {
                "Amit Ayer": {"role": "Principal Architect & Technology Strategist, PartnerAdvisory"}
            },
        }
        title_map = _build_authoritative_title_map(meta)
        assert title_map["amit ayer"] == "Principal Architect & Technology Strategist"

    def test_recap_healer_replaces_compound_attendee_role(self):
        text = (
            "## Attendees\n"
            "- PartnerAdvisory: Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)\n"
            "- Apex Global Enterprises (NEA): harper Hoffman (Product Manager)\n\n"
            "## Executive Summary\nSummary text here."
        )
        meta = {
            "hitl_role_resolutions": {
                "Amit Ayer": "Principal Architect & Technology Strategist"
            }
        }
        changes: List[str] = []
        audit_log: List[Dict[str, Any]] = []
        healed = _heal_output_2_placeholder_titles(text, meta, changes, audit_log)
        assert "Amit Ayer (Principal Architect & Technology Strategist)" in healed
        assert "Lead Client Partner /" not in healed

    def test_recap_healer_appendix_header_strips_compound_role(self):
        text = (
            "# Appendix: Detailed Stakeholder Perspectives\n\n"
            "# Amit Ayer — PartnerAdvisory, Lead Client Partner / Principal Architect & Technology Strategist\n"
            "Spoken perspectives.\n"
        )
        meta = {
            "hitl_role_resolutions": {
                "Amit Ayer": "Principal Architect & Technology Strategist"
            }
        }
        changes: List[str] = []
        audit_log: List[Dict[str, Any]] = []
        healed = _heal_output_2_placeholder_titles(text, meta, changes, audit_log)
        assert "# Amit Ayer — PartnerAdvisory, Principal Architect & Technology Strategist" in healed
        assert "Lead Client Partner /" not in healed

    def test_appendix_healer_extract_attendees_enforces_authoritative_roles(self):
        text = (
            "## Attendees\n"
            "- PartnerAdvisory: Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)\n"
        )
        meta = {
            "hitl_role_resolutions": {
                "Amit Ayer": "Principal Architect & Technology Strategist, PartnerAdvisory"
            }
        }
        entries = _extract_attendee_details(text, metadata=meta)
        amit = next(e for e in entries if e[0] == "Amit Ayer")
        assert amit[1] == "Principal Architect & Technology Strategist"
        assert "/" not in amit[1]


class TestWave4GraphHealers:
    """Verifies Output 3 graph healer and ontology normalizer eliminate hardcoded compound roles."""

    def test_graph_ontology_amit_healed_to_atomic_role(self):
        ent = {
            "id": "person:amit-ayer",
            "name": "Amit Ayer",
            "role": "Lead Client Partner / Principal Architect & Technology Strategist",
            "description": "Old description",
        }
        changes: List[str] = []
        _heal_single_person_entity(ent, {"person:amit-ayer": "Principal Architect & Technology Strategist at PartnerAdvisory."}, {}, changes)
        assert ent["role"] == "Principal Architect & Technology Strategist"
        assert ent["description"] == "Principal Architect & Technology Strategist at PartnerAdvisory."

    def test_graph_ontology_single_fact_descriptions_purified(self):
        entities = [
            {"id": "person:amit-ayer", "name": "Amit Ayer"},
            {"id": "person:eduardo-juan-more", "name": "Eduardo Juan More"},
            {"id": "person:jen-pan", "name": "Jen Pan"},
        ]
        changes: List[str] = []
        _heal_person_and_golden_entities(entities, {}, changes)
        amit = next(e for e in entities if e["id"] == "person:amit-ayer")
        eduardo = next(e for e in entities if e["id"] == "person:eduardo-juan-more")
        jen = next(e for e in entities if e["id"] == "person:jen-pan")

        assert amit["description"] == "Principal Architect & Technology Strategist at PartnerAdvisory."
        assert "Atlas Lead" in eduardo["description"]
        assert "Product Manager" in jen["description"]

    def test_graph_healer_roster_attendees_refreshes_existing_person(self):
        data: Dict[str, Any] = {
            "evidence_index": {},
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:amit_ayer",
                        "type": "person",
                        "name": "Amit Ayer",
                        "role": "Lead Client Partner / Principal Architect & Technology Strategist",
                        "description": "Lead Client Partner / Principal Architect & Technology Strategist at PartnerAdvisory.",
                    }
                ],
                "relationship_signals": [],
            },
        }
        metadata = {
            "hitl_role_resolutions": {
                "Amit Ayer": "Principal Architect & Technology Strategist"
            },
            "roster": [
                {"name": "Amit Ayer", "role": "Principal Architect & Technology Strategist", "org": "PartnerAdvisory"}
            ],
        }
        changes: List[str] = []
        _heal_roster_attendees(data, data["graph_mutations"], changes, metadata)
        ent = data["graph_mutations"]["entity_upserts"][0]
        assert ent["role"] == "Principal Architect & Technology Strategist"
        assert "Lead Client Partner /" not in ent["description"]
        assert "Principal Architect & Technology Strategist" in ent["description"]


class TestWave4SpeakerRoleReconciler:
    """Verifies speaker role reconciler precedence, description overwrite, and signal pruning."""

    def test_speaker_role_reconciler_priority_auto_vs_hitl(self):
        final_data: Dict[str, Any] = {"graph_mutations": {}}
        metadata = {
            "auto_role_resolutions": {
                "Amit Ayer": {"resolved_role": "Auto Inferred Role", "provenance": "AUTO"}
            },
            "speaker_role_resolutions": {
                "Amit Ayer": {"resolved_role": "Speaker Confirmed Role", "provenance": "ROSTER_PRIOR"}
            },
            "hitl_role_resolutions": {
                "Amit Ayer": {"resolved_role": "Principal Architect & Technology Strategist", "provenance": "HITL_LEDGER", "is_human_confirmed": True}
            },
        }
        reconciled = reconcile_speaker_role_resolutions(final_data, metadata)
        person_ent = next(e for e in reconciled["graph_mutations"]["entity_upserts"] if e["name"] == "Amit Ayer")
        assert person_ent["role"] == "Principal Architect & Technology Strategist"
        assert person_ent["provenance"] == "HITL_LEDGER"

    def test_speaker_role_reconciler_overwrites_compound_description(self):
        final_data: Dict[str, Any] = {
            "graph_mutations": {
                "entity_upserts": [
                    {
                        "id": "person:amit_ayer",
                        "name": "Amit Ayer",
                        "type": "person",
                        "role": "Lead Client Partner / Principal Architect & Technology Strategist",
                        "description": "Amit Ayer is Lead Client Partner / Principal Architect & Technology Strategist.",
                    }
                ],
                "relationship_signals": [],
            }
        }
        metadata = {
            "hitl_role_resolutions": {
                "Amit Ayer": {"resolved_role": "Principal Architect & Technology Strategist", "provenance": "HITL_LEDGER"}
            }
        }
        reconciled = reconcile_speaker_role_resolutions(final_data, metadata)
        person_ent = reconciled["graph_mutations"]["entity_upserts"][0]
        assert person_ent["role"] == "Principal Architect & Technology Strategist"
        assert person_ent["description"] == "Amit Ayer is Principal Architect & Technology Strategist."

    def test_speaker_role_reconciler_prunes_competing_signals_and_orphan_roles(self):
        entity_upserts = [
            {"id": "person:amit_ayer", "name": "Amit Ayer", "type": "person"},
            {"id": "role:lead_client_partner", "name": "Lead Client Partner", "type": "role"},
            {"id": "role:director", "name": "Director", "type": "role"},
        ]
        relationship_signals = [
            {"signal_id": "sig1", "from_id": "person:amit_ayer", "to_id": "role:lead_client_partner", "relationship": "HAS_ROLE"},
            {"signal_id": "sig2", "from_id": "person:other", "to_id": "role:director", "relationship": "HAS_ROLE"},
        ]
        _upsert_role_relationship(
            entity_upserts=entity_upserts,
            relationship_signals=relationship_signals,
            role="Principal Architect & Technology Strategist",
            person_id="person:amit_ayer",
            p_slug="amit_ayer",
            ev_id="ev-1",
            ev_entry={},
            is_human=True,
            prov_str="HITL_LEDGER",
        )
        # Old role:lead_client_partner signal for amit must be pruned
        amit_signals = [r for for_id in ("person:amit_ayer", "person:amit-ayer") for r in relationship_signals if r["from_id"] == for_id]
        assert len(amit_signals) >= 1
        sig_to_ids = [s["to_id"] for s in amit_signals]
        assert any("principal-architect" in s for s in sig_to_ids)

        # Orphan role:lead_client_partner must be pruned from entity_upserts
        role_ids = [e["id"] for e in entity_upserts if e.get("type") == "role"]
        assert "role:lead_client_partner" not in role_ids
        # Non-orphan role:director remains
        assert "role:director" in role_ids

    def test_speaker_role_reconciler_reconciles_lineage_speaker_role(self):
        final_data: Dict[str, Any] = {
            "graph_mutations": {
                "proposal_updates": [
                    {
                        "proposal_id": "prop-1",
                        "lineage": {
                            "speaker": "person:amit-ayer",
                            "speaker_role": "Lead Client Partner / Principal Architect & Technology Strategist",
                        },
                    }
                ]
            }
        }
        metadata = {
            "hitl_role_resolutions": {
                "Amit Ayer": {"resolved_role": "Principal Architect & Technology Strategist"}
            }
        }
        reconciled = reconcile_speaker_role_resolutions(final_data, metadata)
        prop = reconciled["graph_mutations"]["proposal_updates"][0]
        assert prop["lineage"]["speaker_role"] == "Principal Architect & Technology Strategist"


class TestWave4OfflineSynthesizerAndAttribution:
    """Verifies offline synthesizer and attribution builder sync authoritative roles."""

    def test_offline_synthesizer_attendees_lines_syncs_hitl_roles(self):
        metadata = {
            "attendees": {
                "PartnerAdvisory": ["Amit Ayer (Lead Client Partner / Principal Architect & Technology Strategist)"]
            },
            "hitl_role_resolutions": {
                "Amit Ayer": "Principal Architect & Technology Strategist"
            },
        }
        lines = OfflineTranscriptSynthesizer._build_attendees_lines(metadata)
        joined = "\n".join(lines)
        assert "Amit Ayer (Principal Architect & Technology Strategist)" in joined
        assert "Lead Client Partner /" not in joined

    def test_attribution_builder_syncs_hitl_roles(self):
        metadata = {
            "roster": [
                {"name": "Amit Ayer", "role": "Lead Client Partner / Principal Architect & Technology Strategist", "org": "PartnerAdvisory"},
                {"name": "harper Hoffman", "role": "Product Manager", "org": "Apex Global Analytics"},
            ],
            "hitl_role_resolutions": {
                "Amit Ayer": "Principal Architect & Technology Strategist, PartnerAdvisory"
            },
        }
        summary = AttributionSummaryBuilder.build_summary(metadata, dialogue_text="Amit Ayer: Hello team.")
        assert "Amit Ayer | PartnerAdvisory | Principal Architect & Technology Strategist" in summary
        assert "Lead Client Partner /" not in summary


class TestWave4CrossMeetingAndD6Ambiguity:
    """Verifies cross-meeting context and D6 ambiguity detector live GraphDB precedence."""

    def test_d6_entity_candidates_purified_from_jim_and_compound_roles(self):
        assert "jim" not in DEFAULT_ENTERPRISE_CANDIDATES
        for token, cands in DEFAULT_ENTERPRISE_CANDIDATES.items():
            for c in cands:
                assert " / " not in c["role"]

    def test_d6_entity_detector_prioritizes_graphdb_candidates(self):
        class MockResolver:
            first_name_index = {
                "alex": [
                    {
                        "canonical_name": "Alex Sanchez",
                        "organization": "PartnerAdvisory",
                        "role": "Principal Engineer, PartnerAdvisory",
                        "basis": "graph_traversal",
                    }
                ]
            }

        detector = EntityClarificationDetector(identity_resolver=MockResolver())
        alex_cands = detector._enterprise_directory.get("alex", [])
        assert len(alex_cands) > 0
        top = alex_cands[0]
        assert top["canonical_name"] == "Alex Sanchez"
        assert top["role"] == "Principal Engineer"
        assert top["confidence"] == 0.95
        assert top["is_graph_grounded"] is True
